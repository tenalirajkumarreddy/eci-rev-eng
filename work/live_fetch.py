#!/usr/bin/env python3
"""
live_fetch.py - fire the REAL ECINET EPIC lookup requests and record the wire.

Public electoral search (no login, no user account):
    GET https://electoralsearch.in/api/search
        ?epic_no=<EPIC>&search_type=epic&passKey=<sha512(input + secureKey)>&page_no=1
    passKey = Utils.GetHashNew(input, secureKey) = lowercase hex SHA-512 of
    (trim(input) + trim(secureKey)), verified in
    smali_classes13/com/eci/citizen/utility/Utils.smali.
    The secret part (secureKey) is `new String(Base64.decode(
    getNativeOfficialDetailSecureKey()))` (BaseActivity), i.e. a native-lib
    constant.  Without it the script tries fallback variants so the server
    response tells us exactly what it validates.

Garuda gateway probe (--probe-gateway) fires the three authenticated EEPIC
endpoints without X-API-KEY to capture the server's auth requirement.

Usage:
    python live_fetch.py --epic SXQ2097129
    python live_fetch.py --epic SXQ2097129 --probe-gateway
"""

from __future__ import annotations

import argparse
import hashlib
import json
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
OUT_DIR = BASE_DIR / "out"

SEARCH_URL = "https://electoralsearch.in/api/search"
GARUDA_HOST = "https://gateway-vha.eci.gov.in"
GARUDA_V1 = GARUDA_HOST + "/api/v1"

MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)


def ssl_context(verify: bool = True) -> ssl.SSLContext:
    """TLS context.

    verify=True  -> normal verification against a CA bundle that exists
                    (this Python build ships no default store; certifi is used).
    verify=False -> app-equivalent mode.  The ECINET app itself installs a
                    no-op X509TrustManager (ApiClient$1.checkServerTrusted does
                    nothing) plus a permissive HostnameVerifier, so it accepts
                    any certificate - including the expired one that
                    electoralsearch.in currently serves.
    """
    if not verify:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    candidates = []
    try:
        import certifi  # type: ignore
        candidates.append(certifi.where())
    except Exception:
        pass
    candidates += [
        r"C:\Program Files\Git\mingw64\etc\ssl\certs\ca-bundle.crt",
        "/etc/ssl/certs/ca-certificates.crt",
    ]
    for cafile in candidates:
        try:
            if Path(cafile).is_file():
                return ssl.create_default_context(cafile=cafile)
        except Exception:
            continue
    return ssl.create_default_context()


def sha512_hex(*parts: str) -> str:
    """Utils.GetHashNew: trim each part, concatenate, SHA-512, lowercase hex."""
    data = "".join(str(p).strip() for p in parts).encode("utf-8")
    return hashlib.sha512(data).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fire(url: str, method: str = "GET", headers: dict | None = None,
         body: dict | None = None, timeout: int = 30,
         ctx: ssl.SSLContext | None = None) -> dict:
    req = urllib.request.Request(url, method=method)
    req.add_header("User-Agent", MOBILE_UA)
    req.add_header("Accept", "application/json, text/plain, */*")
    req.add_header("Accept-Language", "en-IN,en;q=0.9")
    req.add_header("Referer", "https://electoralsearch.in/")
    for k, v in (headers or {}).items():
        req.add_header(k, v)

    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")

    result: dict = {"url": url, "method": method, "sent_at": utc_now()}
    try:
        with urllib.request.urlopen(
            req, data=data, timeout=timeout,
            context=ctx or ssl_context(),
        ) as resp:
            raw = resp.read()
            result.update(
                status=resp.status,
                bytes=len(raw),
                content_type=resp.headers.get("Content-Type"),
                body=raw[:4000].decode("utf-8", "replace"),
            )
    except urllib.error.HTTPError as e:
        raw = e.read()
        result.update(
            status=e.code,
            bytes=len(raw),
            content_type=e.headers.get("Content-Type"),
            body=raw[:4000].decode("utf-8", "replace"),
        )
    except Exception as e:  # network/TLS/timeout
        result.update(status=None, bytes=0, error=f"{type(e).__name__}: {e}")
    return result


