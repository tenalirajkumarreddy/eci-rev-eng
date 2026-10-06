"""Background worker for the old_eci collector.

One thread owns a connection and pulls jobs from old_eci.jobs
(FOR UPDATE SKIP LOCKED). Job kinds:

  seed_states     copy the state list (from the database's public.states if
                  present, else the live anonymous API)
  seed_acs        same for one state's assembly constituencies
  discover_parts  walk candidate old part numbers of one AC and record the ones
                  that exist in the old roll (name discovered from the route)
  collect_part    full serial sweep of one old part -> electors rows; marks the
                  part done so it is never repeated (unless force)
  collect_auto    keep picking the best pending part and collecting it
  epic_lookup     run one EPIC through the national search and store the record

Auto mode (settings.auto_enabled) turns idle time into collection: the worker
picks the best pending part by itself until nothing is left or it is switched
off.
"""
from __future__ import annotations

import json
import statistics
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import client
import db

STOP = threading.Event()
# `tick` is bumped every loop turn so a stalled worker is visible instead of
# looking healthy. `thread` lets /api/worker/restart revive one that died.
STATE = {"running": False, "job_id": None, "note": "", "tick": 0.0,
         "started": 0.0, "error": None, "thread": None}

# A worker that has not turned its loop in this long is stuck (see db.CONN_KWARGS
# for what stops a dropped connection from hanging forever). A single old part can
# take up to ~60s, and collect_part bumps the heartbeat as it sweeps, so this only
# trips on a genuine stall.
STALE_AFTER = 90.0


def worker_status():
    age = time.time() - STATE["tick"] if STATE["tick"] else None
    return {"running": STATE["running"], "job_id": STATE["job_id"],
            "note": STATE["note"], "error": STATE["error"],
            "tick_age": None if age is None else round(age, 1),
            "alive": bool(STATE["running"] and age is not None and age < STALE_AFTER)}


# ------------------------------------------------------------------ job queue

def claim_job(conn):
    with conn.cursor() as cur:
        cur.execute("""
            update jobs set status='running', started_at=now()
            where id = (select id from jobs where status='queued'
                        order by priority desc, id
                        limit 1 for update skip locked)
            returning *""")
        return cur.fetchone()


def job_cancelled(conn, job_id):
    if job_id is None:
        return False
    row = db.q("select cancel from jobs where id=%s", (job_id,), fetch="one")
    return bool(row and row["cancel"])


def finish(conn, job_id, status, result=None, error=None):
    if job_id is None:
        return
    db.q("update jobs set status=%s, finished_at=now(), result=%s, error=%s "
         "where id=%s", (status, json.dumps(result) if result is not None else None,
                         error, job_id), fetch=None)


def progress(conn, job_id, prog):
    if job_id is None:
        return
    db.q("update jobs set progress=%s where id=%s",
         (json.dumps(prog), job_id), fetch=None)


def recover_orphans(conn=None):
    """Put work left behind by a previous process back in the queue. `conn` is
    accepted for call-site symmetry; every query goes through `db.q`.

    A part killed mid-collection (crash, close the window, Ctrl-C) stays marked
    `running` in the ledger, and `best_pending_part` only looks at
    `pending`/`error` - so without this the part is stranded forever. Nothing can
    legitimately be in flight when a fresh process starts, so reset those rows.

    Assumes one worker process per database. Re-collecting is safe: the elector
    upsert is keyed on `source_id`, so a second sweep overwrites rows instead of
    duplicating them.
    """
    parts = db.q("""update old_parts set status='pending', last_serial=0,
                          updated_at=now()
                   where status='running'
                   returning state_cd, ac_no, part_no""", fetch="all")
    jobs = db.q("""update jobs set status='error', finished_at=now(),
                          error='interrupted by restart'
                   where status='running' returning id""", fetch="all")
    if parts:
        db.event("worker", "requeued %d part(s) left running by a previous process: %s"
                 % (len(parts), ", ".join("%s AC%s P%s" % (p["state_cd"], p["ac_no"],
                                                            p["part_no"])
                                          for p in parts)))
    if jobs:
        db.event("worker", "marked %d interrupted job(s) as error: %s"
                 % (len(jobs), ", ".join("#%s" % j["id"] for j in jobs)), level="warn")
    return {"parts": len(parts or []), "jobs": len(jobs or [])}


