"""Compare the part ("polling station") lists for one AC across every source.

The web UI at voters.eci.gov.in shows ~153 parts for S01 AC 1 while the old-roll
collector discovered 12, so this lines up each source and re-tests the old count
from scratch:

  1. voters.eci.gov.in  GET /citizen/sir/getPartByAc?Asmbly=<ac>   (the web app)
  2. vha gateway        GET /common/part/get/bystatecd/districtcd/acNumber
  3. the 2003 roll      POST /elastic-sir-citizen/get-eroll-data-2003, probed
                        exhaustively over part numbers, to settle how many old
                        parts actually exist

Read-only.

    python -X utf8 work/compare_parts.py --state S01 --ac 1
"""
from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor

import certifi
import requests

sys.path.insert(0, "work/old_eci")

WEB_API = "https://gateway-voters.eci.gov.in/api/v1/"

WEB_HEADERS = {
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

VHA_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "applicationName": "VHA",
    "appName": "VHA",
    "channelidobo": "VHA",
    "platform-type": "ANDROIDMOB",
    "currentRole": "citizen",
    "User-Agent": "okhttp/4.9.2",
}


def session(headers):
    s = requests.Session()
    s.verify = certifi.where()
    s.headers.update(headers)
    return s


def payload_of(resp):
    try:
        data = resp.json()
    except ValueError:
        return None
    if isinstance(data, dict):
        for key in ("payload", "data", "result"):
            if isinstance(data.get(key), list):
                return data[key]
        return data
    return data


def web_part_by_ac(state, ac):
    s = session(WEB_HEADERS)
    r = s.get(WEB_API + "citizen/sir/getPartByAc", params={"Asmbly": str(ac)},
              headers={"state": state}, timeout=30)
    print("  getPartByAc      status %s" % r.status_code)
    rows = payload_of(r) or []
    if rows and isinstance(rows[0], dict):
        print("  keys of a row: %s" % sorted(rows[0].keys()))
        print("  first row: %s" % {k: rows[0][k] for k in list(rows[0])[:10]})
    return rows


def web_acs(state):
    s = session(WEB_HEADERS)
    r = s.get(WEB_API + "citizen/sir/getAsmbly", headers={"state": state}, timeout=30)
    rows = payload_of(r) or []
    print("  getAsmbly        status %s -> %s rows" % (r.status_code, len(rows)))
    return rows


def vha_parts(state, ac):
    s = session(VHA_HEADERS)
    r = s.get("https://gateway-vha.eci.gov.in/api/v1/"
              "common/part/get/bystatecd/districtcd/acNumber",
              params={"stateCd": state, "acNumber": str(ac)},
              headers={"state": state}, timeout=40)
    rows = payload_of(r) or []
    print("  vha bystatecd    status %s -> %s rows" % (r.status_code, len(rows)))
    return rows


def old_parts_exist(state, ac, hi=220, workers=8):
    """Which old-roll part numbers actually return data? (exhaustive probe)"""
    s = session(VHA_HEADERS)
    url = ("https://gateway-vha.eci.gov.in/api/v1/"
           "elastic-sir-citizen/get-eroll-data-2003")

    def probe(n):
        body = {"oldStateCd": state, "oldAcNo": str(ac), "oldPartNo": str(n),
                "oldPartSerialNo": ""}
        try:
            r = s.post(url, json=body, timeout=30)
        except requests.RequestException:
            return n, -1, None
        if r.status_code != 200:
            return n, r.status_code, None
        try:
            pl = r.json().get("payload") or []
        except ValueError:
            return n, r.status_code, None
        name = next((x.get("oldPartName") for x in pl if x.get("oldPartName")), None)
        return n, r.status_code, name

    found = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for n, status, name in pool.map(probe, range(1, hi + 1)):
            if status == 200:
                found[n] = name
    return found


def nums(rows, keys):
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for k in keys:
            if row.get(k) is not None:
                try:
                    out.append(int(row[k]))
                except (TypeError, ValueError):
                    pass
                break
    return sorted(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="S01")
    ap.add_argument("--ac", type=int, default=1)
    ap.add_argument("--ac2", type=int, default=2)
    ap.add_argument("--hi", type=int, default=220, help="highest old part no to probe")
    args = ap.parse_args()

    for ac in (args.ac, args.ac2):
        print("\n================ %s AC %s ================" % (args.state, ac))

        print("\n[voters.eci.gov.in]")
        web = web_part_by_ac(args.state, ac)
        web_nums = nums(web, ("partNumber", "partNo", "part_number", "partId"))
        if web_nums:
            print("  -> %d parts, min %s max %s, gaps: %s"
                  % (len(web_nums), web_nums[0], web_nums[-1],
                     "none" if len(web_nums) == web_nums[-1] - web_nums[0] + 1 else "yes"))

        print("\n[gateway-vha]")
        vha = vha_parts(args.state, ac)
        vha_nums = nums(vha, ("partNumber", "partNo", "part_number"))
        if vha_nums:
            print("  -> %d parts, min %s max %s" % (len(vha_nums), vha_nums[0], vha_nums[-1]))

        print("\n[2003 roll, probing part numbers 1..%d]" % args.hi)
        old = old_parts_exist(args.state, ac, hi=args.hi)
        print("  -> %d old parts exist: %s" % (len(old), sorted(old)))
        for n in sorted(old):
            print("      old part %-4s %s" % (n, old[n]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
