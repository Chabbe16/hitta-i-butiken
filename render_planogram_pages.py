from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path
from zipfile import ZipFile

LOG = logging.getLogger("render_planogram_pages")

_PAGES_TOKEN_RE = re.compile(r"^\s*(\d+)?\s*(?:-\s*(\d+)?)?\s*$")


def _safe_filename(value: str) -> str:
    value = value.strip()
    value = re.sub(r"[^\w\-]+", "_", value, flags=re.UNICODE).strip("_")
    return value or "unknown"


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )


def extract_pdfs_from_zip(zip_path: Path, extract_dir: Path) -> Path:
    """
    Extract PDFs from a .zip into extract_dir (safe against zip-slip).
    Returns the folder containing the extracted PDFs (extract_dir).
    """
    extract_dir.mkdir(parents=True, exist_ok=True)
    extracted = 0

    with ZipFile(zip_path) as zf:
        for member in zf.infolist():
            name = member.filename.replace("\\", "/")
            if member.is_dir():
                continue
            if not name.lower().endswith(".pdf"):
                continue

            dest = (extract_dir / name).resolve()
            root = extract_dir.resolve()
            if root not in dest.parents and dest != root:
                raise ValueError(f"Unsafe zip entry path: {member.filename}")

            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member, "r") as src, open(dest, "wb") as dst:
                dst.write(src.read())
            extracted += 1

    LOG.info("Extracted %d PDFs to %s", extracted, extract_dir)
    return extract_dir


def _parse_pages_spec(spec: str, page_count: int) -> list[int]:
    """
    Parse a pages spec into a sorted list of 1-based page numbers.

    Supported:
      - all
      - 1,2,5
      - 1-3
      - -3  (first 3 pages)
      - 10- (from page 10 to end)
    """
    text = (spec or "").strip().lower()
    if not text or text == "all":
        return list(range(1, page_count + 1))

    pages: set[int] = set()
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue

        match = _PAGES_TOKEN_RE.match(token)
        if not match:
            raise ValueError(f"Invalid --pages token: {token!r}")

        start_raw, end_raw = match.group(1), match.group(2)
        is_range = "-" in token

        if is_range:
            start = int(start_raw) if start_raw else 1
            end = int(end_raw) if end_raw else page_count
        else:
            start = int(start_raw)
            end = start

        if start < 1 or end < 1:
            raise ValueError(f"Invalid page range (must be >= 1): {token!r}")
        if start > end:
            raise ValueError(f"Invalid page range (start > end): {token!r}")

        start = max(1, start)
        end = min(page_count, end)
        for page in range(start, end + 1):
            pages.add(page)

    return sorted(pages)


def _render_with_pymupdf(pdf_path: Path, page_index: int, output_path: Path, *, dpi: int) -> None:
    import fitz  # PyMuPDF

    if page_index < 0:
        raise ValueError(f"Invalid page_index={page_index} (must be >= 0)")

    doc = fitz.open(pdf_path)
    try:
        if page_index >= doc.page_count:
            raise IndexError(
                f"Page out of range for {pdf_path.name}: page_index={page_index}, page_count={doc.page_count}"
            )
        page = doc.load_page(page_index)
        zoom = dpi / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        pix.save(output_path.as_posix())
    finally:
        doc.close()


def _render_with_pdf2image(pdf_path: Path, page_number_1based: int, output_path: Path, *, dpi: int) -> None:
    # pdf2image requires Poppler installed and available on PATH on Windows.
    from pdf2image import convert_from_path

    if page_number_1based < 1:
        raise ValueError(f"Invalid page_number={page_number_1based} (must be >= 1)")
    images = convert_from_path(
        pdf_path.as_posix(),
        dpi=dpi,
        first_page=page_number_1based,
        last_page=page_number_1based,
        fmt="png",
    )
    if not images:
        raise RuntimeError(f"No image rendered for {pdf_path.name} page {page_number_1based}")
    images[0].save(output_path.as_posix())


