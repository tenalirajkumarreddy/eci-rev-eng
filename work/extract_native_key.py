#!/usr/bin/env python3
"""
extract_native_key.py - pull the ECINET app keys out of libnative_lib.so.

Where the keys live
-------------------
BaseActivity (smali_classes13/com/eci/citizen/BaseActivity.smali) declares a
pile of `private native` getters and wraps each one as:

    getXxx() = new String(Base64.decode(getNativeXxx(), 0))

so the real values are Base64-encoded constants inside the native library
`libnative_lib.so` (System.loadLibrary("native_lib")).  Nothing is tied to a
user account: no login/signup is needed to obtain them; they ship with the app.

The lib is NOT in base.apk on split installs - it lives in the ABI split
(split_config.arm64_v8a.apk / base__abi.apk).  Pull it from the device:

    adb shell pm path in.gov.eci.app
    adb pull /data/app/~~.../in.gov.eci.app-.../split_config.arm64_v8a.apk
    python extract_native_key.py --apk split_config.arm64_v8a.apk

or pass the .so directly:

    python extract_native_key.py --so libnative_lib.so

What it does
------------
1. Extracts lib/**/*.so from an APK/ZIP if needed.
2. Finds the JNI method names (getNativeOfficialDetailSecureKey,
   getNativeEciTechAPIKEY, ...) in the binary.
3. Harvests Base64-looking tokens near those names (and globally), decodes
   them, and reports the ones that decode to printable text.

Output: JSON on stdout, saved to out/native_keys.json by default.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import tempfile
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
OUT_DIR = BASE_DIR / "out"

JNI_NAMES = [
    # the two the APK-style EPIC search needs (app_search.py)
    "getNTc",
    "getNTExternal",
    # everything else BaseActivity wraps
    "getNativeOfficialDetailSecureKey",
    "getNativeElectoralSearchSECURE_KEY",
    "getNativeElectorsDetailSecureKey",
    "getNativeElectorsDetailEpic",
    "getNativeECISITEAPIKEY",
    "getNativeEciTechAPIKEY",
    "getNativeSveepAPIKEY",
    "getNativeLocalLoginClientAPIKEY",
    "getNativeLocalRegistrationClientAPIKEY",
    "getNativeEepicHashNew",
    "getNativeEvpApiSecureEci",
    "getNativeEvpDigitalSecureApi",
    "getNativeEvpDigitalSecureApiIv",
    "getNativeAuthenticationTokenCredentials",
    "getNativeElectionResultTokenKey",
    "getNativeNgspClientKey",
    "getNativeDigitalApiSecureKey",
]

B64_RE = re.compile(rb"[A-Za-z0-9+/]{16,}={0,2}")


def decode_candidate(token: bytes) -> str | None:
    padded = token + b"=" * (-len(token) % 4)
    try:
        raw = base64.b64decode(padded, validate=True)
    except Exception:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if len(text) < 6:
        return None
    printable = sum(ch.isprintable() for ch in text)
    if printable != len(text):
        return None
    return text


def scan_blob(data: bytes, label: str) -> dict:
    hits: dict[str, list[dict]] = {"jni_names": [], "candidates": []}
    for name in JNI_NAMES:
        idx = data.find(name.encode())
        if idx >= 0:
            window = data[max(0, idx - 4096): idx + 4096]
            cands = []
            for m in B64_RE.finditer(window):
                text = decode_candidate(m.group(0))
                if text:
                    cands.append({"token": m.group(0).decode(), "decoded": text})
            hits["jni_names"].append({"name": name, "offset": idx, "nearby": cands[:8]})

    # Global harvest (de-duplicated) so obfuscated registration still gives leads.
    seen: set[str] = set()
    for m in B64_RE.finditer(data):
        token = m.group(0).decode()
        if token in seen:
            continue
        text = decode_candidate(m.group(0))
        if text and len(text) >= 12 and any(c.isdigit() for c in text):
            seen.add(token)
            hits["candidates"].append({"token": token, "decoded": text})
        if len(hits["candidates"]) >= 200:
            break
    hits["source"] = label
    return hits


def extract_libs(apk: Path, workdir: Path) -> list[Path]:
    out: list[Path] = []
    with zipfile.ZipFile(apk) as z:
        for info in z.infolist():
            if info.filename.startswith("lib/") and info.filename.endswith(".so"):
                dest = workdir / Path(info.filename).name
                dest.write_bytes(z.read(info))
                out.append(dest)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Extract ECINET native keys")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--so", type=Path, help="path to libnative_lib.so")
    src.add_argument("--apk", type=Path, help="path to APK/ZIP holding lib/*.so")
    ap.add_argument("--out", default=str(OUT_DIR / "native_keys.json"))
    args = ap.parse_args(argv)

    targets: list[Path] = []
    tmp: tempfile.TemporaryDirectory | None = None
    if args.so:
        targets = [args.so]
    else:
        tmp = tempfile.TemporaryDirectory()
        targets = extract_libs(args.apk, Path(tmp.name))
        if not targets:
            print(f"[extract] no lib/**/*.so inside {args.apk}", file=sys.stderr)
            print("[extract] this APK has no native code - it is a split install; "
                  "pull split_config.arm64_v8a.apk from the device", file=sys.stderr)
            return 2

    report = {"sources": [], "hits": []}
    for lib in targets:
        data = lib.read_bytes()
        report["sources"].append({"path": str(lib), "bytes": len(data)})
        report["hits"].append(scan_blob(data, lib.name))
        for entry in report["hits"][-1]["jni_names"]:
            print(f"[extract] {lib.name}: found {entry['name']}", file=sys.stderr)
            for cand in entry["nearby"]:
                print(f"          base64 {cand['token'][:40]}... -> {cand['decoded']!r}",
                      file=sys.stderr)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"\n[extract] wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
