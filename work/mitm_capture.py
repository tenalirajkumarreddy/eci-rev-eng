#!/usr/bin/env python3
"""
mitm_capture.py - record the ECI app's real HTTP traffic through a local proxy.

Why this works without installing a CA cert: the ECI app builds its OkHttp
client from TLSSocketFactory(1) with a trust-all X509TrustManager and a
permissive HostnameVerifier, so it accepts any certificate the proxy presents.

Setup (all reversible):
    python work/mitm_capture.py                 # listens on 127.0.0.1:8080
    adb reverse tcp:8080 tcp:8080               # phone's localhost:8080 -> host
    adb shell settings put global http_proxy 127.0.0.1:8080
    ... run the search in the app ...
    adb shell settings put global http_proxy :0 # undo

Flows land in work/out/app_capture.jsonl (one JSON object per line).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

from mitmproxy import options  # type: ignore
from mitmproxy.tools.dump import DumpMaster  # type: ignore

OUT = pathlib.Path(__file__).resolve().parent / "out" / "app_capture.jsonl"
HOST_FILTERS = ("eci.gov.in", "electoralsearch")


def record(obj: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")


def interesting(url: str) -> bool:
    return any(h in url for h in HOST_FILTERS)


class Recorder:
    def request(self, flow) -> None:
        r = flow.request
        if not interesting(r.pretty_url):
            return
        rec = {
            "kind": "request",
            "method": r.method,
            "url": r.pretty_url,
            "http_version": r.http_version,
            "headers": dict(r.headers),
            "body": r.get_text(strict=False),
        }
        record(rec)
        print(f"[capture] --> {r.method} {r.pretty_url}", flush=True)

    def response(self, flow) -> None:
        r = flow.response
        if r is None or not interesting(flow.request.pretty_url):
            return
        rec = {
            "kind": "response",
            "url": flow.request.pretty_url,
            "status": r.status_code,
            "headers": dict(r.headers),
            "body": r.get_text(strict=False),
        }
        record(rec)
        print(f"[capture] <-- {r.status_code} {flow.request.pretty_url}", flush=True)


async def main(port: int) -> None:
    opts = options.Options(listen_host="127.0.0.1", listen_port=port)
    master = DumpMaster(opts, with_termlog=False, with_dumper=False)
    master.addons.add(Recorder())
    print(f"[capture] listening on 127.0.0.1:{port} -> {OUT}", flush=True)
    await master.run()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Record ECI app traffic to JSONL")
    ap.add_argument("--port", type=int, default=8899)
    args = ap.parse_args()
    try:
        asyncio.run(main(args.port))
    except KeyboardInterrupt:
        sys.exit(0)
