#!/usr/bin/env python3
"""
old_eci_collector.py - the whole old-roll project in ONE file. No local imports.

This is a self-contained port of the web app in work/old_eci/ (db.py + client.py +
worker.py + the EPIC processor from work/app_search.py). Drop it in Colab, press
run, and it catalogs, discovers and collects the ECI 2003/SIR roll on its own.

It writes to the SAME `old_eci` database as the web app, so the two are
interchangeable views of one queue: run either, or both. If the web app's worker
is live, pass --no-recover (otherwise this requeues whatever that worker has in
flight - harmless, because every write is an upsert keyed on the record id, but
it wastes a sweep). This script does not touch the shared `auto_enabled` setting,
so starting it never switches the web app's collector on as a side effect.

  * database      PostgreSQL `old_eci` (credentials embedded below)
  * routes        the anonymous 2003 roll route + the national EPIC search
  * auto mode     discovers the next AC, then collects the best pending part,
                  forever, and marks each part done so it is never re-collected
  * EPIC processor EPIC in -> current-roll details out

Colab usage (either one):

    !python old_eci_collector.py                # run forever in auto mode
    !python old_eci_collector.py --parts 20     # stop after 20 collected parts
    !python old_eci_collector.py --minutes 120  # stop after 2 hours

...or paste the whole file into a cell and run it (the bottom of the file calls
main() automatically - in a notebook it ignores the kernel's argv and goes
straight into auto mode; set OLD_ECI_NO_AUTORUN=1 to import it without running).
Missing packages install themselves on the first run.

Other modes:

    --status              print current DB state and exit
    --epic TBG0342345     EPIC processor: look one EPIC up and print the details
    --selftest            offline crypto + DB + route checks, nothing written
    --export out.csv      stream every collected elector to a CSV, then exit

Env overrides (optional): ECI_PG_DSN, ECI_PG_SCHEMA, ECI_GEO_DSN.
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import random
import re
import secrets
import statistics
import queue
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime


# ===========================================================================
# 0. dependencies - Colab has none of the DB driver, so install on first run
# ===========================================================================
def ensure_deps():
    """Import-check the third-party packages, pip-installing whatever is absent.

    Runs before the real imports below, so `psycopg` etc. can be imported at
    module level normally. On Colab only psycopg/psycopg_pool are usually
    missing; the rest are preinstalled and this is a no-op.
    """
    need = []
    for mod, pkg in (("psycopg", "psycopg[binary]"),
                     ("psycopg_pool", "psycopg_pool"),
                     ("requests", "requests"),
                     ("cryptography", "cryptography"),
                     ("certifi", "certifi")):
        try:
            __import__(mod)
        except ImportError:
            need.append(pkg)
    if not need:
        return
    print("[setup] installing missing packages: %s" % " ".join(need), flush=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", *need], check=False)


ensure_deps()

import certifi                                                    # noqa: E402
import psycopg                                                    # noqa: E402
import requests                                                   # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization   # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding      # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM     # noqa: E402
from psycopg.rows import dict_row                                  # noqa: E402
from psycopg_pool import ConnectionPool                            # noqa: E402


def log(msg):
    print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)


# ===========================================================================
# 1. configuration - credentials embedded, env can override
# ===========================================================================
DSN = os.environ.get(
    "ECI_PG_DSN", "postgresql://eci_app:Raj%40A2Nkufyg@129.225.75.85:5432/old_eci")
SCHEMA = os.environ.get("ECI_PG_SCHEMA", "public")
# Read-only catalogue source (the legacy `eci` DB): 38 states / ~4.1k ACs. Never
# written to; it is only a bootstrap for the state and AC lists.
GEO_DSN = os.environ.get(
    "ECI_GEO_DSN", "postgresql://eci_app:Raj%40A2Nkufyg@129.225.75.85:5432/eci")

# --- the two app constants the national EPIC search needs (from libnative_lib.so
# --- via work/app_keys.json). Device-verified; see work/app_search.py.
EPIC_TC = "P79vtNtk/WZaAXsQKCHClA"
EPIC_EXTERNAL = (
    "TUlJQklqQU5CZ2txaGtpRzl3MEJBUUVGQUFPQ0FROEFNSUlCQ2dLQ0FRRUFyYjcrK0J4TC9ZTjhP"
    "SWxuKzZGTDlHbnc1RE5tUS9WRlpYc3MrSitUdVF5SmM4OTFKYnFiaWp4WVFORWluMmMydStDbnBY"
    "cG9HUS8xZ1VTekRNSmVOUzNzTlNsSVV5a3AyZHQ3eEltL2NtVjRzWi9jNzY5dkN4VlJvc01mUmFa"
    "Sm5CQWFoK20xWDI2bEVobk9vMHdwQUI5VHhyOFJJeUJlNmg3UGlRV3lrZUplaDZVYWNPQkJYMjhr"
    "Z2txNyt2SmhXOEhnQjM4bHQzMlhSb2N6blJZd1M5THFSN1p3ZUZtUWhUcjErRUdycWlFS0NPQ3hN"
    "WWdIUjJTUWNrYjk2aFo5a1d6ZnpldW40YlVPNW9YS0pjaUxraVMxSWdLaWVBREV2WUxndTEyOVpJ"
    "cG4xSCs4SCs4aWtOTlZFVHFFRERNdHFjUWNRbVdwcEp2Y1dIYVhBcytmOFFJREFRQUI")

OLD_EROLL = ("https://gateway-vha.eci.gov.in/api/v1/"
             "elastic-sir-citizen/get-eroll-data-2003")
PART_API = ("https://gateway-vha.eci.gov.in/api/v1/"
            "common/part/get/bystatecd/districtcd/acNumber")
STATES_API = "https://gateway-vha.eci.gov.in/api/v1/common/states"
ASM_API = "https://gateway-vha.eci.gov.in/api/v1/citizen/sir/getAsmbly"
# The voters.eci.gov.in web service. Its part list is AC-scoped; the vha route
# above answers for the whole DISTRICT (asked for AC 1 of S01 it returns 319 rows
# where the AC has 153), so this one is preferred wherever available.
WEB_API = "https://gateway-voters.eci.gov.in/api/v1/"
PART_BY_AC = WEB_API + "citizen/sir/getPartByAc"
ASM_API_WEB = WEB_API + "citizen/sir/getAsmbly"
EPIC_ENDPOINT = ("https://gateway-vha.eci.gov.in/api/v1/"
                 "elastic/search-by-epic-from-national-display-v1")

APP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "applicationName": "VHA",
    "appName": "VHA",
    "channelidobo": "VHA",
    "platform-type": "ANDROIDMOB",
    "currentRole": "citizen",
    "User-Agent": "okhttp/4.9.2",
}
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
MOBILE_UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36")
CA_BUNDLE = certifi.where() if os.path.exists(certifi.where()) else True


# ===========================================================================
# 2. database - schema, migrations, pool, helpers
# ===========================================================================
def geo_q(sql, params=None):
    """Best-effort read of the legacy catalogue DB ([] when unavailable)."""
    try:
        with psycopg.connect(GEO_DSN, connect_timeout=10, row_factory=dict_row,
                             keepalives=1,
                             options="-c statement_timeout=60000") as c:
            with c.cursor() as cur:
                cur.execute(sql, params or ())
                return cur.fetchall()
    except Exception:
        return []


# The server is remote, so a NAT or firewall can drop a connection silently.
# Without keepalives that leaves a blocked read hanging forever; `statement_timeout`
# bounds any single query, so a dead peer raises instead of stalling the run.
CONN_KWARGS = {
    "connect_timeout": 15,
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 10,
    "keepalives_count": 5,
    "options": "-c statement_timeout=120000",
}

_pool = None


def _setup(conn):
    with conn.cursor() as cur:
        cur.execute("set search_path to %s, public" % SCHEMA)


def pool():
    """Shared pool - the DB is remote, so reconnecting per query is painful."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(DSN, min_size=1, max_size=10, timeout=30,
                               configure=_setup, reconnect_failed=None,
                               kwargs=dict(CONN_KWARGS, row_factory=dict_row,
                                           autocommit=True))
        _pool.wait(timeout=20)
    return _pool


def connect(autocommit=True):
    """Standalone (unpooled) connection for the long-lived collection loop."""
    conn = psycopg.connect(DSN, row_factory=dict_row, autocommit=autocommit,
                           **CONN_KWARGS)
    with conn.cursor() as cur:
        cur.execute("set search_path to %s, public" % SCHEMA)
    return conn


def q(sql, params=None, fetch="all"):
    with pool().connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params or ())
        if fetch == "all":
            return cur.fetchall()
        if fetch == "one":
            return cur.fetchone()
        return None


