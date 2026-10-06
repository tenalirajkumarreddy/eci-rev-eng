"""Extract hosts + API paths from smali trees (old garuda + current base APK).

Usage: python work/host_mine.py <smali-root> [<smali-root> ...]
Writes nothing; prints sorted unique hosts with file counts and the paths seen
in files that also mention a host, so we can spot route families worth probing.
"""
import os
import re
import sys
from collections import defaultdict

HOST_RE = re.compile(rb"https?://([A-Za-z0-9._\-]+)")
PATH_RE = re.compile(rb'value = "([^"]{2,120})"')


def scan(root):
    hosts = defaultdict(set)   # host -> set(files)
    for dp, dn, fn in os.walk(root):
        for f in fn:
            if not f.endswith(".smali"):
                continue
            p = os.path.join(dp, f)
            try:
                b = open(p, "rb").read()
            except Exception:
                continue
            for m in HOST_RE.finditer(b):
                hosts[m.group(1).decode()].add(p)
    return hosts


def main():
    for root in sys.argv[1:]:
        hosts = scan(root)
        print("=" * 20, root, "-", len(hosts), "hosts")
        for h in sorted(hosts):
            print("%-46s %4d" % (h, len(hosts[h])))


if __name__ == "__main__":
    main()
