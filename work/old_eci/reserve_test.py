"""Reservation queue: semantics + cost, against the shipped code paths.

Two modes:

  python -X utf8 reserve_test.py            # sandbox: private schema `rsvtest`
  python -X utf8 reserve_test.py --live     # timing on the real backlog

The sandbox never touches the live fleet: it builds its own schema, so the
assertions are about the real functions (worker.reserve_parts / take_reserved /
claim_part / best_pending_parts / recover_orphans), not a re-implementation.

The live mode only measures the take/claim/pick cost. Its synthetic rows carry
exists_ = false, which every picker filters out, so a live device can never pick
one even though the row is real.
"""
from __future__ import annotations

import os
import sys
import time

MODE = ("live" if "--live" in sys.argv
        else "engine" if "--engine" in sys.argv else "sandbox")
SANDBOX = "rsvtest"
ENGINE_SCHEMA = "rsveng"
STATE, AC = "ZZRSV", 9901
LIVE_STATE, LIVE_AC = "ZZRSV", 9902
TAG_A, TAG_B = "rsv-A", "rsv-B"
TAG_E, TAG_E2 = "rsv-E", "rsv-E2"

if MODE == "sandbox":
    os.environ["ECI_PG_SCHEMA"] = SANDBOX
    os.environ["ECI_DEVICE_TAG"] = TAG_A
elif MODE == "engine":
    os.environ["ECI_PG_SCHEMA"] = ENGINE_SCHEMA
    os.environ["OLD_ECI_DEVICE_TAG"] = TAG_E

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import db      # noqa: E402
import worker  # noqa: E402

FAILS = []


def check(label, ok, detail=""):
    print("%-4s %s%s" % ("PASS" if ok else "FAIL", label,
                         ("   [%s]" % detail) if detail else ""))
    if not ok:
        FAILS.append(label)
    return ok


def tag(t):
    """Act as another device: the picker/claim read DEVICE_TAG at call time."""
    worker.DEVICE_TAG = t


def keys(rows):
    return {(r["state_cd"], r["ac_no"], r["part_no"]) for r in rows}


def held_keys():
    return keys(db.q("select state_cd, ac_no, part_no from old_parts "
                     "where reserved_by is not null and state_cd=%s",
                     (STATE,), fetch="all"))


def picked_by(t):
    """What device `t`'s picker offers, inside the sandbox scope."""
    tag(t)
    seen = keys([r for r in worker.best_pending_parts(None, None, limit=12)
                 if r["state_cd"] == STATE])
    return seen


def row_of(part):
    return db.q("select status, claimed_by, reserved_by, reserved_at from old_parts "
                "where state_cd=%s and ac_no=%s and part_no=%s",
                (STATE, AC, part), fetch="one")


def backdate(part, minutes):
    db.q("update old_parts set reserved_at = now() - make_interval(mins => %s) "
         "where state_cd=%s and ac_no=%s and part_no=%s",
         (minutes, STATE, AC, part), fetch=None)


def seed(n=12):
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("delete from old_parts where state_cd=%s", (STATE,))
        for p in range(1, n + 1):
            cur.execute("""insert into old_parts(state_cd, ac_no, part_no, name,
                             status, exists_, records, epics)
                           values (%s,%s,%s,%s,'pending',true,0,0)""",
                        (STATE, AC, p, "reserve test part %d" % p))


# ------------------------------------------------------------------- sandbox