# ----------------------------------------------------------------- the best part

BEST_PART_SQL = """
select p.state_cd, p.ac_no, p.part_no, p.name,
       (select count(*) from old_parts d
         where d.state_cd=p.state_cd and d.ac_no=p.ac_no and d.status='done'
           and abs(d.part_no - p.part_no) <= 2)                      as neighbours_done,
       coalesce((select avg(d.epics) from old_parts d
         where d.state_cd=p.state_cd and d.ac_no=p.ac_no and d.status='done'
           and abs(d.part_no - p.part_no) <= 2), 0)                  as neighbour_yield
from old_parts p
where p.status in ('pending','error') and coalesce(p.exists_, true)
  {filters}
order by neighbours_done desc, neighbour_yield desc, p.state_cd, p.ac_no, p.part_no
limit 1
"""


def best_pending_part(conn, state_cd=None, ac_no=None):
    filters, params = "", []
    if state_cd:
        filters += " and p.state_cd = %s"
        params.append(state_cd)
    if ac_no is not None:
        filters += " and p.ac_no = %s"
        params.append(ac_no)
    return db.q(BEST_PART_SQL.format(filters=filters), params, fetch="one")


# --------------------------------------------------------------------- catalog

def seed_states(conn):
    rows = []
    pub = db.geo_q("select state_cd, name from public.states")
    if pub:
        rows = [{"state_cd": r["state_cd"], "name": r["name"], "source": "db"} for r in pub]
    if not rows:
        rows = [{"state_cd": r["state_cd"], "name": r["name"], "source": "api"}
                for r in client.states_live()]
    for r in rows:
        db.q("insert into states(state_cd,name,source) values (%s,%s,%s) "
             "on conflict (state_cd) do update set name=coalesce(excluded.name, states.name)",
             (r["state_cd"], r["name"], r["source"]), fetch=None)
    return {"states": len(rows)}


def seed_acs(conn, state_cd):
    """One state's ACs.

    Delegates to `seed_acs_all` so both paths behave identically. They used to
differ: this one trusted the legacy catalogue whenever it had any rows, so it
    never picked up the 12 ACs the catalogue omits for S01 and never stored
    `ac_type` - the same job giving different answers depending on the button.
    """
    return seed_acs_all(conn, state_cd=state_cd)


def seed_acs_all(conn, live=True, state_cd=None):
    """Seed ACs, from the legacy catalogue merged with live (all states, or one).

    The per-state `seed_acs` round-trips once per row, which is fine for one
    state but means minutes across the ~4k ACs of the whole country. This pulls
    them all with a single write.

    The legacy catalogue alone is NOT complete: it lists 175 ACs for S01 while
    `citizen/sir/getAsmbly` serves 187, and every one of the 12 extra ACs has
    old-roll data. Taking the catalogue at face value silently skipped them, so
    the live list is merged on top by (state_cd, ac_no).
    """
    merged = {}
    geo_sql = ("select state_cd, ac_number, ac_name, district_cd from public.acs "
               + ("where state_cd=%s " if state_cd else "")
               + "order by state_cd, ac_number")
    for r in db.geo_q(geo_sql, (state_cd,) if state_cd else None):
        merged[(r["state_cd"], int(r["ac_number"]))] = {
            "name": r.get("ac_name"), "name_l1": None,
            "district_cd": r.get("district_cd")}
    from_legacy = len(merged)

    live_only = 0
    if live:
        state_rows = ([{"state_cd": state_cd}] if state_cd
                      else db.q("select state_cd from states order by state_cd"))
        for s in state_rows:
            sc = s["state_cd"]
            try:
                rows = client.acs_live(sc)
            except Exception as exc:  # noqa: BLE001 - catalogue is best effort
                db.event("worker", "live AC list failed for %s: %s" % (sc, exc),
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
              v.get("ac_type"))
             for (sc, ac), v in sorted(merged.items())])
    states = len({sc for sc, _ in merged})
    db.event("worker", "seeded %d ACs across %d states (%d from the legacy catalogue, "
             "%d only in the live list)" % (len(merged), states, from_legacy, live_only))
    return {"acs": len(merged), "states": states, "from_legacy": from_legacy,
            "live_only": live_only}


