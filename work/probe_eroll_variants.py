"""Probe the gateway for sibling variants of `get-eroll-data-2003`.

The route name carries a year, which strongly implies the gateway serves other
roll vintages the same way. This sends the same anonymous request to each
candidate path and reports the status plus the gateway's own message, because a
status code alone is not enough: unknown paths and token-gated paths both answer
401 here.

Read-only: it only issues POSTs, one per candidate.

    python -X utf8 work/probe_eroll_variants.py
"""
from __future__ import annotations

import os
import sys

import certifi
import requests

API = "https://gateway-vha.eci.gov.in/api/v1/"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "applicationName": "VHA",
    "appName": "VHA",
    "channelidobo": "VHA",
    "platform-type": "ANDROIDMOB",
    "currentRole": "citizen",
    "User-Agent": "okhttp/4.9.2",
}
BODY = {"oldStateCd": "S01", "oldAcNo": "1", "oldPartNo": "1", "oldPartSerialNo": ""}

# The known-good route is included as a control: if it ever stops answering 200,
# the probe result means nothing.
CONTROL = "elastic-sir-citizen/get-eroll-data-2003"


def message(resp):
    try:
        data = resp.json()
    except ValueError:
        return (resp.text or "").strip()[:90]
    if isinstance(data, dict):
        for key in ("message", "error", "status", "statusCode"):
            if data.get(key) not in (None, ""):
                return "%s=%s" % (key, str(data[key])[:70])
    return str(data)[:90]


def probe(path, session):
    url = API + path
    try:
        r = session.post(url, json=BODY, timeout=25)
    except requests.RequestException as exc:
        return "ERR", str(exc)[:70]
    note = message(r)
    if r.status_code == 200:
        try:
            payload = r.json().get("payload")
        except ValueError:
            payload = None
        note = "payload=%s %s" % (
            len(payload) if isinstance(payload, list) else type(payload).__name__, note)
    return str(r.status_code), note


def main():
    session = requests.Session()
    session.verify = certifi.where()
    session.headers.update(HEADERS)

    control_status, control_note = probe(CONTROL, session)
    print("control  %-48s %s  %s" % (CONTROL, control_status, control_note))
    if control_status != "200":
        print("control route is not answering 200 - aborting, results below would be noise")
        return 1

    paths = []
    for year in range(1995, 2031):
        paths.append("elastic-sir-citizen/get-eroll-data-%d" % year)
    paths += ["elastic-sir-citizen/get-eroll-data"]
    for suffix in ("final", "latest", "current", "all", "list", "years", "vintages",
                   "2003-final", "2003-all", "2011-final", "2020-final"):
        paths.append("elastic-sir-citizen/get-eroll-data-%s" % suffix)
    # A deliberately invented path, to show what a non-existent route looks like.
    paths.append("elastic-sir-citizen/get-eroll-data-does-not-exist-xyz")

    print("\n=== get-eroll-data-<variant> ===")
    hits = []
    for path in paths:
        status, note = probe(path, session)
        variant = path.rsplit("-", 1)[-1]
        if status == "200" or (status != "404" and "does-not-exist" not in path
                               and variant != "xyz" and status != "401"):
            print("  %-48s %s  %s" % (path, status, note))
        elif variant == "xyz":
            print("  %-48s %s  %s   <- baseline for a non-existent route"
                  % (path, status, note))
        if status == "200":
            hits.append(path)

    print("\n=== other route families worth a look ===")
    for path in ("elastic-sir-citizen/get-eroll-data-2002",
                 "elastic-sir-citizen/get-eroll-data-2003-summary",
                 "elastic-sir-citizen/get-eroll-part-list",
                 "elastic-sir-citizen/get-part-list",
                 "elastic-sir/get-eroll-data-2003",
                 "sir-citizen/get-eroll-data-2003",
                 "elastic-sir-citizen/getElectorDetails",
                 "citizen/sir/get-eroll-data-2003",
                 "common/sir/get-eroll-data-2003"):
        status, note = probe(path, session)
        print("  %-48s %s  %s" % (path, status, note))

    print("\n=== API docs / service inventory ===")
    for path in ("v3/api-docs", "api-docs", "swagger-ui/index.html",
                 "actuator/mappings", "actuator", "common/states"):
        url = API + path
        try:
            r = session.get(url, timeout=20)
        except requests.RequestException as exc:
            print("  %-48s ERR %s" % (path, str(exc)[:60]))
            continue
        print("  %-48s %s  %s" % (path, r.status_code, message(r)))

    print("\nvintages that answered 200: %s" % (hits or "none beyond the control"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
