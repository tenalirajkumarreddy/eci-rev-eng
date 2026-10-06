"""Booth-ready printable sheet for a part's EPIC list.

Input: the CSV from work/part_epic_list.py (regenerate with --refresh if needed).
Output: a self-contained, print-ready HTML (A4) with (# / EPIC / name / relation /
relative / gender / age / tick box) plus, with --pdf, a real PDF rendered by
headless Chrome or Edge.

Telugu names are joined in from the sweep cache (work/out/part_epics/*.jsonl) so
the sheet carries both scripts for the same elector.

Usage:
  python work/part_booth_sheet.py --state S01 --ac 1 --part 2
  python work/part_booth_sheet.py --state S01 --ac 1 --part 12 --refresh --pdf
  python work/part_booth_sheet.py --state S01 --ac 1 --part 2 --sort name --per-page 45
"""
import argparse
import csv
import glob
import html
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import part_epic_list  # noqa: E402

BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def telugu_names(ac, part, state):
    """epic -> (name_l1, relative_l1) from the cached sweep records."""
    out = {}
    for path in glob.glob(os.path.join(HERE, "out", "part_epics", "*.jsonl")):
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    epic = (r.get("bloMappedEpicNo") or "").strip()
                    if not epic or epic in out:
                        continue
                    out[epic] = (r.get("oldFullNameL1") or "",
                                 r.get("oldRelativeFullNameL1") or "")
        except OSError:
            continue
    return out