DDL = """
create table if not exists {s}.states (
  state_cd      text primary key,
  name          text,
  has_old_data  boolean default false,
  source        text default 'api',
  last_checked  timestamptz,
  created_at    timestamptz default now()
);

create table if not exists {s}.acs (
  state_cd        text not null,
  ac_no           integer not null,
  name            text,
  name_l1         text,
  district_cd     text,
  old_parts_found integer default 0,
  discover_status text default 'pending',
  discover_max    integer,
  discovered_at   timestamptz,
  last_error      text,
  created_at      timestamptz default now(),
  primary key (state_cd, ac_no)
);

create table if not exists {s}.old_parts (
  state_cd       text not null,
  ac_no          integer not null,
  part_no        integer not null,
  name           text,
  exists_        boolean default true,
  status         text not null default 'pending',
  priority       integer default 100,
  attempts       integer default 0,
  last_serial    integer default 0,
  roll_end       integer,
  records        integer default 0,
  epics          integer default 0,
  unmapped       integer default 0,
  cur_part_mode  integer,
  mapping_offset integer,
  started_at     timestamptz,
  finished_at    timestamptz,
  last_error     text,
  created_at     timestamptz default now(),
  updated_at     timestamptz default now(),
  primary key (state_cd, ac_no, part_no)
);
create index if not exists old_parts_queue_idx
  on {s}.old_parts (status, priority desc, state_cd, ac_no, part_no);
create index if not exists old_parts_cur_idx on {s}.old_parts (cur_part_mode);
-- Supports the picker's two correlated neighbour lookups. Without it the pick
-- full-scans every pending part twice and times out on a large table.
create index if not exists old_parts_neigh_idx
  on {s}.old_parts (state_cd, ac_no, status, part_no);

create table if not exists {s}.electors (
  source_id     text primary key,
  state_cd      text not null,
  ac_no         integer not null,
  part_no       integer not null,
  serial_no     integer,
  full_name     text,
  full_name_l1  text,
  relative_name text,
  relative_name_l1 text,
  relation_type text,
  gender        text,
  age_snapshot  integer,
  epic_2003     text,
  marked_by_blo text,
  cur_state_cd  text,
  cur_ac_no     integer,
  cur_part_no   integer,
  cur_epic      text,
  first_seen    timestamptz default now(),
  last_seen     timestamptz default now()
);
create index if not exists electors_epic_idx on {s}.electors (cur_epic);
create index if not exists electors_old_idx on {s}.electors (state_cd, ac_no, part_no, serial_no);
create index if not exists electors_cur_idx on {s}.electors (cur_state_cd, cur_ac_no, cur_part_no);
create index if not exists electors_name_idx on {s}.electors (lower(full_name));

create table if not exists {s}.current_parts (
  state_cd    text not null,
  ac_no       integer not null,
  part_no     integer not null,
  part_name   text,
  part_name_l1 text,
  part_id     bigint,
  district_cd text,
  fetched_at  timestamptz default now(),
  primary key (state_cd, ac_no, part_no)
);

create table if not exists {s}.epic_lookups (
  epic          text primary key,
  found         boolean,
  http_status   integer,
  hits          integer,
  name          text,
  name_local    text,
  relation      text,
  relation_local text,
  relation_type text,
  age           integer,
  gender        text,
  state_cd      text,
  state_name    text,
  district      text,
  ac_no         integer,
  ac_name       text,
  part_no       integer,
  part_name     text,
  part_name_l1  text,
  part_id       bigint,
  serial_no     integer,
  section_no    integer,
  ps_building   text,
  ps_building_l1 text,
  record_id     text,
  raw           jsonb,
  fetched_at    timestamptz default now()
);

create table if not exists {s}.jobs (
  id          bigserial primary key,
  kind        text not null,
  payload     jsonb not null default '{{}}',
  mode        text default 'manual',
  status      text default 'queued',
  priority    integer default 100,
  progress    jsonb default '{{}}',
  result      jsonb,
  error       text,
  cancel      boolean default false,
  device      text,
  created_at  timestamptz default now(),
  started_at  timestamptz,
  finished_at timestamptz
);
create index if not exists jobs_queue_idx on {s}.jobs (status, priority desc, id);
-- Per-device tasks: each device claims only its own queued jobs.
create index if not exists jobs_device_idx on {s}.jobs (device, status, priority desc, id);

create table if not exists {s}.events (
  id      bigserial primary key,
  ts      timestamptz default now(),
  level   text default 'info',
  source  text,
  message text,
  device  text
);
-- Per-device logs: every event carries the producing device's tag.
create index if not exists events_device_idx on {s}.events (device, id desc);

create table if not exists {s}.settings (
  key        text primary key,
  value      jsonb,
  updated_at timestamptz default now()
);

create or replace view {s}.v_overall as
select
  (select count(*) from {s}.states)                              as states,
  (select count(*) from {s}.acs)                                 as acs,
  (select count(*) from {s}.old_parts)                           as old_parts,
  (select count(*) from {s}.old_parts where status = 'done')     as done_parts,
  (select count(*) from {s}.old_parts where status = 'pending')  as pending_parts,
  (select count(*) from {s}.old_parts where status = 'running')  as running_parts,
  (select count(*) from {s}.old_parts where status = 'error')    as error_parts,
  (select coalesce(sum(records),0) from {s}.old_parts)           as records,
  (select coalesce(sum(epics),0) from {s}.old_parts)             as epics,
  (select count(*) from {s}.electors)                            as electors,
  (select count(distinct cur_epic) from {s}.electors
     where cur_epic is not null and cur_epic <> '')              as unique_epics,
  (select count(*) from {s}.epic_lookups)                        as epic_lookups;

create or replace view {s}.v_ac_progress as
select state_cd, ac_no,
       count(*)                                as parts,
       count(*) filter (where status='done')   as done,
       count(*) filter (where status='error')  as errors,
       count(*) filter (where status='pending')as pending,
       coalesce(sum(records),0)                as records,
       coalesce(sum(epics),0)                  as epics,
       max(finished_at)                        as last_finished
from {s}.old_parts
group by 1, 2;
"""

# `create table if not exists` never touches an existing table, so extra columns
# need their own idempotent statements.
MIGRATIONS = """
alter table {s}.old_parts add column if not exists old_state_name text;
alter table {s}.old_parts add column if not exists old_dist_no    text;
alter table {s}.old_parts add column if not exists old_dist_name  text;
alter table {s}.old_parts add column if not exists old_ac_name    text;
alter table {s}.acs add column if not exists ac_type text;
alter table {s}.current_parts add column if not exists ps_type     text;
alter table {s}.current_parts add column if not exists ps_caty    text;
alter table {s}.current_parts add column if not exists old_pdf_url text;
-- Multi-device coordination: which collector holds a part, and when an AC's
-- part discovery started (so a stale one can be reclaimed without stealing a
-- live device's work).
alter table {s}.old_parts add column if not exists claimed_by text;
alter table {s}.acs add column if not exists discover_started_at timestamptz;
-- Per-device logs and tasks: every event/job carries the tag of the device.
alter table {s}.events add column if not exists device text;
alter table {s}.jobs   add column if not exists device text;
"""

DEFAULTS = {
    "auto_enabled": True,
    "workers": 6,
    "parts_parallel": 2,
    "request_pause_ms": 0,
    "discover_max_part": 400,
    "calibrate_offset": True,
    "collect_serial_cap": 3000,
}


REQUIRED_TABLES = ("states", "acs", "old_parts", "electors", "current_parts",
                   "epic_lookups", "jobs", "events", "settings")
REQUIRED_COLUMNS = (("old_parts", "old_ac_name"), ("acs", "ac_type"),
                    ("current_parts", "old_pdf_url"),
                    ("old_parts", "claimed_by"),
                    ("acs", "discover_started_at"))


def _schema_ready(cur):
    """True when every table and migrated column already exists.

    Checked first because the DDL below takes ACCESS EXCLUSIVE locks, which
    deadlock against another collector's inserts while it is busy - observed for
    real as `DeadlockDetected` with the web app's worker running. Reading the
    catalogue takes no such lock, so an existing schema is left untouched.
    """
    cur.execute("""select count(*) c from information_schema.tables
                   where table_schema = %s and table_name = any(%s)""",
                (SCHEMA, list(REQUIRED_TABLES)))
    if cur.fetchone()["c"] < len(REQUIRED_TABLES):
        return False
    # Compared as text, not as a row type: psycopg cannot adapt a list of Python
    # tuples into an anonymous composite (`= any((t,c),(t,c))` raises
    # FeatureNotSupported).
    cur.execute("""select count(*) c from information_schema.columns
                   where table_schema = %s
                     and table_name || '.' || column_name = any(%s)""",
                (SCHEMA, ["%s.%s" % (t, c) for t, c in REQUIRED_COLUMNS]))
    return cur.fetchone()["c"] >= len(REQUIRED_COLUMNS)


def db_init(attempts=4):
    """Create schema, tables, views and settings rows. Fully idempotent."""
    last = None
    for i in range(attempts):
        try:
            with connect() as conn, conn.cursor() as cur:
                # `if not exists` is a cheap catalogue check, so this runs even
                # on an up-to-date schema (whose DDL is skipped below) - a
                # missing neighbour index makes the picker scan every pending
                # part twice and time out.
                cur.execute("create index if not exists old_parts_neigh_idx "
                            "on %s.old_parts (state_cd, ac_no, status, part_no)"
                            % SCHEMA)
                # Same reasoning for the per-device log / task indexes.
                cur.execute("create index if not exists events_device_idx "
                            "on %s.events (device, id desc)" % SCHEMA)
                cur.execute("create index if not exists jobs_device_idx "
                            "on %s.jobs (device, status, priority desc, id)"
                            % SCHEMA)
                if _schema_ready(cur):
                    cur.execute("""insert into settings(key, value)
                                   select k, v from jsonb_each(%s::jsonb) as e(k, v)
                                   on conflict (key) do nothing""",
                                (json.dumps(DEFAULTS),))
                    return
                if SCHEMA != "public":
                    cur.execute("create schema if not exists %s" % SCHEMA)
                cur.execute(DDL.format(s=SCHEMA))
                cur.execute(MIGRATIONS.format(s=SCHEMA))
                for k, v in DEFAULTS.items():
                    cur.execute("insert into settings(key, value) values (%s, %s) "
                                "on conflict (key) do nothing", (k, json.dumps(v)))
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            if i + 1 < attempts:
                log("schema init attempt %d/%d failed (%s: %s) - retrying"
                    % (i + 1, attempts, type(exc).__name__, str(exc).splitlines()[0]))
                time.sleep(1.5 * (i + 1))
    raise last


def setting(key, default=None, tag=None):
    """Per-device fallthrough: '<key>@<tag>' first, then the shared '<key>'.

    A '' or None tag means 'global only' (the pre-per-device behaviour), which
    keeps the engine's own status output and the web dashboard reading the
    fleet values."""
    if tag:
        row = q("select value from settings where key=%s", (key + "@" + tag,),
                fetch="one")
        if row:
            return row["value"]
    row = q("select value from settings where key=%s", (key,), fetch="one")
    return row["value"] if row else default


def set_setting(key, value, tag=None):
    key = key if (tag in (None, "")) else "%s@%s" % (key, tag)
    q("insert into settings(key,value) values (%s,%s) "
      "on conflict (key) do update set value=excluded.value, updated_at=now()",
      (key, json.dumps(value)), fetch=None)


def event(source, message, level="info", device=None):
    """Log a line stamped with this device's tag (defaults to THIS process's
    OLD_ECI_DEVICE_TAG), so each device can show its own feed."""
    try:
        q("insert into events(level, source, message, device) "
          "values (%s,%s,%s,%s)",
          (level, source, message,
           device if device is not None else globals().get("DEVICE_TAG", "")),
          fetch=None)
    except Exception:
        pass


# ===========================================================================
# 3. API client - anonymous old-roll route + catalog routes
# ===========================================================================
_local = threading.local()
_epic_lock = threading.Lock()
_epic_last = [0.0]


def _session(kind="app"):
    attr = "_s_" + kind
    if not hasattr(_local, attr):
        s = requests.Session()
        s.verify = CA_BUNDLE
        s.headers.update(APP_HEADERS if kind == "app" else WEB_HEADERS)
        setattr(_local, attr, s)
    return getattr(_local, attr)


