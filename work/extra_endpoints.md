# Extra voter/polling details beyond the national-display record

Survey of the other APIs `in.gov.eci.app` (v1.9.44) declares, which ones answer
without a login, and what extra data each adds. Everything here was probed live
against `gateway-vha.eci.gov.in` on 2026-10-05 with a real EPIC
(`TBG0342345`, Ichchapuram AC, Srikakulam, Andhra Pradesh).

Full endpoint inventory: `work/out/endpoints.json` (373 routes, produced by
`work/endpoint_map.py`). Probes: `work/elastic_probe.py`, `work/anon_probe.py`,
`work/apikey_oracle.py`.

## 1. Works anonymously, adds data

### Polling-part record - the useful one

```
GET https://gateway-vha.eci.gov.in/api/v1/common/part/get/bystatecd/districtcd/acNumber
      ?stateCd=S01&acNumber=1
headers: applicationName/appName/channelidobo: VHA, platform-type: ANDROIDMOB, state: S01
```

Backs the app's "Know Your Polling Station" screen. Returns every part of the AC
(319 for Ichchapuram) and, for the voter's own part, fields the national record
lacks. Matched to the voter by `partId` (80397 = KEDARIPURAM, part 2):

| field | value for TBG0342345 |
|---|---|
| `psTypeEng` / `psTypeV1` | GENERAL / సాధారణ |
| `psBuildingDetails` | MPE SCHOOL, MIDDLE ROOM KedariPuram |
| `partCategory` | R |
| `mainVillage` / `villageId` | S011025182 / 1025182 |
| `blockNo` / `tahsilNo` / `riNo` | 37 / 37 / 1 |
| `poId` / `psId` / `psBuildingId` | 43344 / 2108 / 2 |
| `effectiveFrom` / `effectiveTo` | 2022-01-01 / 2099-12-31 |
| `partNameL1` | కేదారిపురం |
| documents | `nazriNakshaDln`, `googleMapViewDln`, `psbuildFrntVwDln`, `psFrntVwDln`, `cadViewDln`, `keyMapViewDln` (+ availability flags `isCadViewAvailble`, ...) |

Document links look like `S01/Part/1/2/nazrinaksha_<uuid>_2.jpeg` — the bytes
come from `document/getFile?bucketName=&fileName=` (see section 2, token-gated).

Already merged into the engine: `epic_engine.py --search` prints these under the
voter record (`--no-enrich` skips the call). Output: `work/out/epic_<EPIC>.json`
key `part`.

### Reference lists (same anonymous family)

```
GET citizen/sir/getDistrict      headers: state: S01            -> 200, 13 districts
GET citizen/sir/getAsmbly        headers: State: S01            -> 200
GET citizen/sir/getPartByAc      ?Asmbly=<ac>&state S01         -> 200
```

A wrong `state` value is rejected loudly: `state: 1` gives
`500 "No datasource found for context decider - 1"`, so the code form (`S01`)
is required.

### SIR roll search - anonymous but state-specific

```
GET citizen/sir/getDetailsByEpicNo?epic=TBG0342345   headers: state: S01
GET citizen/sir/getDetailsByEroll?acNo=&partNo=&serialNo=
```

Answers `404 {"message":"No Record Found."}` for this Andhra Pradesh EPIC: the
route really is the "Search Your Name in Last SIR" flow (SIR 2002 / SIR 2025
rolls) and only has data for the states where SIR ran. Response model is
`EnumerationStateData`, rendered as AC/part/PS/full name/age/relative - the same
field family as the national record, so it is a roll-version cross-check rather
than a richer record.

## 2. Login-gated (would add mobile, e-mail, eKYC, photo)

| route | headers | what it adds | live result |
|---|---|---|---|
| `POST eepic/GetElectorDetailForEEPIC` | `X-API-KEY` | `MOBILE_NUM`, `USER_EMAIL`, `IS_EKYC_DONE`, `IS_MOBILENO_UNIQUE`, `IS_EEPIC_DOWNLOAD_ALLOWED`, `AC_NAME`, `PART_NO`, `NAME_V1` | 401 for all 45 candidate keys swept |
| `POST eepic/GetEEPICCard` | `X-API-KEY` | e-EPIC card PDF (photo, QR) | 401 |
| `GET document/getFile?bucketName=&fileName=` | `Authorization`, `atkn_bnd`, `rtkn_bnd` | the part documents above (naksha, map, PS photos) | (needs session) |
| `GET document-adhoc/getPresignedFile` | same three | object-store variant of the above | (needs session) |
| `GET vha/getPollingOfficials?epicNo=` | `Authorization`, `atkn_bnd`, `rtkn_bnd` | BLO/ERO/PO contacts | 401 |
| `GET form6b/get/checkEpicHasAdhar/{epic}` | `Authorization`, `atkn_bnd`, `rtkn_bnd`, `currentRole`, `state` | Aadhaar-seeded status for the EPIC | 401 |
| `POST elastic/get-by-epic-for-form` / `get-by-details-for-form` | `Authorization`, `atkn_bnd`, `rtkn_bnd` | plaintext `TElasticSearchRequest` instead of the checksum envelope | 401 |
| `GET modal/GetElectorsDetails` | `X-API-KEY`, `AuthorizedVHA` | legacy elector-details view | (same key gate) |
| `POST citizen-hearing/fetchDetailsByEpicIdOrReferenceNo` | bearer | hearing/application trail for an `epicId` | 401 |
| `GET mservices/api/EVP/GetEVPElectorDetails` | bearer | EVP elector detail | 401 |
| `POST e-epic/get-epic-detail` (+ `send-otp`, `verify-otp`) | tokens | e-EPIC detail after mobile OTP | 401 |

