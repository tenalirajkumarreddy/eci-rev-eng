#!/usr/bin/env python3
"""
eci_eroll.py - pull official per-part electoral-roll PDFs from the ECI portal.

`https://voters.eci.gov.in/download-eroll?stateCode=XX` is the Citizen Service
Portal's "Electoral Roll" download page.  It is the only public source of a
*complete* part roll (every EPIC in the part, with serial numbers), because none
of the app/gateway routes list electors by part.

Reverse-engineered contract (2026-10-06, portal build main.39d30fac.js + chunk
6838):

  base    gateway-voters.eci.gov.in     (portal API)
  ext     gateway-vpd.eci.gov.in        (file bytes; no captcha once a fileId exists)

  1. captcha   GET  /api/v1/captcha-service/getCaptcha/EROLL
               -> {id, captcha(b64 jpg)}                     # HUMAN reads this
  2. generate  POST /api/v1/printing-publish/generate-published-pdfs
               body {"stateCd","acNumber","partNumberList":[...],"districtCd",
                     "captcha","captchaId","langCd","publishedRollId","misKey"}
               headers applicationName/channelidobo/PLATFORM-TYPE
               -> {statusCode, message, payload:[fileId, ...]}   # ONE captcha
                                                                 # = many parts
  3. file      GET  /api/v1/ext-printing-publish/get-published-file?fileId=...
               -> {payload: "<base64 pdf>", refId: "<name>.pdf"}

The captcha is a deliberate human check: this tool never solves it.  Fetch the
image with `captcha`, a human reads it, then pass the text (and id) to
`generate`.  One solved captcha covers every part in `--parts`.

    python work/eci_eroll.py captcha
    python work/eci_eroll.py generate --state S01 --ac 1 --district S0101 \
        --lang ENGLISH --parts 2 --captcha-id <id> --captcha <text> --save
    python work/eci_eroll.py file --file-id <id> --out work/out/roll_pdfs/p2.pdf

Run `generate --save` and the PDFs land in work/out/roll_pdfs/ directly.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE_PORTAL = "https://gateway-voters.eci.gov.in/api/v1/"
BASE_FILES = "https://gateway-vpd.eci.gov.in/api/v1/ext-printing-publish/"
CAPTCHA_URL = BASE_PORTAL + "captcha-service/getCaptcha/EROLL"
GENERATE_URL = BASE_PORTAL + "printing-publish/generate-published-pdfs"
FILE_URL = BASE_FILES + "get-published-file"

UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36")
HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "applicationName": "VHA",
    "CurrentRole": "citizen",
    "channelidobo": "VHA",
    "PLATFORM-TYPE": "ANDROIDMOB",
}
MIS_KEY = "EROLLA32DVI09AJH"        # hardcoded in the portal bundle
DEFAULT_PUBLISHED_ROLL_ID = ""      # filled from --roll-id (SIR draft id)

BASE_DIR = Path(__file__).resolve().parent
OUT_DIR = BASE_DIR / "out"
ROLL_DIR = OUT_DIR / "roll_pdfs"


def call(url: str, method: str = "GET", body: dict | None = None,
         timeout: int = 60) -> tuple[int, str, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, method=method, data=data)
    req.add_header("User-Agent", UA)
    for k, v in HEADERS.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers)
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}", {}


def cmd_captcha(args: argparse.Namespace) -> int:
    status, raw, _ = call(CAPTCHA_URL)
    print(f"HTTP {status}")
    if status != 200:
        print(raw[:400])
        return 1
    data = json.loads(raw)
    png = OUT_DIR / "captcha_eroll.jpg"
    png.parent.mkdir(parents=True, exist_ok=True)
    png.write_bytes(base64.b64decode(data["captcha"]))
    print(json.dumps({"captchaId": data.get("id"), "image": str(png)}, indent=2))
    print("\nOpen that image, read the characters, then run:")
    print(f"  python work/eci_eroll.py generate ... --captcha-id {data.get('id')} "
          f"--captcha <TEXT>")
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    parts = [p.strip() for p in str(args.parts).split(",") if p.strip()]
    body = {
        "stateCd": args.state,
        "acNumber": str(args.ac),
        "partNumberList": parts,
        "districtCd": args.district or "",
        "captcha": args.captcha,
        "captchaId": args.captcha_id,
        "langCd": args.lang,
        "publishedRollId": args.roll_id,
        "misKey": MIS_KEY,
    }
    status, raw, _ = call(GENERATE_URL, "POST", body)
    print(f"HTTP {status}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print(raw[:500])
        return 1
    if status != 200 or data.get("statusCode") not in (0, 200, None):
        print(json.dumps(data, ensure_ascii=False)[:600])
        return 1
    file_ids = data.get("payload") or []
    print(json.dumps({"message": data.get("message"), "refId": data.get("refId"),
                      "fileIds": file_ids}, indent=2)[:2000])
    if args.save and file_ids:
        for i, fid in enumerate(file_ids):
            part = parts[i] if i < len(parts) else str(i + 1)
            out = ROLL_DIR / f"{args.state}_AC{args.ac}_part{part}.pdf"
            rc = download_file(fid, out, quiet=False)
            if rc != 0:
                print(f"  !! {fid[:24]}... failed", file=sys.stderr)
    return 0


def download_file(file_id: str, out: Path, quiet: bool = True) -> int:
    status, raw, _ = call(f"{FILE_URL}?fileId={file_id}")
    if status != 200:
        if not quiet:
            print(f"HTTP {status} {raw[:200]}", file=sys.stderr)
        return 1
    data = json.loads(raw)
    pdf = base64.b64decode(data["payload"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(pdf)
    if not quiet:
        print(f"  {out.name}: {len(pdf)} bytes  refId={data.get('refId')}")
    return 0


def cmd_file(args: argparse.Namespace) -> int:
    out = Path(args.out) if args.out else ROLL_DIR / f"{args.file_id}.pdf"
    rc = download_file(args.file_id, out, quiet=not args.quiet)
    if rc == 0:
        print(json.dumps({"saved": str(out), "bytes": out.stat().st_size}))
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ECI e-Roll PDF downloader")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("captcha", help="fetch captcha image + id for a human to read")
    c.set_defaults(func=cmd_captcha)

    g = sub.add_parser("generate", help="mint fileIds for parts (needs solved captcha)")
    g.add_argument("--state", default="S01")
    g.add_argument("--ac", required=True)
    g.add_argument("--district", default="")
    g.add_argument("--lang", default="ENGLISH")
    g.add_argument("--parts", required=True, help="comma list, e.g. 2 or 1,2,3")
    g.add_argument("--roll-id", default=DEFAULT_PUBLISHED_ROLL_ID,
                   help="publishedRollId from the portal's Roll Type dropdown")
    g.add_argument("--captcha-id", required=True)
    g.add_argument("--captcha", required=True)
    g.add_argument("--save", action="store_true", help="download each fileId too")
    g.set_defaults(func=cmd_generate)

    f = sub.add_parser("file", help="download one already-minted fileId")
    f.add_argument("--file-id", required=True)
    f.add_argument("--out")
    f.add_argument("--quiet", action="store_true")
    f.set_defaults(func=cmd_file)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
