#!/usr/bin/env python3
"""
portal_search.py - real EPIC lookup against the CURRENT public Voters' Services
Portal (https://electoralsearch.eci.gov.in), replicating exactly what the
browser does.  No login, no app key - but the portal's captcha is meant for a
human, so step 1 fetches it and YOU read the text; step 2 sends it.

Ground truth from the portal's own JS bundle (out/portal_main.js):
  * captcha:
      GET https://gateway-voters.eci.gov.in/api/v1/captcha-service/getCaptcha/sir
      headers {appName: ELECTORAL-SEARCH}
      response {data: <b64>}  --AES-256-GCM(po)-->  {captcha: <b64 jpg>, id}
      (po = the bundle constant "...".slice(15,59), 32 raw bytes)
  * search:
      POST https://gateway-voters.eci.gov.in/api/v1/elastic/search-by-epic-from-national-display-v1
      headers {applicationName, channelidobo, appName: ELECTORAL-SEARCH}
      body: {encryptedPayload, encryptedKey, iv}
            payload  = AES-256-GCM(plaintext, random key+iv)
            key      = RSA-OAEP(SHA-256) against the portal's SPKI public key
            plaintext= {epicNumber, isPortal:true, captchaId, captchaData,
                        securityKey:"na", eSEARCHYNEFjd3S:"1021"}

Usage:
  python portal_search.py captcha
  python portal_search.py search --epic SXQ2097129 --captcha AB12CD
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

BASE_DIR = Path(__file__).resolve().parent
OUT_DIR = BASE_DIR / "out"

GATEWAY = "https://gateway-voters.eci.gov.in"
CAPTCHA_URL = GATEWAY + "/api/v1/captcha-service/getCaptcha/sir"
SEARCH_URL = (
    GATEWAY
    + "/api/v1/elastic/search-by-epic-from-national-display-v1"
)

PORTAL_ORIGIN = "https://electoralsearch.eci.gov.in/"

# The bundle's request interceptor sets all three on every call; the captcha
# service answers "Unauthorized request" if any of them is missing.
PORTAL_HEADERS = {
    "applicationName": "ELECTORAL-SEARCH",
    "channelidobo": "ELECTORAL-SEARCH",
    "appName": "ELECTORAL-SEARCH",
}

MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)

# From the bundle:
#   uo = the RSA public key (base64 SPKI)
#   po = "SFfIO0YsOlOKawZe855n97lc4tcPkj7WWsi38yNWpalLBLZzQdkqHWYbZ0=GhSJk2raUo".slice(15,59)
RSA_PUB_B64 = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEArb7++BxL/YN8OIln+6FL9Gnw5DNmQ/VF"
    "ZXss+J+TuQyJc891JbqbijxYQNEin2c2u+CnpXpoGQ/1gUSzDMJeNS3sNSlIUykp2dt7xIm/cmV4"
    "sZ/c769vCxVRosMfRaZJnBAah+m1X26lEhnOo0wpAB9Txr8RIyBe6h7PiQWykeJeh6UacOBBX28k"
    "gkq7+vJhW8HgB38lt32XRocznRYwS9LqR7ZweFmQhTr1+EGrqiEKCOCxMYgHR2SQckb96hZ9kWzf"
    "zeun4bUO5oXKJciLkiS1IgKieADEvYLgu129ZIpn1H+8H+8ikNNVETqEDDMtqcQcQmWppJvcWHaX"
    "As+f8QIDAQAB"
)
RESPONSE_KEY_B64 = (
    "SFfIO0YsOlOKawZe855n97lc4tcPkj7WWsi38yNWpalLBLZzQdkqHWYbZ0=GhSJk2raUo"[15:59]
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def b64d(text: str) -> bytes:
    return base64.b64decode(text)


def b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def decrypt_response(payload_b64: str) -> dict:
    """ho() from the bundle: key=po, iv = first 12 bytes, AES-256-GCM."""
    blob = b64d(payload_b64)
    iv, ciphertext = blob[:12], blob[12:]
    plain = AESGCM(b64d(RESPONSE_KEY_B64)).decrypt(iv, ciphertext, None)
    return json.loads(plain.decode("utf-8"))


def encrypt_request(body: dict) -> dict:
    """yo() from the bundle: RSA-OAEP(SHA-256)-wrapped AES-256-GCM."""
    public_key = serialization.load_der_public_key(b64d(RSA_PUB_B64))
    aes_key = os.urandom(32)
    iv = os.urandom(12)
    ciphertext = AESGCM(aes_key).encrypt(iv, json.dumps(body).encode("utf-8"), None)
    wrapped_key = public_key.encrypt(
        aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return {
        "encryptedPayload": b64e(ciphertext),
        "encryptedKey": b64e(wrapped_key),
        "iv": b64e(iv),
    }


def call(url: str, method: str = "GET", headers: dict | None = None,
         body: dict | None = None, timeout: int = 40) -> tuple[int | None, str, dict]:
    req = urllib.request.Request(url, method=method)
    req.add_header("User-Agent", MOBILE_UA)
    req.add_header("Accept", "application/json, text/plain, */*")
    req.add_header("Accept-Language", "en-IN,en;q=0.9")
    req.add_header("Origin", PORTAL_ORIGIN.rstrip("/"))
    req.add_header("Referer", PORTAL_ORIGIN)
    for k, v in (headers or {}).items():
        req.add_header(k, v)

    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        status = e.code
    except Exception as e:
        return None, "", {"error": f"{type(e).__name__}: {e}"}

    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        parsed = {"_raw": raw[:2000]}
    return status, raw, parsed


def cmd_captcha(_args: argparse.Namespace) -> int:
    status, raw, parsed = call(CAPTCHA_URL, headers=PORTAL_HEADERS)
    print(f"[captcha] GET -> {status}", file=sys.stderr)
    if status != 200 or "data" not in parsed:
        print(json.dumps(parsed, indent=2)[:2000])
        return 1

    captcha = decrypt_response(parsed["data"])
    captcha_id = captcha.get("id")
    image_b64 = captcha.get("captcha")
    if not image_b64:
        print("[captcha] server refused: " + json.dumps(captcha)[:500], file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "captcha.jpg").write_bytes(b64d(image_b64))
    meta = {"id": captcha_id, "fetched_at": utc_now(), "source": CAPTCHA_URL}
    (OUT_DIR / "captcha.json").write_text(json.dumps(meta, indent=2) + "\n",
                                          encoding="utf-8")

    html = (
        "<!doctype html><meta charset='utf-8'>"
        "<title>ECI captcha</title>"
        "<body style='background:#111;color:#eee;font:16px system-ui;"
        "display:flex;flex-direction:column;align-items:center;gap:12px;"
        "padding:24px'>"
        "<h2 style='margin:0'>Enter this captcha in the reply</h2>"
        f"<img src='data:image/jpg;base64,{image_b64}' "
        "style='border:1px solid #666;background:#fff;padding:4px'>"
        f"<div style='font:12px monospace;color:#888'>captchaId={captcha_id}</div>"
        "</body>"
    )
    (OUT_DIR / "captcha.html").write_text(html, encoding="utf-8")

    print(json.dumps({"captchaId": captcha_id, "image": "out/captcha.jpg",
                      "preview": "out/captcha.html"}, indent=2))
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    meta_path = OUT_DIR / "captcha.json"
    if not meta_path.exists():
        print("[search] no captcha on file - run `python portal_search.py captcha` first",
              file=sys.stderr)
        return 2
    captcha_id = json.loads(meta_path.read_text(encoding="utf-8"))["id"]

    plaintext = {
        "epicNumber": " ".join(args.epic.split()).upper(),
        "isPortal": True,
        "captchaId": captcha_id,
        "captchaData": args.captcha,
        "securityKey": "na",
        "eSEARCHYNEFjd3S": "1021",
    }
    encrypted = encrypt_request(plaintext)

    status, raw, parsed = call(SEARCH_URL, method="POST", headers=PORTAL_HEADERS,
                               body=encrypted)
    print(f"[search] POST -> {status}", file=sys.stderr)

    # The portal's search endpoints answer with plain JSON arrays; accept an
    # encrypted {data: b64} response too, for safety.
    if isinstance(parsed, dict) and set(parsed.keys()) == {"data"} and \
            isinstance(parsed.get("data"), str):
        parsed = decrypt_response(parsed["data"])

    report = {
        "engine": "portal_search",
        "version": "1.0.0",
        "epic": plaintext["epicNumber"],
        "endpoint": SEARCH_URL,
        "sent_at": utc_now(),
        "http_status": status,
        "request_plaintext": plaintext,
        "response": parsed,
    }
    out_path = OUT_DIR / "portal_search_result.json"
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    # Compact human summary of whatever the server returned.
    rows = parsed if isinstance(parsed, list) else parsed.get("content") or []
    if isinstance(rows, dict):
        rows = [rows]
    print(json.dumps(report, indent=2)[:8000])
    print(f"\n[search] wrote {out_path}", file=sys.stderr)
    if not rows:
        print("[search] no records in response (wrong captcha or EPIC not found)",
              file=sys.stderr)
        return 1
    for i, row in enumerate(rows, 1):
        content = row.get("content", row) if isinstance(row, dict) else row
        print(f"[search] record {i}: " + json.dumps(
            {k: v for k, v in content.items() if v not in ("", None)}, indent=2),
            file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Current public ECI portal search")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_cap = sub.add_parser("captcha", help="fetch + save a captcha image")
    p_cap.set_defaults(func=cmd_captcha)

    p_search = sub.add_parser("search", help="run the EPIC search with a solved captcha")
    p_search.add_argument("--epic", required=True)
    p_search.add_argument("--captcha", required=True, help="the text from out/captcha.jpg")
    p_search.set_defaults(func=cmd_search)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
