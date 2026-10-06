#!/usr/bin/env python3
"""
anon_probe.py - probe the APK's *anonymous* voter/part metadata routes.

TRestClient carries a family of routes the app calls before any login - the
"WithOutToken" variants - plus the common/part lookups. They return district,
assembly, part and polling-station metadata (the "Know Your Polling Station"
and address/room data) which the national-display voter record does not carry.

    python work/anon_probe.py --state S01 --district S0101 --ac 10
    python work/anon_probe.py --state 1 --district 1 --ac 10 --show
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
API = "https://gateway-vha.eci.gov.in/api/v1/"
UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")

BASE_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "applicationName": "VHA",
    "appName": "VHA",
    "channelidobo": "VHA",
    "platform-type": "ANDROIDMOB",
}


def call(method: str, path: str, query: dict | None = None,
         headers: dict | None = None, body: dict | None = None,
         timeout: int = 30) -> tuple[int, str]:
    url = API + path
    if query:
        from urllib.parse import urlencode
        url += "?" + urlencode(query)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, method=method, data=data)
    req.add_header("User-Agent", UA)
    for k, v in {**BASE_HEADERS, **(headers or {})}.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Probe anonymous part/PS routes")
    ap.add_argument("--state", default="S01")
    ap.add_argument("--district", default="S0101")
    ap.add_argument("--ac", default="10")
    ap.add_argument("--show", action="store_true", help="print response bodies")
    ap.add_argument("--delay", type=float, default=1.6)
    args = ap.parse_args()

    trials = [
        ("districts", "GET", "citizen/sir/getDistrict", {}, {"state": args.state}),
        ("assemblies(state)", "GET", "citizen/sir/getAsmbly", {}, {"State": args.state}),
        ("assemblies(district)", "GET", "citizen/sir/getAssmblyByDist",
         {"District": args.district}, {"state": args.state}),
        ("parts(ac)", "GET", "citizen/sir/getPartByAc",
         {"Asmbly": args.ac}, {"state": args.state}),
        ("parts(common)", "GET", "common/part/get/bystatecd/districtcd/acNumber",
         {"stateCd": args.state, "acNumber": args.ac}, {"state": args.state}),
        ("part-ext", "GET", "getPartByAc", {"Asmbly": args.ac}, {"state": args.state}),
    ]

    for label, method, path, query, headers in trials:
        status, raw = call(method, path, query, headers)
        n = -1
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                n = len(parsed)
            elif isinstance(parsed, dict):
                n = len(parsed.get("data") or parsed.get("content") or parsed)
        except json.JSONDecodeError:
            pass
        print(f"{label:22s} {path[:56]:58s} HTTP {status:3d}  items={n}")
        if args.show and status == 200:
            print("      ", raw[:400].replace("\n", " "))
        elif status != 200:
            print("      ->", raw[:120])
        time.sleep(args.delay)
    return 0


if __name__ == "__main__":
    sys.exit(main())
