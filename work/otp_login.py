#!/usr/bin/env python3
"""
otp_login.py - the app's own citizen login (mobile + OTP), in one command.

This is the missing piece for every Bearer-gated route: the gateway rejects
portal tokens, so the only session it accepts is the one this flow mints.

Reconstructed from the smali (TRestClient + AuthFlowRequest/AuthFlowResponse):

  POST .../api/v1/authn-voter/otp-flow-send    body: ChecksumRequest
  POST .../api/v1/authn-voter/otp-flow-verify  body: ChecksumRequest
  POST .../api/v1/authn-voter/refresh          Authorization/atkn_bnd + AuthFlowRequest

Both OTP calls wrap the *same* AES+RSA envelope as the EPIC search, so they use
exactly the crypto already in app_search.py; the inner JSON is an
AuthFlowRequest (applicationName/appName "VHA", roleCode "*", empty strings by
default, plus mobileNo and otp). The reply is a plain AuthFlowResponse with
access_token, refresh_token, atkn_bnd and rtkn_bnd - the three headers
AuthenticatorInterceptor replays after any 401.

Usage:
  python work/otp_login.py --mobile 9876543210          # sends the OTP, prompts for it
  python work/otp_login.py --mobile 9876543210 --otp 123456
  python work/otp_login.py --mobile 9876543210 --probe  # then hit the gated routes
  python work/otp_login.py --refresh                    # mint a new access token
  python work/otp_login.py --mobile 0000000000 --check  # contract check, no SMS expected

The inner JSON is AuthFlowRequest exactly as NvspLogin builds it: send sets only
`username` (the phone box), verify sets `username` + `otp`.  Nothing else is
populated, and `refreshToken` is null so Gson omits it - do not "improve" this
by filling in the other fields, the gateway validates them.

Tokens go to the git-ignored work/secrets.json (work/live_config.json is
committed, so credentials must never be written there) and are never printed in
full.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import app_search as A  # noqa: E402
import secrets_store  # noqa: E402

BASE = Path(__file__).resolve().parent
API = "https://gateway-vha.eci.gov.in/api/v1/"
CONFIG = BASE / "live_config.json"          # tracked: non-secret knobs only
SECRETS = BASE / "secrets.json"             # git-ignored: live session tokens

SEND = "authn-voter/otp-flow-send"
VERIFY = "authn-voter/otp-flow-verify"
REFRESH = "authn-voter/refresh"

LOGIN_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "channelidobo": "VHA",
    "applicationName": "VHA",
    "appName": "VHA",
    "platform-type": "ANDROIDMOB",
}

# AuthFlowRequest's ctor defaults, in field-declaration order, exactly as
# `new GsonBuilder().create().toJson(req)` prints them.  Only `refreshToken` is
# left uninitialised (null) so default Gson drops it from the JSON.
FLOW_DEFAULTS = {
    "applicationName": "VHA",
    "appName": "VHA",
    "epicRefNo": "",
    "mobileNo": "",
    "otp": "",
    "password": "",
    "roleCode": "*",
    "stateCd": "",
    "username": "",
}


def flow_inner(**over) -> dict:
    inner = dict(FLOW_DEFAULTS)
    inner.update(over)
    return inner

SECRET_KEYS = ("access_token", "refresh_token", "atkn_bnd", "rtkn_bnd")


def load_cfg() -> dict:
    """Public config overlaid with the git-ignored secrets."""
    return secrets_store.read_config()


def save_cfg(cfg: dict) -> None:
    """Route each key to the right file: secrets stay out of the tracked one."""
    secrets_store.write_secrets(cfg)
    public = {k: v for k, v in cfg.items() if k not in secrets_store.SECRET_KEYS}
    secrets_store.write_public({k: v for k, v in public.items()
                                if k in ("api_key", "auth_proxy", "verbose")})


def login_headers() -> dict:
    """Every call goes through the app's DecryptionInterceptor, which stamps the
    same `device-id: AppController.DI` on all of them.  Persist ours so the login
    and the later gated calls present one stable device instead of a new UUID.
    """
    cfg = load_cfg()
    dev = cfg.get("device_id")
    if not dev:
        dev = str(uuid.uuid4())
        cfg["device_id"] = dev
        save_cfg(cfg)
    return {**LOGIN_HEADERS, "device-id": dev}


def describe(value: str) -> str:
    """Length + byte shape, without leaking the secret."""
    raw = value.encode("latin-1", "replace")
    non_ascii = sum(1 for b in raw if b > 0x7F)
    return (f"<{len(value)} chars, {len(raw)} bytes, {non_ascii} non-ascii, "
            f"{raw[:8].hex()}...>")


def redact(obj):
    """Never print whole tokens."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in SECRET_KEYS and isinstance(v, str) and len(v) > 16:
                out[k] = describe(v)
            else:
                out[k] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


