#!/usr/bin/env python3
"""
elastic_probe.py - try every `elastic/*` route the APK declares for one EPIC.

All of them take the same encrypted envelope (AKgn.encryptData over a Gson
TElasticSearchRequest + KGn.gPK securityKey), so once app_keys.json is verified
each route is one call away. This compares what each route returns and reports
fields the national-display record does not have.

    python work/elastic_probe.py --epic TBG0342345
    python work/elastic_probe.py --epic SXQ2097129 --show-fields
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import app_search as A  # noqa: E402

BASE = Path(__file__).resolve().parent
API_BASE = A.ENDPOINT.rsplit("elastic/", 1)[0]  # https://gateway-vha.eci.gov.in/api/v1/

# every elastic/* route in TRestClient that carries a ChecksumRequest
ROUTES = [
    ("national-epic", "elastic/search-by-epic-from-national-display-v1", {}),
    ("state-details", "elastic/search-by-details-from-state-display-v1", {}),
    ("state-mobile", "elastic/search-by-mobile-from-state-search-display-v1", {}),
    ("form-epic", "elastic/get-by-epic-for-form", {}),
    ("form-details", "elastic/get-by-details-for-form", {}),
]

FORM_HEADERS = {"Authorization": "", "atkn_bnd": "", "rtkn_bnd": ""}


def call(path: str, inner: dict, external: str, extra: dict | None = None) -> tuple[int, str]:
    body = A.encrypt_data(json.dumps(inner).encode(), A.load_public_key(external))
    req = urllib.request.Request(API_BASE + path, method="POST",
                                 data=json.dumps(body).encode())
    req.add_header("User-Agent", A.MOBILE_UA)
    for k, v in A.request_headers().items():
        req.add_header(k, v)
    for k, v in (extra or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def fields_of(raw: str) -> set[str]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return set()
    hits = data if isinstance(data, list) else [data]
    out: set[str] = set()
    for h in hits:
        content = (h or {}).get("content") if isinstance(h, dict) else None
        if isinstance(content, dict):
            out |= set(content.keys())
        elif isinstance(h, dict):
            out |= set(h.keys())
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Probe every elastic/* route")
    ap.add_argument("--epic", required=True)
    ap.add_argument("--keys", default=str(BASE / "app_keys.json"))
    ap.add_argument("--show-fields", action="store_true")
    ap.add_argument("--delay", type=float, default=1.6)
    ap.add_argument("--only", help="run a single route label (e.g. state-details)")
    ap.add_argument("--extra", action="append", default=[],
                    metavar="KEY=VALUE",
                    help="extra inner JSON field, e.g. stateCd=S01 (repeatable)")
    args = ap.parse_args()

    extra_fields: dict[str, str] = {}
    for pair in args.extra:
        if "=" in pair:
            k, v = pair.split("=", 1)
            extra_fields[k.strip()] = v.strip()

    tc, external = A.load_keys(Path(args.keys))
    if not tc or not external:
        print("[probe] no keys in app_keys.json", file=sys.stderr)
        return 2
    epic = args.epic.strip().upper()

    import time
    baseline: set[str] = set()
    for label, path, extra_headers in ROUTES:
        if args.only and label != args.only:
            continue
        inner = {"captchaId": "na", "captchaData": "na",
                 "epicNumber": epic, "securityKey": A.gpk(epic, tc)}
        inner.update(extra_fields)
        status, raw = call(path, inner, external,
                           FORM_HEADERS if "form" in label else None)
        fields = fields_of(raw)
        count = 0
        try:
            parsed = json.loads(raw)
            count = len(parsed) if isinstance(parsed, list) else (1 if parsed else 0)
        except json.JSONDecodeError:
            count = -1
        if label == "national-epic":
            baseline = fields
        extra = fields - baseline if baseline else set()
        print(f"{label:14s} {path[:52]:54s} HTTP {status:3d}  "
              f"records={count:2d}  fields={len(fields):3d}"
              + (f"  (+{len(extra)} beyond national)" if extra else ""))
        if status != 200:
            print(f"                 -> {raw[:120]}")
        if args.show_fields and fields:
            only = sorted(fields if not extra else extra)
            print(f"                 fields: {', '.join(only[:26])}"
                  + (" ..." if len(only) > 26 else ""))
        time.sleep(args.delay)

    return 0


if __name__ == "__main__":
    sys.exit(main())
