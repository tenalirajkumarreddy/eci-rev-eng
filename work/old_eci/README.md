# old_eci — collector for the ECI old (2003 / SIR) electoral roll

One app: PostgreSQL store + FastAPI backend + single-page web UI. It logs every
state / AC / old part, harvests the old roll per part, and marks each part `done`
so reruns are skipped. Manual and auto mode, plus an EPIC processor.

## Run

```bash
cd work/old_eci
python -X utf8 -m uvicorn app:app --host 127.0.0.1 --port 8008
# open http://127.0.0.1:8008/
```

The schema is created on startup (`create table if not exists`), so the first boot
is also the migration. Requires `psycopg`, `psycopg_pool`, `fastapi`, `uvicorn`,
`jinja2` (all present) — no SQLAlchemy.

## Config

| env | default | meaning |
| --- | --- | --- |
| `ECI_PG_DSN` | `postgresql://…@129.225.75.85:5432/old_eci` | the store (read/write) |
| `ECI_PG_SCHEMA` | `public` | schema to work in |
| `ECI_GEO_DSN` | `…/eci` | legacy populated DB, **read-only** catalog seed |

## Data model (`old_eci/public`)

| table | role |
| --- | --- |
| `states`, `acs` | catalog, seeded from the live/legacy catalog |
| `old_parts` | **the ledger**: one row per old part, `status` = `pending \| running \| done \| error`, plus `roll_end`, `records`, `epics`, `unmapped`, `mapping_offset`, `cur_part_mode` |
| `electors` | the harvest, one row per old-roll serial; `cur_epic`, `cur_ac_no`, `cur_part_no` added by calibration |
| `current_parts` | cached today-roll part list per AC |
| `epic_lookups` | EPIC-processor results (mapped columns + raw 85-field record) |
| `jobs`, `events` | work log and audit trail |
| `settings` | `auto_enabled`, `workers`, `discover_max_part`, `calibrate_offset`, `collect_serial_cap` |
| `v_overall`, `v_ac_progress` | progress views |

Rerun prevention: `collect_part` checks `status` first and returns
`{"skipped": true, "reason": "already done"} `— no network traffic. Send
`force: true` to re-collect; the upsert is keyed on `source_id`, so rows are
overwritten, never duplicated.

**Crash safety.** A part killed mid-collection (Ctrl-C, closing the window) would
otherwise stay `running` forever, because auto mode only picks up `pending` and
`error`. On startup the worker therefore requeues any `running` part, marks
interrupted jobs as `error` and logs both as events — so restarting the app
resumes instead of stranding work. Assumes one worker process per database;
re-collection is idempotent either way.

## Modes

* **Manual** — pick state → AC → part, press *collect*. `re-collect` forces it.
* **Auto** — tick *auto mode* (or `POST /api/auto {"enabled": true}`). The worker
  loop keeps picking `best_pending_part` (parts whose neighbours are done, highest
  neighbour yield) and continues on its own. `POST /api/collect_auto` does the same
  scoped to one AC.

## API

`/api/health` `/api/summary` `/api/events` `/api/states` `POST /api/states/seed`
`/api/acs` `POST /api/acs/seed` `POST /api/acs/seed_all` `POST /api/acs/discover`
`/api/parts` `/api/part` `/api/breakdown` `/api/current_parts` `POST /api/collect`
`POST /api/collect_auto` `POST /api/auto` `POST /api/settings` `/api/jobs`
`POST /api/jobs/{id}/cancel` `POST /api/worker/restart` `/api/electors`
`/api/export.csv` `POST /api/epic` `/api/epic/{epic}` `/api/epics`

### Relation type and gender

The route returns single-letter codes, so `F`/`H`/`M`/`O` mean Father, Husband,
Mother and Other, and gender is `M`/`F`. The mapping is measured against the
national search for the same person (12/12 agreement) - see
`client.RELATION_TYPES`. `relation_label()`/`gender_label()` decode them for the
API, the CSV export and the UI, and `/api/breakdown` lists every code actually
present so a new one shows up instead of hiding. An unrecognised code is passed
through unchanged rather than dropped.

`GET /api/breakdown?state=&ac=` returns the relation and gender domains with row
counts plus how many of each carry a current EPIC, alongside a coverage summary
(rows, rows with an EPIC, rows missing a name or a relative name).

## Source of the data

`POST https://gateway-vha.eci.gov.in/api/v1/elastic-sir-citizen/get-eroll-data-2003`
— anonymous, no token or captcha. Body
`{oldStateCd, oldAcNo, oldPartNo, oldPartSerialNo}`; serial `""` returns a random
~50-record window (also used for part-name discovery), serial `"<n>"` returns one
record and `404`s when absent. The old→current part correspondence is many-to-many
by village, and `bloMappedPartNo` can lag the published roll by +1 (self-calibrated
against the national EPIC search, which is rate-limited to ~1 req/s).

Names and ages in this dataset are **2003 vintage**; EPICs are current.

See `work/extra_endpoints.md` §6–7 for the full contract, findings and caveats.