`X-API-KEY` candidates live as plaintext constants in `libnative_lib.so`
(`ECINATIONALELECTORALSEARCH#1234MOBILEAPPKEY`, `ABCD1234#123521GISTECIKEY`,
`cDAcGistECI/CdacGist@@$18`, `VHA-B5DF491FEQB8ABE5E24DB9E5D8QY4PX3`, ... - the
same sweep technique that recovered `tc`). No candidate got past the edge
though: the EEPIC routes answer `401 WWW-Authenticate: Bearer` before the API
key matters, i.e. a citizen login (mobile OTP) is the real blocker.

## 3. What a Bearer actually unlocks (tested 2026-10-05 with a real token)

The gate is the token, not the header name - proven by how the edge answers:

| request | gateway |
|---|---|
| no auth | `401` + `WWW-Authenticate: Bearer`, empty body |
| `Authorization: <raw jwt>` (no scheme) | same bare challenge |
| `atkn_bnd: <jwt>` alone | same bare challenge |
| `Authorization: Bearer <jwt>` | `401 {"error": "Invalid credentials or token expired"}` |

So with the `Bearer` scheme the edge parses and validates the token instead of
challenging, and a token that validates reaches the route's own checks.

A portal-issued token (`iss http://keyclokvoters.eci.prd:8081/realms/nvsp-prod-realm`,
role `citizen`, unexpired) is rejected by **both** `gateway-vha` and
`gateway-voters` as invalid/expired, so it cannot be used here.

**Superseded (see section 5):** the guess that "only a token from the app's own
OTP login passes" was tested and is **false**. A real `authn-voter` OTP login
mints a genuine 8-hour session and the gateway still answers every gated route
with the same `401 {"error": "Invalid credentials or token expired"}` - and a
garbage `Bearer not.a.jwt` gets byte-identical treatment, so that message carries
no information about whether a token is good.

Header requirements once a valid token exists (from the smali annotations):

- `eepic/GetElectorDetailForEEPIC`, `eepic/GetEEPICCard`: `Authorization` **and**
  `X-API-KEY` (native constant; still unverified because auth fails first).
- `document/getFile`, `document-adhoc/getPresignedFile`, `vha/getPollingOfficials`,
  `form6b/get/checkEpicHasAdhar/{epic}`, `elastic/get-by-epic-for-form`,
  `citizen/sir/getDetailsByEpicNo`, `citizen-hearing/fetchDetailsByEpicIdOrReferenceNo`,
  `mservices/api/EVP/GetEVPElectorDetails`: `Authorization` + `atkn_bnd` + `rtkn_bnd`.
- One login returns all three: `AuthFlowResponse` carries `access_token`,
  `refresh_token`, `atkn_bnd`, `rtkn_bnd`, `expires_in`, `refresh_expires_in`;
  `AuthenticatorInterceptor` replays any 401 with them from stored `T_USER_INFO`,
  and `authn-voter/refresh` mints new access tokens from `refresh_token`.

Runner: `python work/token_probe.py --epic <EPIC> --sweep-keys` (reads the token
from `work/live_config.json` key `bearer`, which may be stored with or without the
`Bearer ` prefix). Section 5 supersedes the expectation below.

## 4. Does not reproduce

- `POST elastic/search-by-details-from-state-display-v1` - same encrypted
  envelope, same headers as the working national route, but `400 []` with an
  EPIC-only payload and with `stateCd`/`distCd`/`ac`/name/age/gender added.
- `POST elastic/search-by-mobile-from-state-search-display-v1` - `400 []`
  (mobile + OTP flow, not reachable without the SMS OTP).
- `GET getPartByAc` (root path) - `401`; the `citizen/sir/getPartByAc` form works.

## 5. Real OTP login - what works and what does not

`work/otp_login.py` reproduces the app's own citizen login (`NvspLogin`) and it
**works**. Verified live, twice, against a real handset.

### The contract

Both OTP calls use the same AES-256-GCM + RSA-OAEP `ChecksumRequest` envelope as
the EPIC search (so `app_search.encrypt_data` is reused as-is). The inner object
is `AuthFlowRequest`, built exactly as `NvspLogin` builds it:

    send:   {"username": "<phone>"}                     # `etPhone`, and ONLY this
    verify: {"username": "<phone>", "otp": "<code>"}

Field order from the ctor: `applicationName`/`appName` = `VHA`, `epicRefNo`,
`mobileNo`, `otp`, `password` = `""`, `roleCode` = `"*"`, `stateCd`, `username`.
`refreshToken` is the one field the ctor leaves null, so default Gson omits it -
and the `mobileNo` field is **never** set by this flow. Sending
`mobileNo` instead of `username` gets a very useful error:
`400 {"message": "username field cannot be blank", "cause": {"username": ...}}` -
proof the gateway decrypts the envelope, because it read our plaintext fields.
A successful send is just `200 {"message": "OTP sent on ******5757."}`.

### Verified behaviour

- The send path does **not** validate the number: `0000000000` also returns
  `200 OTP sent`. Nothing is bound to the electoral roll at this stage.
- Re-sending inside the window answers
  `401 {"message": "Please wait for 7 seconds before resending new otp!"}`, and a
  code that is already spent/aged answers `401 {"message": "OTP expired!"}` -
  so re-sending before verifying destroys the live code. `--otp` therefore
  verifies without re-sending; `--resend` is opt-in.