def _post(url, body, timeout=30, retries=3):
    for attempt in range(retries):
        try:
            r = _session("app").post(url, json=body, timeout=timeout)
        except requests.RequestException:
            time.sleep(1.0 * (attempt + 1) * (0.5 + random.random()))
            continue
        if r.status_code == 429:
            # Jitter: several devices retrying the same rate-limited route must
            # not land in lockstep and re-trigger the limiter together.
            time.sleep(2.0 * (attempt + 1) * (0.5 + random.random()))
            continue
        try:
            payload = r.json()
        except ValueError:
            payload = None
        return r.status_code, payload
    return 0, None


def fetch_serial(state, ac, part, serial, timeout=30):
    """One serial of an old part -> (http_status, payload list)."""
    body = {"oldStateCd": state, "oldAcNo": str(ac), "oldPartNo": str(part),
            "oldPartSerialNo": str(serial)}
    status, payload = _post(OLD_EROLL, body, timeout=timeout)
    if status == 200 and isinstance(payload, dict):
        return status, payload.get("payload") or []
    return status, []


def fetch_window(state, ac, part, timeout=30):
    """A ~50-record window of an old part (also used for name discovery)."""
    return fetch_serial(state, ac, part, "", timeout=timeout)


def probe_roll_end(state, ac, part, hard_cap=3000, hint=0):
    """Highest serial that answers, +20 margin (30 if the part looks empty).

    `hint` seeds the probe with a neighbour part's roll_end: if the hint
    answers, the result is identical to the full ascending probe but skips the
    wasted low candidates; if the hint misses, the full probe runs.
    """
    cands = (50, 100, 200, 300, 400, 500, 650, 800, 1000, 1200, 1500, 2000, 2500)
    seq = cands
    last = 0
    if hint and int(hint) >= 50:
        h = min(int(hint), hard_cap)
        status, payload = fetch_serial(state, ac, part, h)
        if status == 200 and payload:
            last = h
            seq = tuple(c for c in cands if c > h)
    for cand in seq:
        if cand > hard_cap:
            break
        status, payload = fetch_serial(state, ac, part, cand)
        if status == 200 and payload:
            last = cand
        elif last and cand > last + 120:
            break
    return min(hard_cap, (last + 20) if last else 30)


def _rows(data):
    if isinstance(data, dict):
        for key in ("payload", "data", "result"):
            if isinstance(data.get(key), list):
                return data[key]
        return []
    return data if isinstance(data, list) else []


def current_parts(state, ac, timeout=30):
    """Current-roll parts of one AC, normalised to the fields we store.

    Prefers the web app's AC-scoped `getPartByAc`. The vha fallback answers for
    the whole district, so it is either filtered to rows naming this AC or
    discarded outright - that is what once made AC 1 look like it had 319 parts
    when it has 153.
    """
    def norm(x):
        dist = x.get("distNo", x.get("districtCd"))
        return {
            "acNumber": x.get("acNumber", ac),
            "partNumber": x.get("partNumber"),
            "partName": x.get("partName"),
            "partNameL1": x.get("partNameV1") or x.get("partNameL1"),
            "partId": x.get("id", x.get("partId")),
            "districtCd": None if dist is None else str(dist),
            "psType": x.get("psType"),
            "psCaty": x.get("psCaty"),
            "oldPdfUrl": x.get("oldPdfUrl"),
        }

    try:
        r = _session("web").get(PART_BY_AC, params={"Asmbly": str(ac)},
                                headers={"state": str(state)}, timeout=timeout)
        if r.status_code == 200:
            rows = [norm(x) for x in _rows(r.json()) if isinstance(x, dict)]
            rows = [x for x in rows if x["partNumber"] is not None]
            if rows:
                return rows
    except Exception:
        pass

    try:
        r = _session("app").get(PART_API, headers={"state": str(state)},
                                params={"stateCd": state, "acNumber": str(ac)},
                                timeout=timeout)
        rows = [x for x in _rows(r.json()) if isinstance(x, dict)]
    except Exception:
        return []
    if rows and all(x.get("acNumber") is not None for x in rows):
        rows = [x for x in rows if str(x.get("acNumber")) == str(ac)]
    elif rows:
        return []
    return [norm(x) for x in rows]


def store_current_parts(conn, state, ac):
    """Refresh the current-roll part list of one AC (best effort)."""
    rows = current_parts(state, ac)
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """insert into current_parts(state_cd, ac_no, part_no, part_name,
                    part_name_l1, part_id, district_cd, ps_type, ps_caty,
                    old_pdf_url, fetched_at)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
               on conflict (state_cd, ac_no, part_no) do update set
                 part_name=coalesce(excluded.part_name, current_parts.part_name),
                 part_name_l1=coalesce(excluded.part_name_l1, current_parts.part_name_l1),
                 part_id=coalesce(excluded.part_id, current_parts.part_id),
                 district_cd=coalesce(excluded.district_cd, current_parts.district_cd),
                 ps_type=coalesce(excluded.ps_type, current_parts.ps_type),
                 ps_caty=coalesce(excluded.ps_caty, current_parts.ps_caty),
                 old_pdf_url=coalesce(excluded.old_pdf_url, current_parts.old_pdf_url),
                 fetched_at=now()""",
            [(state, ac, int(r["partNumber"]), r.get("partName"),
              r.get("partNameL1"), _int(r.get("partId")), r.get("districtCd"),
              r.get("psType"), r.get("psCaty"), r.get("oldPdfUrl"))
             for r in rows if r.get("partNumber") is not None])
    return len(rows)


def states_live(timeout=30):
    try:
        r = _session("app").get(STATES_API, timeout=timeout)
        data = r.json()
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("payload") or data.get("data") or []
    out = []
    for row in data or []:
        cd = row.get("stateCd") or row.get("stateCode") or row.get("state_cd")
        nm = row.get("stateName") or row.get("name")
        if cd:
            out.append({"state_cd": cd, "name": nm})
    return out


# The reserved-seat category is spelled differently per state: 'GEN'/'General'/
# 'G'/'(ST)', and even Devanagari (अ.ज.जा. = Scheduled Caste, अ.जा. = Scheduled
# Tribe). Unknown spellings pass through unchanged rather than being coerced.
AC_TYPES = {
    "GEN": "GEN", "GENERAL": "GEN", "G": "GEN", "UR": "GEN", "UNRESERVED": "GEN",
    "SC": "SC", "(SC)": "SC", "अ.ज.जा.": "SC",
    "ST": "ST", "(ST)": "ST", "अ.जा.": "ST",
}


def ac_type_label(code):
    if code is None or str(code).strip() == "":
        return None
    raw = str(code).strip()
    return AC_TYPES.get(raw.upper(), raw)


def _ac_row(row):
    ac = (row.get("acNo") or row.get("asmblyNo") or row.get("ac_number")
          or row.get("assemblyNo"))
    if ac is None:
        return None
    dist = row.get("distNo", row.get("districtCd"))
    return {"ac_no": int(ac),
            "name": row.get("acNameV1") or row.get("acName") or row.get("ac_name"),
            "name_l1": row.get("acName"),
            "ac_type": ac_type_label(row.get("acType")),
            "district_cd": None if dist is None else str(dist)}


def acs_live(state, timeout=30):
    """Assembly constituencies of a state (web gateway first: it has `acType`)."""
    try:
        r = _session("web").get(ASM_API_WEB, headers={"state": str(state)},
                                timeout=timeout)
        if r.status_code == 200:
            out = [x for x in (_ac_row(row) for row in _rows(r.json())) if x]
            if out:
                return out
    except Exception:
        pass
    try:
        r = _session("app").get(ASM_API, headers={"state": str(state)}, timeout=timeout)
        data = r.json()
    except Exception:
        return []
    return [x for x in (_ac_row(row) for row in _rows(data) if isinstance(row, dict)) if x]


# ------------------------------------------------------------- labels / mapping
# The old-roll route returns single-letter relation codes. Resolved against the
# national search for the same person (12/12 agreement on F->FTHR, H->HSBN,
# M->MTHR, O->OTHR, relative names matching too), so the mapping is measured.
# 50k collected rows never produced a code outside these four.
RELATION_TYPES = {
    "F": ("FTHR", "Father"),
    "H": ("HSBN", "Husband"),
    "M": ("MTHR", "Mother"),
    "O": ("OTHR", "Other"),
}
GENDER_TYPES = {"M": "Male", "F": "Female", "T": "Third gender", "O": "Other"}


def relation_label(code, short=False):
    if code is None or code == "":
        return None
    hit = RELATION_TYPES.get(str(code).strip().upper())
    if not hit:
        return code          # a new code shows up instead of vanishing
    return hit[0] if short else hit[1]


def gender_label(code):
    if code is None or code == "":
        return None
    return GENDER_TYPES.get(str(code).strip().upper(), code)


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ===========================================================================
# 4. EPIC processor - national display search (no captcha, from the APK smali)
# ===========================================================================
def _b64d(text):
    text = (text or "").strip()
    return base64.b64decode(text + "=" * (-len(text) % 4))


def _try_decode_value(text, want):
    """Values pass through `new String(Base64.decode(native))` in the app, so an
    extracted constant may need one or two Base64 unwraps."""
    for _ in range(2):
        try:
            raw = _b64d(text)
        except Exception:
            return None
        if len(raw) in want:
            return raw
        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError:
            return None
        if not re.fullmatch(r"[A-Za-z0-9+/=]+", text):
            return None
    return None


def _decode_variants(text):
    out = []
    current = (text or "").strip()
    for _ in range(3):
        try:
            raw = _b64d(current)
        except Exception:
            break
        out.append(raw)
        try:
            current = raw.decode("ascii").strip()
        except UnicodeDecodeError:
            break
        if not re.fullmatch(r"[A-Za-z0-9+/=]+", current):
            break
    return out


def gpk(primary, tc):
    """KGn.gPK: primary + ':' + timestamp + ':' + 6 digits, AES-GCM, zero IV."""
    ts = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    rand6 = "".join(secrets.choice("1234567890") for _ in range(6))
    plaintext = ("%s:%s:%s" % (primary, ts, rand6)).encode("utf-8")
    key = _try_decode_value(tc, (16, 24, 32))
    if key is None:
        raise ValueError("tc does not decode to a 16/24/32-byte AES key")
    return base64.b64encode(AESGCM(key).encrypt(b"\x00" * 16, plaintext, None)).decode()


def load_public_key(external):
    for blob in _decode_variants(external):
        if len(blob) < 64:
            continue
        try:
            key = serialization.load_der_public_key(blob)
        except Exception:
            continue
        if hasattr(key, "encrypt"):
            return key
    raise ValueError("external is not a Base64 X.509/SPKI RSA public key")


