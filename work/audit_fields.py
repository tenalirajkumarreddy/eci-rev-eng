"""Read-only field audit for the old (2003/SIR) roll route.

Sweeps every serial of one old part, then reports:

  * every key the API actually returns, and how often
  * which of those keys the collector persists (worker._row_tuple)
  * the distinct values of the short/code-like fields, so mappings such as
    relationType F/H/M/O -> Father/Husband/Mother/Other are grounded in data

Nothing is written to the database - this only reads the live route.

    python -X utf8 work/audit_fields.py --state S01 --ac 1 --part 2
    python -X utf8 work/audit_fields.py --state S01 --ac 1 --part 12 --workers 6
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "old_eci"))

import client  # noqa: E402

# Exactly the keys worker._row_tuple reads. Anything the API returns outside this
# set is silently discarded today - that is what this audit is looking for.
STORED_KEYS = {
    "id", "oldPartSerialNo", "oldFullName", "firstName", "oldFullNameL1",
    "oldRelativeFullName", "relativeFName", "oldRelativeFullNameL1",
    "relationType", "gender", "age", "epicNumber", "markedByBlo",
    "bloMappedStateCd", "bloMappedAcNo", "bloMappedPartNo", "bloMappedEpicNo",
}

# Fields whose value domain is small enough to list in full.
CODE_FIELDS = ("relationType", "gender", "epicNumber", "markedByBlo")


def audit(state, ac, part, workers=6, cap=5000, dump=None):
    status, payload = client.fetch_window(state, ac, part)
    if status != 200 or not payload:
        print("no data for %s AC %s part %s (status %s)" % (state, ac, part, status))
        return 1
    end = client.probe_roll_end(state, ac, part, hard_cap=cap)
    print("sweeping %s AC %s part %s: serials 1..%s with %d workers\n"
          % (state, ac, part, end, workers))

    def one(serial):
        st, body = client.fetch_serial(state, ac, part, serial)
        return st, body

    key_count = collections.Counter()       # key -> records carrying it
    blanks = collections.Counter()           # key -> records where the value was falsy
    values = {f: collections.Counter() for f in CODE_FIELDS}
    extents = collections.defaultdict(lambda: [0, 0])   # key -> [seen, maxlen]
    samples = {}
    records = misses = errors = 0
    keyed_rows = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for st, body in pool.map(one, range(1, end + 1)):
            if st != 200 or not body:
                misses += 1 if st == 404 else 0
                errors += 0 if st == 404 else 1
                continue
            for rec in body:
                records += 1
                keyed_rows.append(rec)
                for k, v in rec.items():
                    key_count[k] += 1
                    if v in (None, "", [], {}):
                        blanks[k] += 1
                    else:
                        extents[k][1] = max(extents[k][1], len(str(v)))
                    extents[k][0] = max(extents[k][0], 1)
                    if k in values:
                        values[k][v] += 1
                    if k not in samples and not isinstance(v, (dict, list)):
                        samples[k] = v

    print("records %d | misses(404) %d | errors %d | distinct record ids %d\n"
          % (records, misses, errors, len({r.get("id") for r in keyed_rows})))

    print("%-26s %8s %8s  %s" % ("key", "present", "blank", "persisted?"))
    print("-" * 62)
    for k, n in sorted(key_count.items(), key=lambda x: (-x[1], x[0])):
        print("%-26s %8d %8d  %s"
              % (k, n, blanks[k], "yes" if k in STORED_KEYS else "*** NO ***"))

    extra = sorted(set(key_count) - STORED_KEYS)
    print("\nkeys returned but NOT persisted: %s" % (extra or "none"))
    never = sorted(k for k in STORED_KEYS if k not in key_count)
    print("keys we read that never appeared: %s" % (never or "none"))

    print("\ncode-like field domains:")
    for f, ctr in values.items():
        if not ctr:
            continue
        print("  %s:" % f)
        for v, n in ctr.most_common(12):
            print("      %-22s %7d" % (repr(v), n))
        if len(ctr) > 12:
            print("      ... %d more distinct values" % (len(ctr) - 12))

    print("\nkey -> longest value seen / sample:")
    for k in sorted(key_count, key=lambda k: -extents[k][1]):
        if extents[k][1]:
            print("  %-26s %4d  %s" % (k, extents[k][1], repr(samples.get(k))[:70]))

    if dump:
        with open(dump, "w", encoding="utf-8") as fh:
            json.dump(keyed_rows, fh, ensure_ascii=False, indent=1)
        print("\nraw records -> %s" % dump)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="S01")
    ap.add_argument("--ac", type=int, default=1)
    ap.add_argument("--part", type=int, default=2)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--json", help="also dump every raw record to this file")
    args = ap.parse_args()
    return audit(args.state, args.ac, args.part, args.workers, dump=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
