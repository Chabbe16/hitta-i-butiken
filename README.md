# hitta-i-butiken

## Run

1. Place the map + data files in this folder:
   - `Älmhult - 250902.svg`
   - `Hyllöversättnnig-json.json`
   - `planogram_sammanställning-json.json`
2. Start the local server: `node server.js`
3. Open: `http://127.0.0.1:5173`

## Use

- Search by product name or EAN, pick a result, and the app shows Hyll-ID + planogram.
- If the highlighted area is not correct: click the correct zone/shelf in the SVG map, then press **Bind vald yta**.

## Match shelf -> products (Python)

Creates a one-row-per-product export by matching:
- `Planogram-fil` (Hyllöversättning) with `Filnamn` (planogram)
- `Sektion/Disk` (Hyllöversättning) with `Sektion` (planogram)

Run (defaults to the JSON files in this folder):

- `python .\hyll_product_matcher.py`

Custom inputs/outputs (also supports `.xlsx`, `.xls`, `.csv`):

- `python .\hyll_product_matcher.py --shelf-mapping path\\to\\Hylloversattning.xlsx --planogram path\\to\\planogram.xlsx --output hyll_produkter_matchade.xlsx`

## Render planogram section pages (Python)

Renders pages from the planogram PDFs to PNGs (one folder per PDF under `--output-dir`).

- `python .\render_planogram_pages.py --pdf-root path\\to\\pdfs --pages 1-3`

Outputs PNGs to `.\planogram_pages\<pdfname>\page_001.png` (configurable via `--output-dir`). This is useful if you want static images.

Notes:
- Default renderer is PyMuPDF (`fitz`). If you use `--renderer pdf2image`, you must have Poppler installed on Windows.
- If your PDFs are in a zip, use `--pdf-zip` (extracts PDFs to `.\planogram_pdfs` by default):
  - `python .\render_planogram_pages.py --pdf-zip "c:\\Users\\PC\\Documents\\HITTA_I_BUTIKEN\\PLANOGRAM.zip" --pages all`

## Show planogram PDF page in the web UI

The product panel can preview the correct PDF page (section -> page via offset) by loading:
- `/planogram/<Planogram-fil>#page=<Sektion+OFFSET>`

Configure the PDF folder for the local server:
- PowerShell: `$env:PLANOGRAM_ROOT="C:\\Users\\PC\\Documents\\HITTA_I_BUTIKEN\\PDF-EXCEL\\PLANOGRAM"`
- Start: `node server.js`
