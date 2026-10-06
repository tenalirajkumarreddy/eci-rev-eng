"""Enumerate the EPIC numbers of a polling part - no captcha, no PDF.

Route (anonymous, found by mining the app's BLO/SIR client):
  POST https://gateway-vha.eci.gov.in/api/v1/elastic-sir-citizen/get-eroll-data-2003
  body {"oldStateCd", "oldAcNo", "oldPartNo", "oldPartSerialNo"}

Notes from live probing:
  * oldPartSerialNo ""    -> a random ~50-record window of the old part
  * oldPartSerialNo "<n>" -> that one record (404 if not in the roll)
  * each record carries bloMappedEpicNo + bloMapped{StateCd,AcNo,PartNo}:
    the elector's *current* EPIC and current part, plus name/relative/age.

Old (pre-delimitation) parts are split/merged into current parts, so a current
part is best assembled by sweeping the old parts around it and filtering on
bloMappedPartNo. `--old-part` accepts a list/range: 1,2,3 or 1-6.

Usage:
  python work/part_epics.py --state S01 --ac 1 --old-part 1
  python work/part_epics.py --state S01 --ac 1 --old-part 1-4 --merge-part 2
  python work/part_epics.py --state S01 --ac 1 --old-part 2 --serial-end 800 --workers 6
"""
import argparse
import csv
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import certifi
import requests

URL = "https://gateway-vha.eci.gov.in/api/v1/elastic-sir-citizen/get-eroll-data-2003"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "applicationName": "VHA",
    "appName": "VHA",
    "channelidobo": "VHA",
    "platform-type": "ANDROIDMOB",
    "currentRole": "citizen",
    "User-Agent": "okhttp/4.9.2",
}

lock = threading.Lock()
stats = {"hit": 0, "miss": 0, "err": 0, "r429": 0, "lat": []}
LOCAL = threading.local()


def session():
    if not hasattr(LOCAL, "s"):
        s = requests.Session()
        s.verify = certifi.where()
        LOCAL.s = s
    return LOCAL.s


def fetch(state, ac, part, serial, retries=3):
    """Return the payload list for one serial ([] on 404)."""
    body = {"oldStateCd": state, "oldAcNo": str(ac), "oldPartNo": str(part),
            "oldPartSerialNo": str(serial)}
    for attempt in range(retries):
        t0 = time.time()
        try:
            r = session().post(URL, headers=HEADERS, json=body, timeout=30)
        except requests.RequestException:
            time.sleep(1.0 * (attempt + 1))
            continue
        with lock:
            stats["lat"].append(time.time() - t0)
        if r.status_code == 429:
            with lock:
                stats["r429"] += 1
            time.sleep(2.0 * (attempt + 1))
            continue
        if r.status_code == 200:
            with lock:
                stats["hit"] += 1
            try:
                return r.json().get("payload") or []
            except ValueError:
                return []
        if r.status_code == 404:
            with lock:
                stats["miss"] += 1
            return []
        with lock:
            stats["err"] += 1
        time.sleep(0.5 * (attempt + 1))
    return []


def probe_end(state, ac, part, hard_cap=3000):
    """Last serial that answers, +20 margin (30 if the part has no records)."""
    last = 0
    for cand in (50, 100, 200, 300, 400, 500, 650, 800, 1000, 1200, 1500, 2000, 2500):
        if cand > hard_cap:
            break
        if fetch(state, ac, part, cand):
            last = cand
        elif last and cand > last + 120:
            break
    return min(hard_cap, (last + 20) if last else 30)


def parse_parts(spec):
    out = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if "-" in chunk:
            a, b = chunk.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        elif chunk:
            out.append(int(chunk))
    return out


