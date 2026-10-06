"""One command -> the EPIC list of a polling part (no PDF, no captcha, no login).

    python work/part_epic_list.py --state S01 --ac 1 --part 2

How it works: the anonymous route
`elastic-sir-citizen/get-eroll-data-2003` returns each old-roll elector of an old
part together with `bloMappedEpicNo` (their current EPIC) and the current
state/ac/part they were mapped into. Today's parts were carved out of the old
ones by village, so a current part's electors live in the old part(s) carrying
the *same part name* (current MANDAPALLI <- old "Mandapalli"; current KEDARIPURAM
<- old "Kedari Puram", ...). This tool:

  1. reads the target part's name from the anonymous part endpoint,
  2. discovers old-part names cheaply (one window request per old part, up to
     --discover-max, ~0.15 s each),
  3. sweeps the old parts whose name matches the target (full serial sweep),
  4. keeps every elector whose CURRENT location is the requested
     (state, ac, part) and writes the EPIC list.

Outputs (under --out, default work/out/part_lists):
  <STATE>_AC<ac>_P<part>.txt   one EPIC per line
  <STATE>_AC<ac>_P<part>.csv   EPIC + name/relative/age/old serial + provenance
  <STATE>_AC<ac>_P<part>.json  summary (counts, discovery table, runtime)

Sweeps are cached as work/out/part_epics/<tag>.jsonl, so re-runs and nearby
queries are instant for parts already visited.

Other modes:
  --old-parts 1-4      skip name discovery, sweep these old parts explicitly
  --scan-all           sweep every old part 1..--max-old-part (complete, slow:
                       ~3 h for a 319-part AC at 6 workers, resumable)
  --discover-only      just print the old-part name map for the discovery range
  --verify 3           re-check N sampled EPICs through the national search
                       (1 request/s; confirms the current part number)
  --workers 6          parallel sweep workers (6 showed zero 429s)
"""
import argparse
import csv
import difflib
import json
import os
import random
import re
import subprocess
import sys
import time
from types import SimpleNamespace

import certifi
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import part_epics  # noqa: E402  (sweep/fetch helpers live there)

PART_URL = "https://gateway-vha.eci.gov.in/api/v1/common/part/get/bystatecd/districtcd/acNumber"
PART_HEADERS = {
    "applicationName": "VHA", "appName": "VHA", "channelidobo": "VHA",
    "platform-type": "ANDROIDMOB",
}
ENGINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "epic_engine.py")
NORM_RE = re.compile(r"[^a-z0-9]")


def norm(name):
    return NORM_RE.sub("", (name or "").lower())


def name_match(nm, want):
    """True when an old part name denotes the target part (spelling drift allowed)."""
    if not nm or not want:
        return False
    a, b = norm(nm), norm(want)
    if a == b:
        return True
    if len(a) >= 5 and len(b) >= 5 and (a in b or b in a):
        return True
    if len(a) >= 6 and len(b) >= 6:
        return difflib.SequenceMatcher(None, a, b).ratio() >= 0.82
    return False


def current_parts(state, ac):
    try:
        s = requests.Session()
        s.verify = certifi.where()
        r = s.get(PART_URL, headers=dict(PART_HEADERS, state=state),
                  params={"stateCd": state, "acNumber": str(ac)}, timeout=30)
        parts = r.json()
        if isinstance(parts, dict):
            parts = parts.get("payload") or parts.get("data") or []
        return {str(p.get("partNumber")): p for p in parts}
    except Exception:
        return {}


def discover_old_part_name(state, ac, old_part):
    """Cheapest way to learn an old part's name: a window request, then probes."""
    recs = part_epics.fetch(state, ac, old_part, "")
    for rec in recs:
        if rec.get("oldPartName"):
            return rec["oldPartName"], len(recs)
    for serial in (1, 25, 100):
        recs = part_epics.fetch(state, ac, old_part, serial)
        if recs:
            return recs[0].get("oldPartName"), 1
    return None, 0


def national_part(epic):
    """Ask the national search which part an EPIC is in now (1 request/s)."""
    try:
        subprocess.run([sys.executable, ENGINE, "--epic", epic, "--search",
                        "--no-enrich"], capture_output=True, timeout=120)
        prof = (json.load(open(os.path.join("work", "out", "epic_%s.json" % epic),
                               encoding="utf-8")).get("profile") or {})
        return str(prof.get("part_no") or prof.get("partNo") or ""), prof.get("name") or ""
    except Exception:
        return "", ""


