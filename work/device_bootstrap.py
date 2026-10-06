#!/usr/bin/env python3
"""
device_bootstrap.py - one command from device to a real EPIC lookup.

Route 1 (phone connected over USB or wireless debugging):
    python device_bootstrap.py
    python device_bootstrap.py --epic SXQ2097129 --run

    - finds adb
    - lists devices (explains 'unauthorized' if you forgot the phone prompt)
    - adb shell pm path in.gov.eci.app
    - pulls the ABI split (split_config.arm64_v8a.apk / base__abi.apk)
    - extract_native_key.py -> classifies tc (AES key) and external (RSA pubkey)
    - writes work/app_keys.json

Route 2 (no device - you dropped a file):
    python device_bootstrap.py --from-apk split_config.arm64_v8a.apk
    python device_bootstrap.py --from-apk libnative_lib.so

Exit codes: 0 ok, 1 error, 2 no device, 3 app not installed, 4 keys not found.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import app_search
import key_oracle

BASE_DIR = Path(__file__).resolve().parent
OUT_DIR = BASE_DIR / "out"
SPLIT_DIR = OUT_DIR / "device_splits"
KEYS_PATH = BASE_DIR / "app_keys.json"
PACKAGE = "in.gov.eci.app"

ADB_FALLBACKS = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Android/Sdk/platform-tools/adb.exe",
    Path(os.environ.get("ANDROID_HOME", "")) / "platform-tools/adb.exe",
    Path(os.environ.get("ANDROID_SDK_ROOT", "")) / "platform-tools/adb.exe",
]


def find_adb() -> str | None:
    found = shutil.which("adb")
    if found:
        return found
    for cand in ADB_FALLBACKS:
        if cand and cand.is_file():
            return str(cand)
    return None


def run(cmd: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    # UTF-8 explicitly: voter names come back in local scripts and the Windows
    # default (cp1252) would mangle them.
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")


def adb_devices(adb: str) -> list[tuple[str, str]]:
    out = run([adb, "devices"])
    devices: list[tuple[str, str]] = []
    for line in out.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[0] != "*":
            devices.append((parts[0], parts[1]))
    return devices


def pm_path(adb: str, serial: str) -> list[str]:
    out = run([adb, "-s", serial, "shell", "pm", "path", PACKAGE])
    return [ln.split("package:", 1)[1].strip()
            for ln in out.stdout.splitlines() if ln.startswith("package:")]


def pull(adb: str, serial: str, remote: str, local: Path) -> bool:
    local.parent.mkdir(parents=True, exist_ok=True)
    out = run([adb, "-s", serial, "pull", remote, str(local)])
    if out.returncode == 0 and local.is_file() and local.stat().st_size:
        return True
    # some Android builds block 'adb pull' on /data/app; stream it instead
    print(f"[bootstrap] adb pull failed ({out.stderr.strip()[:120]}), trying exec-out",
          file=sys.stderr)
    with open(local, "wb") as fh:
        out2 = subprocess.run([adb, "-s", serial, "exec-out", "cat", remote],
                              stdout=fh, stderr=subprocess.PIPE, timeout=600)
    return out2.returncode == 0 and local.is_file() and local.stat().st_size > 0


def pick_abi_split(paths: list[str]) -> str | None:
    for needle in ("arm64_v8a", "arm64", "armeabi", "x86_64", "x86"):
        for path in paths:
            if needle in path:
                return path
    for path in paths:
        if "split_config" in path or "abi" in path.lower():
            return path
    return None


def read_native_so(artifact: Path) -> bytes:
    """Bytes of libnative_lib.so, from either the .so itself or an ABI split."""
    if artifact.suffix == ".so":
        return artifact.read_bytes()
    with zipfile.ZipFile(artifact) as zf:
        names = [n for n in zf.namelist()
                 if n.endswith("/libnative_lib.so") or n.endswith("libnative_lib.so")]
        if not names:
            return b""
        return zf.read(names[0])


B64_RE = re.compile(r"[A-Za-z0-9+/=]{8,1200}")  # the RSA SPKI token is 523 chars
_ORDER = {"b64b64": 0, "b64(": 1, "ascii": 2, "hex": 3}


def extract_candidates(artifact: Path) -> tuple[str, list[tuple[str, bytes]]]:
    """Return (external key token, [(label, raw AES key)]).

    The JNI-name-anchored report is still generated for evidence, but the key
    material is taken from *every* string in the .so: libnative_lib.so ships
    near-duplicate decoys next to the real constants and its .text is packed,
    so neither proximity to a JNI symbol nor decode shape can identify the real
    `tc`. Only a live check can (verify_tc below).
    """
    keys_json = OUT_DIR / "native_keys.json"
    flag = "--so" if artifact.suffix == ".so" else "--apk"
    out = run([sys.executable, str(BASE_DIR / "extract_native_key.py"),
               flag, str(artifact), "--out", str(keys_json)])
    if out.returncode != 0:
        print(out.stderr.strip()[-800:], file=sys.stderr, end="")

    blob = read_native_so(artifact)
    if not blob:
        return "", []
    texts = key_oracle.strings_in(blob)

    external = ""
    for text in texts:
        if not B64_RE.fullmatch(text):
            continue
        try:
            app_search.load_public_key(text)
        except Exception:
            continue
        external = text
        break

    candidates: list[tuple[str, bytes]] = []
    seen: set[bytes] = set()
    for label, key in key_oracle.key_candidates(texts):
        if key in seen:
            continue
        seen.add(key)
        candidates.append((label, key))
    # the real constants are stored as b64(b64(key)) - try those shapes first
    candidates.sort(key=lambda lk: _ORDER.get(lk[0].split("(")[0], 9))
    return external, candidates


def verify_tc(candidates: list[tuple[str, bytes]], external: str,
              epic: str) -> bytes | None:
    """Ask the live gateway which candidate key is the real `tc`.

    A genuine EPIC returns 200 with a record; a decoy key makes the server
    reject the securityKey (400 "[]").
    """
    for i, (label, key) in enumerate(candidates):
        tc_b64 = base64.b64encode(key).decode()
        status, body = key_oracle.fire(epic, tc_b64, external)
        print(f"[bootstrap] candidate {i:02d} {label[:28]:30s} -> HTTP {status}",
              file=sys.stderr)
        if status in (200, 201):
            print(f"[bootstrap] verified tc after {i + 1} of {len(candidates)} "
                  f"candidates ({label})", file=sys.stderr)
            return key
        time.sleep(1.6)  # gateway allows 1 request/s (burst 3)
    return None


def write_keys(path: Path, tc: str, external: str) -> None:
    path.write_text(json.dumps({"tc": tc, "external": external}, indent=2) + "\n",
                    encoding="utf-8")


def from_device(args) -> int:
    adb = args.adb or find_adb()
    if not adb:
        print("[bootstrap] adb not found - pass --adb <path> or use --from-apk",
              file=sys.stderr)
        return 2

    devices = adb_devices(adb)
    if not devices:
        print("[bootstrap] no device attached.\n"
              "  phone: Developer options -> USB debugging ON, plug in a data cable,\n"
              "         accept 'Allow USB debugging?'; or enable Wireless debugging,\n"
              "         run: adb pair <ip:port> <code> then adb connect <ip:port>",
              file=sys.stderr)
        return 2

    serial, state = devices[0]
    if state != "device":
        print(f"[bootstrap] device {serial} is '{state}' - accept the USB debugging "
              "prompt on the phone (or re-run adb connect for wireless)", file=sys.stderr)
        return 2
    print(f"[bootstrap] using device {serial}")

    paths = pm_path(adb, serial)
    if not paths:
        print(f"[bootstrap] {PACKAGE} is not installed on {serial}", file=sys.stderr)
        return 3
    print("[bootstrap] installed modules:\n  " + "\n  ".join(paths))

    split = pick_abi_split(paths) or next((p for p in paths if p.endswith(".apk")), None)
    if not split:
        print("[bootstrap] no APK module found to pull", file=sys.stderr)
        return 3
    if split == paths[0]:
        print("[bootstrap] warning: no ABI split listed; base.apk usually has no "
              "native code - falling back to it anyway", file=sys.stderr)

    local = SPLIT_DIR / Path(split).name
    if not pull(adb, serial, split, local):
        print("[bootstrap] could not read the APK from the device", file=sys.stderr)
        return 1
    print(f"[bootstrap] pulled {split} -> {local} ({local.stat().st_size} bytes)")
    return finish(local, args)


def finish(artifact: Path, args) -> int:
    external, candidates = extract_candidates(artifact)
    if not external or not candidates:
        print(
            "[bootstrap] keys not found automatically.\n"
            f"  external={'found' if external else 'MISSING'}  "
            f"tc candidates={len(candidates)}\n"
            "  Inspect out/native_keys.json and paste the two values into "
            f"{KEYS_PATH} yourself if the native lib obfuscates them.",
            file=sys.stderr,
        )
        return 4

    print(f"[bootstrap] {len(candidates)} AES-shaped tc candidates in the lib "
          "(decoys included)", file=sys.stderr)
    key = candidates[0][1]
    if args.epic:
        verified = verify_tc(candidates, external, args.epic.upper())
        if verified:
            key = verified
        else:
            print("[bootstrap] no candidate verified against the gateway; using "
                  "the first one - run key_oracle.py --epic <real EPIC> to find "
                  "the right key", file=sys.stderr)
    else:
        print("[bootstrap] no --epic given, so the tc cannot be verified "
              "(the lib ships decoys). Pass --epic <a real EPIC> to verify.",
              file=sys.stderr)

    tc = base64.b64encode(key).decode()
    keys_out = Path(args.keys_out)
    write_keys(keys_out, tc, external)
    print(f"[bootstrap] wrote {keys_out}")
    print(f"[bootstrap] tc       = {tc[:16]}...  ({len(key)} AES key bytes)")
    print(f"[bootstrap] external = {external[:24]}...")

    if args.run:
        if not args.epic:
            print("[bootstrap] --run needs --epic", file=sys.stderr)
            return 1
        out = run([sys.executable, str(BASE_DIR / "epic_engine.py"),
                   "--epic", args.epic, "--search", "--keys", str(keys_out)],
                  timeout=180)
        print(out.stdout[-6000:])
        print(out.stderr[-2000:], file=sys.stderr)
        return out.returncode
    return 0


def _utf8_stdout() -> None:
    """Voter names come back in local scripts (Telugu, Devanagari); the default
    Windows console code page cannot encode them."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - best effort
            pass


def main(argv: list[str] | None = None) -> int:
    _utf8_stdout()
    ap = argparse.ArgumentParser(description="Device -> app_keys.json bootstrap")
    ap.add_argument("--from-apk", type=Path,
                    help="ABI split .apk or libnative_lib.so (skips adb)")
    ap.add_argument("--adb", help="explicit adb path")
    ap.add_argument("--keys-out", default=str(KEYS_PATH),
                    help="where to write app_keys.json")
    ap.add_argument("--epic", help="EPIC for --run")
    ap.add_argument("--run", action="store_true",
                    help="immediately run app_search.py with the extracted keys")
    args = ap.parse_args(argv)

    if args.from_apk:
        if not args.from_apk.is_file():
            print(f"[bootstrap] not a file: {args.from_apk}", file=sys.stderr)
            return 1
        return finish(args.from_apk, args)
    return from_device(args)


if __name__ == "__main__":
    sys.exit(main())
