"""Probe the voters.eci.gov.in web gateway for roll/elector routes.

Our collection still runs on the VHA mobile gateway (`gateway-vha`); the only
call on the web gateway (`gateway-voters`) is `getPartByAc`. Since the web app
clearly serves SIR data, this checks whether the same host exposes an
elector-level or current-roll route.

Status codes are read with controls, because a gated prefix makes a 401
meaningless (see extra_endpoints.md 7c):

    404 -> the path does not exist
    401 -> the prefix is auth-gated; proves nothing about the route
    405 -> the route exists, wrong method
    400/500 -> the route exists, the input is wrong
    200 -> exists and reachable

Read-only.

    python -X utf8 work/probe_voters_routes.py
"""
from __future__ import annotations

import sys

import certifi
import requests

API = "https://gateway-voters.eci.gov.in/api/v1/"
HEADERS = {
    "Accept": "*/*",
    "applicationname": "VSP",
    "channelidobo": "VSP",
    "currentrole": "citizen",
    "platform-type": "ECIWEB",
    "Origin": "https://voters.eci.gov.in",
    "Referer": "https://voters.eci.gov.in/",
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"),
}
STATE = "S01"

# Known-good routes, used as a liveness control.
CONTROLS = [
    ("citizen/sir/getPartByAc", {"Asmbly": "1"}),
    ("citizen/sir/getAsmbly", {}),
]

# A path that certainly does not exist, to learn the failure mode.
BASELINE = [
    "citizen/sir/definitely-not-real-xyz",
    "definitely-not-real-xyz",
]

# Candidate elector / roll routes. `get-eroll-data-2003` is included to test
# whether the new host mirrors the VHA route.
CANDIDATES = [
    # elector listings by part / ac
    "citizen/sir/getElectorList", "citizen/sir/getElectors",
    "citizen/sir/getElectorByPart", "citizen/sir/getPartElectors",
    "citizen/sir/getPartData", "citizen/sir/getElectorData",
    "citizen/sir/getVoterList", "citizen/sir/getEroll",
    "citizen/sir/getErollData", "citizen/sir/getSIRData",
    "citizen/sir/getElectoralRoll", "citizen/sir/downloadElectoralRoll",
    "citizen/sir/getPartByAc",       # control, repeated for shape
    "citizen/sir/getPartList", "citizen/sir/getDistrictByState",
    # mirrors of the VHA route
    "citizen/sir/get-eroll-data-2003", "citizen/sir/get-eroll-data",
    "elastic-sir-citizen/get-eroll-data-2003",
    "elastic/search-by-epic-from-national-display-v1",
    # search / lookup style
    "citizen/sir/searchByEpic", "citizen/sir/getEpicDetails",
    "citizen/sir/searchElector", "citizen/sir/getElectorByEpic",
]


def call(session, path, params, method="GET"):
    url = API + path
    try:
        r = session.request(method, url, params=params or None,
                            headers={"state": STATE}, timeout=25)
    except requests.RequestException as exc:
        return "ERR", str(exc)[:60]
    note = ""
    if r.status_code == 200:
        try:
            data = r.json()
        except ValueError:
            note = (r.text or "")[:70]
        else:
            if isinstance(data, list):
                note = "list=%d" % len(data)
                if data and isinstance(data[0], dict):
                    note += " keys=%s" % sorted(data[0])[:8]
            elif isinstance(data, dict):
                for key in ("payload", "data", "result"):
                    if isinstance(data.get(key), list):
                        note = "payload=%d" % len(data[key])
                        if data[key] and isinstance(data[key][0], dict):
                            note += " keys=%s" % sorted(data[key][0])[:8]
                        break
                else:
                    note = "dict keys=%s" % list(data)[:8]
    else:
        note = (r.text or "").strip()[:70]
    return str(r.status_code), note


def main():
    session = requests.Session()
    session.verify = certifi.where()
    session.headers.update(HEADERS)

    print("=== controls (must be 200, else nothing below means anything) ===")
    ok = True
    for path, params in CONTROLS:
        status, note = call(session, path, params)
        print("  %-46s %s  %s" % (path, status, note))
        ok = ok and status == "200"
    if not ok:
        print("controls failed - aborting")
        return 1

    print("\n=== failure-mode baseline (does this host 404 honestly?) ===")
    for path in BASELINE:
        print("  %-46s %s  %s" % ((path,) + call(session, path, {})))

    print("\n=== candidate routes ===")
    hits = []
    for path in CANDIDATES:
        status, note = call(session, path, {"Asmbly": "1"})
        print("  %-46s %s  %s" % (path, status, note))
        if status == "200":
            hits.append(path)

    print("\nreachable: %s" % (hits or "none beyond the controls"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