def epic_fetch(epic, timeout=40):
    """Fire the real captcha-less national search and return the raw result."""
    epic = " ".join(str(epic).split()).upper()
    inner = {"captchaData": "na", "captchaId": "na", "epicNumber": epic,
             "securityKey": gpk(epic, EPIC_TC)}
    aes_key = AESGCM.generate_key(bit_length=256)
    iv = secrets.token_bytes(12)
    ciphertext = AESGCM(aes_key).encrypt(iv, json.dumps(inner).encode("utf-8"), None)
    wrapped = load_public_key(EPIC_EXTERNAL).encrypt(
        aes_key, padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),
                              algorithm=hashes.SHA256(), label=None))
    body = {"encryptedKey": base64.b64encode(wrapped).decode(),
            "iv": base64.b64encode(iv).decode(),
            "encryptedPayload": base64.b64encode(ciphertext).decode()}
    headers = dict(APP_HEADERS, **{"device-id": str(uuid.uuid4()),
                                  "User-Agent": MOBILE_UA})
    req = urllib.request.Request(EPIC_ENDPOINT, method="POST",
                                data=json.dumps(body).encode())
    for k, v in headers.items():
        req.add_header(k, v)
    status, raw = 0, ""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, raw = resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001 - reported as a result, not raised
        return {"epic": epic, "status": 0, "raw": "%s: %s" % (type(e).__name__, e),
                "hits": []}
    try:
        hits = json.loads(raw) if raw.strip().startswith("[") else []
    except json.JSONDecodeError:
        hits = []
    return {"epic": epic, "status": status, "raw": raw, "hits": hits}


