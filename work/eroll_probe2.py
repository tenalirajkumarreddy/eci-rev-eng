"""Probe #2: body-shape oracle for elastic-sir-citizen/get-eroll-data-2003 and
friends on gateway-vha, plus the anonymous serial lookup routes.

Usage: python work/eroll_probe2.py
"""
import json
import time

import certifi
import requests

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

BASE = "https://gateway-vha.eci.gov.in/api/v1"

VARIANTS = [
    ("old-4", {"oldStateCd": "S01", "oldAcNo": "1", "oldPartNo": "2",
               "oldPartSerialNo": "16"}),
    ("all-8", {"stateCd": "S01", "acNo": "1", "partNo": "2", "partSerialNo": "16",
               "oldStateCd": "S01", "oldAcNo": "1", "oldPartNo": "2",
               "oldPartSerialNo": "16"}),
    ("basic-4", {"stateCd": "S01", "acNo": "1", "partNo": "2",
                 "partSerialNo": "16"}),
    ("basic-ints", {"stateCd": "S01", "acNo": 1, "partNo": 2,
                    "partSerialNo": 16}),
    ("old-ints", {"oldStateCd": "S01", "oldAcNo": 1, "oldPartNo": 2,
                  "oldPartSerialNo": 16}),
    ("empty", {}),
]

ROUTES = [
    ("eroll-2003", "POST", "/elastic-sir-citizen/get-eroll-data-2003"),
    ("eroll-final", "POST", "/elastic-sir-citizen/get-eroll-data-final"),
    ("old-details-2003", "POST", "/elastic-sir-citizen/search-by-old-details-2003"),
    ("getDetailsByEroll", "GET", "/citizen/sir/getDetailsByEroll"),
]

s = requests.Session()
s.verify = certifi.where()


def show(tag, r):
    body = (r.text or "").replace("\n", " ")[:300]
    print("%-22s %-18s -> %s  %s" % (tag[0], tag[1], r.status_code, body))


for rname, method, path in ROUTES:
    if method == "GET":
        q = {"acNo": "1", "partNo": "2", "serialNo": "16"}
        for hdr_extra in ({}, {"state": "S01"}):
            h = dict(H)
            h.update(hdr_extra)
            r = s.get(BASE + path, headers=h, params=q, timeout=25)
            show((rname, "get" + ("+state" if hdr_extra else "")), r)
            time.sleep(0.7)
        continue
    for vname, body in VARIANTS:
        for hdr_extra in ({}, {"state": "S01"}):
            h = dict(H)
            h.update(hdr_extra)
            r = s.post(BASE + path, headers=h, json=body, timeout=25)
            show((rname + "/" + vname, "post" + ("+state" if hdr_extra else "")), r)
            time.sleep(0.7)
