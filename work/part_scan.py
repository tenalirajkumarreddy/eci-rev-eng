#!/usr/bin/env python3
"""
part_scan.py - enumerate which EPIC numbers belong to a polling part.

The national-display EPIC search (see app_search.py) is anonymous, cheap and
returns the full electoral record *including* `partNumber`, `partSerialNumber`,
`sectionNo` and a stable `epicId`.  It has no "list the roll of part N" route,
so the only way to turn it into a roll is to sweep EPIC numbers and bucket the
answers by part.

EPIC format is 3 letters + 7 digits (e.g. TBG0342345), so a sweep is
    --prefix TBG --start 342300 --end 342500
and every probed number lands in exactly one of three buckets:

    200 + record -> EPIC exists (part/serial in the row)
    200 + []     -> EPIC does not exist
    400 + []     -> request rejected (keys/logic problem - stop and check)

Results stream to a JSONL file so a sweep can be resumed and re-analysed:

    python work/part_scan.py --prefix TBG --start 342300 --end 342400
    python work/part_scan.py --state S01 --ac 1 --part 2 --list          # roll of a part
    python work/part_scan.py --state S01 --ac 1 --part 2 --list --from-scan X.jsonl

Usage notes:
  * `--workers` (default 4) is deliberately small - this endpoint is the live
    production gateway, not a lab.
  * `--part N` filters the report; `--list` prints just the EPICs of that part
    sorted by serial number.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import app_search

BASE_DIR = Path(__file__).resolve().parent
OUT_DIR = BASE_DIR / "out"

# raw field (inside hit["content"]) -> short name used in reports
ROW_FIELDS = (
    ("epic", "epicNumber"),
    ("epic_id", "epicId"),
    ("part_id", "partId"),
    ("part_no", "partNumber"),
    ("serial_no", "partSerialNumber"),
    ("section_no", "sectionNo"),
    ("name", "fullName"),
    ("ac_no", "acNumber"),
    ("ac_name", "asmblyName"),
    ("ps", "psbuildingName"),
    ("state_cd", "stateCd"),
    ("district_cd", "districtCd"),
)


def epic_range(prefix: str, start: int, end: int) -> list[str]:
    """TBG + zero-padded 7-digit number."""
    return [f"{prefix}{n:07d}" for n in range(start, end + 1)]


def extract(hit: dict) -> dict:
    content = (hit or {}).get("content") or {}
    row = {short: content.get(raw) for short, raw in ROW_FIELDS}
    return {k: v for k, v in row.items() if v not in (None, "")}


def probe(epic: str, keys: Path, retries: int = 2, timeout: int = 40) -> dict:
    """One EPIC -> one JSONL row. Retries transport errors only."""
    for attempt in range(retries + 1):
        try:
            res = app_search.fetch(epic, keys, timeout=timeout)
        except ValueError as e:                 # missing keys - no point retrying
            return {"epic": epic, "status": -1, "error": str(e)}
        if res["status"] != 0:
            row = {
                "epic": epic,
                "status": res["status"],
                "count": len(res["hits"]),
                "ts": res["sent_at"],
            }
            if res["hits"]:
                row.update(extract(res["hits"][0]))
            return row
        if attempt == retries:
            return {"epic": epic, "status": 0, "error": res["raw"][:200]}
        time.sleep(1.5 * (attempt + 1))
    raise AssertionError("unreachable")


def load_rows(path: Path) -> list[dict]:
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def report(rows: list[dict], args: argparse.Namespace) -> None:
    found = [r for r in rows if r.get("count")]
    absent = [r for r in rows if r.get("status") == 200 and not r.get("count")]
    rejected = [r for r in rows if r.get("status") not in (0, 200)]
    errors = [r for r in rows if r.get("status") == 0]

    if args.list:
        want = [r for r in found
                if (args.part is None or str(r.get("part_no")) == str(args.part))
                and (args.ac is None or str(r.get("ac_no")) == str(args.ac))]
        want.sort(key=lambda r: (int(r.get("part_no") or 0),
                                 int(r.get("serial_no") or 0)))
        print(f"# {len(want)} EPIC(s) in "
              f"state={args.state or '*'} ac={args.ac or '*'} part={args.part or '*'}")
        print(f"{'serial':>6}  {'epic':12s} {'section':>7}  name")
        for r in want:
            print(f"{str(r.get('serial_no','')):>6}  {r.get('epic',''):12s} "
                  f"{str(r.get('section_no','')):>7}  {r.get('name','')}")
        return

    print(f"probed {len(rows)}  found {len(found)}  absent {len(absent)}  "
          f"rejected {len(rejected)}  errors {len(errors)}")
    if rejected:
        print("  rejected sample:",
              json.dumps(rejected[0], ensure_ascii=False)[:200])
    if errors:
        print("  error sample:", json.dumps(errors[0], ensure_ascii=False)[:200])
    if not found:
        return
    parts: dict[str, int] = {}
    for r in found:
        key = f"ac{r.get('ac_no')}/part{r.get('part_no')}"
        parts[key] = parts.get(key, 0) + 1
    print("  parts hit:", json.dumps(parts, sort_keys=True))
    for r in found[: int(args.show)]:
        print(f"  {r['epic']:12s} part {str(r.get('part_no','?')):>3} "
              f"serial {str(r.get('serial_no','?')):>5}  {r.get('name','')}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sweep EPIC numbers -> part buckets")
    ap.add_argument("--prefix", help="3 letters, e.g. TBG")
    ap.add_argument("--start", type=int, help="first 7-digit number (inclusive)")
    ap.add_argument("--end", type=int, help="last 7-digit number (inclusive)")
    ap.add_argument("--epic", action="append", default=[],
                    help="explicit EPIC (repeatable); skips --prefix/--start/--end")
    ap.add_argument("--keys", default=str(BASE_DIR / "app_keys.json"))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", help="JSONL path (default derived from the range)")
    ap.add_argument("--resume", action="store_true",
                    help="skip EPICs already present in the JSONL")
    ap.add_argument("--report-only", action="store_true",
                    help="do not probe; just analyse --from-scan / --out")
    ap.add_argument("--from-scan", help="JSONL to analyse instead of probing")
    ap.add_argument("--list", action="store_true",
                    help="print the roll (EPIC + serial) instead of a summary")
    ap.add_argument("--part", help="filter/report one part number")
    ap.add_argument("--state")
    ap.add_argument("--ac", help="filter/report one assembly number")
    ap.add_argument("--show", default=15, help="rows to print in the summary")
    ap.add_argument("--delay", type=float, default=0.0,
                    help="sleep between requests inside a worker")
    args = ap.parse_args(argv)

    if args.from_scan:
        rows = load_rows(Path(args.from_scan))
        report(rows, args)
        return 0

    targets = [e.upper() for e in args.epic]
    if not targets:
        if not (args.prefix and args.start is not None and args.end is not None):
            ap.error("give --epic or --prefix/--start/--end")
        if args.end < args.start:
            ap.error("--end must be >= --start")
        targets = epic_range(args.prefix.upper(), args.start, args.end)

    if args.out:
        out_path = Path(args.out)
    else:
        tag = (f"{targets[0]}..{targets[-1]}" if len(targets) > 1 else targets[0])
        out_path = OUT_DIR / f"part_scan_{tag}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    done: set[str] = set()
    if args.resume:
        done = {r.get("epic") for r in load_rows(out_path)}
        targets = [t for t in targets if t not in done]
        print(f"[part_scan] resume: {len(done)} already done, "
              f"{len(targets)} to probe", file=sys.stderr)

    if args.report_only:
        report(load_rows(out_path), args)
        return 0

    keys = Path(args.keys)
    print(f"[part_scan] {len(targets)} EPICs, {args.workers} workers -> {out_path}",
          file=sys.stderr)
    lock = threading.Lock()
    started = time.time()
    done_n = 0

    def work(epic: str) -> dict:
        row = probe(epic, keys)
        if args.delay:
            time.sleep(args.delay)
        return row

    with out_path.open("a", encoding="utf-8") as fh, \
            ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        for row in pool.map(work, targets):
            with lock:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()
                done_n += 1
                if done_n % 10 == 0 or done_n == len(targets):
                    rate = done_n / max(time.time() - started, 1e-9)
                    print(f"[part_scan] {done_n}/{len(targets)} "
                          f"({rate:.1f}/s)", file=sys.stderr)

    rows = load_rows(out_path)
    report(rows, args)
    print(f"[part_scan] rows -> {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(BASE_DIR))
    sys.exit(main())