def public_search_variants(epic: str, ctx: ssl.SSLContext | None = None) -> list[dict]:
    """Try the real request with plausible passKey derivations."""
    inputs = {
        "epic": epic,
        "epic+IPS2062445": epic + "IPS2062445",
        "IPS2062445": "IPS2062445",
    }
    empty_secret = ""
    variants: list[tuple[str, str | None]] = []
    for label, inp in inputs.items():
        variants.append((f"sha512({label} + empty-secret)", sha512_hex(inp, empty_secret)))
    variants.append(("no-passKey", None))
    variants.append(("empty-passKey", ""))

    results = []
    for label, pass_key in variants:
        query = [("epic_no", epic), ("search_type", "epic"), ("page_no", "1")]
        if pass_key is not None:
            query.append(("passKey", pass_key))
        url = SEARCH_URL + "?" + urllib.parse.urlencode(query)
        res = fire(url, ctx=ctx)
        res["variant"] = label
        res["passKey"] = pass_key
        results.append(res)
        print(
            f"[public] {label:<34} -> status={res.get('status')} "
            f"bytes={res.get('bytes')} {res.get('error', '')}",
            file=sys.stderr,
        )
    return results


def gateway_probe(epic: str, api_key: str = "", bearer: str = "") -> list[dict]:
    """Fire the three authenticated EEPIC endpoints.

    gateway-vha.eci.gov.in serves a valid *.eci.gov.in certificate, so this
    probe always uses normal verification.  Credentials come from
    live_config.json (api_key -> X-API-KEY, bearer -> Authorization); without
    them the server answers 401.
    """
    probe_body = {"body": {"epic_no": epic, "search_type": "epic"}}
    headers: dict[str, str] = {}
    if api_key:
        headers["X-API-KEY"] = api_key
    if bearer:
        headers["Authorization"] = "Bearer " + bearer
    targets = [
        ("eepic_search", "POST", f"{GARUDA_V1}/eepic/GetElectorDetailForEEPIC"),
        ("eepic_card_pdf", "POST", f"{GARUDA_HOST}/eepic/GetEEPICCard"),
        ("verification", "POST", f"{GARUDA_V1}/vh_epicno_verify"),
    ]
    results = []
    for name, method, url in targets:
        res = fire(url, method=method, headers=headers, body=probe_body)
        res["id"] = name
        results.append(res)
        print(
            f"[gateway] {name:<16} -> status={res.get('status')} "
            f"bytes={res.get('bytes')} {res.get('error', '')}",
            file=sys.stderr,
        )
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Live ECINET EPIC fetch")
    ap.add_argument("--epic", required=True, help="voter EPIC number")
    ap.add_argument("--tls", choices=("verify", "app"), default="verify",
                    help="verify = normal TLS check; app = app-equivalent "
                         "(verification off, mirroring the APK's trust-all "
                         "X509TrustManager; needed while electoralsearch.in "
                         "serves an expired certificate)")
    ap.add_argument("--probe-gateway", action="store_true",
                    help="also fire the 3 Garuda EEPIC endpoints to capture auth behaviour")
    ap.add_argument("--config", default=str(BASE_DIR / "live_config.json"),
                    help="JSON with api_key / bearer for the gateway probes")
    ap.add_argument("--out", default=str(OUT_DIR / "live_http.json"))
    args = ap.parse_args(argv)

    api_key = bearer = ""
    cfg_path = Path(args.config)
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            api_key = cfg.get("api_key", "") or ""
            bearer = cfg.get("bearer", "") or ""
        except Exception as e:
            print(f"[live_fetch] config unreadable: {e}", file=sys.stderr)

    epic = " ".join(str(args.epic).split()).upper()
    tls_app = args.tls == "app"
    ctx = ssl_context(verify=not tls_app)
    report: dict = {
        "engine": "live_fetch",
        "version": "1.0.0",
        "epic": epic,
        "started_at": utc_now(),
        "tls_mode": ("app-equivalent (certificate verification disabled)"
                     if tls_app else "verified"),
        "public_search": public_search_variants(epic, ctx=ctx),
    }
    if args.probe_gateway:
        report["gateway_credentials"] = {
            "X-API-KEY": bool(api_key), "Authorization": bool(bearer)}
        report["gateway_probe"] = gateway_probe(epic, api_key, bearer)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(report, indent=2))
    print(f"\n[live_fetch] wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