# Part numbers are probed in chunks and discovery stops only when a whole chunk
# comes back empty, so the cap is a floor rather than a limit.
PART_CHUNK = 200
PART_HARD_CAP = 3000


def discover_parts(conn, job_id, state_cd, ac_no, max_part=None):
    """Probe part numbers upward until a whole chunk is empty.

    `max_part` (`discover_max_part`) is a FLOOR, not a limit. Stopping exactly at
    a cap truncates silently: S01 AC 1 was first discovered with a cap of 12 and
    recorded as "12 parts, done", which hid 141 real parts until a cross-check
    against the live part list caught it. So probing continues while the top of
    each chunk is live, up to PART_HARD_CAP, and the result reports whether it
    hit that ceiling.
    """
    floor = int(max_part or db.setting("discover_max_part", 400))
    db.q("update acs set discover_status='running', discover_max=%s, last_error=null "
         "where state_cd=%s and ac_no=%s", (floor, state_cd, ac_no), fetch=None)
    workers = int(db.setting("workers", 6))

    def probe(n):
        status, payload = client.fetch_window(state_cd, ac_no, n)
        name = None
        for rec in payload or []:
            if rec.get("oldPartName"):
                name = rec["oldPartName"]
                break
        exists = bool(payload) or status == 200
        if exists and not payload:
            for serial in (1, 25, 100):
                st, pl = client.fetch_serial(state_cd, ac_no, n, serial)
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
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for n, exists, name in pool.map(probe, range(start, end + 1)):
                if exists:
                    found[n] = name
                    hits += 1
        probed_to = end
        progress(conn, job_id, {"phase": "discover", "probed_to": end,
                                "found": len(found)})
        # A whole empty chunk past the floor means there is nothing above.
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
                        [(state_cd, ac_no, n, name) for n, name in sorted(found.items())])
    db.q("update states set has_old_data=true, last_checked=now() where state_cd=%s",
         (state_cd,), fetch=None)
    # discover_max records how far probing actually reached, so an AC whose
    # old_parts_found ever equals it can be spotted as possibly truncated.
    db.q("update acs set discover_status='done', old_parts_found=%s, discover_max=%s, "
         "discovered_at=now() where state_cd=%s and ac_no=%s",
         (len(found), probed_to, state_cd, ac_no), fetch=None)
    db.event("catalog", "discover %s AC %s: %d old parts (probed 1..%s%s)"
             % (state_cd, ac_no, len(found), probed_to,
                " - TRUNCATED at the hard cap" if truncated else ""))
    return {"found": len(found), "probed_to": probed_to, "truncated": truncated}


