"""PostgreSQL layer for the old-ECI collector.

Everything lives in $ECI_PG_SCHEMA of the database behind $ECI_PG_DSN - by
default `public` of the dedicated `old_eci` database, which this app owns and
creates its own tables in.

The pre-existing populated `eci` database is a *different* server database used
only as a read-only catalogue source (states / ACs), via $ECI_GEO_DSN. Nothing
here ever writes to it.

DSN:    postgresql://eci_app:...@129.225.75.85:5432/old_eci   (env ECI_PG_DSN)
Schema: public                                                (env ECI_PG_SCHEMA)
Geo:    postgresql://eci_app:...@129.225.75.85:5432/eci        (env ECI_GEO_DSN)
"""
from __future__ import annotations

import json
import os

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

DSN = os.environ.get(
    "ECI_PG_DSN", "postgresql://eci_app:Raj%40A2Nkufyg@129.225.75.85:5432/old_eci")
SCHEMA = os.environ.get("ECI_PG_SCHEMA", "public")
# Optional read-only bootstrap for the state/AC catalogue (the legacy DB that
# already holds 38 states / 4129 ACs / 1.17M current-roll parts).
GEO_DSN = os.environ.get(
    "ECI_GEO_DSN", "postgresql://eci_app:Raj%40A2Nkufyg@129.225.75.85:5432/eci")


def geo_q(sql, params=None):
    """Best-effort read from the legacy catalogue DB ([] when unavailable)."""
    try:
        with psycopg.connect(GEO_DSN, connect_timeout=10, row_factory=dict_row,
                             keepalives=1, options="-c statement_timeout=60000") as c:
            with c.cursor() as cur:
                cur.execute(sql, params or ())
                return cur.fetchall()
    except Exception:
        return []


# The server is remote, so a connection can be dropped silently by a NAT or
# firewall. Without keepalives that leaves a blocked read hanging forever, which
# hangs the worker loop with no error to retry on. Keepalives probe the peer and
# `statement_timeout` bounds any single query, so a dead connection surfaces as an
# exception instead of a permanent stall.
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


def pool() -> ConnectionPool:
    """Shared connection pool - the DB is remote, so reconnecting per query hurts."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool(DSN, min_size=1, max_size=10, timeout=30,
                               configure=_setup, reconnect_failed=None,
                               kwargs=dict(CONN_KWARGS, row_factory=dict_row,
                                           autocommit=True))
        _pool.wait(timeout=20)
    return _pool


def connect(autocommit: bool = True):
    """Standalone (unpooled) connection - used by the long-lived worker thread."""
    conn = psycopg.connect(DSN, row_factory=dict_row, autocommit=autocommit,
                           **CONN_KWARGS)
    with conn.cursor() as cur:
        cur.execute("set search_path to %s, public" % SCHEMA)
    return conn


def q(sql: str, params=None, fetch: str | None = "all"):
    """Query helper backed by the shared pool."""
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
  created_at  timestamptz default now(),
  started_at  timestamptz,
  finished_at timestamptz
);
create index if not exists jobs_queue_idx on {s}.jobs (status, priority desc, id);

create table if not exists {s}.events (
  id      bigserial primary key,
  ts      timestamptz default now(),
  level   text default 'info',
  source  text,
  message text
);

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

# `create table if not exists` never touches an existing table, so new columns
# need their own additive, idempotent statements. These carry the old-location
# descriptor the route echoes on every record (oldStateName / oldDistName /
# oldAcName) - constant per part, so it belongs here, not on every elector row.
MIGRATIONS = """
alter table {s}.old_parts add column if not exists old_state_name text;
alter table {s}.old_parts add column if not exists old_dist_no    text;
alter table {s}.old_parts add column if not exists old_dist_name  text;
alter table {s}.old_parts add column if not exists old_ac_name    text;
-- The web part list carries the polling-station type and, when published, a link
-- to that part's old-roll PDF - the official printed roll.
-- Reserved-seat category (GEN/SC/ST), available from the web gateway's AC list.
alter table {s}.acs add column if not exists ac_type text;
alter table {s}.current_parts add column if not exists ps_type     text;
alter table {s}.current_parts add column if not exists ps_caty    text;
alter table {s}.current_parts add column if not exists old_pdf_url text;
"""

DEFAULTS = {
    "auto_enabled": False,
    "workers": 6,
    "request_pause_ms": 0,
    "discover_max_part": 400,
    "calibrate_offset": True,
    "collect_serial_cap": 3000,
}


def init():
    with connect() as conn, conn.cursor() as cur:
        if SCHEMA != "public":
            cur.execute("create schema if not exists %s" % SCHEMA)
        cur.execute(DDL.format(s=SCHEMA))
        cur.execute(MIGRATIONS.format(s=SCHEMA))
        for k, v in DEFAULTS.items():
            cur.execute(
                "insert into settings(key, value) values (%s, %s) "
                "on conflict (key) do nothing", (k, json.dumps(v)))


def setting(key, default=None):
    row = q("select value from settings where key=%s", (key,), fetch="one")
    return row["value"] if row else default


def set_setting(key, value):
    q("insert into settings(key,value) values (%s,%s) "
      "on conflict (key) do update set value=excluded.value, updated_at=now()",
      (key, json.dumps(value)), fetch=None)


def event(source, message, level="info"):
    try:
        q("insert into events(level, source, message) values (%s,%s,%s)",
          (level, source, message), fetch=None)
    except Exception:
        pass


if __name__ == "__main__":
    init()
    print("schema %s ready in %s" % (SCHEMA, DSN.split("@")[-1]))
    print("states:", q("select count(*) c from states", fetch="one")["c"],
          "| old_parts:", q("select count(*) c from old_parts", fetch="one")["c"])