def detect_offset(buckets, part, probes=3):
    """Find N such that mapping part part-N holds today's part-part electors."""
    if not str(part).isdigit():
        return 0, {"reason": "non-numeric part"}
    target = int(part)
    for off in (0, 1, 2):
        bucket = buckets.get(str(target - off)) or []
        if not bucket:
            continue
        sample = random.sample(bucket, min(probes, len(bucket)))
        hits, seen = 0, []
        for epic, _name in sample:
            nat, _nname = national_part(epic)
            seen.append({"epic": epic, "national_part": nat})
            if nat == str(target):
                hits += 1
            time.sleep(1.3)
        if hits and hits >= len(sample) - 1:
            return off, {"probed": seen, "hits": hits, "sample": len(sample)}
    return 0, {"probed": [], "hits": 0, "sample": 0,
               "note": "no offset confirmed; assuming 0"}


def verify_sample(epics, ac, part, names, n):
    sample = random.sample(epics, min(n, len(epics)))
    ok = bad = 0
    fails = []
    for i, epic in enumerate(sample, 1):
        try:
            subprocess.run([sys.executable, ENGINE, "--epic", epic, "--search",
                            "--no-enrich"], capture_output=True, timeout=120)
            path = os.path.join("work", "out", "epic_%s.json" % epic)
            prof = (json.load(open(path, encoding="utf-8")).get("profile") or {})
        except Exception as e:
            fails.append("%s: %s" % (epic, e))
            continue
        got_part = str(prof.get("part_no") or prof.get("partNo") or "")
        got_name = (prof.get("name") or "").lower()
        want_name = (names.get(epic) or "").lower()
        if got_part == str(part):
            ok += 1
        else:
            bad += 1
            fails.append("%s part=%s (expected %s)" % (epic, got_part, part))
        if want_name and got_name and not (set(want_name.split()) & set(got_name.split())):
            fails.append("%s name '%s' vs '%s'" % (epic, want_name, got_name))
        print("   verify %d/%d %s -> part %s name %r"
              % (i, len(sample), epic, got_part, got_name))
        time.sleep(1.3)
    return ok, bad, fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="S01")
    ap.add_argument("--ac", default="1")
    ap.add_argument("--part", required=True, help="current part number")
    ap.add_argument("--old-parts", default=None,
                    help="old parts to sweep, e.g. 1-4 or 1,2,3 (skips discovery)")
    ap.add_argument("--discover-max", type=int, default=0,
                    help="highest old part to check names for (0 = part+8)")
    ap.add_argument("--scan-all", action="store_true",
                    help="sweep every old part 1..--max-old-part")
    ap.add_argument("--max-old-part", type=int, default=400)
    ap.add_argument("--discover-only", action="store_true")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--verify", type=int, default=0)
    ap.add_argument("--include-unmapped", action="store_true")
    ap.add_argument("--offset", default="auto",
                    help="mapping vintage offset: auto (probe) or 0/1/2; mapping part numbers can lag the published roll by N in some areas")
    ap.add_argument("--out", default="work/out/part_lists")
    args = ap.parse_args()

    part = str(args.part)
    parts = current_parts(args.state, args.ac)
    pname = (parts.get(part) or {}).get("partName")
    print("target: %s AC %s part %s%s"
          % (args.state, args.ac, part, (" (%s)" % pname) if pname else ""))

    if args.scan_all:
        spec = "1-%d" % args.max_old_part
    elif args.old_parts:
        spec = args.old_parts
    else:
        spec = None
    old_parts = part_epics.parse_parts(spec) if spec else None

    discovery = {}
    if old_parts is None:
        cap = args.discover_max or (int(part) + 8)
        cap = min(cap, args.max_old_part)
        t_disc = time.time()
        print("discovering old-part names 1..%d (1 request each)..." % cap)
        want = pname
        for old in range(1, cap + 1):
            nm, n = discover_old_part_name(args.state, args.ac, old)
            discovery[str(old)] = {"name": nm, "window_recs": n}
            hit = name_match(nm, want)
            if hit:
                print("   old part %-3d = %-22s MATCH" % (old, nm))
            if args.discover_only and old % 50 == 0:
                print("   ...%d/%d checked" % (old, cap))
        print("discovery: %.1fs" % (time.time() - t_disc))
        if args.discover_only:
            for old, d in sorted(discovery.items(), key=lambda kv: int(kv[0])):
                print("%4s  %-24s recs=%s" % (old, d["name"], d["window_recs"]))
            return 0
        old_parts = [int(o) for o, d in discovery.items()
                     if name_match(d["name"], want)]
        if not old_parts:
            print("no old part name matches %r; falling back to a numeric window" % pname)
            old_parts = [p for p in range(max(1, int(part) - 1), int(part) + 3)]

    print("sweeping old part(s): %s" % ", ".join(map(str, old_parts)))
    sweep_args = SimpleNamespace(serial_start=1, serial_end=0,
                                 workers=args.workers, out="work/out/part_epics")
    t0 = time.time()
    records = {}
    for old in old_parts:
        for rid, rec in part_epics.sweep(args.state, args.ac, old, sweep_args).items():
            rec["_old_part"] = old
            records["%s|%s" % (old, rid)] = rec

    # bucket the mapped electors by the part number in the mapping...
    buckets = {}
    for rec in records.values():
        same_state = (rec.get("bloMappedStateCd") or args.state) == args.state
        same_ac = str(rec.get("bloMappedAcNo") or "") == str(args.ac)
        epic = (rec.get("bloMappedEpicNo") or "").strip()
        if not (same_state and same_ac and epic):
            continue
        mp = str(rec.get("bloMappedPartNo") or "")
        buckets.setdefault(mp, []).append((epic, rec.get("oldFullName") or ""))

    # ...then find which vintage that numbering is: the mapping can lag the
    # published roll by one or two part numbers (split parts shift the tail).
    if args.offset == "auto":
        offset, calibration = detect_offset(buckets, part)
    else:
        offset, calibration = int(args.offset), {"reason": "explicit --offset"}
    target_mapped = str(int(part) - offset) if str(part).isdigit() else part
    print("mapping offset: +%d  (today's part %s = mapping part %s)"
          % (offset, part, target_mapped))

    rows = []
    for rec in records.values():
        same_state = (rec.get("bloMappedStateCd") or args.state) == args.state
        same_ac = str(rec.get("bloMappedAcNo") or "") == str(args.ac)
        if not (same_state and same_ac
                and str(rec.get("bloMappedPartNo") or "") == target_mapped):
            continue
        epic = (rec.get("bloMappedEpicNo") or "").strip()
        if not epic and not args.include_unmapped:
            continue
        rows.append({
            "epic": epic,
            "name": rec.get("oldFullName") or rec.get("firstName") or "",
            "gender": rec.get("gender") or "",
            "age": rec.get("age") or "",
            "relation": rec.get("relationType") or "",
            "relative": rec.get("oldRelativeFullName") or rec.get("relativeFName") or "",
            "old_part": rec.get("_old_part"),
            "old_serial": rec.get("oldPartSerialNo") or "",
            "old_part_name": rec.get("oldPartName") or "",
            "marked_by_blo": rec.get("markedByBlo") or "",
        })
    rows.sort(key=lambda r: (str(r["old_part"]), str(r["old_serial"])))

    os.makedirs(args.out, exist_ok=True)
    stem = os.path.join(args.out, "%s_AC%s_P%s" % (args.state, args.ac, part))
    epics = sorted({r["epic"] for r in rows if r["epic"]})
    with open(stem + ".txt", "w", encoding="utf-8") as fh:
        fh.write("\n".join(epics) + ("\n" if epics else ""))
    with open(stem + ".csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["epic"])
        w.writeheader()
        for r in rows:
            w.writerow(r)

    per_old = {}
    for r in rows:
        per_old[str(r["old_part"])] = per_old.get(str(r["old_part"]), 0) + 1
    summary = {
        "state": args.state, "ac": args.ac, "part": part, "part_name": pname,
        "epics": len(epics), "rows": len(rows),
        "contributing_old_parts": per_old,
        "mapping_offset": offset,
        "mapping_offset_calibration": calibration,
        "mapping_part_used": target_mapped,
        "old_parts_swept": [str(p) for p in old_parts],
        "discovery": discovery or "skipped (explicit --old-parts)",
        "elapsed_s": round(time.time() - t0, 1),
        "note": "EPICs of the pre-roll cohort mapped into this part; electors "
                "added after that roll are not in this source.",
    }
    if args.verify and epics:
        ok, bad, fails = verify_sample(epics, args.ac, part,
                                       {r["epic"]: r["name"] for r in rows},
                                       args.verify)
        summary["verify"] = {"checked": min(args.verify, len(epics)),
                             "part_match": ok, "part_mismatch": bad, "issues": fails}
    with open(stem + ".json", "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)

    print("\nEPICs: %d   (from %d elector records)" % (len(epics), len(rows)))
    print("contributions by old part: %s"
          % ", ".join("%s:%d" % (k, v) for k, v in sorted(per_old.items())))
    print("elapsed: %.1fs" % (time.time() - t0))
    if epics:
        print("first EPICs:", ", ".join(epics[:10]))
    print("wrote %s.txt | .csv | .json" % stem)
    return 0


if __name__ == "__main__":
    sys.exit(main())