def epic_lookup(epic, timeout=40, min_interval=1.1):
    """National EPIC search, rate-limited to ~1 req/s across threads."""
    with _epic_lock:
        wait = min_interval - (time.time() - _epic_last[0])
        if wait > 0:
            time.sleep(wait)
        try:
            report = epic_fetch(epic, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            return {"epic": epic, "status": 0, "hits": 0, "raw": "",
                    "error": "%s: %s" % (type(exc).__name__, exc)}
        _epic_last[0] = time.time()
    hits = report.get("hits") or []
    content = (hits[0].get("content") or {}) if hits else {}
    return {"epic": report.get("epic", epic), "status": report.get("status", 0),
            "hits": len(hits), "content": content, "raw": report.get("raw", "")}


def profile_from_content(content):
    """Map a national-display record to flat, storable fields."""
    def g(*keys):
        for k in keys:
            v = content.get(k)
            if v not in (None, ""):
                return v
        return None
    return {
        "name": g("fullName", "applicantFirstName"),
        "name_local": g("fullNameL1", "applicantFirstNameL1"),
        "relation": g("relativeFullName", "relationName"),
        "relation_local": g("relativeFullNameL1", "relationNameL1"),
        "relation_type": g("relationType"),
        "relation_label": relation_label(g("relationType")),
        "age": g("age"),
        "gender": g("gender"),
        "state_cd": g("stateCd"),
        "state_name": g("stateName"),
        "district": g("districtValue"),
        "ac_no": g("acNumber"),
        "ac_name": g("asmblyName"),
        "part_no": g("partNumber"),
        "part_name": g("partName"),
        "part_name_l1": g("partNameL1"),
        "part_id": g("partId"),
        "serial_no": g("partSerialNumber"),
        "section_no": g("sectionNo"),
        "ps_building": g("psbuildingName", "buildingAddress"),
        "ps_building_l1": g("psBuildingNameL1", "buildingAddressL1"),
        "record_id": g("id"),
    }


EPIC_UPSERT = """
insert into epic_lookups(epic, found, http_status, hits, name, name_local,
        relation, relation_local, relation_type, age, gender, state_cd, state_name,
        district, ac_no, ac_name, part_no, part_name, part_name_l1, part_id,
        serial_no, section_no, ps_building, ps_building_l1, record_id, raw)
values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
        %s,%s,%s,%s,%s,%s)
on conflict (epic) do update set
  found=excluded.found, http_status=excluded.http_status, hits=excluded.hits,
  name=excluded.name, name_local=excluded.name_local,
  relation=excluded.relation, relation_local=excluded.relation_local,
  relation_type=excluded.relation_type, age=excluded.age,
  gender=excluded.gender, state_cd=excluded.state_cd,
  state_name=excluded.state_name, district=excluded.district,
  ac_no=excluded.ac_no, ac_name=excluded.ac_name, part_no=excluded.part_no,
  part_name=excluded.part_name, part_name_l1=excluded.part_name_l1,
  part_id=excluded.part_id, serial_no=excluded.serial_no,
  section_no=excluded.section_no, ps_building=excluded.ps_building,
  ps_building_l1=excluded.ps_building_l1, record_id=excluded.record_id,
  raw=excluded.raw, fetched_at=now()
"""


def epic_lookup_store(epic):
    """EPIC processor: search, map, persist, return the profile."""
    res = epic_lookup(epic)
    prof = profile_from_content(res.get("content") or {})
    q(EPIC_UPSERT,
      (res.get("epic"), bool(res.get("hits")), res.get("status"), res.get("hits"),
       prof["name"], prof["name_local"], prof["relation"], prof["relation_local"],
       prof["relation_type"], _int(prof["age"]), prof["gender"], prof["state_cd"],
       prof["state_name"], prof["district"], _int(prof["ac_no"]), prof["ac_name"],
       _int(prof["part_no"]), prof["part_name"], prof["part_name_l1"],
       _int(prof["part_id"]), _int(prof["serial_no"]), _int(prof["section_no"]),
       prof["ps_building"], prof["ps_building_l1"], prof["record_id"],
       json.dumps(res.get("content") or {})), fetch=None)
    event("epic", "lookup %s: %s" % (res.get("epic"),
          "found" if res.get("hits") else "no record (%s)" % res.get("status")))
    return {"epic": res.get("epic"), "found": bool(res.get("hits")),
            "status": res.get("status"), "hits": res.get("hits"),
            "profile": prof}


# ===========================================================================
# 5. collector - catalog, discovery, collection, auto loop
# ===========================================================================
def recover_orphans():
    """Requeue work whose holder is gone - STALE rows only, never a part that a
    live PC process, phone or web-app worker is sweeping this minute.

    A part being collected touches its row (batch flush + last_serial) every 200
    serials, so 'running and not updated for 10 minutes' means the holder is
    gone: crash, killed process, closed laptop. Runs at startup AND every ~60s
    from the auto loop, so any surviving device picks up a dead device's parts
    live. `--no-recover` still disables it entirely. Re-collecting is safe: the
    elector upsert is keyed on `source_id`.
    """
    try:
        parts = q("""update old_parts set status='pending', last_serial=0,
                            claimed_by=null, updated_at=now()
                     where status='running'
                       and updated_at < now() - interval '10 minutes'
                     returning state_cd, ac_no, part_no,
                               extract(epoch from now()-started_at)::int as age_s""")
    except Exception:
        parts = []
    try:
        jobs = q("""update jobs set status='error', finished_at=now(),
                           error='interrupted'
                    where status='running'
                      and started_at < now() - interval '30 minutes'
                    returning id""")
    except Exception:
        jobs = []
    try:
        # Same hazard one level up: a discovery killed mid-probe leaves the AC
        # marked running, and the AC picker only looks at pending/error. Only
        # stale discoveries are reclaimed; discover_started_at makes the
        # distinction exact for rows written by this or any newer collector.
        acs = q("""update acs set discover_status='pending'
                   where discover_status='running'
                     and (discover_started_at is null or
                          discover_started_at < now() - interval '30 minutes')
                   returning state_cd, ac_no""")
    except Exception:
        acs = []
    if acs:
        log("requeued %d AC(s) left mid-discovery by a previous process: %s"
            % (len(acs), ", ".join("%s AC%s" % (a["state_cd"], a["ac_no"])
                                   for a in acs[:12])))
    if parts:
        log("requeued %d part(s) left running by a previous process: %s"
            % (len(parts), ", ".join("%s AC%s P%s (%ss in)" % (p["state_cd"], p["ac_no"],
                                                               p["part_no"],
                                                               p.get("age_s"))
                                     for p in parts)))
        if any((p.get("age_s") or 999) < 600 for p in parts):
            log("  note: one of those started recently - if the web app's worker "
                "is live on this database, run with --no-recover so it is not "
                "interrupted (two collectors on one DB work, they just overlap).")
    return {"parts": len(parts or []), "jobs": len(jobs or []),
            "acs": len(acs or [])}


def seed_states():
    rows = []
    pub = geo_q("select state_cd, name from public.states")
    if pub:
        rows = [{"state_cd": r["state_cd"], "name": r["name"], "source": "db"}
                for r in pub]
    if not rows:
        rows = [{"state_cd": r["state_cd"], "name": r["name"], "source": "api"}
                for r in states_live()]
    for r in rows:
        q("insert into states(state_cd,name,source) values (%s,%s,%s) "
          "on conflict (state_cd) do update set name=coalesce(excluded.name, states.name)",
          (r["state_cd"], r["name"], r["source"]), fetch=None)
    return {"states": len(rows)}


def seed_acs_all(conn, live=True, state_cd=None):
    """Seed ACs from the legacy catalogue merged with the live list.

    The catalogue alone is NOT complete: it lists 175 ACs for S01 while
    `citizen/sir/getAsmbly` serves 187, and all 12 extra ACs have old-roll data.
    Taking it at face value silently skipped them (123 nationally).
    """
    merged = {}
    geo_sql = ("select state_cd, ac_number, ac_name, district_cd from public.acs "
               + ("where state_cd=%s " if state_cd else "")
               + "order by state_cd, ac_number")
    for r in geo_q(geo_sql, (state_cd,) if state_cd else None):
        merged[(r["state_cd"], int(r["ac_number"]))] = {
            "name": r.get("ac_name"), "name_l1": None,
            "district_cd": r.get("district_cd")}
    from_legacy = len(merged)

    live_only = 0
    if live:
        state_rows = ([{"state_cd": state_cd}] if state_cd
                      else q("select state_cd from states order by state_cd"))
        for s in state_rows:
            sc = s["state_cd"]
            try:
                rows = acs_live(sc)
            except Exception as exc:  # noqa: BLE001 - catalogue is best effort
                event("catalog", "live AC list failed for %s: %s" % (sc, exc),
                      level="warn")
                continue
            for r in rows:
                key = (sc, int(r["ac_no"]))
                if key not in merged:
                    merged[key] = {}
                    live_only += 1
                cur_ = merged[key]
                cur_["name"] = r.get("name") or cur_.get("name")
                cur_["name_l1"] = r.get("name_l1") or cur_.get("name_l1")
                cur_["ac_type"] = r.get("ac_type") or cur_.get("ac_type")
                cur_["district_cd"] = r.get("district_cd") or cur_.get("district_cd")

    if not merged:
        return {"acs": 0, "source": "none"}
    with conn.cursor() as cur:
        cur.executemany(
            """insert into acs(state_cd, ac_no, name, name_l1, district_cd, ac_type)
               values (%s,%s,%s,%s,%s,%s)
               on conflict (state_cd, ac_no) do update set
                 name=coalesce(excluded.name, acs.name),
                 name_l1=coalesce(excluded.name_l1, acs.name_l1),
                 ac_type=coalesce(excluded.ac_type, acs.ac_type),
                 district_cd=coalesce(excluded.district_cd, acs.district_cd)""",
            [(sc, ac, v.get("name"), v.get("name_l1"), v.get("district_cd"),
              v.get("ac_type")) for (sc, ac), v in sorted(merged.items())])
    states = len({sc for sc, _ in merged})
    event("catalog", "seeded %d ACs across %d states (%d from the legacy catalogue, "
          "%d only in the live list)" % (len(merged), states, from_legacy, live_only))
    return {"acs": len(merged), "states": states, "from_legacy": from_legacy,
            "live_only": live_only}


# Part numbers are probed in chunks and discovery stops only when a whole chunk
# comes back empty, so the cap is a FLOOR, not a limit. Stopping exactly at a cap
# truncates silently: S01 AC 1 was once recorded as "12 parts, done", hiding 141
# real parts until a cross-check against the live part list caught it.
PART_CHUNK = 200
PART_HARD_CAP = 3000


def _worker_count(workers=None):
    """Resolve the concurrency to use. 0/None means "whatever the DB says".

    A bare `workers or setting(...)` is not enough: 0 is falsy but also a valid
    thing for a caller to pass by accident, and ThreadPoolExecutor(0) raises.
    """
    n = int(workers or 0)
    if n <= 0:
        n = int(setting("workers", 6, tag=DEVICE_TAG) or 6)
    return max(1, min(n, 32))


def discover_parts(conn, state, ac, max_part=None, workers=None):
    floor = int(max_part or setting("discover_max_part", 400, tag=DEVICE_TAG))
    workers = _worker_count(workers)
    q("update acs set discover_status='running', discover_max=%s, last_error=null, "
      "discover_started_at=now() "
      "where state_cd=%s and ac_no=%s", (floor, state, ac), fetch=None)

    def probe(n):
        status, payload = fetch_window(state, ac, n)
        name = None
        for rec in payload or []:
            if rec.get("oldPartName"):
                name = rec["oldPartName"]
                break
        exists = bool(payload) or status == 200
        if exists and not payload:
            for serial in (1, 25, 100):
                _st, pl = fetch_serial(state, ac, n, serial)
                if pl:
                    return n, True, pl[0].get("oldPartName")
            return n, False, None
        return n, exists, name

    found = {}
    probed_to = 0
    start = 1
    while start <= PART_HARD_CAP:
        end = min(start + PART_CHUNK - 1, PART_HARD_CAP)
        if probed_to < floor:
            end = min(max(end, floor), PART_HARD_CAP)
        hits = 0
        with ThreadPoolExecutor(max_workers=workers) as tp:
            for n, exists, name in tp.map(probe, range(start, end + 1)):
                if exists:
                    found[n] = name
                    hits += 1
        probed_to = end
        if hits == 0 and probed_to >= floor:
            break
        start = end + 1

    truncated = bool(found.get(probed_to)) and probed_to >= PART_HARD_CAP
    with conn.cursor() as cur:
        cur.executemany("""insert into old_parts(state_cd, ac_no, part_no, name,
                                                  exists_, status)
                           values (%s,%s,%s,%s,true,'pending')
                           on conflict (state_cd, ac_no, part_no)
                           do update set name=coalesce(excluded.name, old_parts.name),
                                         exists_=true, updated_at=now()""",
                        [(state, ac, n, name) for n, name in sorted(found.items())])
    q("update states set has_old_data=true, last_checked=now() where state_cd=%s",
      (state,), fetch=None)
    q("update acs set discover_status='done', old_parts_found=%s, discover_max=%s, "
      "discovered_at=now() where state_cd=%s and ac_no=%s",
      (len(found), probed_to, state, ac), fetch=None)
    event("catalog", "discover %s AC %s: %d old parts (probed 1..%s%s)"
          % (state, ac, len(found), probed_to,
             " - TRUNCATED at the hard cap" if truncated else ""))
    return {"found": len(found), "probed_to": probed_to, "truncated": truncated}


ELECTOR_UPSERT = """
insert into electors(source_id, state_cd, ac_no, part_no, serial_no,
        full_name, full_name_l1, relative_name, relative_name_l1, relation_type,
        gender, age_snapshot, epic_2003, marked_by_blo,
        cur_state_cd, cur_ac_no, cur_part_no, cur_epic, last_seen)
values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
on conflict (source_id) do update set
  serial_no=excluded.serial_no, full_name=excluded.full_name,
  full_name_l1=excluded.full_name_l1, relative_name=excluded.relative_name,
  relative_name_l1=excluded.relative_name_l1, relation_type=excluded.relation_type,
  gender=excluded.gender, age_snapshot=excluded.age_snapshot,
  epic_2003=excluded.epic_2003, marked_by_blo=excluded.marked_by_blo,
  cur_state_cd=excluded.cur_state_cd, cur_ac_no=excluded.cur_ac_no,
  cur_part_no=excluded.cur_part_no, cur_epic=excluded.cur_epic,
  last_seen=now()
"""


def _row_tuple(rec, state, ac, part):
    return (rec.get("id"), state, ac, part,
            _int(rec.get("oldPartSerialNo")),
            rec.get("oldFullName") or rec.get("firstName"),
            rec.get("oldFullNameL1"),
            rec.get("oldRelativeFullName") or rec.get("relativeFName"),
            rec.get("oldRelativeFullNameL1"),
            rec.get("relationType"), rec.get("gender"), _int(rec.get("age")),
            rec.get("epicNumber"), rec.get("markedByBlo"),
            rec.get("bloMappedStateCd"), _int(rec.get("bloMappedAcNo")),
            _int(rec.get("bloMappedPartNo")), rec.get("bloMappedEpicNo"))


# Identity written into old_parts.claimed_by when this collector claims a part,
# so the dashboard can see which PC process, phone or Colab runtime holds what.
WORKER_ID = "colab-%s-%s" % (os.uname().nodename if hasattr(os, "uname") else "host",
                             os.getpid())
# Per-device settings tag: 'key@<tag>' overrides the shared 'key', so this
# runtime's workers/parts_parallel/calibrate never change the PC's or the
# phone's. Stable across runs on purpose (a Colab hostname is random per
# session) - one predictable row per fleet, overridable with
# OLD_ECI_DEVICE_TAG. WORKER_ID keeps the pid: claims stay per-process, while
# knobs stay per-fleet.
DEVICE_TAG = os.environ.get("OLD_ECI_DEVICE_TAG", "colab")


def _mark_running(state, ac, part):
    """Atomically claim a part for collection.

    Returns False when another collector already has it running. With parallel
    part-workers, or the web app / phone / another Colab on one database,
    whoever sees `running` first loses the race - and that must be *visible* (a
    skip in the log), not a silent double sweep of the same part. `claimed_by`
    records which device holds it.
    """
    with pool().connection() as conn, conn.cursor() as cur:
        cur.execute("""insert into old_parts(state_cd, ac_no, part_no, status,
                       started_at, attempts, claimed_by)
                       values (%s,%s,%s,'running', now(), 1, %s)
                       on conflict (state_cd, ac_no, part_no) do update set
                         status='running', started_at=now(),
                         attempts=old_parts.attempts+1, last_error=null,
                         claimed_by=excluded.claimed_by, updated_at=now()
                       where old_parts.status <> 'running'
                       returning state_cd""", (state, ac, part, WORKER_ID))
        return cur.fetchone() is not None


def collect_part(conn, state, ac, part, force=False, workers=None, cancel=None):
    """Full serial sweep of one old part, then mark it done (never rerun)."""
    row = q("select * from old_parts where state_cd=%s and ac_no=%s and part_no=%s",
            (state, ac, part), fetch="one")
    if row and row["status"] == "done" and not force:
        return {"skipped": True, "reason": "already done",
                "records": row["records"], "epics": row["epics"]}
    if not _mark_running(state, ac, part):
        return {"skipped": True, "reason": "already running elsewhere"}
    try:
        return _collect_inner(conn, state, ac, part, workers, cancel)
    except BaseException as exc:  # noqa: BLE001
        # A part must never be left `running` by a failure: the picker only looks
        # at pending/error, so a stranded part is invisible forever. Hand it back
        # to the queue with the reason attached, then let the caller decide.
        try:
            q("""update old_parts set status='pending', last_error=%s,
                    claimed_by=null, updated_at=now()
                    where state_cd=%s and ac_no=%s and part_no=%s
                      and status='running'""",
              ("%s: %s" % (type(exc).__name__, exc), state, ac, part), fetch=None)
        except Exception:
            pass
        raise


def _collect_inner(conn, state, ac, part, workers=None, cancel=None):
    workers = _worker_count(workers)
    t0 = time.time()
    cap = int(setting("collect_serial_cap", 3000, tag=DEVICE_TAG) or 3000)
    # Seed the roll-end probe from finished neighbours of the same AC: one DB
    # read usually replaces ~10 of the ~13 sequential probe requests.
    # probe_roll_end falls back to the full probe when the hint misses, so the
    # answer matches an unhinted probe.
    hint = 0
    nb = q("""select max(roll_end) as r from old_parts
              where state_cd=%s and ac_no=%s and status='done'
                and roll_end is not null and abs(part_no - %s) <= 3""",
           (state, ac, part), fetch="one")
    if nb and nb["r"]:
        hint = int(nb["r"])
    roll_end = probe_roll_end(state, ac, part, hard_cap=cap, hint=hint)
    # Publish roll_end before sweeping: it is the denominator for live progress
    # and ETA, and without it a reader only sees progress from the first 200
    # serials onward (last_serial is written in the same 200-serial batch).
    q("""update old_parts set roll_end=%s, updated_at=now()
         where state_cd=%s and ac_no=%s and part_no=%s""",
      (roll_end, state, ac, part), fetch=None)
    stats = {"records": 0, "epics": 0, "hits": 0, "misses": 0, "errors": 0}
    seen_ids = set()
    pending_rows = []
    meta = {}
    cancelled = False
    # Live speed: `last_mark`/`last_i` window the requests, so the rate printed
    # below is the current speed rather than an average since the part began.
    last_mark, last_i = t0, 0

    def flush():
        if not pending_rows:
            return
        with conn.cursor() as cur:
            cur.executemany(ELECTOR_UPSERT, pending_rows)
        pending_rows.clear()

    def one(serial):
        return fetch_serial(state, ac, part, serial)

    with ThreadPoolExecutor(max_workers=workers) as tp:
        for i, (st, payload) in enumerate(
                tp.map(one, range(1, roll_end + 1)), start=1):
            if st == 200 and payload:
                stats["hits"] += 1
                for rec in payload:
                    if rec.get("id") in seen_ids:
                        continue
                    seen_ids.add(rec["id"])
                    stats["records"] += 1
                    if rec.get("bloMappedEpicNo"):
                        stats["epics"] += 1
                    if not meta:
                        meta.update(old_state_name=rec.get("oldStateName"),
                                    old_dist_no=str(rec.get("oldDistNo") or "") or None,
                                    old_dist_name=rec.get("oldDistName"),
                                    old_ac_name=rec.get("oldAcName"))
                    pending_rows.append(_row_tuple(rec, state, ac, part))
            elif st == 404:
                stats["misses"] += 1
            else:
                stats["errors"] += 1
            if i % 200 == 0:
                flush()
                now = time.time()
                win = max(now - last_mark, 0.01)
                log("    %s AC %s P%s: %d/%d serials  %.0f req/s (window)  "
                    "%d records  %.1f rec/s (part avg)"
                    % (state, ac, part, i, roll_end, (i - last_i) / win,
                       stats["records"], stats["records"] / max(now - t0, 0.01)))
                last_mark, last_i = now, i
                q("""update old_parts set last_serial=%s, records=%s, epics=%s,
                        old_state_name=coalesce(%s, old_state_name),
                        old_dist_no=coalesce(%s, old_dist_no),
                        old_dist_name=coalesce(%s, old_dist_name),
                        old_ac_name=coalesce(%s, old_ac_name),
                        exists_=true, updated_at=now()
                        where state_cd=%s and ac_no=%s and part_no=%s""",
                  (i, stats["records"], stats["epics"], meta.get("old_state_name"),
                   meta.get("old_dist_no"), meta.get("old_dist_name"),
                   meta.get("old_ac_name"), state, ac, part), fetch=None)
                if cancel and cancel():
                    cancelled = True
                    break
        flush()

    # ---- calibration: how far does the mapping's part numbering lag the live roll?
    offset = cur_part_mode = None
    if setting("calibrate_offset", True) and stats["epics"]:
        sample = q("""select cur_epic, cur_part_no from electors
                      where state_cd=%s and ac_no=%s and part_no=%s
                        and cur_epic is not null and cur_epic <> ''
                      order by random() limit 3""", (state, ac, part))
        deltas, parts = [], []
        for s in sample or []:
            res = epic_lookup(s["cur_epic"])
            live_part = (res.get("content") or {}).get("partNumber")
            if live_part is not None and s["cur_part_no"] is not None:
                deltas.append(int(live_part) - int(s["cur_part_no"]))
            if s["cur_part_no"] is not None:
                parts.append(int(s["cur_part_no"]))
        if deltas:
            offset = int(statistics.median(deltas))
        if parts:
            cur_part_mode = Counter(parts).most_common(1)[0][0]

    return _finish_part(conn, state, ac, part, stats, roll_end, offset,
                        cur_part_mode, meta, t0, cancelled)


def _finish_part(conn, state, ac, part, stats, roll_end, offset, cur_part_mode,
                 meta, t0, cancelled):
    """Final status write + speed math. Split from _collect_inner so a parallel
    runner can finalise several parts without racing over each other's rows."""
    status = "pending" if cancelled else "done"
    q("""update old_parts set status=%s, finished_at=now(), claimed_by=null,
            records=%s, epics=%s,
            unmapped=%s, roll_end=%s, mapping_offset=%s, cur_part_mode=%s,
            old_state_name=coalesce(%s, old_state_name),
            old_dist_no=coalesce(%s, old_dist_no),
            old_dist_name=coalesce(%s, old_dist_name),
            old_ac_name=coalesce(%s, old_ac_name), updated_at=now()
            where state_cd=%s and ac_no=%s and part_no=%s""",
      (status, stats["records"], stats["epics"], stats["records"] - stats["epics"],
       roll_end, offset, cur_part_mode, meta.get("old_state_name"),
       meta.get("old_dist_no"), meta.get("old_dist_name"), meta.get("old_ac_name"),
       state, ac, part), fetch=None)
    secs = time.time() - t0
    result = dict(stats, roll_end=roll_end, offset=offset,
                  cur_part_mode=cur_part_mode, seconds=round(secs, 1),
                  req_per_sec=round(roll_end / max(secs, 0.01), 1),
                  records_per_sec=round(stats["records"] / max(secs, 0.01), 1),
                  cancelled=cancelled)
    if not cancelled:
        event("collect", "%s AC %s part %s: %d records, %d EPICs (%ss)"
              % (state, ac, part, stats["records"], stats["epics"], result["seconds"]))
    return result


BEST_PART_SQL = """
select p.state_cd, p.ac_no, p.part_no, p.name,
       (select count(*) from old_parts d
         where d.state_cd=p.state_cd and d.ac_no=p.ac_no and d.status='done'
           and abs(d.part_no - p.part_no) <= 2)                      as neighbours_done,
       coalesce((select avg(d.epics) from old_parts d
         where d.state_cd=p.state_cd and d.ac_no=p.ac_no and d.status='done'
           and abs(d.part_no - p.part_no) <= 2), 0)                  as neighbour_yield
from old_parts p
where p.status = any(%s) and coalesce(p.exists_, true)
  {filters}
-- Per-device tie-break (see worker.py BEST_PART_SQL): the deterministic
-- ranking made every collector pick the same top part at the same moment;
-- hashing the part identity with this device's tag spreads the equally-good
-- frontier parts across devices. The real keys (done neighbours, yield) still
-- dominate, so devices stay on the same AC but stop colliding on one part.
order by neighbours_done desc, neighbour_yield desc,
         md5(p.state_cd || ':' || p.ac_no::text || ':' || p.part_no::text || %s),
         p.state_cd, p.ac_no, p.part_no
limit 1
"""


def best_pending_part(state=None, ac=None, force=False, exclude=()):
    """Best part to collect next: neighbours already done, then highest yield.

    This is what "auto mode" means - it picks by itself and continues, so a run
    left alone walks the whole roll part by part. In `force` mode already-done
    parts are eligible too, oldest-finish first, skipping ones this run already
    re-collected (`exclude`) so the loop still terminates.
    """
    filters, params = "", []
    # The base query already restricts status, so this is its first parameter -
    # keeping the order statuses, state, ac, exclusions.
    params.append(["pending", "error", "done"] if force else ["pending", "error"])
    if state:
        filters += " and p.state_cd = %s"
        params.append(state)
    if ac is not None:
        filters += " and p.ac_no = %s"
        params.append(int(ac))
    if force and exclude:
        filters += " and (p.state_cd, p.ac_no, p.part_no) not in (%s)" % ",".join(
            ["(%s,%s,%s)"] * len(exclude))
        for e in exclude:
            params.extend(e)
    params.append(DEVICE_TAG)
    sql = BEST_PART_SQL.format(filters=filters)
    if force:
        sql = sql.replace(
            "order by neighbours_done desc, neighbour_yield desc, p.state_cd",
            "order by p.finished_at asc nulls first, p.state_cd")
    return q(sql, params, fetch="one")


def best_pending_parts(state=None, ac=None, limit=1, exclude=()):
    """The best `limit` collectable parts at once (thread-safe via _mark_running).

    Same ranking as the single picker (neighbours done first, then yield), so
    parallel part-workers naturally fan out across one AC's neighbourhood. The
    caller marks each pick running before it sweeps; a claim lost to another
    collector shows up as a visible skip, not a double sweep.
    """
    filters, params = "", []
    # The template's WHERE already restricts status; this is its first parameter.
    params.append(["pending", "error"])
    if state:
        filters += " and p.state_cd = %s"
        params.append(state)
    if ac is not None:
        filters += " and p.ac_no = %s"
        params.append(int(ac))
    if exclude:
        filters += " and (p.state_cd, p.ac_no, p.part_no) not in (%s)" % ",".join(
            ["(%s,%s,%s)"] * len(exclude))
        for e in exclude:
            params.extend(e)
    params.append(DEVICE_TAG)
    sql = BEST_PART_SQL.format(filters=filters)
    sql = sql.replace("limit 1", "limit %d" % max(1, int(limit)))
    return q(sql, params) or []


def pending_count(state=None, ac=None):
    sql = "select count(*) c from old_parts where status in ('pending','error')"
    params = []
    if state:
        sql += " and state_cd=%s"
        params.append(state)
    if ac is not None:
        sql += " and ac_no=%s"
        params.append(int(ac))
    return q(sql, params, fetch="one")["c"]


def next_ac_to_discover(state=None, ac=None):
    sql = ("select state_cd, ac_no, name from acs "
           "where discover_status in ('pending','error')")
    params = []
    if state:
        sql += " and state_cd=%s"
        params.append(state)
    if ac is not None:
        sql += " and ac_no=%s"
        params.append(int(ac))
    sql += " order by state_cd, ac_no limit 1"
    return q(sql, params, fetch="one")


# ------------------------------------------------------------------ enriching
def enrich_epics(conn=None, limit=20, state=None):
    """Run harvested EPICs nobody has looked up yet through the national search.

    ~1 req/s, so this is a background nicety rather than part of a sweep.
    """
    sql = """select distinct e.cur_epic from electors e
             left join epic_lookups l on l.epic = e.cur_epic
             where e.cur_epic is not null and e.cur_epic <> ''
               and l.epic is null"""
    params = []
    if state:
        sql += " and e.state_cd = %s"
        params.append(state)
    sql += " order by e.cur_epic limit %s"
    params.append(int(limit))
    rows = q(sql, params)
    for r in rows:
        res = epic_lookup_store(r["cur_epic"])
        log("enrich %s -> %s" % (r["cur_epic"],
                                 "found" if res["found"] else "no record"))
    return {"epics": len(rows)}


# ------------------------------------------------------------------ reporting
def status_report():
    o = q("select * from v_overall", fetch="one") or {}
    disc = q("""select count(*) filter (where discover_status='done') done,
                       count(*) total from acs""", fetch="one") or {}
    print("\n=== old_eci: %s ===" % DSN.split("@")[-1])
    print("  states %-6s ACs %-6s (discovered %s/%s)"
          % (o.get("states"), o.get("acs"), disc.get("done"), disc.get("total")))
    print("  old parts %-6s done %-6s pending %-6s running %-6s error %-5s"
          % (o.get("old_parts"), o.get("done_parts"), o.get("pending_parts"),
             o.get("running_parts"), o.get("error_parts")))
    print("  elector rows %-8s unique EPICs %-8s epic lookups %-6s"
          % (o.get("electors"), o.get("unique_epics"), o.get("epic_lookups")))
    print("  records (with duplicates) %s" % o.get("records"))
    top = q("""select state_cd, count(*) parts,
                      count(*) filter (where status='done') done
               from old_parts group by 1 order by 2 desc limit 5""")
    for t in top:
        print("    %s: %s parts, %s done" % (t["state_cd"], t["parts"], t["done"]))
    rel = q("""select coalesce(relation_type,'?') rt, count(*) c from electors
               group by 1 order by 2 desc""")
    if rel:
        print("  relations: " + ", ".join(
            "%s=%s (%s)" % (r["rt"], r["c"], relation_label(r["rt"])) for r in rel))
    print()


def export_csv(path, state=None, limit=None):
    """Stream every collected elector to CSV (relations and genders decoded)."""
    sql = """select state_cd, ac_no, part_no, serial_no, full_name, full_name_l1,
                    relative_name, relative_name_l1, relation_type, gender,
                    age_snapshot, epic_2003, cur_epic, cur_state_cd, cur_ac_no,
                    cur_part_no
             from electors %s order by state_cd, ac_no, part_no, serial_no"""
    params = []
    if state:
        sql = sql % "where state_cd=%s"
        params.append(state)
    else:
        sql = sql % ""
    if limit:
        sql += " limit %d" % int(limit)
    conn = connect(autocommit=False)
    n = 0
    try:
        cur = conn.cursor(name="exp", row_factory=dict_row)
        cur.itersize = 5000
        cur.execute(sql, params)
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = None
            for row in cur:
                row = dict(row)
                row["relation"] = relation_label(row.get("relation_type"))
                row["gender_label"] = gender_label(row.get("gender"))
                if w is None:
                    w = csv.DictWriter(fh, fieldnames=list(row.keys()))
                    w.writeheader()
                w.writerow(row)
                n += 1
        cur.close()
    finally:
        conn.close()
    log("exported %d rows -> %s" % (n, path))
    return n


# ------------------------------------------------------------------ auto loop
class Runner:
    """The auto loop: catalog -> discover -> collect -> repeat until stopped."""

    def __init__(self, args=None):
        self.a = args
        self.workers = _worker_count(getattr(args, "workers", 0))
        self.parts_done = 0
        self.rows = 0
        self.epics = 0
        self.serials = 0
        self.errors = 0
        self.discoveries = 0
        self.stop = threading.Event()
        self.attempted = set()
        self.conn = None
        self.inflight = {}          # (state, ac, part) -> ThreadPoolExecutor
        self.results = queue.Queue()
        self._harvest = {}          # (state, ac, part) -> threading.Event
        self.t0 = time.time()
    def cancel(self):
        return self.stop.is_set()

    def _worker_run(self, pick):
        """Body of one part-worker thread (single-part pool => one at a time).

        Runs the sweep on its own pooled DB connection so concurrent part-writers
        never share one connection (psycopg connections are not thread-safe).
        Failures are logged and the part handed back to pending via collect_part's
        error path; the runner notices the empty slot and backfills.
        """
        key = (pick["state_cd"], pick["ac_no"], pick["part_no"])
        try:
            conn = connect()
            try:
                res = collect_part(conn, pick["state_cd"], pick["ac_no"],
                                   pick["part_no"], force=self.a.force,
                                   workers=self.workers, cancel=self.cancel)
                res.setdefault("name", pick.get("name"))
                self.results.put((key, res))
            finally:
                try:
                    conn.close()
                except Exception:
                    pass
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            self.results.put((key, {"error": "%s: %s" % (type(exc).__name__, exc)}))

    def _drain(self, block=False):
        """Move finished results from the queue to the counters + log."""
        while True:
            try:
                key, res = self.results.get_nowait()
            except queue.Empty:
                return
            self.inflight.pop(key, None)
            self._harvest.pop(key, None)
            if res.get("skipped"):
                log("skip %s AC %s P%s (%s)"
                    % (key[0], key[1], key[2], res.get("reason", "skipped")))
                continue
            if res.get("error"):
                self.errors += 1
                log("error %s AC %s P%s: %s"
                    % (key[0], key[1], key[2], res["error"]))
                continue
            self.parts_done += 1
            self.rows += res.get("records", 0)
            self.epics += res.get("epics", 0)
            self.serials += res.get("roll_end", 0)
            if res.get("errors"):
                self.errors += res["errors"]
            log("%-4s AC %-4s P%-5s %-22s %5d rows %5d EPICs  %6.1f req/s "
                "%5.1f rec/s  roll_end=%-5s %ss"
                % (key[0], key[1], key[2],
                   (res.get("name") or "")[:22], res.get("records", 0),
                   res.get("epics", 0), res.get("req_per_sec", 0) or 0,
                   res.get("records_per_sec", 0) or 0, res.get("roll_end"),
                   res.get("seconds")))

    def _collect_one(self, part):
        """Serial path: one part, this thread (the original loop)."""
        key = (part["state_cd"], part["ac_no"], part["part_no"])
        # No pre-claim here on purpose: collect_part claims the part itself
        # (_mark_running) once its sweep starts. Claiming in both places makes
        # the second claim lose against the first and every part ends up
        # skipped as "already running elsewhere" - that bug was already dug out
        # once, don't reintroduce it.
        res = collect_part(self._conn(), part["state_cd"], part["ac_no"],
                           part["part_no"], force=self.a.force,
                           workers=self.workers, cancel=self.cancel)
        self.attempted.add(key)
        if res.get("skipped"):
            log("skip %s AC %s P%s (already done)"
                % (key[0], key[1], key[2]))
            return "work"
        self.parts_done += 1
        self.rows += res.get("records", 0)
        self.epics += res.get("epics", 0)
        if res.get("errors"):
            self.errors += res["errors"]
        self.serials += res.get("roll_end", 0)
        log("%-4s AC %-4s P%-5s %-22s %5d rows %5d EPICs  %6.1f req/s "
            "%5.1f rec/s  roll_end=%-5s %ss"
            % (key[0], key[1], key[2], (part.get("name") or "")[:22],
               res.get("records", 0), res.get("epics", 0),
               res.get("req_per_sec", 0) or 0, res.get("records_per_sec", 0) or 0,
               res.get("roll_end"), res.get("seconds")))
        return "work"

    def _conn(self):
        """One long-lived connection for bulk writes; reopened if it dies."""
        if self.conn is None:
            self.conn = connect()
        return self.conn

    def drop_conn(self):
        try:
            if self.conn is not None:
                self.conn.close()
        except Exception:
            pass
        self.conn = None

    def summary(self, reason):
        o = q("select * from v_overall", fetch="one") or {}
        mins = (time.time() - self.t0) / 60.0
        elapsed = max(time.time() - self.t0, 0.01)
        log("stopped (%s). this run: %d parts, %d rows, %d EPICs, %d errors, "
            "%d ACs discovered, %.1f min"
            % (reason, self.parts_done, self.rows, self.epics, self.errors,
               self.discoveries, mins))
        log("this run's average speed: %.1f req/s (%.0f/min), %.1f rows/s "
            "(%.0f/min), %.1f parts/hour"
            % (self.serials / elapsed, self.serials / elapsed * 60,
               self.rows / elapsed, self.rows / elapsed * 60,
               self.parts_done / elapsed * 3600))
        log("database total: %s/%s parts done, %s rows, %s unique EPICs"
            % (o.get("done_parts"), o.get("old_parts"), o.get("electors"),
               o.get("unique_epics")))

    def step(self):
        """One iteration. Returns 'work' if it did something, else 'idle'."""
        a = self.a
        pending = pending_count(a.state, a.ac)

        # Discovery is only run when the buffer of collectable parts is low, so
        # collection keeps flowing instead of cataloguing 4k ACs first. The
        # buffer is measured inside the requested scope, so `--state/--ac` works
        # as a targeted run instead of waiting on the global backlog.
        if not a.no_discover and pending < a.discover_ahead:
            ac = next_ac_to_discover(a.state, a.ac)
            if ac:
                log("discovering %s AC %s %s (pending parts: %d)"
                    % (ac["state_cd"], ac["ac_no"], ac["name"] or "", pending))
                res = discover_parts(self._conn(), ac["state_cd"], ac["ac_no"],
                                     workers=self.workers)
                self.discoveries += 1
                log("  -> %d old parts (probed 1..%s)%s"
                    % (res["found"], res["probed_to"],
                       " TRUNCATED" if res["truncated"] else ""))
                try:
                    ncur = store_current_parts(self._conn(), ac["state_cd"],
                                               ac["ac_no"])
                    if ncur:
                        log("  -> %d current-roll parts refreshed" % ncur)
                except Exception as exc:  # noqa: BLE001
                    event("catalog", "current parts refresh failed: %s" % exc,
                          level="warn")
                return "work"

        if a.parts_parallel <= 1:
            # One part at a time: the simple loop. `workers` is the concurrency
            # that matters here - it parallelises serials *within* the part.
            part = best_pending_part(a.state, a.ac, force=a.force,
                                     exclude=sorted(self.attempted))
            if part:
                return self._collect_one(part)
        else:
            # Several parts at once. Each part-worker still splits its own part
            # across `workers` serial-threads, so the gateway sees at most
            # parts_parallel * workers concurrent requests. The pipeline fills:
            # claim up to the width, harvest whatever finishes, and immediately
            # backfill the freed slot.
            while len(self.inflight) < a.parts_parallel and not self.stop.is_set():
                picks = best_pending_parts(
                    a.state, a.ac, limit=a.parts_parallel - len(self.inflight),
                    exclude=sorted(self.attempted))
                if not picks:
                    break
                claimed = False
                for p in picks:
                    key = (p["state_cd"], p["ac_no"], p["part_no"])
                    self.attempted.add(key)
                    # No pre-claim here: collect_part itself claims the part
                    # (_mark_running) once its worker thread starts. Claiming in
                    # both places used to make collect_part lose the race against
                    # its own runner and skip every part as "already running".
                    claimed = True
                    self.inflight[key] = ThreadPoolExecutor(max_workers=1)
                    self.inflight[key].submit(self._worker_run, p)
                if not claimed:
                    break
            if self.inflight:
                done = threading.Event()
                key = next(iter(self.inflight))
                self._harvest[key] = done
                done.wait(timeout=5)
                self._drain()
                return "work"

        if a.enrich:
            n = enrich_epics(limit=a.enrich, state=a.state)
            if n["epics"]:
                log("enriched %d EPICs" % n["epics"])
                return "work"
        if a.force:
            # Force mode walks the scope once; when the picker runs dry there is
            # nothing left to re-collect, so finish instead of idling.
            return "done"
        return "idle"

    def run(self):
        a = self.a
        if a.no_init:
            log("skipping schema init (--no-init): assuming the tables exist")
        else:
            db_init()
        if a.workers:
            # Scoped to this fleet's tag: an explicit --workers must not change
            # the PC's or the phone's concurrency.
            set_setting("workers", int(a.workers), tag=DEVICE_TAG)
        # NOTE: this deliberately does NOT touch settings.auto_enabled. That flag
        # belongs to the web app's worker, and commending it from here used to
        # turn the web app's collector on as a side effect of starting this script
        # (two collectors, twice the gateway load, from one command).
        if a.no_recover:
            log("skipping orphan recovery (--no-recover): useful when another "
                "collector, e.g. the web app's worker, is live on this database")
        else:
            recover_orphans()
        log("database ready: %s (schema %s)" % (DSN.split("@")[-1], SCHEMA))
        n_states = q("select count(*) c from states", fetch="one")["c"]
        n_acs = q("select count(*) c from acs", fetch="one")["c"]
        if not n_states:
            log("seeding states: %s" % seed_states())
        if not n_acs:
            log("seeding ACs for every state (one call)...")
            log("  -> %s" % seed_acs_all(self._conn()))
        else:
            log("catalogue: %s states, %s ACs" % (n_states, n_acs))

        log("auto mode: discovering ACs as the collectable buffer drops below %d"
            % a.discover_ahead)
        deadline = self.t0 + a.minutes * 60 if a.minutes else None
        idle_since = None
        last_reap = 0.0
        while not self.stop.is_set():
            if deadline and time.time() > deadline:
                self.summary("time limit reached")
                return 0
            if a.parts and self.parts_done >= a.parts:
                self.summary("part limit reached")
                return 0
            # Live reaper: hand back parts whose holder died (crash, killed
            # process, closed laptop). Stale-only, so a live device's part is
            # never stolen; every ~60s so any survivor picks up dead work.
            if time.time() - last_reap >= 60.0:
                last_reap = time.time()
                if not a.no_recover:
                    try:
                        recover_orphans()
                    except Exception as exc:  # noqa: BLE001
                        event("worker", "reap failed: %s" % exc, level="warn")
            try:
                out = self.step()
            except KeyboardInterrupt:
                raise
            except Exception as exc:  # noqa: BLE001 - keep going, log, retry
                self.errors += 1
                event("collect", "loop error: %s" % exc, level="error")
                log("loop error (continuing): %s: %s" % (type(exc).__name__, exc))
                self.drop_conn()
                time.sleep(5)
                continue
            if out == "work":
                idle_since = None
                continue
            if out == "done":
                self.summary("force pass complete")
                return 0
            # idle
            if idle_since is None:
                idle_since = time.time()
                tail = ("no AC left to discover" if a.no_discover
                        else "nothing pending and every AC discovered")
                log("idle - %s" % tail)
            elif a.idle_exit and time.time() - idle_since > a.idle_exit * 60:
                self.summary("idle timeout")
                return 0
            time.sleep(15)
        self.summary("stop requested")
        return 0


# ===========================================================================
# 6. modes / CLI
# ===========================================================================
def run_selftest():
    """Offline checks: crypto round-trip, DB, schema, route reachability."""
    ok = True
    print("--- EPIC crypto (offline) ---")
    tc_synth = base64.b64encode(secrets.token_bytes(32)).decode()
    token = gpk("SXQ2097129", tc_synth)
    plain = AESGCM(_b64d(tc_synth)).decrypt(b"\x00" * 16, _b64d(token), None).decode()
    good = bool(re.fullmatch(r"SXQ2097129:\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}:\d{6}",
                            plain))
    print("  gpk round-trip: %s (%s)" % ("OK" if good else "FAIL", plain))
    ok &= good
    key = load_public_key(EPIC_EXTERNAL)
    print("  external RSA key: OK (%s bits)" % key.key_size)
    print("  embedded tc decodes: %s" % bool(_try_decode_value(EPIC_TC, (16, 24, 32))))

    print("--- labels (offline) ---")
    labels = [relation_label(c) for c in ("F", "H", "M", "O", "X")]
    print("  F/H/M/O/X -> %s" % labels)
    ok &= labels == ["Father", "Husband", "Mother", "Other", "X"]
    print("  genders -> %s" % [gender_label(c) for c in ("M", "F", "T", "Z")])
    print("  ac_type SANGHA passthrough -> %s" % ac_type_label("SANGHA"))

    print("--- database ---")
    db_init()
    o = q("select * from v_overall", fetch="one")
    print("  connected: %s schema=%s" % (DSN.split("@")[-1], SCHEMA))
    print("  states=%s acs=%s old_parts=%s done=%s electors=%s unique_epics=%s"
          % (o["states"], o["acs"], o["old_parts"], o["done_parts"],
             o["electors"], o["unique_epics"]))
    ok &= bool(o["states"])

    print("--- live routes ---")
    st, payload = fetch_serial("S01", 1, 1, 1)
    print("  get-eroll-data-2003 S01/AC1/P1 serial 1: HTTP %s, %d record(s)"
          % (st, len(payload)))
    ok &= (st == 200 and len(payload) >= 1)
    print("  states_live: %d" % len(states_live()))
    print("  acs_live(S01): %d (expect 187)" % len(acs_live("S01")))
    print("  current_parts(S01, 1): %d" % len(current_parts("S01", 1)))

    print("\nSELFTEST_%s" % ("ALL_OK" if ok else "FAILED"))
    return 0 if ok else 1


def print_epic(epic):
    res = epic_lookup_store(epic)
    p = res["profile"]
    print("\n=== EPIC processor: %s ===" % res["epic"])
    print("  found        : %s (http %s, %s hit(s))"
          % (res["found"], res["status"], res.get("hits", 0)))
    if not res["found"]:
        print("  (not in the national display, or the search was rejected)")
        return 0
    fields = [("name", "name"), ("name_local", "name (local)"),
              ("relation", "relation"), ("relation_local", "relation (local)"),
              ("relation_label", "relation type"), ("age", "age"), ("gender", "gender"),
              ("state_name", "state"), ("district", "district"), ("ac_name", "AC"),
              ("ac_no", "AC no"), ("part_no", "part"), ("part_name", "part name"),
              ("part_name_l1", "part name (local)"), ("serial_no", "serial"),
              ("section_no", "section"), ("ps_building", "polling station")]
    for key, label in fields:
        print("  %-14s: %s" % (label, p.get(key)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="old_eci collector: one file, auto mode")
    ap.add_argument("--status", action="store_true", help="print DB state and exit")
    ap.add_argument("--epic", help="EPIC processor: look one EPIC up and exit")
    ap.add_argument("--selftest", action="store_true", help="offline checks")
    ap.add_argument("--export", metavar="CSV", help="export electors to CSV and exit")
    ap.add_argument("--state", help="restrict to one state code, e.g. S01")
    ap.add_argument("--ac", type=int, help="restrict to one AC number")
    ap.add_argument("--parts", type=int, default=0, help="stop after N parts")
    ap.add_argument("--minutes", type=float, default=0, help="stop after N minutes")
    ap.add_argument("--workers", type=int, default=0, help="parallel requests "
                                                         "within one part")
    ap.add_argument("--parts-parallel", type=int, default=1,
                    help="collect this many parts at once (each still uses "
                         "--workers serial threads; gateway sees the product)")
    ap.add_argument("--discover-ahead", type=int, default=40,
                    help="discover a new AC when fewer than N parts are pending")
    ap.add_argument("--no-discover", action="store_true",
                    help="only collect parts already discovered")
    ap.add_argument("--force", action="store_true", help="re-collect done parts")
    ap.add_argument("--no-init", action="store_true",
                    help="skip the schema DDL (faster restart; needs the tables "
                         "to already exist)")
    ap.add_argument("--no-recover", action="store_true",
                    help="do NOT requeue parts left running by another process "
                         "(use when the web app's worker is live on this DB)")
    ap.add_argument("--enrich", type=int, default=0,
                    help="also look up N un-enriched EPICs per idle turn")
    ap.add_argument("--idle-exit", type=float, default=10,
                    help="exit after N idle minutes (0 = never)")
    a = ap.parse_args(argv)

    if a.status or a.export:
        if not a.no_init:
            db_init()
        if a.status:
            status_report()
        if a.export:
            export_csv(a.export, a.state)
        return 0
    if a.epic:
        db_init()
        return print_epic(a.epic)
    if a.selftest:
        return run_selftest()

    runner = Runner(a)
    try:
        return runner.run()
    except KeyboardInterrupt:
        log("interrupted")
        recover_orphans()      # a part killed mid-sweep goes back to pending
        runner.summary("keyboard interrupt")
        return 130
    except Exception as exc:  # noqa: BLE001
        log("fatal: %s: %s" % (type(exc).__name__, exc))
        try:
            recover_orphans()
        except Exception:
            pass
        return 1


# The notebook check must come FIRST: inside a Colab/Jupyter cell `__name__` is
# "__main__" too, and there `sys.argv` holds the kernel's own launcher args
# ('-f', '/root/.local/share/jupyter/runtime/kernel-....json'), which argparse
# would reject. So a pasted cell runs auto mode with defaults instead.
# Set OLD_ECI_NO_AUTORUN=1 to import the module without starting a run.
if os.environ.get("OLD_ECI_NO_AUTORUN"):
    pass
elif "ipykernel" in sys.modules or "google.colab" in sys.modules:
    _rc = main([])
elif __name__ == "__main__":
    _rc = main()
