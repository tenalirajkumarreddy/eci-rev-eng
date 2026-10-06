"""Calibrate `bloMappedPartNo` against the national search's current part number.

The BLO mapping was recorded at some point in the past; the published roll went
through revisions, so for some areas the mapped part is off by one (or more).
This samples K EPICs per mapped part from the cached sweeps and reports the
observed national part + name agreement.

Usage: python work/calibrate_mapping.py --state S01 --ac 1 --parts 2,3,6,8,10,12 --per-part 2
"""
import argparse
import collections
import glob
import json
import os
import random
import subprocess
import sys
import time

ENGINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "epic_engine.py")


def load_pool(state, ac):
    pool = collections.defaultdict(list)
    for path in glob.glob("work/out/part_epics/*.jsonl"):
        try:
            rows = [json.loads(l) for l in open(path, encoding="utf-8")]
        except ValueError:
            continue
        for r in rows:
            epic = (r.get("bloMappedEpicNo") or "").strip()
            if not epic:
                continue
            if (r.get("bloMappedStateCd") or state) != state:
                continue
            if str(r.get("bloMappedAcNo") or "") != str(ac):
                continue
            pool[str(r.get("bloMappedPartNo"))].append(
                (epic, r.get("oldFullName") or r.get("firstName") or ""))
    return pool


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="S01")
    ap.add_argument("--ac", default="1")
    ap.add_argument("--parts", default="2,3,6,8,10,12,13")
    ap.add_argument("--per-part", type=int, default=2)
    args = ap.parse_args()

    pool = load_pool(args.state, args.ac)
    random.seed(11)
    offsets = collections.Counter()
    print("%-6s %-13s %-13s %-8s %-28s %s" % ("mapped", "epic", "national",
                                              "offset", "national name", "swept name"))
    for part in [p.strip() for p in args.parts.split(",") if p.strip()]:
        cand = pool.get(part) or []
        if not cand:
            print("%-6s (no cached EPICs)" % part)
            continue
        for epic, swept_name in random.sample(cand, min(args.per_part, len(cand))):
            subprocess.run([sys.executable, ENGINE, "--epic", epic, "--search",
                            "--no-enrich"], capture_output=True, timeout=120)
            try:
                prof = json.load(open("work/out/epic_%s.json" % epic,
                                      encoding="utf-8")).get("profile") or {}
            except Exception:
                prof = {}
            nat = str(prof.get("part_no") or "")
            off = ""
            if nat.isdigit() and part.isdigit():
                off = "%+d" % (int(nat) - int(part))
                offsets[off] += 1
            print("%-6s %-13s %-13s %-8s %-28s %s"
                  % (part, epic, nat or "?", off, prof.get("name") or "?", swept_name))
            time.sleep(1.3)
    print("\noffset distribution (national - mapped):", dict(offsets))


if __name__ == "__main__":
    sys.exit(main())
