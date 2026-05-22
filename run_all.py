"""
Batch runner — process every PDF in a folder through the full pipeline:
  extract.py  →  <store>_v3.json
  build_artifact.py  →  <store>_explorer.html

Usage:
    python run_all.py                          # uses default "sms pdf/" folder
    python run_all.py --pdfs "my pdfs/"       # custom folder
    python run_all.py --pdfs "sms pdf/" --store STORY  # single store only
"""

import argparse
import subprocess
import sys
from pathlib import Path

# Map PDF stem → canonical store name used in extract output.
# Add entries here when adding new stores.
STORE_NAMES = {
    "story":       "STORY",
    "h&m":         "HM",
    "hm":          "HM",
    "soho":        "SOHO",
    "terminalx":   "TERMINALX",
    "terminalx2":  "TERMINALX",
}

HERE = Path(__file__).parent
OUT_DIR = HERE / "output"


def store_name_for(pdf: Path) -> str:
    stem = pdf.stem.lower().replace(" ", "")
    return STORE_NAMES.get(stem, pdf.stem.upper().replace(" ", ""))


def run(cmd: list, label: str) -> bool:
    print(f"\n  [{label}] {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd, capture_output=False, text=True)
    if result.returncode != 0:
        print(f"  ERROR: {label} exited with code {result.returncode}")
        return False
    return True


def process_pdf(pdf: Path, only_store: str | None) -> bool:
    store = store_name_for(pdf)
    if only_store and store != only_store.upper():
        return True  # skip, not selected

    json_out = OUT_DIR / f"{store}_v3.json"
    html_out = OUT_DIR / f"{store}_explorer.html"

    print(f"\n{'=' * 60}")
    print(f"  PDF:   {pdf.name}")
    print(f"  Store: {store}")

    ok = run(
        [sys.executable, HERE / "extract.py",
         "--pdf", pdf,
         "--store", store,
         "--out", json_out],
        "extract",
    )
    if not ok:
        return False

    ok = run(
        [sys.executable, HERE / "build_artifact.py",
         "--json", json_out,
         "--out", html_out],
        "build",
    )
    return ok


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--pdfs", default="sms pdf", help="Folder containing PDF files")
    p.add_argument("--store", default=None, help="Process only this store (e.g. STORY)")
    args = p.parse_args()

    pdf_dir = Path(args.pdfs)
    if not pdf_dir.is_dir():
        sys.exit(f"ERROR: PDF folder not found: {pdf_dir}")

    pdfs = sorted(pdf_dir.glob("*.pdf"))
    if not pdfs:
        sys.exit(f"ERROR: No PDF files found in {pdf_dir}")

    print(f"Found {len(pdfs)} PDF(s) in '{pdf_dir}':")
    for pdf in pdfs:
        print(f"  {pdf.name}")

    OUT_DIR.mkdir(exist_ok=True)

    failed = []
    for pdf in pdfs:
        ok = process_pdf(pdf, args.store)
        if not ok:
            failed.append(pdf.name)

    print(f"\n{'=' * 60}")
    if failed:
        print(f"FAILED: {', '.join(failed)}")
        sys.exit(1)
    else:
        print(f"Done. HTML files are in: {OUT_DIR.resolve()}")


if __name__ == "__main__":
    main()
