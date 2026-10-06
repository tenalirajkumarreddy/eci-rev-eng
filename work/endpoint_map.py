#!/usr/bin/env python3
"""
endpoint_map.py - enumerate every Retrofit endpoint declared in the app's smali.

Scans work/smali/**/*.smali for `@POST` / `@GET` retrofit annotations, resolves
the method they belong to, and pulls the URL value plus the static `@Headers`
and any `@Header`/`@Query` parameters. Output: work/out/endpoints.json plus a
readable table on stdout.

    python work/endpoint_map.py
    python work/endpoint_map.py --filter eepic
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
SMALI = BASE / "smali"
OUT = BASE / "out" / "endpoints.json"

METHOD_RE = re.compile(r"^\.method .*?(\w[\w$]*)\(([^)]*)\)")
VERB_RE = re.compile(r"\.annotation runtime Lretrofit2/http/(POST|GET|PUT|DELETE|PATCH);")
VALUE_RE = re.compile(r'value = "([^"]+)"')


def scan(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    endpoints: list[dict] = []
    method = params = ""
    collected: list[dict] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        m = METHOD_RE.match(line)
        if m:
            method = m.group(1)
            params = m.group(2)
        v = VERB_RE.search(line)
        if v:
            verb = v.group(1)
            url = ""
            headers: list[str] = []
            j = i
            depth = 0
            while j < len(lines) and j < i + 120:
                s = lines[j].strip()
                if ".annotation runtime Lretrofit2/http/Headers;" in s:
                    k = j
                    while k < len(lines) and k < i + 120:
                        hs = lines[k].strip()
                        hm = re.match(r'"(.*)"', hs)
                        if hm:
                            headers.append(hm.group(1))
                        if hs.startswith(".end annotation"):
                            break
                        k += 1
                val = VALUE_RE.search(s)
                if val and not url:
                    url = val.group(1)
                if s.startswith(".end method") and depth == 0:
                    break
                j += 1
            collected.append({
                "file": path.relative_to(BASE).as_posix(),
                "klass": path.stem,
                "method": method,
                "params": params,
                "verb": verb,
                "url": url,
                "headers": headers,
            })
        i += 1
    return collected


def main() -> int:
    ap = argparse.ArgumentParser(description="List every Retrofit endpoint")
    ap.add_argument("--filter", default="", help="only urls containing this")
    ap.add_argument("--all", action="store_true",
                    help="include third-party libraries (okhttp samples etc.)")
    args = ap.parse_args()

    if not SMALI.is_dir():
        print(f"[map] missing {SMALI}", file=sys.stderr)
        return 2

    seen: set[tuple[str, str, str]] = set()
    rows: list[dict] = []
    for path in SMALI.rglob("*.smali"):
        rel = path.relative_to(BASE).as_posix()
        if not args.all and not (rel.startswith("smali/smali_classes13/com/eci")
                                 or "/in/gov/eci/" in rel):
            continue
        for ep in scan(path):
            key = (ep["klass"], ep["method"], ep["url"])
            if key in seen or not ep["url"]:
                continue
            seen.add(key)
            rows.append(ep)

    rows.sort(key=lambda r: (r["url"], r["klass"]))
    interesting = [r for r in rows if "eci" in r["file"] or "citizen" in r["file"]]
    if args.filter:
        interesting = [r for r in interesting if args.filter.lower() in r["url"].lower()]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"[map] {len(rows)} distinct endpoints -> {OUT}\n")
    for r in interesting:
        hdrs = ", ".join(r["headers"])[:110]
        print(f"{r['verb']:4s} {r['url'][:78]:80s} {r['klass'][:34]:36s} {r['method']}")
        if hdrs:
            print(f"       headers: {hdrs}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
