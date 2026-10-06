"""Find ECI host and route strings inside the apktool smali trees.

`rg` is not on PATH here and both `work/smali` and `work/garuda_smali` are
git-ignored (so the code_search tool skips them), hence a direct byte scan.

    python -X utf8 work/scan_smali_routes.py            # both trees
    python -X utf8 work/scan_smali_routes.py --garuda    # only the old BLO APK
    python -X utf8 work/scan_smali_routes.py --show 6    # more sample lines
"""
from __future__ import annotations

import argparse
import collections
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)))
TREES = {
    "play": os.path.join(ROOT, "smali"),
    "garuda": os.path.join(ROOT, "garuda_smali"),
}

# Patterns worth finding, with why each matters.
PATTERNS = {
    "host:eci.gov.in": re.compile(rb"eci\.gov\.in", re.I),
    "host:ecinet.in": re.compile(rb"ecinet\.in", re.I),
    "svc:elastic": re.compile(rb"elastic[-_/]", re.I),
    "svc:sir-citizen": re.compile(rb"sir[-_]citizen", re.I),
    "route:get-eroll": re.compile(rb"get[-_]eroll[-_]?[A-Za-z0-9]*", re.I),
    "route:eroll-word": re.compile(rb"[A-Za-z]*[Ee]roll[A-Za-z]*"),
    "route:national-display": re.compile(rb"national[-_]display", re.I),
    "route:getAsmbly": re.compile(rb"getAsmbly", re.I),
    "route:part-list": re.compile(rb"byStateCd|byStateCD|districtcd", re.I),
    "word:epic": re.compile(rb"\bEPIC\b|epicNumber", re.I),
    "word:roll": re.compile(rb"\broll\b", re.I),
    "word:sir": re.compile(rb"\bSIR\b"),
    "word:elector": re.compile(rb"elector", re.I),
    "url": re.compile(rb"https?://[A-Za-z0-9./_%?=&-]{4,120}"),
}

SKIP = (b".png", b".jpg", b".so", b".ttf", b".webp", b".gif", b".zip")


def files(tree):
    for base, _dirs, names in os.walk(tree):
        for name in names:
            path = os.path.join(base, name)
            low = name.lower().encode()
            if low.endswith(SKIP):
                continue
            yield path


def scan_one(path):
    try:
        with open(path, "rb") as fh:
            blob = fh.read()
    except OSError:
        return None
    hits = {}
    for label, rx in PATTERNS.items():
        found = rx.findall(blob)
        if found:
            hits[label] = [m if isinstance(m, bytes) else m[0] for m in found]
    return (path, hits) if hits else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--garuda", action="store_true", help="only work/garuda_smali")
    ap.add_argument("--play", action="store_true", help="only work/smali")
    ap.add_argument("--show", type=int, default=3, help="sample lines per pattern")
    args = ap.parse_args()

    trees = dict(TREES)
    if args.garuda:
        trees = {"garuda": TREES["garuda"]}
    elif args.play:
        trees = {"play": TREES["play"]}

    for label, tree in trees.items():
        if not os.path.isdir(tree):
            print("[%s] missing: %s" % (label, tree))
            continue
        paths = list(files(tree))
        print("=== %s (%s): %d files ===" % (label, tree, len(paths)))
        per = collections.Counter()
        unique = {k: collections.Counter() for k in PATTERNS}
        examples = {k: [] for k in PATTERNS}
        with ThreadPoolExecutor(max_workers=16) as pool:
            for res in pool.map(scan_one, paths):
                if not res:
                    continue
                path, hits = res
                for k, vals in hits.items():
                    per[k] += 1
                    for v in vals:
                        unique[k][v] += 1
                    if len(examples[k]) < args.show:
                        examples[k].append((os.path.relpath(path, tree), vals[:3]))
        if not per:
            print("  no patterns matched at all - tree may be empty or unreadable")
        for k in PATTERNS:
            if not per[k]:
                continue
            print("\n  %-24s files=%-6d distinct=%d" % (k, per[k], len(unique[k])))
            for v, n in unique[k].most_common(12):
                print("      %-58s x%d" % (v[:58].decode("utf-8", "replace"), n))
            for rel, vals in examples[k]:
                print("      e.g. %s :: %s" % (rel,
                      " | ".join(v[:60].decode("utf-8", "replace") for v in vals)))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