# ------------------------------------------------------------------ collection

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
    def age(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    return (rec.get("id"), state, ac, part,
            age(rec.get("oldPartSerialNo")),
            rec.get("oldFullName") or rec.get("firstName"),
            rec.get("oldFullNameL1"),
            rec.get("oldRelativeFullName") or rec.get("relativeFName"),
            rec.get("oldRelativeFullNameL1"),
            rec.get("relationType"), rec.get("gender"), age(rec.get("age")),
            rec.get("epicNumber"), rec.get("markedByBlo"),
            rec.get("bloMappedStateCd"), age(rec.get("bloMappedAcNo")),
            age(rec.get("bloMappedPartNo")), rec.get("bloMappedEpicNo"))


def collect_part(conn, job_id, state_cd, ac_no, part_no, force=False):
    row = db.q("select * from old_parts where state_cd=%s and ac_no=%s and part_no=%s",
               (state_cd, ac_no, part_no), fetch="one")
    if row and row["status"] == "done" and not force:
        return {"skipped": True, "reason": "already done",
                "records": row["records"], "epics": row["epics"]}

    db.q("""insert into old_parts(state_cd, ac_no, part_no, status, started_at, attempts)
            values (%s,%s,%s,'running', now(), 1)
            on conflict (state_cd, ac_no, part_no)
            do update set status='running', started_at=now(),
                          attempts=old_parts.attempts+1, last_error=null,
                          updated_at=now()""",
         (state_cd, ac_no, part_no), fetch=None)
    t0 = time.time()
    workers = int(db.setting("workers", 6))
    cap = int(db.setting("collect_serial_cap", 3000))
    roll_end = client.probe_roll_end(state_cd, ac_no, part_no, hard_cap=cap)
    stats = {"hits": 0, "misses": 0, "errors": 0, "records": 0, "epics": 0}
    seen_ids = set()

    def one(serial):
        st, payload = client.fetch_serial(state_cd, ac_no, part_no, serial)
        return st, payload

    pending_rows = []
    # Old-location descriptor the route echoes on every record; constant per part,
    # so it is stored once on old_parts rather than repeated on every elector row.
    meta = {}

    def flush():
        if not pending_rows:
            return
        with conn.cursor() as cur:
            cur.executemany(ELECTOR_UPSERT, pending_rows)
        pending_rows.clear()

    cancelled = False
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, (st, payload) in enumerate(
                pool.map(one, range(1, roll_end + 1)), start=1):
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
                        meta.update(
                            old_state_name=rec.get("oldStateName"),
                            old_dist_no=str(rec.get("oldDistNo") or "") or None,
                            old_dist_name=rec.get("oldDistName"),
                            old_ac_name=rec.get("oldAcName"))
                    pending_rows.append(_row_tuple(rec, state_cd, ac_no, part_no))
            elif st == 404:
                stats["misses"] += 1
            else:
                stats["errors"] += 1
            if i % 200 == 0:
                flush()
                # A part can outlast STALE_AFTER; sweeping counts as progress.
                STATE["tick"] = time.time()
                db.q("""update old_parts set last_serial=%s, records=%s, epics=%s,
                        old_state_name=coalesce(%s, old_state_name),
                        old_dist_no=coalesce(%s, old_dist_no),
                        old_dist_name=coalesce(%s, old_dist_name),
                        old_ac_name=coalesce(%s, old_ac_name),
                        exists_=true, updated_at=now()
                        where state_cd=%s and ac_no=%s and part_no=%s""",
                     (i, stats["records"], stats["epics"], meta.get("old_state_name"),
                      meta.get("old_dist_no"), meta.get("old_dist_name"),
                      meta.get("old_ac_name"), state_cd, ac_no, part_no),
                     fetch=None)
                progress(conn, job_id, {"phase": "collect", "serial": i,
                                        "roll_end": roll_end, "records": stats["records"],
                                        "epics": stats["epics"]})
                if job_cancelled(conn, job_id):
                    cancelled = True
                    break
        flush()

    # ---- calibration: how far does the mapping's numbering lag the live roll?
    offset = cur_part_mode = None
    if db.setting("calibrate_offset", True) and stats["epics"]:
        sample = db.q("""select cur_epic, cur_part_no from electors
                         where state_cd=%s and ac_no=%s and part_no=%s
                           and cur_epic is not null and cur_epic <> ''
                         order by random() limit 3""",
                      (state_cd, ac_no, part_no))
        deltas, parts = [], []
        for s in sample or []:
            res = client.epic_lookup(s["cur_epic"])
            content = res.get("content") or {}
            live_part = content.get("partNumber")
            if live_part is not None and s["cur_part_no"] is not None:
                deltas.append(int(live_part) - int(s["cur_part_no"]))
            if s["cur_part_no"] is not None:
                parts.append(int(s["cur_part_no"]))
        if deltas:
            offset = int(statistics.median(deltas))
        if parts:
            cur_part_mode = Counter(parts).most_common(1)[0][0]

    status = "pending" if cancelled else "done"
    db.q("""update old_parts set status=%s, finished_at=now(), records=%s, epics=%s,
            unmapped=%s, roll_end=%s, mapping_offset=%s, cur_part_mode=%s,
            old_state_name=coalesce(%s, old_state_name),
            old_dist_no=coalesce(%s, old_dist_no),
            old_dist_name=coalesce(%s, old_dist_name),
            old_ac_name=coalesce(%s, old_ac_name), updated_at=now()
            where state_cd=%s and ac_no=%s and part_no=%s""",
         (status, stats["records"], stats["epics"],
          stats["records"] - stats["epics"], roll_end, offset, cur_part_mode,
          meta.get("old_state_name"), meta.get("old_dist_no"),
          meta.get("old_dist_name"), meta.get("old_ac_name"),
          state_cd, ac_no, part_no), fetch=None)
    result = dict(stats, roll_end=roll_end, offset=offset,
                  cur_part_mode=cur_part_mode, seconds=round(time.time() - t0, 1),
                  cancelled=cancelled)
    if not cancelled:
        db.event("collect", "%s AC %s part %s: %d records, %d EPICs (%ss)"
                 % (state_cd, ac_no, part_no, stats["records"], stats["epics"],
                    result["seconds"]))
    return result


def collect_auto(conn, job_id, state_cd=None, ac_no=None, max_parts=50, force=False):
    done = []
    for _ in range(max_parts):
        if job_cancelled(conn, job_id):
            break
        nxt = best_pending_part(conn, state_cd, ac_no)
        if not nxt:
            break
        res = collect_part(conn, job_id, nxt["state_cd"], nxt["ac_no"],
                           nxt["part_no"], force=force)
        done.append({"state_cd": nxt["state_cd"], "ac_no": nxt["ac_no"],
                     "part_no": nxt["part_no"], "result": res,
                     "cur_part_mode": res.get("cur_part_mode")})
        progress(conn, job_id, {"phase": "auto", "finished": len(done),
                                "last": done[-1]})
    return {"parts": len(done), "done": done}


def epic_lookup_job(conn, job_id, epic):
    res = client.epic_lookup(epic)
    content = res.get("content") or {}
    prof = client.profile_from_content(content)
    db.q("""insert into epic_lookups(epic, found, http_status, hits, name, name_local,
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
              raw=excluded.raw, fetched_at=now()""",
         (res.get("epic"), bool(res.get("hits")), res.get("status"), res.get("hits"),
          prof["name"], prof["name_local"], prof["relation"], prof["relation_local"],
          prof["relation_type"], _int(prof["age"]), prof["gender"], prof["state_cd"],
          prof["state_name"], prof["district"], _int(prof["ac_no"]), prof["ac_name"],
          _int(prof["part_no"]), prof["part_name"], prof["part_name_l1"],
          _int(prof["part_id"]), _int(prof["serial_no"]), _int(prof["section_no"]),
          prof["ps_building"], prof["ps_building_l1"], prof["record_id"],
          json.dumps(content)), fetch=None)
    db.event("epic", "lookup %s: %s" % (res.get("epic"),
             "found" if res.get("hits") else "no record (%s)" % res.get("status")))
    return {"epic": res.get("epic"), "found": bool(res.get("hits")),
            "status": res.get("status"), "profile": prof}


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ job runner

def run_job(conn, job):
    kind = job["kind"]
    payload = job["payload"] or {}
    STATE["job_id"] = job["id"]
    STATE["note"] = "%s %s" % (kind, json.dumps(payload)[:80])
    db.event("worker", "job #%s %s %s" % (job["id"], kind, json.dumps(payload)[:120]))
    try:
        if kind == "seed_states":
            out = seed_states(conn)
        elif kind == "seed_acs":
            out = seed_acs(conn, payload["state_cd"])
        elif kind == "seed_acs_all":
            out = seed_acs_all(conn)
        elif kind == "discover_parts":
            out = discover_parts(conn, job["id"], payload["state_cd"], int(payload["ac_no"]),
                                 payload.get("max_part"))
        elif kind == "collect_part":
            out = collect_part(conn, job["id"], payload["state_cd"], int(payload["ac_no"]),
                               int(payload["part_no"]), bool(payload.get("force")))
        elif kind == "collect_auto":
            out = collect_auto(conn, job["id"], payload.get("state_cd"),
                               payload.get("ac_no"), int(payload.get("max_parts", 25)),
                               bool(payload.get("force")))
        elif kind == "epic_lookup":
            out = epic_lookup_job(conn, job["id"], payload["epic"])
        else:
            raise ValueError("unknown job kind %r" % kind)
        finish(conn, job["id"], "done", out)
        return out
    except Exception as exc:  # noqa: BLE001 - recorded, not raised
        finish(conn, job["id"], "error", None, "%s: %s" % (type(exc).__name__, exc))
        db.event("worker", "job #%s failed: %s" % (job["id"], exc), level="error")
        return {"error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        STATE["job_id"] = None


def run_forever(poll=2.0):
    """Job loop + auto mode. Never lets an exception escape: a worker that dies
    silently leaves queued jobs stranded with no way to notice."""
    STATE.update(running=True, started=time.time(), tick=time.time(), error=None)
    db.event("worker", "worker started")
    try:
        recover_orphans(None)
    except Exception as exc:  # noqa: BLE001
        db.event("worker", "orphan recovery failed: %s" % exc, level="error")
    conn = None
    while not STOP.is_set():
        STATE["tick"] = time.time()
        try:
            if conn is None:
                conn = db.connect()
            job = claim_job(conn)
            if job:
                run_job(conn, job)
                continue
            if db.setting("auto_enabled", False):
                nxt = best_pending_part(conn)
                if nxt:
                    STATE["note"] = "auto: %s AC %s part %s" % (
                        nxt["state_cd"], nxt["ac_no"], nxt["part_no"])
                    collect_part(conn, None, nxt["state_cd"], nxt["ac_no"], nxt["part_no"])
                    continue
                # Nothing left to pick: don't keep advertising the last part.
                STATE["note"] = "idle - nothing pending"
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            STATE["error"] = "%s: %s" % (type(exc).__name__, exc)
            db.event("worker", "loop error: %s" % exc, level="error")
            try:
                conn.close()
            except Exception:
                pass
            conn = None
            time.sleep(poll)
        time.sleep(poll)
    STATE["running"] = False
    db.event("worker", "worker stopped")


def start_background():
    t = threading.Thread(target=run_forever, name="old-eci-worker", daemon=True)
    STATE["thread"] = t
    t.start()
    return t


def ensure_worker():
    """Start the worker thread if it is missing or has gone stale.

    `worker_status()['alive']` is the honest answer to "is work progressing" -
    a hung connection makes `running` true while nothing happens.
    """
    st = worker_status()
    if st["alive"]:
        return {"restarted": False, "worker": st}
    STOP.clear()
    if st["running"] and st["tick_age"] is not None and st["tick_age"] >= STALE_AFTER:
        db.event("worker", "worker looked stalled (no loop turn for %ss) - starting a "
                 "replacement; the stuck thread will exit on its own" % st["tick_age"],
                 level="warn")
    start_background()
    return {"restarted": True, "worker": worker_status()}


def run_once(kind, **payload):
    conn = db.connect()
    return run_job(conn, {"id": None, "kind": kind, "payload": payload})


if __name__ == "__main__":
    import sys
    db.init()
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        print(run_once(sys.argv[2], **json.loads(sys.argv[3] if len(sys.argv) > 3 else "{}")))
    else:
        start_background()
        print("worker running; ctrl-c to stop")
        while True:
            time.sleep(5)
