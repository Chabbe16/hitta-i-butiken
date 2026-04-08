from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd


LOG = logging.getLogger("hyll_product_matcher")


def _clean_column_name(name: object) -> str:
    """
    Normalize a column header into a stable key.
    Examples:
      "Sektion/Disk" -> "sektion_disk"
      " Planogram-fil " -> "planogram_fil"
    """
    text = str(name).strip().lower()
    text = re.sub(r"[^\w]+", "_", text, flags=re.UNICODE)
    text = re.sub(r"_+", "_", text).strip("_")
    return text


def _coerce_str_series(value: pd.Series) -> pd.Series:
    # Keep <NA> as <NA>, but convert everything else to a trimmed string.
    return value.astype("string").str.strip()


def _normalize_filename(value: object) -> str | None:
    """
    Normalize file names so they match even if they contain paths/extensions/case.
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None

    text = str(value).strip()
    if not text:
        return None

    # Drop any directory components.
    text = text.replace("\\", "/")
    text = text.split("/")[-1]

    # Drop common extensions.
    text = re.sub(r"\.(pdf|xlsx|xls|json|csv)$", "", text, flags=re.IGNORECASE)

    # Collapse whitespace and normalize case.
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


_SECTION_RE = re.compile(r"(\d+)")


def _normalize_section(value: object) -> int | None:
    """
    Normalize section values to an integer if possible.
    Handles inputs like:
      1, "1", "SEKTION 1", "Sektion 01", "Disk 2", "1.0"
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None

    if isinstance(value, (int,)) and not isinstance(value, bool):
        return int(value)

    text = str(value).strip()
    if not text:
        return None

    # Fast path for "1.0" / "01" etc.
    try:
        as_float = float(text.replace(",", "."))
        if as_float.is_integer():
            return int(as_float)
    except Exception:
        pass

    match = _SECTION_RE.search(text)
    if not match:
        return None
    return int(match.group(1))


def _normalize_ean(value: object) -> str | None:
    """
    Keep EAN as a string for safe Excel export, handling numbers coming in as int/float.
    Does not force zero-padding (EANs are typically 13 digits, but data can vary).
    """
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        return str(value)

    text = str(value).strip()
    if not text:
        return None

    # Strip trailing ".0" or other artifacts.
    text = re.sub(r"\.0$", "", text)
    # Keep only digits if it looks numeric.
    digits = re.sub(r"\D+", "", text)
    return digits or None


@dataclass(frozen=True)
class ColumnSpec:
    canonical: str
    aliases: tuple[str, ...]


SHELF_SPECS: tuple[ColumnSpec, ...] = (
    ColumnSpec("hyll_id", ("hyll_id", "hyllid", "hyll-id", "hyll")),
    ColumnSpec(
        "planogram_fil",
        ("planogram_fil", "planogramfil", "planogram_filnamn", "planogram-fil", "planogram fil"),
    ),
    ColumnSpec(
        "sektion_disk",
        ("sektion_disk", "sektion/disk", "sektion_disk_", "sektion", "disk", "sektion disk"),
    ),
)

PLANOGRAM_SPECS: tuple[ColumnSpec, ...] = (
    ColumnSpec("produktnamn", ("produktnamn", "produkt_namn", "produkt", "namn")),
    ColumnSpec("ean", ("ean", "gtin", "streckkod", "barcode")),
    ColumnSpec("sektion", ("sektion", "sektion_", "disk")),
    ColumnSpec("notch", ("notch", "notch_", "notchposition", "position")),
    ColumnSpec("filnamn", ("filnamn", "fil_namn", "fil", "planogram_fil")),
)


def _pick_column(df: pd.DataFrame, spec: ColumnSpec) -> str:
    """
    Find the best matching column in df for the given spec.
    Raises ValueError if none is found.
    """
    available = {_clean_column_name(c): c for c in df.columns}
    for alias in (spec.canonical, *spec.aliases):
        key = _clean_column_name(alias)
        if key in available:
            return available[key]
    raise ValueError(
        f"Missing required column for '{spec.canonical}'. "
        f"Looked for: {', '.join(spec.aliases)}. Available: {', '.join(map(str, df.columns))}"
    )


def _standardize_columns(df: pd.DataFrame, specs: Iterable[ColumnSpec]) -> pd.DataFrame:
    rename_map: dict[str, str] = {}
    for spec in specs:
        original = _pick_column(df, spec)
        rename_map[original] = spec.canonical
    out = df.rename(columns=rename_map).copy()
    return out


def _read_table(path: Path) -> pd.DataFrame:
    """
    Read Excel or JSON inputs.
    - Excel: first sheet by default.
    - JSON: expects a list-of-objects (records).
    """
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    if suffix == ".json":
        return pd.read_json(path)
    if suffix == ".csv":
        return pd.read_csv(path)
    raise ValueError(f"Unsupported input format: {path.name} (expected .xlsx/.xls/.json/.csv)")


