"""Probe the e-roll / part-elector routes on the live ECI gateways.

Goal: list every EPIC in a polling part. The current app declares
`elastic-sir-citizen/get-eroll-data-final` (POST, body SirSearchRequest2025
{stateCd, acNo, partNo, partSerialNo} or SirSearchRequest with old* extra) and
maps the answer to ElectorDataRequest$ElectorPayload which carries `epic`.

Probes: hosts x paths x auth modes, plus a bogus-route control so we can tell
"route unknown" (404) from "route known but needs auth" (401).

Usage: python work/eroll_probe.py [--json out.jsonl]
"""
import argparse
import json
import os
import ssl
import sys
import time

import certifi
import requests
from requests.adapters import HTTPAdapter

BASE_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "applicationName": "VHA",
    "appName": "VHA",
    "channelidobo": "VHA",
    "platform-type": "ANDROIDMOB",
    "currentRole": "citizen",
    "device-id": "3f2a8c1e-1b6d-4a52-9c1f-6b0d2e7a9c44",
    "User-Agent": "okhttp/4.9.2",
}

HOSTS = [
    "https://gateway-s2-blo.eci.gov.in/api/v1",
    "https://gateway-s1-blo.eci.gov.in/api/v1",
    "https://gateway-vha.eci.gov.in/api/v1",
]

PATHS = [
    ("eroll-final", "/elastic-sir-citizen/get-eroll-data-final",
     {"stateCd": "S01", "acNo": "1", "partNo": "2", "partSerialNo": "16"}),
    ("eroll-2003", "/elastic-sir-citizen/get-eroll-data-2003",
     {"stateCd": "S01", "acNo": "1", "partNo": "2", "partSerialNo": "16"}),
    ("eroll-final-2025-all", "/elastic-sir-citizen/get-eroll-data-final",
     {"stateCd": "S01", "acNo": "1", "partNo": "2", "partSerialNo": "16",
      "oldAcNo": "", "oldPartNo": "", "oldPartSerialNo": "", "oldStateCd": ""}),
    ("section-by-ac-part", "/citizen/sir/getSectionByAcAndPart",
     {"acNo": "1", "partNo": "2"}),
    ("sir-eroll-gateway-old", "/eroll/getDopStatus", None),
    ("get-eroll-elector-list", "/GetErollElectorList", None),
    ("bogus-control", "/definitely-not-a-route-zz9", None),
]

X_API_KEY = "wFd9S@ycS!tYA64x#Pl7*p5Uo"


def load_token():
    try:
        cfg = json.load(open("work/live_config.json", encoding="utf-8"))
    except Exception:
        return None
    tok = cfg.get("bearer") or cfg.get("access_token")
    if isinstance(tok, str) and tok.strip():
        return tok.strip()
    return None


def make_session():
    s = requests.Session()
    s.mount("https://", HTTPAdapter(max_retries=0))
    s.verify = certifi.where()
    return s


def one(sess, host, path, name, body, auth, extra_headers=None, method="POST"):
    url = host + path
    headers = dict(BASE_HEADERS)
    if extra_headers:
        headers.update(extra_headers)
    if auth:
        # auth is (kind, value)
        kind, val = auth
        if kind == "bearer":
            headers["Authorization"] = val if val.lower().startswith("bearer ") else "Bearer " + val
        elif kind == "apikey":
            headers["X-API-KEY"] = val
    t0 = time.time()
    try:
        if method == "POST":
            r = sess.post(url, headers=headers, json=body, timeout=25)
        else:
            r = sess.get(url, headers=headers, params=body, timeout=25)
        dt = int((time.time() - t0) * 1000)
        txt = r.text if r.text else ""
        rec = {
            "host": host, "path": path, "probe": name,
            "method": method, "auth": auth[0] if auth else "none",
            "status": r.status_code, "ms": dt,
            "ctype": r.headers.get("Content-Type", ""),
            "body": txt[:600],
        }
    except Exception as e:
        rec = {"host": host, "path": path, "probe": name, "auth": auth[0] if auth else "none",
               "error": "%s: %s" % (type(e).__name__, e)}
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="work/out/eroll_probe.jsonl")
    args = ap.parse_args()
    os.makedirs(os.path.dirname(args.json), exist_ok=True)

    token = load_token()
    print("token:", "yes (%d chars)" % len(token) if token else "no")

    sess = make_session()
    out = []
    # Phase 1: anonymous -- the make-or-break question.
    for host in HOSTS:
        for name, path, body in PATHS:
            method = "GET" if body is None else "POST"
            rec = one(sess, host, path, name, body, None, method=method)
            out.append(rec)
            print("%-46s %-26s anon -> %s %s" % (
                host.split("//")[1].split("/")[0], name,
                rec.get("status", rec.get("error")),
                rec.get("body", "")[:120].replace("\n", " ")))
            time.sleep(0.6)

    # Phase 2: X-API-KEY variants on the two BLO hosts for the legacy route.
    for host in HOSTS[:2]:
        for name, path, body in [("legacy-key", "/GetErollElectorList", None),
                                 ("legacy-sec", "/GetSectionList", None),
                                 ("bogus-key", "/definitely-not-a-route-zz9", None)]:
            rec = one(sess, host, path, name, body, ("apikey", X_API_KEY), method="GET")
            out.append(rec)
            print("%-46s %-26s apikey -> %s %s" % (
                host.split("//")[1].split("/")[0], name,
                rec.get("status", rec.get("error")),
                rec.get("body", "")[:120].replace("\n", " ")))
            time.sleep(0.6)

    with open(args.json, "w", encoding="utf-8") as fh:
        for rec in out:
            fh.write(json.dumps(rec) + "\n")
    print("wrote", args.json, "(%d probes)" % len(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
