"""Scan base APK smali for live gateway hosts + route/header annotations.

Also scan the old garuda smali for the same patterns so we can diff the two surfaces.
Usage: python work/scan_garuda_routes.py
"""
import os
import re
import sys

BASE = sys.argv[1] if len(sys.argv) > 1 else "work/smali"
PATS = [
    b"gateway-s1-blo",
    b"gateway-s2-blo",
    b"gateway-officials",
    b"gateway-vha",
    b"bloapp-h2h",
    b"GetErollElectorList",
    b"GetSectionList",
    b"formProcessingService",
    b"getBloDetails",
    b"/api/v1/",
]


def scan(root):
    hits = {p: [] for p in PATS}
    n = 0
    for dp, dn, fn in os.walk(root):
        for f in fn:
            if not f.endswith(".smali"):
                continue
            p = os.path.join(dp, f)
            try:
                b = open(p, "rb").read()
            except Exception:
                continue
            n += 1
            for pat in PATS:
                if pat in b:
                    hits[pat].append(p)
    return n, hits


def main():
    n, hits = scan(BASE)
    print("scanned", n, "files under", BASE)
    for pat, lst in hits.items():
        print("==", pat.decode(), "->", len(lst), "files")
        for x in lst[:20]:
            print("   ", x)


if __name__ == "__main__":
    main()