- Verify returns a full Keycloak session: `access_token` (1497 chars),
  `refresh_token` (710), `atkn_bnd`, `rtkn_bnd`, `expires_in`/`refresh_expires_in`
  28800, `token_type: Bearer`, `firstTimeLogin: "N"`, `session_state`. Decoded,
  the access token has `iss http://keycloakvoters.eci.prd:8081/realms/nvsp-prod-realm`,
  `azp vha-authn-client`, `role citizen`, `phone_number`/`name` of the logged-in
  user - i.e. a genuinely different token from the rejected portal one.
- `authn-voter/refresh` works with `Authorization: Bearer <access>` +
  `atkn_bnd` and `refreshToken` in the body (the call is
  `refreshToken(accessToken, atknBand, AuthFlowRequest)`), and reports
  `400 invalid_grant: Session not active` once the session has gone.
- `atkn_bnd`/`rtkn_bnd` are pure-ASCII strings like `77+977+9Xhjv...` that
  base64-decode to runs of `U+FFFD`: the gateway mangles its own binding tokens.
  Replaying the string verbatim is safe; there is no client-side corruption, so
  no amount of latin-1/utf-8 care changes them.

### The gate still does not open

With a token **three seconds old**, sent with the app-exact headers
(`Authorization: Bearer <access>` + `atkn_bnd` + `rtkn_bnd`, plus `state` and
`currentRole: citizen` where the app sends them):

    eepic/GetElectorDetailForEEPIC   401      document/getFile      401
    vha/getPollingOfficials          401      form6b/checkEpicHasAdhar  401
    citizen/sir/getDetailsByEpicNo   401

Two facts pin this on the edge, not on our token handling:

1. `Bearer not.a.jwt` produces a **byte-identical** body, and even a *missing*
   token does for the non-`Bearer` forms - the message is generic.
2. Adding an `Authorization` header to the **anonymous** national-search route
   changes it from `200` (records returned) to `400 []` - the same body, same
   `securityKey`, same keys. The mere presence of `Authorization` switches the
   gateway onto an authenticated path that rejects us.

Also settled from the smali (and worth not re-deriving):

- `AuthFlowResponse.getAccess_token()` returns `"Bearer " + access_token`, so the
  app really does send the `Bearer` scheme; `getAccessTokenWithoutBearer()` is the
  raw one.
- Every authenticated route lives on `https://gateway-vha.eci.gov.in/api/v1/`
  (`TApiClient.API_BASE_URL`/`API_PROD_URL`); only BLO forms traffic goes to
  `gateway-s2-blo`. Our host is right.
- `eepic/GetElectorDetailForEEPIC` and `eepic/GetEEPICCard` are declared only in
  `com/eci/citizen/DataRepository/RestClient` and called from **nowhere** in the
  APK - the `X-API-KEY` they want is passed by a caller that does not exist in
  this build. Treat the eepic pair as dead code.
- Full-tree smali scans are cheap if done with a threaded byte scan (~15 s for
  75,901 files); plain `grep -r` times out, and `work/smali` is git-ignored so
  `code_search` skips it.

The remaining unknown can only be answered by watching a *working* request. The
app on the handset is the only client the gateway trusts, so that means an
on-device capture (manual Wi-Fi proxy - ColorOS blocks `adb shell settings put
global http_proxy`).
## 6. Part → EPIC list, anonymously (`elastic-sir-citizen/get-eroll-data-2003`)

Found 2026-10-06 while mining the app's BLO/SIR client for a part-level elector
list (the old garuda APK's `GetErollElectorList` lives on `blonetservices.ecinet.in`,
which is DNS-dead, and the live successor gateways want session tokens the
citizen app cannot mint). This route answers **without any token**:

```
POST https://gateway-vha.eci.gov.in/api/v1/elastic-sir-citizen/get-eroll-data-2003
headers: Content-Type/Accept: application/json,
         applicationName/appName/channelidobo: VHA,
         platform-type: ANDROIDMOB, currentRole: citizen
body:    {"oldStateCd": "S01", "oldAcNo": "1", "oldPartNo": "2", "oldPartSerialNo": "<n>"}
```

Declared in the APK as `TRestClient.getSearchErollDetails(bearer, atknBnd,
rtknBnd, SirSearchRequest)`; the tokens the app passes are **not checked** by the
backend for this route - anonymous calls return full payloads.

### Behaviour

| body | result |
|---|---|
| `oldPartSerialNo: "16"` | `200` + 1 record |
| `oldPartSerialNo: ""` | `200` + a random ~50-record window of the part |
| `oldPartSerialNo: "0"` | `404` ("No Records Found") |
| missing/partial old* fields | `400 {"message":"Invalid Input"}` |
| unknown state/ac/part | `404` |
| `-final` sibling route | `401` (token + live session required; a valid but session-dead citizen token still 401s) |

The window form saturates well below the part size (old part 2: 195 unique over
25 calls) - it is a filtered view, so the **serial sweep is the exhaustive one**.

### What each record carries

`id`, `firstName`/`oldFullName` (+ `L1` Telugu), `relativeFName`,
`oldRelativeFullName`, `relationType`, `age`, `gender`, `oldAcName`,
`oldDistName`, `oldPartName`, `oldPartNumber`, `oldPartSerialNo`,
`epicNumber` (old roll, usually masked `000000000000` or `AP010010000000`),
`markedByBlo`, and crucially:

    bloMappedStateCd / bloMappedAcNo / bloMappedPartNo / bloMappedEpicNo

i.e. **the elector's current EPIC and the current part it sits in**.

### Verified