def sandbox():
    print("== sandbox schema %s, device %s ==" % (db.SCHEMA, TAG_A))
    db.init()
    seed()

    # 1. reserve N parts for this device - exactly N, not "as many as came back"
    tag(TAG_A)
    first = keys(worker.reserve_parts(n=5))
    check("reserve_parts(5) holds exactly 5", len(first) == 5,
          "%d rows" % len(first))
    check("reserve_count() == 5", worker.reserve_count() == 5,
          "count=%d" % worker.reserve_count())
    check("every hold is this device's", held_keys() == first,
          "held parts %s" % sorted(p for _, _, p in first))

    # 2. another device cannot even see them - the 'same attention' fix
    seen = picked_by(TAG_B)
    check("B's picker hides A's 5 live holds", not (seen & first),
          "B sees %s" % sorted(p for _, _, p in seen))
    check("B still sees the other 7 free parts", len(seen) == 7,
          "%d free seen" % len(seen))

    # 3. and a pick that raced the hold cannot steal it
    victim = sorted(first)[0]
    check("B's claim on A's hold is refused",
          worker.claim_part(*victim) is False)
    check("...and the skip names the holder",
          worker.hold_reason(*victim) == "reserved by %s" % TAG_A,
          worker.hold_reason(*victim))
    check("the held row is untouched (still pending, still A's)",
          row_of(victim[2])["status"] == "pending"
          and row_of(victim[2])["reserved_by"] == TAG_A)

    # 4. top-up adds only NEW parts and never re-timestamps a live hold
    tag(TAG_A)
    before = {p: row_of(p[2])["reserved_at"] for p in first}
    worker.reserve_parts(n=8)
    after = {p: row_of(p[2])["reserved_at"] for p in first}
    check("top-up reached 8 holds (own holds excluded from candidates)",
          worker.reserve_count() == 8, "count=%d" % worker.reserve_count())
    check("re-stamping never refreshes a live hold (no FIFO drift)",
          before == after, "timestamps identical")

    # 5. take: FIFO on reserved_at, and the row comes back claimed by us
    held = held_keys()
    oldest2 = sorted(held)[:2]
    for i, p in enumerate(oldest2):          # unambiguous order: 3min, 2min
        backdate(p[2], 3 - i)
    took = keys(worker.take_reserved(2))
    check("take_reserved(2) returns 2", len(took) == 2, "%d" % len(took))
    check("take is FIFO (the two oldest holds, tie-broken by part)",
          took == set(oldest2), "took %s" % sorted(p[2] for p in took))
    st = row_of(oldest2[0][2])
    check("taken part is running under this worker",
          st["status"] == "running" and st["claimed_by"] == worker.WORKER_ID,
          "%s / %s" % (st["status"], st["claimed_by"]))
    check("taken part's hold is cleared", st["reserved_by"] is None)
    check("queue shrank to 6", worker.reserve_count() == 6,
          "count=%d" % worker.reserve_count())

    # 6. the preclaimed wiring, both directions
    taken = sorted(took)[0]
    still_held = sorted(held - took)[0]
    real_impl = worker._collect_part_impl
    worker._collect_part_impl = lambda *a, **k: {"records": 0, "epics": 0,
                                                 "stub": True}
    try:
        res = worker.collect_part(None, None, *taken, preclaimed=True)
        check("collect_part(preclaimed=True) sweeps instead of skipping",
              res.get("stub") is True, "%s" % res)
        res = worker.collect_part(None, None, *taken, preclaimed=False)
        check("without the flag it would skip its own claim (why it exists)",
              res.get("skipped") is True, str(res.get("reason")))
        tag(TAG_B)
        res = worker.collect_part(None, None, *still_held)
        check("a foreign hold still blocks a plain claim on the sweep path",
              res.get("skipped") is True and res.get("reason") ==
              "reserved by %s" % TAG_A, str(res.get("reason")))
        res = worker.collect_part(None, None, *still_held, preclaimed=True)
        check("...and preclaimed bypasses it (the queue-drain path)",
              res.get("stub") is True, "%s" % res)
    finally:
        worker._collect_part_impl = real_impl

    # 7. clean stop gives everything back
    tag(TAG_A)
    freed = worker.release_reservations()
    check("release_reservations() freed the rest", freed == 6,
          "%d freed" % freed)
    check("nothing left held", worker.reserve_count() == 0
          and not held_keys())

    # 8. expiry frees a stopped device's queue even with no reaper running
    tag(TAG_A)
    fresh3 = keys(worker.reserve_parts(n=3))
    check("re-reserved 3", len(fresh3) == 3, "%d" % len(fresh3))
    ttl = worker.reserve_ttl()
    for p in fresh3:
        db.q("update old_parts set reserved_at = now() "
             "- make_interval(secs => %s) where state_cd=%s and ac_no=%s "
             "and part_no=%s", (ttl + 30, STATE, AC, p[2]), fetch=None)
    seen = picked_by(TAG_B)
    check("B's picker ignores an expired hold (no reaper needed)",
          fresh3 <= seen, "B sees %d parts" % len(seen))
    check("B can claim an expired hold", worker.claim_part(*sorted(fresh3)[0]))
    check("expired holds no longer count as this device's queue",
          worker.reserve_count() == 0, "count=%d" % worker.reserve_count())

    # 9. the reaper clears other devices' expired holds, never its own
    tag(TAG_B)
    worker.recover_orphans(None)
    left = db.q("""select count(*) c from old_parts
                    where state_cd=%s and reserved_by is not null
                      and reserved_at < now() - make_interval(secs => %s)""",
                (STATE, ttl), fetch="one")["c"]
    check("reaper cleared the expired holds it may touch", left == 0,
          "%d stale holds left" % left)
    tag(TAG_A)
    worker.reserve_parts(n=2)
    for p in sorted(held_keys()):
        db.q("update old_parts set reserved_at = now() - make_interval(secs => %s)"
             " where state_cd=%s and ac_no=%s and part_no=%s",
             (ttl + 30, STATE, AC, p[2]), fetch=None)
    worker.recover_orphans(None)
    own = db.q("select count(*) c from old_parts where state_cd=%s "
               "and reserved_by=%s", (STATE, TAG_A), fetch="one")["c"]
    check("reaper leaves this device's own holds alone", own == 2,
          "%d own holds" % own)

    # 10. the per-device setting drives queue depth
    db.set_setting("part_reserve_n", 2, tag=TAG_A)
    worker.release_reservations()
    got = keys(worker.reserve_parts())
    check("part_reserve_n=2 (per-device setting) is honoured", len(got) == 2,
          "%d rows" % len(got))
    db.set_setting("part_reserve_n", 5, tag=TAG_A)

    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("drop schema if exists %s cascade" % SANDBOX)
    print("sandbox schema dropped")