def call(path: str, inner: dict, tc: str, external: str,
         extra_headers: dict | None = None) -> tuple[int, bytes]:
    """Returns RAW bytes on purpose - see as_json()."""
    body = A.encrypt_data(json.dumps(inner).encode(), A.load_public_key(external))
    req = urllib.request.Request(API + path, method="POST",
                                 data=json.dumps(body).encode())
    req.add_header("User-Agent", A.MOBILE_UA)
    for k, v in {**login_headers(), **(extra_headers or {})}.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}".encode()


def as_json(raw: bytes | str) -> dict:
    """Decode the body losslessly with Latin-1.

    Latin-1 maps bytes 1:1 onto codepoints, so nothing is ever replaced and any
    token can be replayed byte-for-byte (http.client encodes outgoing header
    values back to Latin-1).  Worth knowing: capturing the raw body proved this
    is defensive only - atkn_bnd/rtkn_bnd arrive as pure ASCII text like
    "77+977+9Xhjv...", so the earlier `utf-8, replace` decode lost nothing.  The
    `77+9` prefix means they base64-decode to U+FFFD: the gateway itself
    mangles these binding tokens, deterministically, and replays them as-is.
    """
    text = raw.decode("latin-1") if isinstance(raw, (bytes, bytearray)) else raw
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {"payload": data}


def harvest(response: dict) -> dict:
    """Pull tokens out of an AuthFlowResponse, wherever they sit."""
    found: dict[str, str] = {}

    def walk(node) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k in SECRET_KEYS and isinstance(v, str) and v:
                    found.setdefault(k, v)
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(response)
    return found


def persist(found: dict, mobile: str) -> list[str]:
    cfg = load_cfg()
    saved = []
    if found.get("access_token"):
        cfg["bearer"] = "Bearer " + found["access_token"]
        saved.append("bearer")
    for key in ("refresh_token", "atkn_bnd", "rtkn_bnd"):
        if found.get(key):
            cfg[key] = found[key]
            saved.append(key)
    cfg["last_login_mobile"] = mobile
    save_cfg(cfg)
    return saved


def brief(mobile: str) -> str:
    return f"{mobile[:2]}****{mobile[-2:]}"


def do_send(mobile: str, tc: str, external: str) -> tuple[int, dict]:
    # NvspLogin.sendOtpFlow(): only `username` is assigned; everything else keeps
    # the ctor default.  The gateway validates it as "username field cannot be
    # blank", which is how we found this shape.
    inner = flow_inner(username=mobile)
    print(f"[login] POST {SEND}  username={brief(mobile)}")
    print(f"[login] inner: {json.dumps(inner)}")
    status, raw = call(SEND, inner, tc, external)
    response = as_json(raw)
    print(f"[login] http {status}: {json.dumps(redact(response))[:600]}")
    return status, response


