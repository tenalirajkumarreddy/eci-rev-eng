"""Test `elastic-sir-citizen/get-eroll-data-final` with the saved session token.

Reads work/secrets.json (git-ignored); never prints token values, only claims.

Usage: python work/final_probe.py [--state S01 --ac 1 --part 2 --serial 16]
"""
import argparse
import base64
import json
import time

import certifi
import requests

BASE = "https://gateway-vha.eci.gov.in/api/v1"
H = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "applicationName": "VHA",
    "appName": "VHA",
    "channelidobo": "VHA",
    "platform-type": "ANDROIDMOB",
    "currentRole": "citizen",
    "User-Agent": "okhttp/4.9.2",
}


def jwt_claims(tok):
    raw = tok.split()[-1]
    parts = raw.split(".")
    if len(parts) < 2:
        return {}
    pad = "=" * (-len(parts[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(parts[1] + pad))
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="S01")
    ap.add_argument("--ac", default="1")
    ap.add_argument("--part", default="2")
    ap.add_argument("--serial", default="16")
    args = ap.parse_args()

    sec = json.load(open("work/secrets.json", encoding="utf-8"))
    bearer = sec.get("bearer") or ""
    if bearer and not bearer.lower().startswith("bearer "):
        bearer = "Bearer " + bearer
    claims = jwt_claims(bearer) if bearer else {}
    exp = claims.get("exp")
    now = time.time()
    print("token: %s  exp=%s  valid=%s  roles=%s" % (
        "present" if bearer else "MISSING",
        time.strftime("%Y-%m-%d %H:%M", time.localtime(exp)) if exp else "?",
        (exp > now) if exp else "?",
        claims.get("realm_access", {}).get("roles") or claims.get("role")))

    h = dict(H)
    if bearer:
        h["Authorization"] = bearer
        for k in ("atkn_bnd", "rtkn_bnd"):
            if sec.get(k):
                h[k] = sec[k]

    s = requests.Session()
    s.verify = certifi.where()
    bodies = [
        ("new-empty", {"stateCd": args.state, "acNo": args.ac,
                       "partNo": args.part, "partSerialNo": ""}),
        ("new-serial", {"stateCd": args.state, "acNo": args.ac,
                        "partNo": args.part, "partSerialNo": args.serial}),
        ("old-empty", {"oldStateCd": args.state, "oldAcNo": args.ac,
                       "oldPartNo": args.part, "oldPartSerialNo": ""}),
        ("all-empty", {"stateCd": args.state, "acNo": args.ac, "partNo": args.part,
                       "partSerialNo": "", "oldStateCd": args.state,
                       "oldAcNo": args.ac, "oldPartNo": args.part,
                       "oldPartSerialNo": ""}),
    ]
    for tag, b in bodies:
        r = s.post(BASE + "/elastic-sir-citizen/get-eroll-data-final",
                   headers=h, json=b, timeout=30)
        t = (r.text or "")
        n = "?"
        try:
            j = json.loads(t)
            pl = j.get("payload") or []
            n = len(pl)
            first = pl[0] if pl else {}
            sample = {k: first.get(k) for k in ("epic", "epicNumber", "fullName",
                                                "firstName", "serialNo", "partNo",
                                                "acNo") if k in first}
        except ValueError:
            sample = t[:160]
        print("%-10s -> %s n=%s %s" % (tag, r.status_code, n,
                                       json.dumps(sample, ensure_ascii=False)[:220]))
        time.sleep(0.6)


if __name__ == "__main__":
    main()