def sweep(state, ac, part, args):
    """Sweep one old part; returns {id: record} (record tagged with source part)."""
    tag = "S%s_AC%s_oldP%s" % (state, ac, part)
    jsonl = os.path.join(args.out, "%s.jsonl" % tag)
    seen = {}
    if os.path.exists(jsonl):
        with open(jsonl, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("id"):
                    seen[rec["id"]] = rec
        print("[%s] resume: %d records" % (tag, len(seen)))

    t_start = time.time()
    end = args.serial_end or probe_end(state, ac, part)
    have = {r.get("oldPartSerialNo") for r in seen.values()}
    todo = [s for s in range(args.serial_start, end + 1) if s not in have]
    print("[%s] sweeping serials %d..%d (%d to fetch)"
          % (tag, args.serial_start, end, len(todo)))

    def work(serial):
        recs = fetch(state, ac, part, serial)
        if not recs:
            return
        with lock:
            for rec in recs:
                rid = rec.get("id") or "%s|%s" % (serial, rec.get("oldFullName"))
                if rid not in seen:
                    seen[rid] = rec

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        done = 0
        for _ in pool.map(work, todo):
            done += 1
            if done % 200 == 0:
                print("[%s] %d/%d unique=%d hit=%d miss=%d err=%d 429=%d"
                      % (tag, done, len(todo), len(seen), stats["hit"],
                         stats["miss"], stats["err"], stats["r429"]))

    with open(jsonl, "w", encoding="utf-8") as fh:
        for rec in seen.values():
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    mapped = sum(1 for r in seen.values() if r.get("bloMappedEpicNo"))
    print("[%s] %d records, %d with EPIC, %.1fs" % (tag, len(seen), mapped,
                                                    time.time() - t_start))
    for rec in seen.values():
        rec["_old_part"] = part
    return seen


def to_row(rec):
    return {
        "epic": rec.get("bloMappedEpicNo") or "",
        "name": rec.get("oldFullName") or rec.get("firstName") or "",
        "gender": rec.get("gender") or "",
        "age": rec.get("age") or "",
        "relation": rec.get("relationType") or "",
        "relative": rec.get("oldRelativeFullName") or rec.get("relativeFName") or "",
        "old_serial": rec.get("oldPartSerialNo") or "",
        "old_part": rec.get("oldPartNumber") or "",
        "old_part_name": rec.get("oldPartName") or "",
        "current_state": rec.get("bloMappedStateCd") or "",
        "current_ac": rec.get("bloMappedAcNo") or "",
        "current_part": rec.get("bloMappedPartNo") or "",
        "marked_by_blo": rec.get("markedByBlo") or "",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="S01")
    ap.add_argument("--ac", default="1")
    ap.add_argument("--old-part", required=True,
                    help="old part number(s): 2, or 1-6, or 1,2,3")
    ap.add_argument("--serial-start", type=int, default=1)
    ap.add_argument("--serial-end", type=int, default=0, help="0 = probe roll size")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--merge-part", default=None,
                    help="keep only electors currently mapped into this part number")
    ap.add_argument("--all-rows", action="store_true",
                    help="include rows without a current EPIC too")
    ap.add_argument("--out", default="work/out/part_epics")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    parts = parse_parts(args.old_part)
    t_start = time.time()
    merged = {}
    for part in parts:
        seen = sweep(args.state, args.ac, part, args)
        for rid, rec in seen.items():
            merged["%s|%s" % (part, rid)] = rec

    rows = [to_row(r) for r in merged.values()]
    if not args.all_rows:
        rows = [r for r in rows if r["epic"]]
    if args.merge_part:
        rows = [r for r in rows if r["current_part"] == str(args.merge_part)]
    rows.sort(key=lambda r: (r["current_part"], r["old_part"], r["epic"]))

    name = "S%s_AC%s_%s" % (args.state, args.ac,
                            ("curP%s" % args.merge_part) if args.merge_part
                            else "oldP" + args.old_part.replace(",", "_").replace("-", "to"))
    csvf = os.path.join(args.out, "%s.csv" % name)
    with open(csvf, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["epic"])
        w.writeheader()
        for r in rows:
            w.writerow(r)

    lat = stats["lat"]
    epics = sorted({r["epic"] for r in rows})
    print("\n== S%s AC%s old parts %s" % (args.state, args.ac, args.old_part))
    print("rows: %d   unique EPICs: %d" % (len(rows), len(epics)))
    if args.merge_part:
        print("filtered to electors currently in part %s" % args.merge_part)
    if len(parts) > 1:
        for part in parts:
            n = sum(1 for r in rows if str(r["old_part"]) == str(part))
            print("  old part %s -> %d rows" % (part, n))
    print("requests: hit=%d miss=%d err=%d 429=%d  avg latency=%.2fs"
          % (stats["hit"], stats["miss"], stats["err"], stats["r429"],
             (sum(lat) / len(lat)) if lat else 0))
    print("elapsed: %.1fs" % (time.time() - t_start))
    if epics:
        print("first EPICs:", ", ".join(epics[:12]))
    print("wrote", csvf)
    return 0


if __name__ == "__main__":
    sys.exit(main())
