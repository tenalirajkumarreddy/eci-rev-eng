#!/usr/bin/env python3
"""
key_oracle.py - use the live gateway as an oracle to find the real `tc` AES key.

Context: app_search.py reproduces the APK's EPIC flow, and the RSA public key it
extracted is provably the server's (a random RSA key makes the gateway throw
500, while the extracted one yields a clean 400/200). But EPIC TBG0342345 -
which the real app resolves to a full voter record - still comes back 400 "[]",
so the `tc` AES key used by KGn.gPK is suspect (libnative_lib.so carries several
near-duplicate decoys and its .text is packed).

This script sweeps every plausible key candidate found in the .so, sends one
real request per candidate, and reports anything that is not 400 "[]".

    python work/key_oracle.py --epic TBG0342345
    python work/key_oracle.py --epic TBG0342345 --so work/out/native/libnative_lib.so
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import app_search as A  # noqa: E402

BASE = Path(__file__).resolve().parent
PRINTABLE = re.compile(rb"[\x20-\x7e]{8,}")


def strings_in(blob: bytes) -> list[str]:
    return [m.group().decode("ascii") for m in PRINTABLE.finditer(blob)]


def b64d(text: str) -> bytes | None:
    try:
        return base64.b64decode(text + "=" * (-len(text) % 4))
    except Exception:
        return None


def key_candidates(texts: list[str]) -> list[tuple[str, bytes]]:
    """(label, key-bytes) for every way a string could become an AES key."""
    out: list[tuple[str, bytes]] = []
    seen: set[bytes] = set()

    def add(label: str, raw: bytes | None) -> None:
        if raw and len(raw) in (16, 24, 32) and raw not in seen:
            seen.add(raw)
            out.append((label, raw))

    for text in texts:
        if not re.fullmatch(r"[A-Za-z0-9+/=_-]{8,200}", text):
            continue
        # 1) value is base64 of the key            (BaseActivity one decode)
        add(f"b64({text[:24]})", b64d(text))
        # 2) value is base64 of base64 of the key  (getter decode + gPK decode)
        inner = b64d(text)
        if inner:
            add(f"b64b64({text[:24]})", b64d(inner.decode("ascii", "ignore")))
        # 3) the string itself is the raw key (hex/ascii keys)
        add(f"ascii({text[:24]})", text.encode("ascii", "ignore"))
        # 4) hex text -> raw bytes
        if re.fullmatch(r"[0-9a-fA-F]{32,64}", text):
            try:
                add(f"hex({text[:24]})", bytes.fromhex(text))
            except ValueError:
                pass
    return out


def fire(epic: str, tc_b64: str, external: str) -> tuple[int, str]:
    inner = {
        "captchaData": "na",
        "captchaId": "na",
        "epicNumber": epic,
        "securityKey": A.gpk(epic, tc_b64),
    }
    body = A.encrypt_data(json.dumps(inner).encode(), A.load_public_key(external))
    req = urllib.request.Request(A.ENDPOINT, method="POST",
                                data=json.dumps(body).encode())
    req.add_header("User-Agent", A.MOBILE_UA)
    for k, v in A.request_headers().items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Find the real tc via the live server")
    ap.add_argument("--epic", default="TBG0342345")
    ap.add_argument("--so", type=Path, default=BASE / "out/native/libnative_lib.so")
    ap.add_argument("--delay", type=float, default=1.6,
                    help="seconds between requests (gateway: 1 token/s, burst 3)")
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args()

    tc_saved, external = A.load_keys(A.KEYS_PATH)
    if not external:
        print("[oracle] app_keys.json has no external key", file=sys.stderr)
        return 2

    texts = strings_in(args.so.read_bytes()) if args.so.is_file() else []
    print(f"[oracle] {len(texts)} strings in {args.so.name}")

    cands = key_candidates(texts)
    # put the currently-saved tc first so its behaviour is the baseline
    if tc_saved:
        raw = A._try_decode_value(tc_saved, (16, 24, 32))
        if raw:
            cands.insert(0, ("saved app_keys.json tc", raw))
    print(f"[oracle] {len(cands)} key candidates to test\n")

    for i, (label, key) in enumerate(cands[:args.limit]):
        tc_b64 = base64.b64encode(key).decode()
        status, body = fire(args.epic, tc_b64, external)
        head = body[:110].replace("\n", " ")
        flag = "  <-- INTERESTING" if status != 400 or body.strip() != "[]" else ""
        print(f"{i:03d} {label:34s} key={key.hex()[:32]:34s} -> {status} {head}{flag}")
        if status == 200 and body.strip() not in ("", "[]"):
            print(f"\n[oracle] SUCCESS with {label} -> {tc_b64}")
            (BASE / "out" / "key_oracle_winner.json").write_text(
                json.dumps({"tc": tc_b64, "external": external,
                            "label": label, "response": body[:4000]}, indent=2),
                encoding="utf-8")
            return 0
        time.sleep(args.delay)

    print("\n[oracle] no candidate produced a hit")
    return 1


if __name__ == "__main__":
    sys.exit(main())