def do_verify(mobile: str, otp: str, tc: str, external: str) -> tuple[int, dict]:
    # NvspLogin.verifyOtpFlow(): username + otp.
    inner = flow_inner(username=mobile, otp=otp)
    print(f"[login] POST {VERIFY}  username={brief(mobile)} otp=**{otp[-1:]}")
    status, raw = call(VERIFY, inner, tc, external)
    out = BASE / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / "last_auth_response.bin").write_bytes(raw if isinstance(raw, bytes) else raw.encode())
    response = as_json(raw)
    print(f"[login] http {status}: {json.dumps(redact(response))[:700]}")
    print(f"[login] raw body saved -> work/out/last_auth_response.bin "
          f"({len(raw)} bytes)")
    return status, response


def do_refresh(cfg: dict, tc: str, external: str) -> tuple[int, dict]:
    token = (cfg.get("bearer") or "").replace("Bearer ", "").strip()
    refresh_token = cfg.get("refresh_token") or ""
    if not token or not refresh_token:
        print("[login] --refresh needs bearer + refresh_token in live_config.json",
              file=sys.stderr)
        return 2, {}
    inner = flow_inner(refreshToken=refresh_token)
    headers = {"Authorization": "Bearer " + token}
    if cfg.get("atkn_bnd"):
        headers["atkn_bnd"] = cfg["atkn_bnd"]
    status, raw = call(REFRESH, inner, tc, external, headers)
    response = as_json(raw)
    print(f"[login] http {status}: {json.dumps(redact(response))[:700]}")
    return status, response


def quick_probe(epic: str) -> None:
    """Fire the gated routes *immediately* after minting.

    Timing matters: a fresh Keycloak session is what the gateway wants, but a
    token that is minutes old can already be refused, so the only way to tell
    "stale session" from "wrong header shape" is to probe in the same second as
    the verify.

    The shapes below are the app's, read off TRestClient: every gated method
    takes `Authorization` (and `getAccess_token()` prepends "Bearer "), plus
    `atkn_bnd`/`rtkn_bnd`, plus per-route `state` / `currentRole: citizen`.
    """
    import urllib.request

    cfg = load_cfg()

    def hit(label: str, path: str, headers: dict, body: dict | None = None,
            method: str = "POST") -> None:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(API + path, method=method, data=data)
        for k, v in {**login_headers(), **headers}.items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                st, blob = r.status, r.read()
        except urllib.error.HTTPError as e:
            st, blob = e.code, e.read()
        except Exception as e:  # noqa: BLE001
            st, blob = 0, f"{type(e).__name__}: {e}".encode()
        open_flag = "  <-- OPEN" if st == 200 else ""
        print(f"  {label:38s} -> {st}  {blob[:140].decode('latin-1')!r}{open_flag}")

    token = (cfg.get("bearer") or "").replace("Bearer ", "").strip()
    atkn, rtkn = cfg.get("atkn_bnd", ""), cfg.get("rtkn_bnd", "")
    app_all = {"Authorization": "Bearer " + token, "atkn_bnd": atkn, "rtkn_bnd": rtkn}
    print("[login] bound tokens:", describe(atkn), describe(rtkn))
    print("[login] immediate gated-route probe (seconds after minting):")
    hit("eepic: app-exact", "eepic/GetElectorDetailForEEPIC", app_all,
        {"epicNo": epic})
    hit("eepic: Bearer only", "eepic/GetElectorDetailForEEPIC",
        {"Authorization": "Bearer " + token}, {"epicNo": epic})
    hit("document/getFile: app-exact", "document/getFile",
        {**app_all, "state": "S01", "currentRole": "citizen",
         "bucketName": "S01",
         "fileName": "S01/Part/1/2/cadview_381b9e68-8c63-4030-bcaf-ef71d658e78e_2.jpg"},
        None, "GET")
    hit("vha/getPollingOfficials", f"vha/getPollingOfficials?epicNo={epic}",
        app_all, None, "GET")
    hit("form6b/checkEpicHasAdhar",
        f"form6b/get/checkEpicHasAdhar/{epic}",
        {**app_all, "currentRole": "citizen", "state": "S01"}, None, "GET")
    hit("sir/getDetailsByEpicNo", f"citizen/sir/getDetailsByEpicNo?epic={epic}",
        {**app_all, "currentRole": "citizen", "state": "S01"}, None, "GET")