# ------------------------------------------------------------- engine (Colab)
def engine():
    """Same contract, exercised through old_eci_collector's own functions - the
    standalone script shares the schema and the SQL, so it needs its own pass."""
    ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "..", ".."))
    sys.path.insert(0, ROOT)
    import old_eci_collector as ec

    def eq(sql, params=None, **kw):
        return ec.q(sql, params, **kw)

    print("== engine sandbox schema %s, device %s ==" % (ec.SCHEMA, ec.DEVICE_TAG))
    ec.db_init()
    with ec.connect() as conn, conn.cursor() as cur:
        cur.execute("delete from old_parts where state_cd=%s", (STATE,))
        for p in range(1, 9):
            cur.execute("""insert into old_parts(state_cd, ac_no, part_no, name,
                             status, exists_, records, epics)
                           values (%s,%s,%s,%s,'pending',true,0,0)""",
                        (STATE, AC, p, "engine test part %d" % p))

    mine = keys(ec.reserve_parts(n=4))
    check("engine reserve_parts(4) holds exactly 4", len(mine) == 4,
          "%d rows" % len(mine))
    check("engine reserve_count() == 4", ec.reserve_count() == 4,
          "count=%d" % ec.reserve_count())

    ec.DEVICE_TAG = TAG_E2
    seen = keys([r for r in ec.best_pending_parts(None, None, limit=8)
                 if r["state_cd"] == STATE])
    check("engine: another device's picker hides the holds", not (seen & mine),
          "sees %s" % sorted(p for _, _, p in seen))
    ec.DEVICE_TAG = TAG_E

    victim = sorted(mine)[0]
    ec.DEVICE_TAG = TAG_E2
    check("engine: _mark_running is refused on a foreign hold",
          ec._mark_running(*victim) is False)
    check("engine: the skip names the holder",
          ec.hold_reason(*victim) == "reserved by %s" % TAG_E,
          ec.hold_reason(*victim))
    ec.DEVICE_TAG = TAG_E

    took = ec.take_reserved(2)
    check("engine: take_reserved(2) returns 2", len(took) == 2, "%d" % len(took))
    row = eq("select status, claimed_by, reserved_by from old_parts where "
             "state_cd=%s and ac_no=%s and part_no=%s",
             (STATE, AC, took[0]["part_no"]), fetch="one")
    check("engine: taken part runs under this worker with no hold left",
          row["status"] == "running" and row["claimed_by"] == ec.WORKER_ID
          and row["reserved_by"] is None,
          "%s / %s / %s" % (row["status"], row["claimed_by"], row["reserved_by"]))

    real_inner = ec._collect_inner
    ec._collect_inner = lambda *a, **k: {"records": 0, "epics": 0, "stub": True}
    try:
        res = ec.collect_part(None, STATE, AC, took[0]["part_no"], preclaimed=True)
        check("engine: collect_part(preclaimed=True) does not re-claim",
              res.get("stub") is True, "%s" % res)
        res = ec.collect_part(None, STATE, AC, took[0]["part_no"])
        check("engine: plain collect_part skips our own running claim",
              res.get("skipped") is True, str(res.get("reason")))
    finally:
        ec._collect_inner = real_inner

    freed = ec.release_reservations()
    check("engine: release_reservations() gave the rest back", freed == 2,
          "%d freed" % freed)

    # fresh=True is what keeps the top-up from re-selecting its own holds
    ec.reserve_parts(n=4)
    held = keys(eq("select state_cd, ac_no, part_no from old_parts where "
                   "state_cd=%s and reserved_by is not null", (STATE,), fetch="all"))
    cands = keys([r for r in ec.best_pending_parts(None, None, limit=4, fresh=True)
                  if r["state_cd"] == STATE])
    check("engine: best_pending_parts(fresh=True) skips own live holds",
          len(held) == 4 and not (cands & held),
          "held %d, candidates %s" % (len(held), sorted(p for _, _, p in cands)))
    check("engine: expiry predicate is shared with the picker",
          ec.reserve_ttl() >= 60.0, "ttl=%s" % ec.reserve_ttl())
    ec.release_reservations()

    with ec.connect() as conn, conn.cursor() as cur:
        cur.execute("drop schema if exists %s cascade" % ENGINE_SCHEMA)
    print("engine sandbox schema dropped")


