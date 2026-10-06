#!/usr/bin/env python3
"""
app_search.py - the APK's OWN EPIC search: enter EPIC, details come back, no captcha.

Reconstructed from the smali:

  PollingStationSearchActivity.callSearchApiTrial()
  CommonFormSevenProgram / ElectionOfficialsActivity (same pattern)

    TElasticSearchRequest r = new TElasticSearchRequest();  // ctor: captchaId="na", captchaData="na"
    r.epicNumber = EPIC.toUpperCase();
    r.sKey = KGn.gPK(r.epicNumber, getTc());      // securityKey
    json = gson.toJson(r);
    ChecksumRequest body = AKgn.encryptData(json.getBytes(UTF_8),
                                            AKgn.stringToPublicKey(getTExternal()));
    TRestClient.doEpicSearch(body);               // TApiClient.getRetroProdClient

Wire contract:
  POST https://gateway-vha.eci.gov.in/api/v1/elastic/search-by-epic-from-national-display-v1
  headers: Content-Type: application/json
           Accept: application/json
           applicationName: VHA   appName: VHA   channelidobo: VHA
           platform-type: ANDROIDMOB
  body:    {"encryptedKey": b64(RSA-OAEP-SHA256(aesKey)),
            "iv": b64(12-byte GCM nonce),
            "encryptedPayload": b64(AES-256-GCM(json))}

  KGn.gPK(primary, k):
      ts    = SimpleDateFormat("yyyy-MM-dd-HH-mm-ss").format(now)     # local time
      plain = primary + ":" + ts + ":" + <6 random digits>
      key   = Base64.decode(k)                                        # from getTc()
      iv    = 16 zero bytes
      out   = b64(AES/GCM/NoPadding(plain, key, iv))

  AKgn.encryptData:
      aesKey = AES-256 random; iv = 12 random bytes
      ct     = AES/GCM/NoPadding with 128-bit tag  (= ct||tag)
      wrapped= RSA/ECB/OAEPPadding, SHA-256 + MGF1(SHA-256), default PSource
      everything Base64.encodeToString(bytes, NO_WRAP)

Only two app constants are needed, both native (`libnative_lib.so`):
  tc       -> getTc()        (Base64 AES key used by gPK)
  external -> getTExternal() (Base64 X.509/SPKI RSA public key)
No account, no OTP, no captcha.  Fill work/app_keys.json:
  {"tc": "...", "external": "..."}

CAVEAT - `tc` must be VERIFIED, never guessed.  libnative_lib.so ships decoy
strings next to the real constant and its .text is packed, so the first
AES-shaped candidate is usually a decoy; a wrong key silently yields 400 "[]"
for every EPIC.  Resolve it against the live gateway with
`python key_oracle.py --epic <a real EPIC>` (or device_bootstrap.py --epic ...).
The device-verified value is in app_keys.json today.

Response semantics: 200 with a list = records found; 200 with `[]` = that EPIC
is not in the national display; 400 `[]` = the request was rejected (bad key).

Usage:
  python app_search.py --epic TBG0342345              # fires for real when keys exist
  python app_search.py --epic TBG0342345 --dry-run    # print the exact request
  python app_search.py --selftest                     # offline crypto round-trip
  python epic_engine.py --epic TBG0342345 --search    # EPIC -> voter profile
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import secrets
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

BASE_DIR = Path(__file__).resolve().parent
OUT_DIR = BASE_DIR / "out"
KEYS_PATH = BASE_DIR / "app_keys.json"

ENDPOINT = (
    "https://gateway-vha.eci.gov.in/api/v1/"
    "elastic/search-by-epic-from-national-display-v1"
)
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "applicationName": "VHA",
    "appName": "VHA",
    "channelidobo": "VHA",
    "platform-type": "ANDROIDMOB",
}


def request_headers() -> dict:
    """TApiClient.getRetroProdClient attaches DecryptionInterceptor, which adds
    `device-id: AppController.DI` (a random UUID unless Firebase gives one)."""
    return {**HEADERS, "device-id": str(uuid.uuid4())}

MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def b64d(text: str) -> bytes:
    """android.util.Base64.DEFAULT decode is lenient about padding."""
    padded = text.strip() + "=" * (-len(text.strip()) % 4)
    return base64.b64decode(padded)


def _try_decode_value(text: str, want: tuple[int, ...]) -> bytes | None:
    """Values pass through `new String(Base64.decode(native))` in BaseActivity,
    so an extracted constant may need one or two Base64 unwraps."""
    for _ in range(2):
        try:
            raw = b64d(text)
        except Exception:
            return None
        if len(raw) in want:
            return raw
        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError:
            return None
        if not re.fullmatch(r"[A-Za-z0-9+/=]+", text):
            return None
    return None


# --------------------------------------------------------------------------
# KGn.gPK
# --------------------------------------------------------------------------
def gpk(primary: str, tc: str) -> str:
    ts = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    rand6 = "".join(secrets.choice("1234567890") for _ in range(6))
    plaintext = f"{primary}:{ts}:{rand6}".encode("utf-8")
    key = _try_decode_value(tc, (16, 24, 32))
    if key is None:
        raise ValueError("tc does not decode to a 16/24/32-byte AES key")
    ciphertext = AESGCM(key).encrypt(b"\x00" * 16, plaintext, None)
    return b64e(ciphertext)


# --------------------------------------------------------------------------
# AKgn.encryptData
# --------------------------------------------------------------------------
def _decode_variants(text: str) -> list[bytes]:
    """Base64 unwrap levels of a native constant.

    BaseActivity wraps every native getter as new String(Base64.decode(value))
    and callers decode again, so an extracted constant may be 1-3 layers deep.
    """
    out: list[bytes] = []
    current = (text or "").strip()
    for _ in range(3):
        try:
            raw = b64d(current)
        except Exception:
            break
        out.append(raw)
        try:
            current = raw.decode("ascii").strip()
        except UnicodeDecodeError:
            break
        if not re.fullmatch(r"[A-Za-z0-9+/=]+", current):
            break
    return out


def load_public_key(external: str):
    for blob in _decode_variants(external):
        if len(blob) < 64:
            continue
        try:
            key = serialization.load_der_public_key(blob)
        except Exception:
            continue
        if isinstance(key, rsa.RSAPublicKey):
            return key
    raise ValueError("external is not a Base64 X.509/SPKI RSA public key")


def encrypt_data(payload: bytes, public_key) -> dict:
    aes_key = AESGCM.generate_key(bit_length=256)
    iv = secrets.token_bytes(12)
    ciphertext = AESGCM(aes_key).encrypt(iv, payload, None)
    wrapped = public_key.encrypt(
        aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return {
        "encryptedKey": b64e(wrapped),
        "iv": b64e(iv),
        "encryptedPayload": b64e(ciphertext),
    }


def build_request(epic: str, tc: str, external: str) -> tuple[dict, dict]:
    inner = {
        "captchaData": "na",              # TElasticSearchRequest ctor default
        "captchaId": "na",                # TElasticSearchRequest ctor default
        "epicNumber": epic,
        "securityKey": gpk(epic, tc),
    }
    public_key = load_public_key(external)
    body = encrypt_data(json.dumps(inner).encode("utf-8"), public_key)
    return inner, body


# --------------------------------------------------------------------------
# modes
# --------------------------------------------------------------------------
def load_keys(path: Path) -> tuple[str, str]:
    if not path.exists():
        return "", ""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("tc", "") or "", data.get("external", "") or ""


def fetch(epic: str, keys_path: Path | None = None, timeout: int = 40) -> dict:
    """Fire the real captcha-less search and return the raw result.

    Shared by app_search's own CLI, epic_engine and the key oracle so there is
    exactly one implementation of the wire contract.
    """
    tc, external = load_keys(Path(keys_path or KEYS_PATH))
    if not tc or not external:
        raise ValueError(f"missing tc/external in {keys_path or KEYS_PATH}")
    epic = " ".join(epic.split()).upper()
    inner, body = build_request(epic, tc, external)
    headers = request_headers()
    req = urllib.request.Request(ENDPOINT, method="POST",
                                data=json.dumps(body).encode())
    req.add_header("User-Agent", MOBILE_UA)
    for k, v in headers.items():
        req.add_header(k, v)
    status, raw = 0, ""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, raw = resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001 - reported as a result, not raised
        return {"epic": epic, "status": 0, "raw": f"{type(e).__name__}: {e}",
                "inner": inner, "headers": headers, "hits": [], "sent_at": utc_now()}
    try:
        hits = json.loads(raw) if raw.strip().startswith("[") else []
    except json.JSONDecodeError:
        hits = []
    return {"epic": epic, "status": status, "raw": raw, "inner": inner,
            "headers": headers, "hits": hits, "sent_at": utc_now()}


def cmd_selftest(_args: argparse.Namespace) -> int:
    # 1) gPK port: round-trip + shape check
    tc_synth = b64e(secrets.token_bytes(32))
    token = gpk("SXQ2097129", tc_synth)
    plain = AESGCM(b64d(tc_synth)).decrypt(b"\x00" * 16, b64d(token), None).decode()
    assert re.fullmatch(
        r"SXQ2097129:\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}:\d{6}", plain
    ), f"unexpected gPK plaintext: {plain!r}"
    print("SELFTEST gPK OK:", plain)

    # 2) encryptData port: encrypt with a synthetic keypair, decrypt with its pair
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    external_synth = b64e(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    inner, body = build_request("SXQ2097129", tc_synth, external_synth)
    assert body["encryptedKey"] and body["encryptedPayload"] and body["iv"]

    recovered_aes = private_key.decrypt(
        b64d(body["encryptedKey"]),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    recovered = AESGCM(recovered_aes).decrypt(
        b64d(body["iv"]), b64d(body["encryptedPayload"]), None
    )
    recovered_inner = json.loads(recovered)
    assert recovered_inner == inner, "round-trip mismatch"
    print("SELFTEST encryptData OK:", json.dumps(
        {k: (v[:24] + "..." if k == "securityKey" else v)
         for k, v in recovered_inner.items()}))

    # 3) double-Base64 tolerance for extracted constants (native form: the
    #    getter returns b64(x) and the caller decodes x again)
    assert _try_decode_value(b64e(tc_synth.encode()), (16, 24, 32))
    raw_native_external = b64e(external_synth.encode())
    assert load_public_key(raw_native_external) is not None
    assert load_public_key(external_synth) is not None
    print("SELFTEST double-base64 tolerance OK (tc + external, both forms)")

    # 4) HTTP status expectation recorded (no network in selftest)
    print("SELFTEST_ALL_OK")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    result = None
    if not args.dry_run:
        try:
            result = fetch(args.epic, Path(args.keys))
        except ValueError as e:
            print(f"[app_search] {e}", file=sys.stderr)
            return 2

    tc, external = load_keys(Path(args.keys))
    epic = " ".join(args.epic.split()).upper()

    if args.dry_run and (not tc or not external):
        # Dry-run must work without real keys: substitute structurally valid
        # placeholders so the printed request shape is exactly what the app sends.
        if not tc:
            tc = b64e(b"\x00" * 32)
        if not external:
            private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            external = b64e(private.public_key().public_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            ))
        print("[app_search] dry-run with placeholder keys (real tc/external not loaded)",
              file=sys.stderr)

    if not tc or not external:
        print(
            "[app_search] missing tc / external in " + str(Path(args.keys)) + "\n"
            "  -> pull the ABI split and run extract_native_key.py, or hook the\n"
            "     getters with frida_grab_keys.js (see device_key_guide.md),\n"
            "     then put them in app_keys.json\n"
            "  -> run with --dry-run to see the exact request shape without keys",
            file=sys.stderr,
        )
        return 2

    inner, body = build_request(epic, tc, external)

    headers = request_headers()

    if args.dry_run:
        print(json.dumps({
            "endpoint": ENDPOINT,
            "headers": headers,
            "inner_plaintext": {**inner, "securityKey": "<redacted>"},
            "body": {k: v[:32] + "..." for k, v in body.items()},
        }, indent=2))
        return 0

    status, raw = result["status"], result["raw"]
    inner = result["inner"]          # exactly what was encrypted and sent
    if status == 0:
        print(f"[app_search] network error: {raw}", file=sys.stderr)
        return 1

    report = {
        "engine": "app_search",
        "version": "1.0.0",
        "epic": epic,
        "endpoint": ENDPOINT,
        "headers": headers,
        "inner_plaintext": {**inner, "securityKey": "<redacted>"},
        "sent_at": utc_now(),
        "http_status": status,
        "response": raw[:20000],
    }
    out_path = OUT_DIR / "app_search_result.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2)[:8000])
    print(f"\n[app_search] wrote {out_path}", file=sys.stderr)
    return 0 if status == 200 else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="APK-style EPIC search (no captcha)")
    sub = ap.add_subparsers(dest="cmd")

    p_search = sub.add_parser("search", help="run the search (default)")
    p_search.add_argument("--epic", required=True)
    p_search.add_argument("--keys", default=str(KEYS_PATH))
    p_search.add_argument("--dry-run", action="store_true")
    p_search.set_defaults(func=cmd_search)

    p_test = sub.add_parser("selftest", help="offline crypto round-trip")
    p_test.set_defaults(func=cmd_selftest)

    # Convenience: `python app_search.py --epic X` works too (argv is None when
    # invoked from the shell, so fall back to sys.argv[1:]).
    effective = list(sys.argv[1:] if argv is None else argv)
    if effective and effective[0].startswith("-") and \
            effective[0] not in ("-h", "--help"):
        effective = ["search"] + effective

    args = ap.parse_args(effective)
    if not getattr(args, "func", None):
        ap.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
