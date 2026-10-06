"""Harvest a polling part's electors from the anonymous 2003/SIR route.

Route (anonymous, no captcha, no PDF):
  POST https://gateway-vha.eci.gov.in/api/v1/elastic-sir-citizen/get-eroll-data-2003
  body {"oldStateCd","oldAcNo","oldPartNo","oldPartSerialNo"}

* empty oldPartSerialNo  -> a ~50-record random window of the part
* a serial number        -> that one record (404 if missing)

Each record carries `epicNumber` (old roll, usually masked) plus
`bloMappedEpicNo` / `bloMappedStateCd|AcNo|PartNo` - the current EPIC and the
part it was mapped into.

Modes:
  --mode windows --calls 40          random-window sampling, union by id
  --mode serials --start 1 --end 700 per-serial sweep
  --mode both                        windows then serials, merge

Writes JSONL of unique records + a summary. Usage examples:
  python work/eroll2003_collect.py --mode windows --calls 30
  python work/eroll2003_collect.py --mode serials --start 1 --end 120
"""
import argparse
import json
import os
import sys
import time

import certifi
import requests

H = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "applicationName": "VHA",
    "appName": "VHA",
    "channelidobo": "VHA",
    "platform-type": "ANDROIDMOB",
    "currentRole": "citizen",
    "User-Agent": "okhttp/4.9.2",
}
URL = "https://gateway-vha.eci.gov.in/api/v1/elastic-sir-citizen/get-eroll-data-2003"


def post(sess, body, retries=3):
    for i in range(retries):
        try:
            r = sess.post(URL, headers=H, json=body, timeout=30)
            if r.status_code == 429:
                time.sleep(1.5 * (i + 1))
                continue
            return r
        except requests.RequestException:
            time.sleep(1.0 * (i + 1))
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="S01")
    ap.add_argument("--ac", default="1")
    ap.add_argument("--part", default="2")
    ap.add_argument("--mode", default="windows", choices=["windows", "serials", "both"])
    ap.add_argument("--calls", type=int, default=30)
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.25)
    ap.add_argument("--out", default="work/out/eroll2003")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    tag = "S%s_AC%s_P%s" % (args.state, args.ac, args.part)
    jsonl = os.path.join(args.out, "part_%s.jsonl" % tag)
    sess = requests.Session()
    sess.verify = certifi.where()

    seen = {}
    stats = {"windows": 0, "serial_hits": 0, "serial_miss": 0, "errors": 0,
             "lat": [], "empty_window_closed": 0}
    base = {"oldStateCd": args.state, "oldAcNo": args.ac, "oldPartNo": args.part}

    def ingest(payload, bucket):
        added = 0
        for rec in payload or []:
            rid = rec.get("id") or (str(rec.get("oldPartSerialNo")) + "|" + (rec.get("oldFullName") or ""))
            if rid not in seen:
                seen[rid] = rec
                added += 1
        return added

    if args.mode in ("windows", "both"):
        for i in range(args.calls):
            body = dict(base, oldPartSerialNo="")
            t0 = time.time()
            r = post(sess, body)
            dt = time.time() - t0
            stats["lat"].append(round(dt, 2))
            if r is None:
                stats["errors"] += 1
                continue
            stats["windows"] += 1
            if r.status_code != 200:
                print("  window %d -> %s" % (i + 1, r.status_code))
                continue
            j = r.json()
            n = ingest(j.get("payload"), "win")
            print("  window %-3d %4.2fs +%-3d total=%d" % (i + 1, dt, n, len(seen)))
            time.sleep(args.sleep)

    if args.mode in ("serials", "both"):
        serial = args.start
        while True:
            body = dict(base, oldPartSerialNo=str(serial))
            r = post(sess, body)
            if r is None:
                stats["errors"] += 1
            elif r.status_code == 200:
                added = ingest(r.json().get("payload"), "ser")
                stats["serial_hits"] += 1
            elif r.status_code == 404:
                stats["serial_miss"] += 1
            else:
                stats["errors"] += 1
            if serial % 50 == 0:
                print("  serial %d ... %d unique" % (serial, len(seen)))
            if args.end:
                if serial >= args.end:
                    break
            elif stats["serial_miss"] >= 30 and stats["serial_hits"] > 0:
                break
            serial += 1
            time.sleep(args.sleep)

    with open(jsonl, "w", encoding="utf-8") as fh:
        for rec in seen.values():
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    mapped = [r for r in seen.values() if r.get("bloMappedEpicNo")]
    ser = sorted(r.get("oldPartSerialNo") or 0 for r in seen.values())
    missing = []
    if ser:
        present = set(int(x) for x in ser if x)
        missing = [s for s in range(1, max(present) + 1) if s not in present]
    lat = stats.pop("lat")
    print("\n== %s" % tag)
    print("unique records: %d  (with bloMappedEpicNo: %d)" % (len(seen), len(mapped)))
    print("serials: max=%s present=%d missing_in_range=%d" % (
        max(ser) if ser else 0, len(set(ser)), len(missing)))
    if missing[:20]:
        print("first missing:", missing[:20])
    if lat:
        print("latency: n=%d avg=%.2fs max=%.2fs" % (len(lat), sum(lat) / len(lat), max(lat)))
    print("stats:", stats)
    print("wrote", jsonl)


if __name__ == "__main__":
    sys.exit(main())