def render_planogram_pages(
    pdf_root: Path,
    output_dir: Path,
    *,
    pages: str,
    dpi: int,
    renderer: str,
    include_subfolders: bool,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(pdf_root.rglob("*.pdf") if include_subfolders else pdf_root.glob("*.pdf"))
    if not pdfs:
        LOG.error("No PDFs found under: %s", pdf_root)
        return

    # We use PyMuPDF to get page counts (even when rendering with pdf2image).
    import fitz  # PyMuPDF

    for pdf_path in pdfs:
        try:
            doc = fitz.open(pdf_path)
            try:
                wanted_pages = _parse_pages_spec(pages, doc.page_count)
            finally:
                doc.close()
        except Exception as exc:
            LOG.error("Failed reading PDF %s: %s", pdf_path, exc)
            continue

        pdf_out_dir = output_dir / _safe_filename(pdf_path.stem)
        pdf_out_dir.mkdir(parents=True, exist_ok=True)

        for page_number in wanted_pages:
            out_path = pdf_out_dir / f"page_{page_number:03d}.png"
            try:
                if renderer == "pymupdf":
                    _render_with_pymupdf(pdf_path, page_number - 1, out_path, dpi=dpi)
                elif renderer == "pdf2image":
                    _render_with_pdf2image(pdf_path, page_number, out_path, dpi=dpi)
                else:
                    raise ValueError(f"Unknown renderer: {renderer}")
                LOG.info("Wrote: %s (pdf=%s page=%d)", out_path, pdf_path.name, page_number)
            except Exception as exc:
                LOG.error("Failed rendering %s page %d: %s", pdf_path, page_number, exc)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Render planogram PDF pages to PNG images.\n"
            "Outputs one folder per PDF under --output-dir."
        )
    )
    parser.add_argument(
        "--pdf-root",
        type=Path,
        default=None,
        help="Folder that contains the planogram PDFs.",
    )
    parser.add_argument(
        "--pdf-zip",
        type=Path,
        default=None,
        help="Path to a .zip containing planogram PDFs. If provided, PDFs are extracted before rendering.",
    )
    parser.add_argument(
        "--extract-dir",
        type=Path,
        default=Path("planogram_pdfs"),
        help="Where to extract PDFs when using --pdf-zip. Default: planogram_pdfs",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("planogram_pages"),
        help="Where to save images. Default: planogram_pages",
    )
    parser.add_argument(
        "--pages",
        default="all",
        help='Pages to render per PDF. Examples: "all", "1", "1-3", "-3", "10-", "1-3,5". Default: all',
    )
    parser.add_argument(
        "--no-subfolders",
        action="store_true",
        help="Only look for PDFs directly under --pdf-root (no recursive search).",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=150,
        help="Render resolution (higher = larger PNG). Default: 150",
    )
    parser.add_argument(
        "--renderer",
        choices=["pymupdf", "pdf2image"],
        default="pymupdf",
        help="PDF rendering backend. Default: pymupdf",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")

    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    if args.pdf_zip and args.pdf_root:
        LOG.error("Use only one of --pdf-root or --pdf-zip.")
        return 2
    if not args.pdf_zip and not args.pdf_root:
        LOG.error("You must provide either --pdf-root or --pdf-zip.")
        return 2

    pdf_root = args.pdf_root
    if args.pdf_zip:
        if not args.pdf_zip.exists():
            LOG.error("PDF zip not found: %s", args.pdf_zip)
            return 2
        pdf_root = extract_pdfs_from_zip(args.pdf_zip, args.extract_dir)

    if not pdf_root or not pdf_root.exists():
        LOG.error("PDF root folder not found: %s", pdf_root)
        return 2

    render_planogram_pages(
        pdf_root,
        args.output_dir,
        pages=args.pages,
        dpi=args.dpi,
        renderer=args.renderer,
        include_subfolders=not args.no_subfolders,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