def main() -> int:
    ap = argparse.ArgumentParser(description="App citizen login (mobile + OTP)")
    ap.add_argument("--mobile", help="10-digit mobile number")
    ap.add_argument("--otp", help="OTP (skips the prompt)")
    ap.add_argument("--check", action="store_true",
                    help="send with the given number and stop (contract check)")
    ap.add_argument("--resend", action="store_true",
                    help="send a fresh OTP even when --otp is given (throttled!)")
    ap.add_argument("--no-send", action="store_true",
                    help="verify an OTP that was already sent (never re-triggers an SMS)")
    ap.add_argument("--refresh", action="store_true", help="mint a new access token")
    ap.add_argument("--probe", action="store_true",
                    help="probe the gated routes right after minting (fast)")
    ap.add_argument("--probe-keys", action="store_true",
                    help="also run the slow token_probe.py X-API-KEY sweep")
    ap.add_argument("--epic", default="TBG0342345", help="EPIC for --probe")
    ap.add_argument("--keys", default=str(BASE / "app_keys.json"))
    args = ap.parse_args()

    tc, external = A.load_keys(Path(args.keys))
    if not tc or not external:
        print("[login] missing tc/external in app_keys.json", file=sys.stderr)
        return 2
    cfg = load_cfg()

    if args.refresh:
        status, response = do_refresh(cfg, tc, external)
        found = harvest(response)
        if found:
            print("[login] saved:", ", ".join(persist(found,
                                                      cfg.get("last_login_mobile", ""))))
        return 0 if status == 200 else 1

    mobile = (args.mobile or "").strip() or input("mobile number: ").strip()
    digits = "".join(ch for ch in mobile if ch.isdigit())[-10:]
    if len(digits) != 10:
        print(f"[login] need a 10-digit mobile number, got {mobile!r}", file=sys.stderr)
        return 2

    # Sending again inside the resend window answers 401 "Please wait for N
    # seconds before resending new otp!", which aborts the run and throws away a
    # perfectly good code.  So an explicit --otp verifies straight away.
    if args.otp and not args.resend and not args.check:
        print("[login] --otp given: verifying the existing code (no new SMS)")
        status, response = do_verify(digits, args.otp.strip(), tc, external)
        found = harvest(response)
        if found:
            print("[login] saved:", ", ".join(persist(found, digits)))
            if args.probe:
                quick_probe(args.epic.strip().upper())
            if args.probe_keys:
                import subprocess
                subprocess.run([sys.executable, str(BASE / "token_probe.py"),
                                "--epic", args.epic, "--sweep-keys"], check=False)
        else:
            print("[login] no tokens in the response", file=sys.stderr)
        return 0 if status == 200 else 1

    if not args.no_send:
        status, _response = do_send(digits, tc, external)
        if status != 200:
            print("[login] OTP send did not return 200 - stopping (nothing more to try)",
                  file=sys.stderr)
            return 1
    if args.check:
        print("[login] contract check done; OTP request accepted by the gateway")
        return 0

    otp = (args.otp or "").strip() or input("otp: ").strip()
    if not otp:
        print("[login] no OTP given", file=sys.stderr)
        return 2
    status, response = do_verify(digits, otp, tc, external)
    found = harvest(response)
    if found:
        print("[login] saved:", ", ".join(persist(found, digits)))
        if args.probe:
            quick_probe(args.epic.strip().upper())
        if args.probe_keys:
            import subprocess
            subprocess.run([sys.executable, str(BASE / "token_probe.py"),
                            "--epic", args.epic, "--sweep-keys"], check=False)
    else:
        print("[login] no tokens in the response", file=sys.stderr)
    return 0 if status == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