def build_html(rows, meta, per_page, compact=False, show_l1=True):
    def chunk(seq, n):
        if not n:
            return [seq]
        return [seq[i:i + n] for i in range(0, len(seq), n)]

    cols = ["#", "EPIC", "Name", "Relation", "Relative", "Sex", "Age*", ""]
    head = "".join("<th>%s</th>" % c for c in cols)
    pages = []
    start = 1
    for group in chunk(rows, per_page):
        body = []
        for i, r in enumerate(group, start):
            name = html.escape(r["name"])
            if show_l1 and r.get("name_l1"):
                name += '<div class="l1">%s</div>' % html.escape(r["name_l1"])
            rel = html.escape(r.get("relative", ""))
            if show_l1 and r.get("relative_l1"):
                rel += '<div class="l1">%s</div>' % html.escape(r["relative_l1"])
            body.append(
                "<tr><td class='num'>%d</td><td class='epic'>%s</td>"
                "<td>%s</td><td class='c'>%s</td><td>%s</td>"
                "<td class='c'>%s</td><td class='num'>%s</td>"
                "<td class='tick'>&#9744;</td></tr>"
                % (i, html.escape(r["epic"]), name, html.escape(r.get("relation", "")),
                   rel, html.escape(r.get("gender", "")), html.escape(str(r.get("age", "")))))
        pages.append("<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>"
                     % (head, "".join(body)))
        start += len(group)

    head_meta = "".join("<span><b>%s</b> %s</span>" % (html.escape(k), html.escape(str(v)))
                        for k, v in meta.items())
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Booth sheet - %(title)s</title>
<style>
  @page { size: A4 portrait; margin: 12mm 9mm; }
  body { font-family: "Segoe UI", "Noto Sans Telugu", Arial, sans-serif;
         font-size: 10pt; color: #111; margin: 0; }
  h1 { font-size: 15pt; margin: 0 0 2mm; }
  .meta { display: flex; gap: 7mm; flex-wrap: wrap; font-size: 9pt;
          color: #333; margin-bottom: 2mm; }
  .meta b { color: #000; }
  .note { font-size: 8pt; color: #555; margin-bottom: 3mm; }
  table { width: 100%%; border-collapse: collapse; page-break-inside: auto; }
  thead { display: table-header-group; }
  tr { page-break-inside: avoid; }
  th, td { border: 0.4pt solid #888; padding: 1.1mm 1.4mm; vertical-align: top; }
  th { background: #ececec; font-size: 9pt; text-align: left; }
  td.num { text-align: right; width: 9mm; }
  td.c { text-align: center; width: 10mm; }
  td.tick { width: 8mm; text-align: center; font-size: 13pt; }
  td.epic { font-family: Consolas, monospace; white-space: nowrap; width: 30mm; }
  .l1 { font-size: 8.5pt; color: #333; }
  .pbreak { page-break-after: always; }
</style></head><body>
<h1>%(h1)s</h1>
<div class="meta">%(meta)s</div>
<div class="note">%(note)s</div>
%(tables)s
%(compact_css)s
</body></html>
""" % {
        "title": meta.get("part", ""),
        "h1": html.escape(meta.get("sheet title", "Electoral roll sheet")),
        "meta": head_meta,
        "note": html.escape(meta.get("note", "")),
        "tables": "<div class='pbreak'></div>".join(pages),
        "compact_css": ("<style>body{font-size:9pt} th,td{padding:0.7mm 1.1mm}"
                        " .l1{font-size:7.5pt} td.tick{font-size:11pt}</style>")
                       if compact else "",
    }


def render_pdf(html_path, pdf_path):
    url = "file:///" + os.path.abspath(html_path).replace("\\", "/")
    for browser in BROWSERS:
        if not os.path.exists(browser):
            continue
        cmd = [browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
               "--print-to-pdf-no-header",
               "--print-to-pdf=" + os.path.abspath(pdf_path), url]
        try:
            subprocess.run(cmd, capture_output=True, timeout=180)
        except Exception:
            continue
        if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 1000:
            return browser
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="S01")
    ap.add_argument("--ac", default="1")
    ap.add_argument("--part", required=True)
    ap.add_argument("--csv", default=None, help="input CSV (default: part_lists/<...>.csv)")
    ap.add_argument("--refresh", action="store_true",
                    help="regenerate the EPIC list first via part_epic_list.py")
    ap.add_argument("--sort", default="serial", choices=["serial", "name", "epic"])
    ap.add_argument("--per-page", type=int, default=0,
                    help="force N rows per printed page (0 = let the browser paginate)")
    ap.add_argument("--pdf", action="store_true", help="also render a PDF")
    ap.add_argument("--compact", action="store_true",
                    help="smaller type/padding: fewer printed pages")
    ap.add_argument("--no-l1", action="store_true",
                    help="English names only (halves the rows' height)")
    ap.add_argument("--out", default="work/out/booth_sheets")
    args = ap.parse_args()

    stem = "%s_AC%s_P%s" % (args.state, args.ac, args.part)
    csv_path = args.csv or os.path.join("work", "out", "part_lists", stem + ".csv")
    if args.refresh or not os.path.exists(csv_path):
        print("generating EPIC list first...")
        subprocess.run([sys.executable, os.path.join(HERE, "part_epic_list.py"),
                        "--state", args.state, "--ac", args.ac, "--part", args.part],
                       check=False)
    if not os.path.exists(csv_path):
        print("no CSV at %s - run part_epic_list.py first" % csv_path)
        return 1

    rows = list(csv.DictReader(open(csv_path, encoding="utf-8")))
    l1 = telugu_names(args.ac, args.part, args.state)
    for r in rows:
        n1, rel1 = l1.get(r["epic"], ("", ""))
        r["name_l1"], r["relative_l1"] = n1, rel1
    if args.sort == "serial":
        rows.sort(key=lambda r: (str(r.get("old_part", "")), int(r.get("old_serial") or 0)))
    elif args.sort == "name":
        rows.sort(key=lambda r: (r.get("name", "") or "").lower())
    else:
        rows.sort(key=lambda r: r.get("epic", ""))

    parts = part_epic_list.current_parts(args.state, args.ac)
    pname = (parts.get(str(args.part)) or {}).get("partName") or "?"
    meta = {
        "sheet title": "Electoral roll sheet - part %s (%s), AC %s, %s"
                       % (args.part, pname, args.ac, args.state),
        "part": args.part,
        "electors": len(rows),
        "AC": args.ac,
        "state": args.state,
    }
    note = ("Source: ECI anonymous SIR/2003 mapping route - electors of the pre-roll "
            "cohort mapped into this part; not the complete published roll. "
            "* age and name spellings are from that snapshot (2003 vintage), "
            "Age* = age then. “#” is a sheet row counter, not the roll serial. "
            "Verify against the official roll before field use.")
    meta["note"] = note

    os.makedirs(args.out, exist_ok=True)
    html_path = os.path.join(args.out, stem + "_sheet.html")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(build_html(rows, meta, args.per_page, args.compact,
                            show_l1=not args.no_l1))
    print("rows: %d" % len(rows))
    print("wrote %s" % html_path)
    if args.pdf:
        pdf_path = os.path.join(args.out, stem + "_sheet.pdf")
        browser = render_pdf(html_path, pdf_path)
        if browser:
            try:
                import fitz
                n = fitz.open(pdf_path).page_count
            except Exception:
                n = "?"
            print("wrote %s  (%s, %s pages, %.0f KB)"
                  % (pdf_path, os.path.basename(browser), n,
                     os.path.getsize(pdf_path) / 1024))
        else:
            print("PDF skipped: no Chrome/Edge found; open the HTML and print instead")
    return 0


if __name__ == "__main__":
    sys.exit(main())