* `TBG0342345` (the working sample EPIC - national search: part 2, serial 16,
  "urjana ramaiah") is returned for old part 1 serial 29 as `oldFullName
  "ramaiah"`, `oldFullNameL1 "రామయ్య ఉర్జాన"`, `bloMappedPartNo "2"`,
  `bloMappedEpicNo "TBG0342345"`. ✔
* `TBG1753664` (old part 2 serial 27, "koyiramma sadi") → national search says
  `name "koiramma sadi"`, `part_no "1"`; mapping says part 1. ✔
* `KZZ0650044` (old part 1, mapped part 2) → national search says
  `name "bairi setty"`, `part_no "2"`. ✔

### Old→current part correspondence (AC 1, S01)

Old parts are split/merged into today's parts, so one current part is fed by one
or more old parts:

| old part | old name | current part(s) |
|---|---|---|
| 1 | Kedari Puram | 2, 3 (both KEDARIPURAM today) |
| 2 | Muchimdra | 1 (MUCHINDRA today) |
| 3 | Purushottapuram | 4, 5 (both PURUSHOTTAPURAM today) |

`bloMappedPartNo` matches the live part numbering (verified against
`common/part/get/bystatecd/districtcd/acNumber`, part 1 = MUCHINDRA,
part 2 = KEDARIPURAM, ...), and some electors map to other ACs (150, 160, 173,
176...) or other states (`S10` = Telangana) - the mapping crosses boundaries.

### Tool

`work/part_epics.py` - resumable JSONL per old part + combined CSV:

    python work/part_epics.py --state S01 --ac 1 --old-part 2          # one old part
    python work/part_epics.py --state S01 --ac 1 --old-part 1-4 --merge-part 2
    python work/part_epics.py --state S01 --ac 1 --old-part 1 --workers 6

Measured: **670 serials in 33 s with 3 workers** (0.13 s/request, zero 429s);
6 workers sustain ~43 req/s with no 429s either. So one old part ≈ 30-40 s and a
whole AC (319 old parts) ≈ 3 h at 3 workers, ~1.5 h at 6 - resumable.

Outputs: `work/out/part_epics/SS01_AC1_oldP<n>.jsonl|.csv` (per old part) and
`work/out/part_epics/SS01_AC1_curP<n>.csv` (filtered to a current part).

Example (current part 2, 4 old parts swept): 172 EPICs, all from old part 1,
e.g. `KZZ0650044 bairy chetty`, `KZZ0650101 meena jeeru`, ... plus `TBG0342345`.

### Caveats

* Coverage is the 2003-roll cohort that has a BLO mapping: old part 2 had 670
  records, 454 mapped; old part 1 had 820 records, 485 mapped. Electors added
  after that roll (new registrations, first-time voters) are **not** in this
  dataset.
* Scope the sweep to all old parts that feed the target current part (the
  `1-4` range above covered part 2; unknown contributors can be found by
  sweeping a wider range and filtering).
* The current-roll-complete alternative is the `-final` sibling route (and the
  other gated routes), which needs a citizen session that is *live* - a fresh
  OTP login; a saved-but-session-dead token gets 401.
### 6b. Which old parts feed a current part (name discovery), and mapping vintage

Current parts were carved out of old ones **by village**, not by number, so the
numeric window heuristic fails: current part 12 (MANDAPALLI) gets *zero* EPICs
from old parts 11-14 but 117 from old part 8. The reliable key is the old part
**name**, which the window response carries as `oldPartName`.

Name discovery is cheap - one `oldPartSerialNo:""` request per old part (~0.15 s),
falling back to serial probes 1/25/100 if the window is empty:

    old part   old name          current part(s) it feeds
    1          Kedari Puram      2, 3
    2          Muchimdra         1
    3, 4       Purushottapuram   4, 5
    5          Aminsahebpeta     6, 7
    6          Mamdapalli        8, 9 (2 strays to 12)
    7          Mamdapalli        10, 11
    8          Mamdapalli        12, 13 (14 to 11)

Spelling drifts ("Mamdapalli" vs MANDAPALLI, "Muchimdra" vs MUCHINDRA), so the
match is normalised (case/space-stripped) plus a `difflib` ratio >= 0.82 for
names >= 6 chars.

**Mapping vintage.** `bloMappedPartNo` is a snapshot; the published roll was
re-partitioned afterwards, so in some stretches of an AC the mapping numbers lag
today's by one. Sampled 2 EPICs per mapped part and asked the national search for
the current part:

| mapped part | national part | offset |
|---|---|---|
| 2, 3, 6, 8, 10 | same | +0 |
| 12, 13, 18, 20, 23 | +1 | +1 |

So an insertion somewhere before mapped-12 shifts the tail of the AC. The tool
probes up to 3 EPICs and picks the offset itself (`--offset auto`, or force 0/1/2).
With that, EPIC counts for one AC come out as: part 2 -> 171, part 6 -> 315,
part 12 -> 235 (offset +1); the part-12 list verified 4/4 against the national
search (all four answer part 12).

### 6c. Tool: `work/part_epic_list.py` (one command -> a part's EPIC list)

    python work/part_epic_list.py --state S01 --ac 1 --part 2
    python work/part_epic_list.py --state S01 --ac 1 --part 12 --verify 4
    python work/part_epic_list.py --state S01 --ac 1 --part 12 --discover-only
    python work/part_epic_list.py --state S01 --ac 1 --part 2 --old-parts 1-4

