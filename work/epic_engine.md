# EEPIC Engine

Turns an EPIC (voter EPIC number) into a machine-readable API-request contract by
reading the ECINET APK disassembly as ground truth.

## What it produces

For every EPIC entry point in the app, it emits a `requests[]` array. Each entry
contains:

- `id`, `name`, `verb`
- `host` (e.g. `https://gateway-vha.eci.gov.in/api/v1/`)
- `path` (e.g. `eepic/GetElectorDetailForEEPIC`)
- `query`, `headers`, `body`
- `response_model`
- `evidences`: smali file name, Android method annotation, and response model,
  so the request can be traced back to the APK disassembly

Endpoints discovered from `DataRepository.RestClient`:

| ID | Verb | Path | Response |
|----|------|------|----------|
| `public_voter_search` | GET | `https://electoralsearch.in/api/search` | Voter record (weak auth) |
| `eepic_search` | POST | `eepic/GetElectorDetailForEEPIC` | `ElectorDetails` |
| `eepic_card_pdf` | POST | `eepic/GetEEPICCard` | `ResponseBody` (PDF) |
| `verification` | POST | `vh_epicno_verify` | `VerifyEVPResponse` |

## Usage

```bash
python3 work/epic_engine.py --epic 123456789012 --secure-key <key>
python3 work/epic_engine.py --list-endpoints
```

Outputs JSON to stdout and writes `epic_contract.json` under `--out`
(default: `work/out`).

## Why `passKey` is not hardcoded

The weak voter-search endpoint computes:

```python
pass_key = SHA512(APP_CONST + secure_key)
```

with `APP_CONST = "IPS2062445"`. The `secure_key` is produced natively by the
`.so` layer and is **not** recoverable from this static analysis, so the engine
leaves it as an optional input. The public search is intentionally flagged as
unauthenticated regardless of that key.
