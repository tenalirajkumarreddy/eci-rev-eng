#!/usr/bin/env python3
"""
EEPIC Engine (live mode)
========================
Turns an EPIC input (voter EPIC number) into concrete API requests that the
ECINET APK (Android, in.gov.eci.app, PAIRIP DEX-encrypted super-app) performs
for:
  * public voter search   - GET https://electoralsearch.in/api/search
  * EEPIC detail search   - POST https://gateway-vha.eci.gov.in/api/v1/eepic/GetElectorDetailForEEPIC
  * EEPIC card PDF        - POST https://gateway-vha.eci.gov.in/eepic/GetEEPICCard
  * EEPIC verification     - POST https://gateway-vha.eci.gov.in/api/v1/vh_epicno_verify

Run:
  python epic_engine.py --epic SXQ2097129
  python epic_engine.py --epic SXQ2097129 --live --config work/live_config.json

WORKING FLOW (verified live, 2026-10-05):
  python epic_engine.py --epic TBG0342345 --search

  POST https://gateway-vha.eci.gov.in/api/v1/elastic/search-by-epic-from-national-display-v1
  headers: applicationName/appName/channelidobo=VHA, platform-type=ANDROIDMOB, device-id
  body:    ChecksumRequest{encryptedKey, iv, encryptedPayload}  (see app_search.py)
  -> 200 [{"content": {...full voter record...}}]

  This is the app's own captcha-less EPIC search (PollingStationSearchActivity /
  ElectoralSearchActivity). It needs the two native constants from
  libnative_lib.so (work/app_keys.json); everything else is public.
  The eepic/* routes below are the older contract view kept for reference.

Only the EPIC number is required on the command line. All keys, hosts, headers,
response models and evidence strings are embedded in the code. Live credentials
(api_key, auth_proxy) are read from a small config file so the script stays
secret-free on the command line.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import app_search  # noqa: E402  (same directory): the live captcha-less flow

BASE_DIR = Path(__file__).resolve().parent
SMALI_DIR = BASE_DIR / "smali"
OUT_DIR = BASE_DIR / "out"
LIVE_OUT_DIR = OUT_DIR / "live"

# ---------------------------------------------------------------------------
# Ground truth pulled from the PAIRIP-encrypted APK (smali subset).
# The native .so key behind getOfficialDetailSecureKey() is not recoverable
# statically, so pass_key stays a computed SHA-512("IPS2062445"+key).  Until
# the app is run on a device and the native getter is hooked (Frida/mitm),
# the safe default is to leave pass_key computed with an empty key and flag
# the public voter search as weak-auth / unauthenticated.
# ---------------------------------------------------------------------------

APP_CONST = "IPS2062445"  # hardcoded salt in DigitalEpicActivity.callSearchApi

ELECTORAL_SEARCH = {
    "name": "Public voter record search (weak auth)",
    "verb": "GET",
    "host": "https://electoralsearch.in",
    "path": "/api/search",
    "id": "public_voter_search",
}

GARUDA_HOST = "https://gateway-vha.eci.gov.in"
GARUDA_V1 = GARUDA_HOST + "/api/v1"

# Verified Retrofit URL strings in RestClient.smali.
EEPIC_SEARCH = {
    "name": "EEPIC detail search (authenticated)",
    "verb": "POST",
    "host": GARUDA_V1,
    "path": "/eepic/GetElectorDetailForEEPIC",
    "id": "eepic_search",
    "headers": {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-API-KEY": "<from binary/config>",
    },
    "body": {
        "fields": [
            "content",
            "accept",
            "api",
            "body",
        ],
        "example": {
            "content": "application/json",
            "accept": "application/json",
            "api": "application/json",
            "body": {},
        },
    },
    "response_model": "ElectorDetails",
    "evidence": [
        "smali_classes13/com/eci/citizen/DataRepository/RestClient.smali",
        "POST eepic/GetElectorDetailForEEPIC",
        "response type: Lcom/eci/citizen/DataRepository/Model/ElectorDetails;",
    ],
}

EEPIC_CARD_PDF = {
    "name": "EEPIC card PDF download",
    "verb": "POST",
    "host": GARUDA_HOST,
    "path": "/eepic/GetEEPICCard",
    "id": "eepic_card_pdf",
    "headers": {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-API-KEY": "<from binary/config>",
    },
    "body": {
        "fields": [
            "content",
            "accept",
            "api",
            "body",
        ],
        "example": {
            "content": "application/json",
            "accept": "application/json",
            "api": "application/json",
            "body": {},
        },
    },
    "response_model": "ResponseBody (EPIC card PDF)",
    "evidence": [
        "smali_classes13/com/eci/citizen/DataRepository/RestClient.smali",
        "POST eepic/GetEEPICCard",
        "response type: Lokhttp3/ResponseBody;",
    ],
}

EEPIC_VERIFY = {
    "name": "EEPIC verification lookup",
    "verb": "POST",
    "host": GARUDA_V1,
    "path": "/api/v1/vh_epicno_verify",
    "id": "verification",
    "headers": {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-API-KEY": "<from binary/config>",
    },
    "body": {
        "fields": [
            "body",
        ],
        "example": {
            "body": {},
        },
    },
    "response_model": "VerifyEVPResponse",
    "evidence": [
        "smali_classes13/com/eci/citizen/DataRepository/RestClient.smali",
        "POST vh_epicno_verify",
        "response type: verifyEVPResponse;",
    ],
}

REQUEST_TEMPLATES = [
    ELECTORAL_SEARCH,
    EEPIC_SEARCH,
    EEPIC_CARD_PDF,
    EEPIC_VERIFY,
]


def normalize_epic(value: str) -> str:
    return " ".join(str(value).split()).upper()


def sha512_hex(*parts: str) -> str:
    data = (parts[0] if parts else "").encode("utf-8")
    for p in parts[1:]:
        data += p.encode("utf-8")
    return hashlib.sha512(data).hexdigest()


def sha512_hex_string(*parts: str) -> str:
    # Faithful port of Utils.GetHashNew: trim both inputs, SHA-512, lowercase hex.
    return sha512_hex(*[str(p).strip() for p in parts])


@dataclass
class LiveConfig:
    """Runtime credentials. Deliberately not on the CLI."""

    api_key: str = ""
    auth_proxy: str = ""
    verbose: bool = False


def load_config(path: Path, cfg: LiveConfig) -> LiveConfig:
    if not path.exists():
        return cfg
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        cfg.api_key = data.get("api_key", cfg.api_key) or cfg.api_key
        cfg.auth_proxy = data.get("auth_proxy", cfg.auth_proxy) or cfg.auth_proxy
        cfg.verbose = data.get("verbose", cfg.verbose) or cfg.verbose
    return cfg


def _build_url(host: str, path: str, query: list[tuple[str, str | None]]) -> str:
    qs = urllib.parse.urlencode(
        [(k, v) for k, v in query if v is not None]
    )
    return f"{host}{path}?{qs}" if qs else f"{host}{path}"


def _method_name(template: dict) -> str:
    return f"{template['verb']} {template['host']}{template['path']}"


ELECTOR_DETAILS_MAP = {
    # com/eci/citizen/DataRepository/Model/ElectorDetails.smali
    # (smali_classes13/com/eci/citizen/DataRepository/Model/ElectorDetails.smali)
    "AC_NAME": "ac_name",
    "AC_NO": "ac_no",
    "EPIC_NO": "epic_no",
    "IS_EEPIC_DOWNLOAD_ALLOWED": "is_eepic_download_allowed",
    "IS_EKYC_DONE": "is_ekyc_done",
    "IS_MOBILENO_UNIQUE": "is_mobile_no_unique",
    "MOBILE_NUM": "mobile_num",
    "NAME": "name",
    "NAME_V1": "name_v1",
    "PART_NO": "part_no",
    "RELATIVE_NAME": "relative_name",
    "RELATIVE_NAME_V1": "relative_name_v1",
    "STATE_NAME": "state_name",
    "USER_EMAIL": "user_email",
}


def map_elector_details(payload: dict) -> dict:
    """Normalize the raw EEPIC detail response into a voter profile.

    The app's model is ``ElectorDetails`` (smali_classes13/.../ElectorDetails.smali)
    plus the FormSevenNewResponse set (name/age/gender/dob/mobileno/etc.).  The
    API returns the combined JSON; we split it into a raw ``payload`` and a clean
    ``profile``.
    """
    payload = payload or {}

    profile: dict = {
        "epic_no": payload.get("EPIC_NO") or payload.get("epic_no"),
        "name": payload.get("NAME") or payload.get("NAME_V1") or payload.get("name"),
        "name_v1": payload.get("NAME_V1"),
        "name_v2": payload.get("nAMEV1"),
        "state_name": payload.get("STATE_NAME") or payload.get("st_name"),
        "email": payload.get("USER_EMAIL") or payload.get("mobileno"),
        "mobile_num": payload.get("MOBILE_NUM") or payload.get("mobileno"),
        "relative_name": payload.get("RELATIVE_NAME") or payload.get("rlnFmNmEn"),
        "age": payload.get("age", payload.get("AGE")),
        "gender": payload.get("gender", payload.get("GENDER")),
        "dob": payload.get("DOB") or payload.get("dob"),
        "part_no": payload.get("PART_NO") or payload.get("part_no"),
    }
    return {"payload": payload, "profile": {k: v for k, v in profile.items() if v not in (None, "", [], {})} }
def _body_from(template: dict, epic: str) -> dict:
    """Minimal stand-in body aligned to the smali field names."""
    return {"body": {"epic_no": epic, "search_type": "epic"}}


def _headers_from(template: dict, api_key: str, proxy: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for k, v in template["headers"].items():
        if k == "X-API-KEY":
            out.append((k, api_key or "<unset>"))
        else:
            out.append((k, v))
    if proxy:
        out.append(("X-Forwarded-Proto", "https"))
        out.append(("X-Forwarded-Host", "gateway-vha.eci.gov.in"))
    return out


def _query_from(template: dict, epic: str, pass_key: str | None) -> list[tuple[str, str]]:
    return [
        ("epic_no", epic),
        ("search_type", "epic"),
        ("pass_key", pass_key),
        ("page_no", "1"),
    ]


def _build_requests(epic: str, cfg: LiveConfig) -> tuple[dict, list[dict]]:
    """Return (contract, live_requests). contract is the existing JSON shape
    kept for the UI. live_requests carry everything the UI needs to actually
    fire the network calls."""
    pass_key = sha512_hex_string(APP_CONST, "")  # no per-user secret yet

    public_note = (
        "pass_key is client-computed and identical for every installation. "
        "No per-user authentication."
    )

    requests = [
        {
            "id": "public_voter_search",
            "name": "Public voter record search (weak auth)",
            "verb": "GET",
            "host": "https://electoralsearch.in",
            "path": "/api/search",
            "query": [
                ("epic_no", epic),
                ("search_type", "epic"),
                ("pass_key", pass_key),
                ("page_no", "1"),
            ],
            "note": public_note,
            "evidences": [
                "smali_classes13/com/eci/citizen/features/home/evp/"
                "DigitalEpicActivity.smali",
                "callSearchApi: GET api/search?epic_no=...&search_type=epic"
                "&passKey=...&page_no=...",
            ],
        }
    ]

    live_requests: list[dict] = []
    for template in REQUEST_TEMPLATES:
        request_id = template["id"]
        if request_id == "public_voter_search":
            # Already covered above; leave as the weak-auth GET.
            requests.append(
                {
                    "id": "public_voter_search",
                    "name": "Public voter record search (weak auth)",
                    "verb": "GET",
                    "host": "https://electoralsearch.in",
                    "path": "/api/search",
                    "query": [
                        ("epic_no", epic),
                        ("search_type", "epic"),
                        ("pass_key", pass_key),
                        ("page_no", "1"),
                    ],
                    "note": public_note,
                    "evidences": [
                        "smali_classes13/com/eci/citizen/features/home/evp/"
                        "DigitalEpicActivity.smali",
                        "callSearchApi: GET api/search?epic_no=...&search_type=epic"
                        "&passKey=...&page_no=...",
                    ],
                }
            )
            # Do NOT double-add public search to live requests.
            continue

        headers = _headers_from(template, cfg.api_key, cfg.auth_proxy)
        if template["id"] == "eepic_card_pdf":
            # Card PDF POSTs to the bare gateway host.
            url = f"{GARUDA_HOST}{'/eepic/GetEEPICCard'}"
        elif template["id"] == "verification":
            # Post to gateway-vha with api/v1 prefix.
            url = f"{GARUDA_V1}/vh_epicno_verify"
        else:
            # Search endpoint.
            url = f"{GARUDA_V1}/eepic/GetElectorDetailForEEPIC"

        live_requests.append(
            {
                "id": request_id,
                "name": template["name"],
                "verb": template["verb"],
                "url": url,
                "headers": headers,
                "body": _body_from(template, epic),
                "response_model": template["response_model"],
                "evidence": template["evidence"],
            }
        )
        requests.append(
            {
                "id": request_id,
                "name": template["name"],
                "verb": template["verb"],
                "host": template["host"],
                "path": template["path"],
                "query": [],
                "headers": headers,
                "body": _body_from(template, epic),
                "response_model": template["response_model"],
                "evidences": template["evidence"],
            }
        )
        if template["id"] == "eepic_search":
            requests[-1]["response_profile"]= map_elector_details(requests[-1]["body"])

    return {
        "engine": "epic_engine",
        "version": "1.0.0",
        "epic": epic,
        "requests": requests,
        "endpoints": [
            {
                "id": t["id"],
                "name": t["name"],
                "verb": t["verb"],
                "host": t["host"],
                "path": t["path"],
                "headers": t.get("headers", {}),
                "response_model": t.get("response_model"),
            }
            for t in REQUEST_TEMPLATES
        ],
    }, live_requests


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_live(cfg: LiveConfig, epic: str, out_dir: Path) -> int:
    contract, live_requests = _build_requests(epic, cfg)
    out_dir.mkdir(parents=True, exist_ok=True)

    report_path = out_dir / "epic_contract.json"
    report_path.write_text(
        json.dumps(contract, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )

    for req in live_requests:
        req["sent_at"] = _utc_now()
        req.setdefault("status", "not_started")

    # Merge the public-search GET into the live requests so every request the
    # app makes is in one list. It uses the computed pass_key but no credentials.
    public_req = {
        "id": "public_voter_search",
        "name": "Public voter record search (weak auth)",
        "verb": "GET",
        "url": _build_url(
            "https://electoralsearch.in",
            "/api/search",
            [
                ("epic_no", epic),
                ("search_type", "epic"),
                ("pass_key", sha512_hex_string(APP_CONST, "")),
                ("page_no", "1"),
            ],
        ),
        "headers": [("User-Agent", "Mozilla/5.0 ECIPIN Engine")],
        "body": None,
        "response_model": "ElectorDetails",
        "evidence": [
            "smali_classes13/com/eci/citizen/features/home/evp/"
            "DigitalEpicActivity.smali",
            "callSearchApi: GET api/search?epic_no=...&search_type=epic&passKey="
            "&page_no=...",
        ],
        "sent_at": _utc_now(),
        "status": "not_started",
    }
    live_requests.insert(0, public_req)

    for req in live_requests:
        out_path = out_dir / f"{req['id']}.json"
        payload = {
            "request": req,
            "response": {"status": None, "bytes": 0, "summary": None},
            "error": None,
        }
        out_path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n",
                            encoding="utf-8")
        if cfg.verbose:
            print(f"[engine] {req['verb']} {req['url']}  ->  {out_path}", file=sys.stderr)

    print(json.dumps(contract, indent=2, sort_keys=False))
    print(f"\n[engine] wrote {report_path}", file=sys.stderr)
    for req in live_requests:
        print(f"[engine] live {req['id']} -> {out_dir / (req['id'] + '.json')}",
              file=sys.stderr)
    return 0


# ---------------------------------------------------------------------------
# The live captcha-less search: EPIC in -> voter record out
# ---------------------------------------------------------------------------
SEARCH_ENDPOINT = app_search.ENDPOINT


PROFILE_FIELDS = (
    ("epic", "epicNumber"),
    ("epic_id", "epicId"),
    ("record_id", "id"),
    ("part_id", "partId"),
    ("name", "fullName"),
    ("name_local", "fullNameL1"),
    ("relation", "relativeFullName"),
    ("relation_local", "relativeFullNameL1"),
    ("relation_type", "relationType"),
    ("age", "age"),
    ("gender", "gender"),
    ("part_no", "partNumber"),
    ("part_name", "partName"),
    ("part_name_local", "partNameL1"),
    ("serial_no", "partSerialNumber"),
    ("section_no", "sectionNo"),
    ("assembly", "asmblyName"),
    ("assembly_local", "asmblyNameL1"),
    ("assembly_no", "acNumber"),
    ("parliamentary", "prlmntName"),
    ("parliamentary_no", "prlmntNo"),
    ("district", "districtValue"),
    ("district_local", "districtValueL1"),
    ("district_code", "districtCd"),
    ("state", "stateName"),
    ("state_code", "stateCd"),
    ("polling_station", "psbuildingName"),
    ("polling_station_local", "psBuildingNameL1"),
    ("building_address", "buildingAddress"),
    ("active", "isActive"),
    ("epic_updated", "epicDatetime"),
    ("created", "createdDttm"),
)


def map_national_display(hit: dict) -> dict:
    """Flatten one TElasticSearchResponse from the national-display search."""
    content = (hit or {}).get("content") or {}
    profile = {name: content.get(json_name) for name, json_name in PROFILE_FIELDS}
    return {k: v for k, v in profile.items() if v not in (None, "", [], {})}


def search_epic(epic: str, keys_path: Path | None = None) -> dict:
    """Live EPIC lookup. Returns {epic, status, count, profile, raw, sent_at}."""
    result = app_search.fetch(epic, keys_path)
    hits = result.get("hits") or []
    return {
        "engine": "epic_engine",
        "endpoint": SEARCH_ENDPOINT,
        "epic": result["epic"],
        "http_status": result["status"],
        "count": len(hits),
        "profile": map_national_display(hits[0]) if hits else None,
        "raw": result["raw"][:20000],
        "sent_at": result.get("sent_at"),
    }


PART_API = ("https://gateway-vha.eci.gov.in/api/v1/"
            "common/part/get/bystatecd/districtcd/acNumber")

PART_EXTRA_FIELDS = (
    ("part_category", "partCategory"),
    ("part_name_local", "partNameL1"),
    ("ps_type", "psTypeEng"),
    ("ps_building_details", "psBuildingDetails"),
    ("village_id", "villageId"),
    ("main_village", "mainVillage"),
    ("block_no", "blockNo"),
    ("tahsil_no", "tahsilNo"),
    ("ri_no", "riNo"),
    ("po_id", "poId"),
    ("ps_id", "psId"),
    ("ps_building_id", "psBuildingId"),
    ("electors_count", "electorsCount"),
    ("effective_from", "effectiveFrom"),
    ("town_no", "townNo"),
    ("ward_no", "wardNo"),
    ("part_address", "partAddress"),
    ("part_lat_long", "partLagLong"),
)

PART_DOCUMENTS = (
    ("nazri_naksha", "nazriNakshaDln"),
    ("google_map_view", "googleMapViewDln"),
    ("ps_building_front_view", "psbuildFrntVwDln"),
    ("ps_front_view", "psFrntVwDln"),
    ("cad_view", "cadViewDln"),
    ("key_map_view", "keyMapViewDln"),
    ("ps_building_photograph", "psBuildingPhotographDln"),
)


def fetch_part_extras(profile: dict, timeout: int = 30) -> dict | None:
    """Anonymous polling-part record for the voter's own part.

    `common/part/get/bystatecd/districtcd/acNumber` needs no login (it backs the
    app's "Know Your Polling Station" screen) and carries PS building details,
    the part's village/block/tahsil codes, officer ids and the document links
    (naZri naksha, Google map view, PS building CAD/front views) that the
    national-display record does not have.
    """
    state_cd = profile.get("state_code")
    ac_no = profile.get("assembly_no")
    part_id = profile.get("part_id")
    part_no = profile.get("part_no")
    if not (state_cd and ac_no is not None):
        return None

    url = f"{PART_API}?stateCd={urllib.parse.quote(str(state_cd))}&acNumber={ac_no}"
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("applicationName", "VHA")
    req.add_header("appName", "VHA")
    req.add_header("channelidobo", "VHA")
    req.add_header("platform-type", "ANDROIDMOB")
    req.add_header("state", str(state_cd))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            rows = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - enrichment is best-effort
        return None
    if not isinstance(rows, list):
        return None

    row = next((r for r in rows if part_id is not None and r.get("partId") == part_id), None)
    if row is None and part_no is not None:
        row = next((r for r in rows if str(r.get("partNumber")) == str(part_no)), None)
    if row is None:
        return None

    extras = {name: row.get(json_name) for name, json_name in PART_EXTRA_FIELDS}
    extras = {k: v for k, v in extras.items() if v not in (None, "", [], {})}
    docs = {name: row.get(json_name) for name, json_name in PART_DOCUMENTS}
    extras["documents"] = {k: v for k, v in docs.items() if v}
    extras["parts_in_ac"] = len(rows)
    return extras


def format_profile(report: dict) -> str:
    lines = [f"EPIC {report['epic']}  ->  HTTP {report['http_status']}, "
             f"{report['count']} record(s)"]
    profile = report.get("profile")
    if not profile:
        lines.append("  no record in the national electoral display for this EPIC")
        return "\n".join(lines)
    rows = (
        ("Name", "name"), ("Name (local)", "name_local"),
        ("Relation", "relation"), ("Age", "age"), ("Gender", "gender"),
        ("Assembly", "assembly"), ("Parliamentary", "parliamentary"),
        ("District", "district"), ("State", "state"),
        ("Part", "part_name"), ("Part No", "part_no"), ("Serial No", "serial_no"),
        ("Polling station", "polling_station"), ("Address", "building_address"),
        ("Record id", "record_id"), ("Active", "active"),
    )
    width = max(len(label) for label, _ in rows)
    for label, key in rows:
        if profile.get(key) not in (None, ""):
            lines.append(f"  {label:<{width}} : {profile[key]}")

    part = report.get("part") or {}
    if part:
        lines.append("  " + "-" * (width + 12))
        for label, key in (("PS type", "ps_type"),
                           ("PS building", "ps_building_details"),
                           ("Part category", "part_category"),
                           ("Village (main)", "main_village"),
                           ("Block/Tahsil/RI", None),
                           ("Officer ids (PO/PS)", None),
                           ("Parts in AC", "parts_in_ac")):
            if key is None:
                continue
            if part.get(key) not in (None, ""):
                lines.append(f"  {label:<{width}} : {part[key]}")
        if part.get("block_no") or part.get("tahsil_no") or part.get("ri_no"):
            lines.append(f"  {'Block/Tahsil/RI':<{width}} : "
                         f"{part.get('block_no')}/{part.get('tahsil_no')}/{part.get('ri_no')}")
        if part.get("po_id") or part.get("ps_id"):
            lines.append(f"  {'Officer ids (PO/PS)':<{width}} : "
                         f"{part.get('po_id')}/{part.get('ps_id')}")
        if part.get("documents"):
            lines.append(f"  {'Documents':<{width}} : "
                         + ", ".join(part["documents"].keys()))
    return "\n".join(lines)


def _utf8_stdout() -> None:
    """The gateway returns local-language names (Telugu, Devanagari); the
    Windows console defaults to cp1252 and would raise on them."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - best effort, old consoles
            pass


def run_search(epic: str, keys_path: Path | None, enrich: bool = True) -> int:
    _utf8_stdout()
    try:
        report = search_epic(epic, keys_path)
    except ValueError as e:
        print(f"[engine] {e}", file=sys.stderr)
        return 2
    if enrich and report.get("profile"):
        report["part"] = fetch_part_extras(report["profile"])
    print(format_profile(report))
    if report["http_status"] not in (200, 201):
        print(f"[engine] raw response: {report['raw'][:300]}", file=sys.stderr)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"epic_{report['epic']}.json"
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"[engine] wrote {out_path}", file=sys.stderr)
    return 0 if report["http_status"] == 200 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EEPIC Engine")
    parser.add_argument(
        "--epic",
        required=True,
        help="voter EPIC number (only required input)",
    )
    parser.add_argument(
        "--config",
        default=str(BASE_DIR / "live_config.json"),
        help="JSON config with api_key / auth_proxy (not on the CLI)",
    )
    parser.add_argument(
        "--out",
        default=str(OUT_DIR),
        help="output directory (default: work/out)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="actually render the network request contract",
    )
    parser.add_argument(
        "--list-endpoints",
        action="store_true",
        help="print the known endpoint contract only",
    )
    parser.add_argument(
        "--search",
        action="store_true",
        help="LIVE: run the app's captcha-less EPIC search and print the voter "
             "record (needs work/app_keys.json)",
    )
    parser.add_argument(
        "--keys",
        default=str(BASE_DIR / "app_keys.json"),
        help="path to app_keys.json (tc + external)",
    )
    parser.add_argument(
        "--no-enrich",
        action="store_true",
        help="skip the anonymous polling-part lookup (PS building, village codes, "
             "map/photo document links)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="print launch lines to stderr",
    )
    args = parser.parse_args(argv)

    if args.search:
        return run_search(normalize_epic(args.epic), Path(args.keys),
                          enrich=not args.no_enrich)

    cfg = LiveConfig()
    cfg = load_config(Path(args.config), cfg)
    cfg.verbose = args.verbose

    if args.list_endpoints:
        out = {
            "engine": "epic_engine",
            "version": "1.0.0",
            "endpoints": [
                {
                    "id": t["id"],
                    "name": t["name"],
                    "verb": t["verb"],
                    "path": t["path"],
                    "host": t["host"],
                    "headers": t.get("headers", {}),
                    "response_model": t.get("response_model"),
                }
                for t in REQUEST_TEMPLATES
            ],
        }
        print(json.dumps(out, indent=2))
        return 0

    if not args.live:
        # Legacy contract mode: only the EPIC number is still required.
        contract = _build_requests(args.epic, cfg)[0]
        print(json.dumps(contract, indent=2, sort_keys=False))
        return 0

    return run_live(cfg, args.epic, Path(args.out))


if __name__ == "__main__":
    sys.exit(main())
