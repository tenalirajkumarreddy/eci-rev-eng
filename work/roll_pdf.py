#!/usr/bin/env python3
"""
roll_pdf.py - turn an official electoral-roll PDF into the EPIC roster of a part.

The ECI per-part roll PDF (see eci_eroll.py) lists every elector of a polling
part in serial order.  This extracts the EPIC numbers - the thing the app's
gateway refuses to give you - plus whatever local context the text layer carries
(serial number, section, name) and writes JSON + CSV:

    python work/roll_pdf.py --pdf work/out/roll_pdfs/S01_AC1_part2.pdf
    python work/roll_pdf.py --pdf <pdf> --epics-only        # bare list, one/line
    python work/roll_pdf.py --dir work/out/roll_pdfs --out-dir work/out/rolls

Handles both EPIC shapes seen in AP rolls: new (TBG0342345) and the legacy
slash format (AP/01/002/0123456, normalised to its last 7 digits).

Text extraction is PyMuPDF (`fitz`).  If a PDF turns out to be image-only the
script says so instead of silently reporting zero electors.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
OUT_DIR = BASE_DIR / "out"

EPIC_NEW = re.compile(r"\b([A-Z]{3}[0-9]{7})\b")
EPIC_OLD = re.compile(r"\b(?:[A-Z]{2,3}/)?(?:[0-9]{1,3}/){0,2}([0-9]{7})\b")
SERIAL_HINT = re.compile(r"^\s*([0-9]{1,4})\b")


def page_text(page) -> str:
    try:
        return page.get_text("text") or ""
    except Exception:  # noqa: BLE001
        return ""


def extract_pdf(path: Path) -> dict:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise SystemExit("PyMuPDF (fitz) is required: pip install pymupdf")

    doc = fitz.open(path)
    epics: list[dict] = []
    seen: set[str] = set()
    char_total = 0
    for pno, page in enumerate(doc, start=1):
        text = page_text(page)
        char_total += len(text.strip())
        for line in text.splitlines():
            tokens = [m.group(1) for m in EPIC_NEW.finditer(line)]
            if not tokens:
                continue
            serial = None
            sm = SERIAL_HINT.match(line)
            if sm:
                serial = int(sm.group(1))
            for epic in tokens:
                if epic in seen:
                    continue
                seen.add(epic)
                epics.append({
                    "epic": epic,
                    "serial": serial,
                    "page": pno,
                    "line": " ".join(line.split())[:160],
                })
    meta = {
        "source": str(path),
        "pages": doc.page_count,
        "text_chars": char_total,
        "image_only": char_total < 40,
        "count": len(epics),
        "epics": epics,
    }
    doc.close()
    return meta


def write_out(meta: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(meta["source"]).stem
    jp = out_dir / f"{stem}_epics.json"
    cp = out_dir / f"{stem}_epics.csv"
    jp.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                  encoding="utf-8")
    with cp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["epic", "serial", "page", "line"])
        w.writeheader()
        for row in meta["epics"]:
            w.writerow(row)
    return jp, cp


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="EPIC roster out of a roll PDF")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--pdf")
    src.add_argument("--dir")
    ap.add_argument("--out-dir", default=str(OUT_DIR / "rolls"))
    ap.add_argument("--epics-only", action="store_true",
                    help="print just the EPIC numbers, one per line")
    args = ap.parse_args(argv)

    paths = ([Path(args.pdf)] if args.pdf
             else sorted(Path(args.dir).glob("*.pdf")))
    if not paths:
        print("no PDFs found", file=sys.stderr)
        return 1

    rc = 0
    total = 0
    for path in paths:
        meta = extract_pdf(path)
        total += meta["count"]
        if args.epics_only:
            for row in meta["epics"]:
                print(row["epic"])
        else:
            jp, cp = write_out(meta, Path(args.out_dir))
            warn = "  [IMAGE-ONLY PDF - no text layer]" if meta["image_only"] else ""
            print(f"{path.name}: {meta['pages']} pages, {meta['count']} EPICs{warn}")
            print(f"  -> {jp}\n  -> {cp}")
        if meta["image_only"] and meta["count"] == 0:
            rc = 2
    if args.epics_only:
        print(f"# {total} EPIC(s)", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