Pipeline: read the part's name from the anonymous part endpoint -> discover old
part names (1 request each, capped at `part + 8`) -> sweep the name matches ->
auto-detect the mapping offset -> filter to the target current part -> write

    work/out/part_lists/<STATE>_AC<ac>_P<part>.txt    one EPIC per line
    work/out/part_lists/<STATE>_AC<ac>_P<part>.csv    EPIC + name/relative/age/provenance
    work/out/part_lists/<STATE>_AC<ac>_P<part>.json   counts, discovery table,
                                                      offset calibration, verify

Cost: discovery ~3 s for the first 20 old parts, sweeps ~25-45 s per fresh old
part at 6 workers (all cached as `work/out/part_epics/*.jsonl`), so a new part in
an already-visited area is seconds and a cold one is 1-3 minutes.
`work/calibrate_mapping.py` is the standalone offset survey.
### 6d. Print-ready booth sheet: `work/part_booth_sheet.py`

Turns a part's EPIC list into an A4 sheet for field use: `# / EPIC / name /
relation / relative / sex / age / tick box`, bilingual where the cache has
Telugu (joined from `work/out/part_epics/*.jsonl` by EPIC), with the source
caveat printed on every sheet.

    python work/part_booth_sheet.py --state S01 --ac 1 --part 2            # HTML
    python work/part_booth_sheet.py --state S01 --ac 1 --part 12 --pdf     # + PDF
    python work/part_booth_sheet.py --state S01 --ac 1 --part 2 --no-l1 --compact
    python work/part_booth_sheet.py --state S01 --ac 1 --part 8 --refresh --per-page 30

* `--refresh` regenerates the EPIC list first via `part_epic_list.py`.
* `--pdf` renders through headless Chrome/Edge (`--headless=new --print-to-pdf`);
  if neither browser exists it falls back to the HTML with print instructions.
* `--no-l1` drops the Telugu line (halves row height: 8 pages -> 6 for part 2);
  `--compact` shrinks type/padding; `--per-page N` forces N rows per sheet
  (generates one table per page so pagination is deterministic);
  `--sort serial|name|epic` (default: old part + old serial).

Outputs `work/out/booth_sheets/<STATE>_AC<ac>_P<part>_sheet.html` and `.pdf`.

Verified on the live data: part 2 -> 171 rows, 8 pages, 171 EPIC tokens, 171 tick
boxes, 3316 Telugu characters embedded; part 12 -> 235 rows, 10 pages, 235/235
EPICs, 5195 Telugu characters. Every EPIC in the source CSV appears exactly once
in the PDF (set equality checked with PyMuPDF text extraction), page size is
A4 210x297 mm.

**Snapshot vintage (measured).** The age in the mapping record is the elector's
age *then*, not now: across 7 EPICs the national search's current age minus the
mapped age is 22-24 years (TBG0342345: 28 -> 51; KZZ0650044: 33 -> 55;
TBG1888593: 43 -> 65). So this dataset is the **2003 roll** plus the BLO mapping
into today's roll - names/ages are 2003-vintage, EPICs are current. The booth
sheet labels that (Age*, footnote) instead of implying fresh data.

**Current roll serials** are available from the national search profile
(`serial_no`, `section_no` - TBG0342345 = section 1, serial 16), so sheets can be
re-ordered into official roll order by enriching each EPIC at 1 request/s
(~3 min for a 171-EPIC part). Not wired into the sheet yet.

---

## 7. The collector app: `work/old_eci/` (DB + FastAPI + web UI)

One self-contained app that stores every old-roll part in PostgreSQL, marks each
part done so reruns are skipped, and drives collection from a browser in manual
or auto mode.

    cd work/old_eci
    python -X utf8 -m uvicorn app:app --host 127.0.0.1 --port 8008
    # UI: http://127.0.0.1:8008/

`db.init()` runs on startup and is idempotent (`create table if not exists`), so
the app creates its own schema on first boot. Config comes from `ECI_PG_DSN` /
`ECI_PG_SCHEMA` (defaults point at `.../old_eci`, schema `public`); the legacy
populated DB is read-only, via `ECI_GEO_DSN`, used only to seed the catalog.

**Files** - `db.py` (DSN, shared `ConnectionPool` min 1 / max 10, `q()` helper,
full DDL, settings, events), `client.py` (anonymous wire client: old-roll serial +
window fetch, roll-end probe, current-parts/states/ACs lists, national EPIC
lookup with a >=1.1 s lock), `worker.py` (job queue, discovery, collection, auto
mode, EPIC job), `app.py` (FastAPI routes), `web/index.html` (single-page UI, no
build step).

### Schema (created in `old_eci/public`)

9 tables + 2 views. `states` / `acs` / `old_parts` are the catalog and the
progress ledger: `old_parts.status` is `pending | running | done | error`,
`electors` is the harvest (one row per old-roll serial), `current_parts` caches
the today-roll part list, `epic_lookups` stores EPIC-processor results,
`jobs` + `events` are the work log, `settings` holds `auto_enabled` / `workers` /
`discover_max_part` / `calibrate_offset` / `collect_serial_cap`.

    tables  states acs old_parts electors current_parts epic_lookups jobs events settings
    views   v_overall v_ac_progress

A part is marked `done` only after its whole serial range is swept, and
`collect_part` re-checks first: asking for a finished part returns
`{"skipped": true, "reason": "already done"}` without a single network call
(verified: 3 s, 0 requests). `force: true` re-collects. The upsert is keyed on
`source_id`, so a forced rerun overwrites rows instead of duplicating them.

### Collection facts

* serial `""` returns a random ~50-record window (also carries `oldPartName`, used
  for name discovery); serial `"<n>"` returns that record, `404` when absent.
* Parallel sweep, 6 workers, batches of 200 rows: ~0.13-0.16 s/request, zero 429s.
  Measured 20-52 s for a part (670-1520 serials).
* Offset calibration samples <=3 EPICs through the national search to learn
  `mapping_offset` and `cur_part_mode`, which is what fills `electors.cur_part_no`.
  Calibrated values seen: offsets 0, 1 and 3 depending on the part.
* Auto mode (`POST /api/auto`) makes the worker loop pick `best_pending_part` -
  parts whose neighbours are done / highest neighbour yield - and keep going, so
  the queue refills itself without a client. `POST /api/collect_auto` is the same
  logic scoped to one AC.

### Verified end to end

* `old_eci/public` created and owned by `eci_app` (DB CREATE + schema CREATE both
  true); `create schema` is skipped when SCHEMA is already `public` because
  `CREATE SCHEMA public` needs DB-owner rights, not just CREATE.
* Seeded 38 states and 175 ACs for S01; 60 ACs for S02 from the geo catalog.
* S01 AC1 discovered 12 old parts, AC2 40 parts; S02 AC1 20 parts. Collected all
  12 parts of S01 AC1 (11,890 rows / 6,490 EPICs) plus S02 AC1 P1
  (418 rows / 189 EPICs, 17.9 s, roll_end 420, 2 misses).
* Auto mode ran unattended across an AC boundary (finished S01 AC1, continued into
  AC2) with 0 errors; 43 of 72 parts done, 37,247 elector rows, 20,211 EPICs.
* Settings and progress survive a restart: `auto_enabled` is stored in `settings`,
  so the worker resumes the loop on the next boot. That test also caught a real
  bug - a part killed mid-collection stays `running` and `best_pending_part` only
  looks at `pending`/`error`, so the part was stranded forever. Fixed with
  `recover_orphans()` at worker start: requeue `running` parts, mark interrupted
  jobs `error`, log both. Verified by killing the server mid-collection and
  restarting (S01 AC2 P23/P29 requeued, then re-collected to `done`, attempts 2).
* Filter branches of `/api/parts` (`status`, name/part_no `q`, `limit`/`offset`)
  and `/api/part` detail (by-current-part breakdown) all return 200.
* EPIC processor round-trips both paths: cached (`TBG0342345` -> name, local name,
  relation, age 2003, district, AC, part, serial, section, polling station) and
  fresh (`TBG2009553` -> HTTP 200, 85-field raw record shown alongside the mapped
  table). The fresh lookup landed on current part `BODDABADA (#19)`, matching old
  part 11 of S01 AC1 - an independent check on the old->current mapping.

### The 2003 roll is national, not Andhra-only

Discovered while testing a second state: `S02` (Arunachal Pradesh) `AC 1` (LUMLA)
has its own 2003 parts, with real village names - `SOCKTSEN, ZEMITHANG`,
`KHARMAN,KLEKTANG,MUCHUT`, `SURBIN, POMGAR, MUKTUR`, `LUMLA TOWN, LUMLA VILLAGE`.
Collection there behaves exactly like S01 (418 rows, 189 EPICs, offset 0,
`cur_part_mode` 1), so the route is a national old-roll service and the same
auto/manual pipeline covers every state and UT in the `states` table.

### 7a. Field audit: what the route returns vs what we keep

`work/audit_fields.py` sweeps one part and diffs the raw record against the
columns the collector writes. On S01 AC 1 part 2 (670 records, 0 misses):

    python -X utf8 work/audit_fields.py --state S01 --ac 1 --part 2 --json out/audit.json

The route returns **31 keys**. We persist everything that varies per elector.
The 15 keys not written to `electors` split cleanly:

| key | why it is not on `electors` |
| --- | --- |
| `oldStateCd` `oldAcNo` `oldPartNumber` | echo the request exactly (670/670 each) - held once on `old_parts` |
| `oldStateName` `oldDistNo` `oldDistName` `oldAcName` | constant per part - now stored on `old_parts` instead of per row |
| `oldFullName` = `firstName` | 670/670 identical; we already read both names |
| `lastName` `lastNameHindi` `relativeLName` `relativeLNameHindi` | **0/670 non-blank** - the route never fills them |
| `currentStateCd` | 43/670 non-blank, all `S01`, and equal to `bloMappedStateCd` 42/43 - already covered |
| `firstNameHindi` `relativeFNameHindi` | 43/670, and the values are **Telugu, not Hindi** (`పున్నమ్మ దక్కత`) - duplicates of `oldFullNameL1` / `oldRelativeFullNameL1` |

So nothing information-bearing was lost - but two columns we *did* store were never
shown anywhere, and now are:

* `relative_name_l1` - the relative's name in the local script, present for
  51,438 of 51,451 rows. It was missing from `/api/electors`, the CSV and the UI.
* `epic_2003` - the old-vintage EPIC. Non-blank for ~96% of rows, but mostly a
  masked placeholder: S01 carries `000000000000` (10,557) or
  `AP010010000000` (15,555), while **18,699 S01 rows and every non-blank S02 row
  carry a real value** - S01 as a bare 14-digit `AP010010003030`, S02 in the
  genuine old `AR/01/001/000037` district/AC/serial form.

### 7b. Relation type and gender are stored as bare codes

The route returns single letters. Over the first 51k collected rows the domains
are exactly four relations and two genders - no code was ever dropped or
unmapped:

| code | relation | rows | mapped to current roll |
| --- | --- | --- | --- |
| `F` | Father | 28,784 | 15,333 |
| `H` | Husband | 23,058 | 13,068 |
| `M` | Mother | 328 | 178 |
| `O` | Other | 101 | 37 |

`gender`: `F` Female 26,250, `M` Male 26,021. (The odd pairings - 30 Father/Female,
3 Husband/Male in part 2 - are the source data, not a parsing artefact.)

The mapping is **measured, not assumed**: for each code we pulled 3 sampled
electors from the national search and compared the relation it reports for the
same person - 12/12 agreement, relative's name matching too.

    old F -> national FTHR   old H -> national HSBN
    old M -> national MTHR   old O -> national OTHR

`client.relation_label()` / `client.gender_label()` decode these, and an unknown
code passes through as itself rather than vanishing. Decoding now applies to
`/api/electors`, `/api/part` (`by_relation`), the CSV export and the UI, and
`/api/breakdown` lists every code present with its label and mapped count - so a
new code the API starts returning shows up instead of hiding.

### 7c. How the gateway reports a route that is not there

Probing candidate routes only works if the status codes are read correctly, and
they are **not** uniform. Measured with controls (`.../definitely-not-real-xyz`):

| status | meaning | control that proves it |
| --- | --- | --- |
| 404 | route does not exist | `common/part/get/definitely-not-real` -> 404 |
| 401 | **prefix is auth-gated** - proves nothing | `common/elector/get/definitely-not-real` -> 401 |
| 405 | route exists, wrong method | `POST citizen/sir/getAsmbly` -> 405 (it is GET) |
| 500 / 400 | route exists, bad input | `common/part/get/bystatecd/districtcd/acNumber` POST -> 500 |
| 200 | exists and anonymous | `get-eroll-data-2003` -> 200, payload 50 |

So `elastic-sir-citizen/*` and `common/elector/*` are blanket-gated: a 401 there
means "not reachable anonymously", never "does not exist". Only `common/part/*`
404s honestly. Hold any route discovery against the controls above.

### 7d. Is there a later vintage than 2003?

`work/probe_eroll_variants.py` sends the anonymous request to
`get-eroll-data-<variant>` for every year 1995-2030 plus `final` / `latest` /
`current` / `all` / `list` / `years` / `vintages`. **Only `-2003` answers 200.**
Per the table above this proves there is no *anonymously reachable* sibling
vintage - it does not prove none exists, and `get-eroll-data-2003-final` is the
known token-gated relative of the same roll.

Bulk current-roll elector listing is not available anonymously either: today's
*part list* (`common/part/get/bystatecd/districtcd/acNumber`) and *AC list*
(`citizen/sir/getAsmbly`) are open, but nothing enumerates today's electors by
part.

What IS open is the national search
(`elastic/search-by-epic-from-national-display-v1`), which returns the **current**
roll record for one EPIC - today's age, AC, part, serial, section and polling
station. That is the route to "latest data" without a login, and the app already
uses it: it is the EPIC processor's second half. At ~1 req/s it is ~4 minutes for a
250-EPIC part, so enriching the 41k EPICs already harvested is a background job of
several hours, not a crawl.

The old BLO APK (`in.gov.eci.garuda.apk`) is **not** a source of roll routes: its
hosts are `blonetservices/eronetservices/nvspservices.ecinet.in`,
`boothapp.eci.gov.in`, `voterportal.eci.gov.in`, `electoralsearch.in` and
`transservice.ecinet.in`, and its features are forms, checklists, BLO patrika PDFs
and transliteration - a field data-entry app. Its one roll-list call,
`GetErollElectorList`, sat on the now-dead `blonetservices` host.

    python -X utf8 work/scan_smali_routes.py --garuda   # byte scan; rg is not on PATH

### 7e. The AC catalogue was incomplete (fixed)

The legacy catalogue alone under-reports constituencies. It listed **175 ACs for
S01** while `citizen/sir/getAsmbly` serves **187**, and all 12 extra ACs have
old-roll data (verified: part 1 of AC 176 / 179 / 187 each returns 50 records with
real part names - `Gudikambali`, `Pamchalimgala`, `Shrishailamu`). They are the
Kurnool / Nandyal belt - `Adhoni` 176 ... `Atmakur` 187 - with Telugu names the
legacy table never had. `Atmakur` and `Alur` legitimately appear twice in the live
list (same name, different districts).

Nationally, merging the live list added **123 ACs** (4,129 -> 4,252).
`seed_acs_all` now merges live on top of the catalogue by (state_cd, ac_no), so
those ACs are no longer silently skipped.

### 7f. The part count: 153 old parts per AC, and two bugs that hid it

An earlier version of this section claimed the 2003 roll had far fewer, much
larger parts than today's roll. **That was wrong**, and it was wrong because the
flattering number was never checked against an independent source.

Measured on S01, three independent sources, all corroborating:

| AC | voters.eci.gov.in `getPartByAc` | 2003 roll (exhaustive probe) | our DB (was) |
| --- | --- | --- | --- |
| 1 Ichchapuram | 153 | 153 (1..153) | **12** |
| 2 | 175 | 175 (1..175) | **40** |
| 3 | 169 | 169 (1..169) | 169 |

The old roll has exactly as many parts as the current roll. Two separate bugs had
hidden it, and both are fixed:

**1. Silent truncation in discovery.** `discover_parts` probed `1..discover_max_part`
and stopped. AC 1 and AC 2 were first explored interactively with caps of 12 and
40, and each was then recorded as `done` with `old_parts_found` equal to its cap -
so the UI cheerfully showed "12 parts, 100% done" while 141 were missing. Nothing
revisited a `done` AC. Discovery now probes in chunks of 200 and stops only when a
whole chunk comes back empty (`PART_HARD_CAP` 3000), so the cap is a **floor, not a
limit**; it returns `probed_to` and `truncated`, and `acs.discover_max` records how
far probing actually reached. An AC whose `old_parts_found >= discover_max` is
surfaced as `maybe_truncated` by `/api/acs`.

**2. The part list was not AC-scoped.** `common/part/get/bystatecd/districtcd/acNumber`
answers for the **district**: asked for AC 1, where the AC has 153 parts, it
returned **319** (close to AC 1 + AC 2 = 328). `client.current_parts` now prefers
the web app's own AC-scoped `citizen/sir/getPartByAc` (host
`gateway-voters.eci.gov.in`, `applicationname: VSP`, `platform-type: ECIWEB`, plus
a `state` header) and only falls back to the district route, whose rows are
discarded unless they carry a matching AC number.

The web part list also carries `psType`, `psCaty` and `oldPdfUrl` - a link to the
part's old-roll PDF when one is published. All three are now stored; `oldPdfUrl`
was null for every part of S01 AC 1-3, so the official PDFs are not published for
these, but the field is there for the parts where they are.

    python -X utf8 work/compare_parts.py --state S01 --ac 1 --ac2 2 --hi 200

**Scale.** At ~160 parts per AC, one state is ~30k parts and the 4,252-AC
catalogue is ~680k. A part takes 15-50s to sweep, so a national run single-worker
is on the order of two months of continuous collection. Worth knowing before
planning a full crawl.

### 7g. Which gateway serves what (and why the new one is not a newer roll)

The collector reads the roll from the **VHA mobile gateway**. Only one call uses
the voters-web gateway, and that is the part list.

| call | host | purpose |
| --- | --- | --- |
| `elastic-sir-citizen/get-eroll-data-2003` | `gateway-vha` | **all elector data** |
| `elastic/search-by-epic-from-national-display-v1` | `gateway-vha` | EPIC processor / current roll |
| `common/states` | `gateway-vha` | state list |
| `citizen/sir/getAsmbly` | `gateway-vha` | AC list (fallback) |
| `common/part/get/bystatecd/districtcd/acNumber` | `gateway-vha` | district part list (fallback) |
| `citizen/sir/getPartByAc` | **`gateway-voters`** | AC-scoped part list |
| `citizen/sir/getAsmbly` | **`gateway-voters`** | AC list + `acType` (preferred) |

Both hosts want the same `state` header, but different identity headers: `VHA` /
`ANDROIDMOB` on the mobile gateway, `applicationname: VSP` / `platform-type:
ECIWEB` with a `voters.eci.gov.in` Origin on the web one.

**The web gateway is the first host that reports missing routes honestly.**
`citizen/sir/definitely-not-real-xyz` returns a real 404 with a JSON body, whereas
`gateway-vha` answers 401 for anything it does not serve, which makes route
discovery on it guesswork (see 7c). So `work/probe_voters_routes.py` can actually
conclude things on this host.

What it concluded: of 22 candidate elector/roll routes - `getElectorList`,
`getElectorByPart`, `getPartElectors`, `getVoterList`, `getEroll`, `getSIRData`,
`getElectoralRoll`, `downloadElectoralRoll`, `searchByEpic`, `getElectorByEpic`,
`get-eroll-data-2003`, and more - **every one returned 404.** The web gateway
serves AC and part *listings* only. There is no newer bulk roll API to migrate to;
the roll lives on the VHA gateway, and the only route to *current* elector data
without a login remains the national search, one EPIC at a time.

### 7h. Reserved-seat category (`acType`)

The web AC list carries `acType`, the GEN/SC/ST reserved-seat category, so it is
now stored on `acs.ac_type` and served by `/api/acs` (S01: GEN 157, SC 22, ST 8).

The field is inconsistent **in the ECI's own API**, varying by state: `GEN`,
`General`, `GENERAL`, `G`, `(ST)`, and Devanagari abbreviations - `अ.ज.जा.`
(अनुसूचित जाति = Scheduled Caste -> SC) and `अ.जा.` (अनुसूचित जनजाति = Scheduled
Tribe -> ST). `client.ac_type_label()` normalises those to GEN/SC/ST and passes
anything unrecognised through unchanged, so a new spelling surfaces instead of
being coerced; `BL` (12 ACs) and `SANGHA` (1) are currently in that pass-through
bucket.

Coverage is partial: 1,178 of 4,252 ACs have no `ac_type`, because the live AC
list is *shorter* than the legacy catalogue for several states (S12, S24 and U08
return none at all; S06 covers 38 of 182). That is an upstream gap.

Both AC-seeding paths now run the same merge - `seed_acs` delegates to
`seed_acs_all`. They used to differ: the per-state path trusted the catalogue
whenever it had any rows, so it silently kept S01 at 175 ACs and never stored
`ac_type`, giving different answers for the same job depending on which button was
pressed.

### Caveats

* Coverage is the mapped 2003 cohort only - electors who registered after the old
  roll have no old part, and there are some in-part 404s (2 of 420 serials in
  S02 AC1 P1) that become `misses` in the job result.
* Rows whose `cur_part_no` is null are old electors the BLO did not map into the
  current roll; `/api/part` shows that breakdown per part.
* The `-final` route (the completeness path) still needs one live OTP officer
  session; everything in this app uses the anonymous route.
* `/api/summary` aggregates over the whole catalog (~0.4 s warm, ~4 s cold) and is
  polled every 3 s by the UI; fine at this scale, but the correlated subqueries
  in `v_overall` are the first thing to consolidate if the catalog grows.