def build_matched_dataframe(
    shelf_mapping: pd.DataFrame,
    planogram: pd.DataFrame,
    *,
    keep_unmatched_shelves: bool = False,
) -> pd.DataFrame:
    """
    Return one row per product per Hyll-ID by matching:
      shelf_mapping.planogram_fil <-> planogram.filnamn
      shelf_mapping.sektion_disk <-> planogram.sektion
    """
    shelf = _standardize_columns(shelf_mapping, SHELF_SPECS)
    plan = _standardize_columns(planogram, PLANOGRAM_SPECS)

    # Clean and normalize join keys.
    shelf["hyll_id"] = _coerce_str_series(shelf["hyll_id"])
    shelf["planogram_fil_key"] = shelf["planogram_fil"].map(_normalize_filename)
    shelf["sektion_key"] = shelf["sektion_disk"].map(_normalize_section)

    plan["filnamn_key"] = plan["filnamn"].map(_normalize_filename)
    plan["sektion_key"] = plan["sektion"].map(_normalize_section)

    # Extra safety: a single (file+section) must map to exactly one Hyll-ID.
    shelf_keys = shelf.dropna(subset=["planogram_fil_key", "sektion_key"]).copy()
    if not shelf_keys.empty:
        by_key = (
            shelf_keys.groupby(["planogram_fil_key", "sektion_key"])["hyll_id"]
            .nunique(dropna=True)
            .reset_index(name="unique_hyll_id")
        )
        ambiguous = by_key[by_key["unique_hyll_id"] > 1]
        if not ambiguous.empty:
            examples = (
                shelf_keys.merge(
                    ambiguous[["planogram_fil_key", "sektion_key"]],
                    on=["planogram_fil_key", "sektion_key"],
                    how="inner",
                )[["hyll_id", "planogram_fil", "sektion_disk"]]
                .drop_duplicates()
                .head(20)
            )
            raise ValueError(
                "Ambiguous shelf mapping: multiple Hyll-ID share the same (Planogram-fil + Sektion/Disk). "
                "Fix the shelf-mapping input. Examples:\n"
                f"{examples.to_string(index=False)}"
            )

    # Clean output fields.
    plan["produktnamn"] = _coerce_str_series(plan["produktnamn"])
    plan["ean"] = plan["ean"].map(_normalize_ean).astype("string")

    # Merge (one-to-many).
    merged = shelf.merge(
        plan[["produktnamn", "ean", "sektion_key", "notch", "filnamn_key"]],
        left_on=["planogram_fil_key", "sektion_key"],
        right_on=["filnamn_key", "sektion_key"],
        how="left" if keep_unmatched_shelves else "inner",
        validate="m:m",
    )

    out = merged.rename(
        columns={
            "hyll_id": "Hyll-ID",
            "produktnamn": "Produktnamn",
            "ean": "EAN",
            "sektion_key": "Sektion",
            "notch": "Notch",
        }
    )[["Hyll-ID", "Produktnamn", "EAN", "Sektion", "Notch"]]

    # Optional: enforce consistent dtypes.
    out["Hyll-ID"] = out["Hyll-ID"].astype("string")
    out["Produktnamn"] = out["Produktnamn"].astype("string")
    out["EAN"] = out["EAN"].astype("string")

    return out


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Match Hyllöversättning (shelf mapping) with planogram product lists.\n"
            "Outputs one row per product per Hyll-ID (one-to-many)."
        )
    )
    parser.add_argument(
        "--shelf-mapping",
        type=Path,
        default=Path("Hyllöversättnnig-json.json"),
        help="Path to Hyllöversättning input (.xlsx/.xls/.json/.csv). Default: Hyllöversättnnig-json.json",
    )
    parser.add_argument(
        "--planogram",
        type=Path,
        default=Path("planogram_sammanställning-json.json"),
        help="Path to planogram sammanställning input (.xlsx/.xls/.json/.csv). Default: planogram_sammanställning-json.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("hyll_produkter_matchade.xlsx"),
        help="Output Excel file path. Default: hyll_produkter_matchade.xlsx",
    )
    parser.add_argument(
        "--keep-unmatched-shelves",
        action="store_true",
        help="Keep shelves even if no products match (rows with empty product fields).",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")

    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    if not args.shelf_mapping.exists():
        LOG.error("Shelf mapping file not found: %s", args.shelf_mapping)
        return 2
    if not args.planogram.exists():
        LOG.error("Planogram file not found: %s", args.planogram)
        return 2

    LOG.info("Reading shelf mapping: %s", args.shelf_mapping)
    shelf_df = _read_table(args.shelf_mapping)
    LOG.info("Reading planogram: %s", args.planogram)
    plan_df = _read_table(args.planogram)

    LOG.info("Matching…")
    out_df = build_matched_dataframe(
        shelf_df,
        plan_df,
        keep_unmatched_shelves=args.keep_unmatched_shelves,
    )

    if out_df.empty:
        LOG.warning("No matches produced. Check file-name and section normalization.")
    else:
        LOG.info("Rows produced: %s", len(out_df))

    try:
        out_df.to_excel(args.output, index=False)
    except ModuleNotFoundError as exc:
        LOG.error(
            "Excel export requires 'openpyxl'. Install it and re-run. Details: %s",
            exc,
        )
        return 3

    LOG.info("Wrote: %s", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