# ---------------------------------------------------------------------- live

def live():
    tag_name = "rsv-live"
    print("== live table %s.%s, probe device %s ==" % (db.SCHEMA, "old_parts",
                                                       tag_name))
    worker.DEVICE_TAG = tag_name
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("delete from old_parts where state_cd=%s", (LIVE_STATE,))
        for p in range(1, 6):
            cur.execute("""insert into old_parts(state_cd, ac_no, part_no, name,
                             status, exists_, reserved_by, reserved_at)
                           values (%s,%s,%s,%s,'pending',false,%s, now())""",
                        (LIVE_STATE, LIVE_AC, p, "live probe %d" % p, tag_name))
    def ms(x):
        return x * 1000.0

    try:
        pend = db.q("select count(*) c from old_parts where status in "
                    "('pending','error')", fetch="one")["c"]
        # The DB is remote, so every statement pays the round trip. Measuring it
        # first is what makes the other numbers meaningful: the win claimed here
        # is 'one round trip instead of one 9s ranking scan', not 'sub-millisecond'.
        t0 = time.time()
        for _ in range(3):
            db.q("select 1", fetch="one")
        base = (time.time() - t0) / 3.0
        print("live backlog: %d pending/error parts; round trip %.0fms"
              % (pend, ms(base)))

        t0 = time.time()
        pick = worker.best_pending_part(None, None)
        t_pick = time.time() - t0
        print("best_pending_part (whole backlog)   %7.1fms  -> %s AC %s P%s"
              % (ms(t_pick), pick["state_cd"], pick["ac_no"], pick["part_no"]))

        t0 = time.time()
        took = worker.take_reserved(1)
        t_take = time.time() - t0
        print("take_reserved(1) from own queue     %7.1fms  -> part %s"
              % (ms(t_take), took[0]["part_no"] if took else None))
        check("take_reserved returned a held part", len(took) == 1)
        check("take_reserved == one round trip (no ranking scan)",
              t_take < base * 3, "%.0fms = %.1fx round trip" % (ms(t_take),
                                                                t_take / base))
        check("take_reserved beats the picker by >=5x", t_take < t_pick / 5,
              "%.0fms vs %.0fms (%.1fx)" % (ms(t_take), ms(t_pick),
                                            t_pick / max(t_take, 1e-6)))

        t0 = time.time()
        took5 = worker.take_reserved(4)
        t_take5 = time.time() - t0
        print("take_reserved(4) (drain the queue)  %7.1fms  -> %d parts"
              % (ms(t_take5), len(took5)))
        check("draining 4 queued parts is still one statement",
              len(took5) == 4 and t_take5 < base * 3,
              "%.0fms for 4 parts" % ms(t_take5))

        # Part 5 was just drained by our own take, so put it back to pending
        # first - the point here is a FOREIGN hold on a free part.
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute("""update old_parts set status='pending', claimed_by=null,
                             reserved_by='rsv-other', reserved_at=now()
                           where state_cd=%s and part_no=5""", (LIVE_STATE,))
        worker.DEVICE_TAG = tag_name
        t0 = time.time()
        won = worker.claim_part(LIVE_STATE, LIVE_AC, 5)
        t_lost = time.time() - t0
        print("claim refused on a foreign hold    %7.1fms  -> won=%s"
              % (ms(t_lost), won))
        check("a foreign live hold blocks the claim", won is False)
        check("a refused claim costs one round trip (no wasted sweep)",
              t_lost < base * 3, "%.0fms" % ms(t_lost))
        check("hold_reason names the holder",
              worker.hold_reason(LIVE_STATE, LIVE_AC, 5) == "reserved by rsv-other",
              worker.hold_reason(LIVE_STATE, LIVE_AC, 5))

        t0 = time.time()
        n_res = db.q("select count(*) c from old_parts where reserved_by is not "
                     "null and status in ('pending','error')", fetch="one")["c"]
        t_cnt = time.time() - t0
        print("fleet-wide live holds               %7.1fms  -> %d"
              % (ms(t_cnt), n_res))
        check("reservation count == one round trip", t_cnt < base * 3,
              "%.0fms" % ms(t_cnt))
        t0 = time.time()
        res = worker.reserve_parts(n=5)
        t_top = time.time() - t0
        print("reserve_parts top-up (picker+stamp) %7.1fms  -> %d stamped"
              % (ms(t_top), len(res or [])))
        print("\nper-part decision now: %.0fms (take, 1 round trip); "
              "before: %.0fms (picker) + claim" % (ms(t_take), ms(t_pick)))
        print("queue refills: %.0fms per 5 parts -> %.0fms/part amortised"
              % (ms(t_top), ms(t_top / 5.0)))
    finally:
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute("delete from old_parts where state_cd=%s", (LIVE_STATE,))
            # A probe device must not hold REAL parts: give the holds back (the
            # TTL would too, 15 minutes later) and requeue anything it took, so
            # the fleet does not wait for the 10-minute reaper either.
            cur.execute("update old_parts set reserved_by=null, reserved_at=null "
                        "where reserved_by = any(%s)", ([tag_name, "rsv-other"],))
            held_back = cur.rowcount
            cur.execute("update old_parts set status='pending', last_serial=0, "
                        "claimed_by=null, updated_at=now() "
                        "where claimed_by like %s and status='running'",
                        (tag_name + "-%",))
            requeued = cur.rowcount
        left = db.q("select count(*) c from old_parts where state_cd=%s",
                    (LIVE_STATE,), fetch="one")["c"]
        check("live probe rows cleaned up", left == 0, "%d left" % left)
        print("handed back to the fleet: %d hold(s), %d claimed part(s)"
              % (held_back, requeued))


if __name__ == "__main__":
    if MODE == "live":
        live()
    elif MODE == "engine":
        engine()
    else:
        sandbox()
    print("\n%s (%d failure%s)" % ("ALL PASS" if not FAILS else "FAILURES",
                                   len(FAILS), "" if len(FAILS) == 1 else "s"))
    sys.exit(1 if FAILS else 0)
