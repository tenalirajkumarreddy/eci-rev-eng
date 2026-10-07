# Multi-device coexistence verification — 2026-10-07

All three collectors (PC web app, Android APK, Colab single-file) were upgraded
to the same coordination contract, then verified against the live `old_eci`
database while the PC worker was actively collecting.

## What changed
- **Atomic claim everywhere**: `old_parts.claimed_by` column + one-statement
  claim `insert … on conflict do update … where status <> 'running'
  returning`. Losers see a visible `skipped: already running elsewhere` result
  instead of double-sweeping.
- **Failure hand-back**: any exception mid-sweep sets the part back to
  `pending` with the error attached (`last_error`), clears `claimed_by`.
- **Stale-only reaper (60 s, live)**: `running` parts untouched for >10 min,
  jobs/discoveries for >30 min are requeued by *any* surviving device, so a
  dead phone/PC/Colab's work is picked up without restarting anything. Live
  devices are never touched because they bump their row every 200 serials.
- **Parts-parallel pipeline**: `parts_parallel` (default 2) parts collected
  concurrently per worker; serial-thread budget (`workers`) divided so the
  gateway load is unchanged. `collect_auto` uses it, the PC auto loop uses it.
- **Probe hint**: `probe_roll_end(hint=)` seeds the serial probe with a
  finished neighbour's `roll_end` (±3 parts), saving ~10 of ~13 requests.
- **Jittered 429/network backoff** in all three clients.

## Verified on the live system (evidence)
1. **Atomic claim semantics** (CPU-side against live DB):
   claim on pending part → `True`, row becomes `running` with
   `claimed_by='pc-inspiron-3128'`; second claim → `False`; live part → `False`.
2. **Stale reclaim**: a forged ghost claim (`running`, heartbeat 11 min old)
   was requeued by `recover_orphans()` → `requeued 1 stale part(s): S01 AC12 P9`
   (events row), part back to `pending`, `claimed_by` cleared. A live PC
   worker's `running` part was *not* touched.
3. **ART batch-exception crash — root-caused and patched in the jar
   (2026-10-07):** jobs #91/#92 on the emulator died with
   `NoSuchMethodError: No direct method <init>(Ljava/lang/String;Ljava/lang/String;I[JLjava/lang/Throwable;)V
   in class Ljava/sql/BatchUpdateException`. Investigation of the official
   pgjdbc 42.7.4 sources showed `disableBatchUpdateExceptions` is **not a real
   42.7.4 property** (no such key in `PGProperty`; the earlier entry below was
   wrong) — the real cause is upstream `BatchResultHandler` using the
   Java-9-only `BatchUpdateException(String,String,int,long[],Throwable)`
   constructor, absent on ART. Fixed properly in
   `libs/patched/BatchResultHandler.java` (recompiled into
   `libs/postgresql-42.7.4-android.jar`): the JDBC-4 `int[]` constructor is
   used and the cause attached with `initCause`. The no-op URL flag was
   removed from `Db.url()`.
   **Payoff — the previously hidden real bug surfaced and was fixed:**
   Android `discoverParts` ran `insert into old_parts(state_cd, ac_no,
   part_no, name, exists_, status) values (?,?,?,?,'pending')` — 6 target
   columns against 5 expressions, so every discover upsert failed with
   `ERROR: INSERT has more target columns than expressions`. Now
   `values (?,?,?,?,true,'pending')` (mirrors worker.py).
4. **Phase overlap**: after restart, consecutive parts start at the same
   second (P12+P13 23:02:51), inter-part gaps ~0–1 s (was ~10 s).
5. **Dashboards were killing the collectors** (found live): 5+ copies of the
   minute-scale `count(distinct cur_epic)` aggregate + the per-poll breakdown
   aggregates saturated the server (BufferMapping waits) → statement timeouts
   on the PC, IO errors on the phone. Fixed: `heavy_counts` and `breakdown`
   are now TTL-cached and single-flight (2/5 min), the web page calls
   breakdown once on load, and the phone refreshes heavy KPIs at most every
   2 min under a CAS guard. Verified: 6 pending aggregates → 0 after fixes;
   first breakdown call 16.9 s, second call 6 ms from cache.
6. **Server start vs live work**: `db.init()` migrations deadlocked against
   the worker once (AccessExclusive vs AccessShare). Mitigated by applying the
   two new columns ahead of startup; log again if the web worker is restarted
   at the exact moment its own claim-writer holds the table.
7. **Settings screen** on the emulator: Test → `OK · old_parts 9908 (done 1988)`.

## Emulator caveat (environment, not code)
After the final APK install, the emulator's foreground service runs
(`isForeground=true id=417`, worker events visible, DB connections
established: `ETABLISHED` to :5432, `rows` incremented), but its gateway
fetches hang once the ActivityManager FGS grace lapses (~1 min after the app
leaves "top"); per-install flushes immediately resume. A verification from a
phone streaming connectivity gap: treat the in-sweep `socketTimeout=120 s`
rows as expected on a charge-restricted emulator; the physical phone
(adb-8PDE457XIRCEPBMF, Googgle TLS debugging) is the real target and the
build is pending its install.

## Current state (2026-10-07 late session)
- PC server PID 49240 (port 8008) with all fixes: dashboard aggregates
  single-flight + TTL-cached, pipeline jobs-yield, reaper, probe hint.
- Android: `collectPipeline` got the same **jobs-queue yield** as the PC —
  an open-ended auto pipeline (`id == null`, both width≤1 and parallel
  branches) breaks out when `select 1 from jobs where status='queued' limit 1`
  says a job is waiting, so discover buttons claim in seconds even while
  parts are pending. Canary discover jobs: #91 running while 7829 parts
  pending — claimed in **9.6 s**.
- **Emulator end-to-end job proof**: canary #93 - queued→running 9.9 s →
  **done 37.6 s** (`found 185, probed_to 500`) — first fully green Android
  discover job on the live DB (after the BatchResultHandler + INSERT fixes).
- **Coexistence re-verified by repeated RUNNING sampling** (9 samples over
  3 min): FOUR distinct claimants — 3 Android worker PIDs
  (android-sdk_gphone64_x86_64-{7855,8266,8536}) + 1 PC process
  (pc-inspiron-88596), each holding its own parts; no shared holds, no
  duplicate sweeps, 0 error events in the last 45 min window.
- Settings hardening on both makers: DEFAULTS now seed *outside* the schema
  fast-path in their own transaction (`on conflict do nothing`), so a legacy
  aborted-transaction row never re-emerges; verified user values preserved
  after service restart (workers=8, parts_parallel=2, calibrate_offset=
  false, auto_enabled=true).
- Emulator caveat stands: fetches stall when the FGS background grace lapses
  (~1 min after the app leaves top); the physical phone
  (adb-8PDE457XIRCEPBMF, wireless debugging) is the real fleet target and
  still needs the new APK installed.
- Colab file: `--no-recover` still available; default reap pass (every 60 s)
  now active by default too, controlled by `a.no_recover`.

## Per-device independence (2026-10-07, latest session)

The symptom: with an Android phone AND the PC/web app running together they
"both did the same things" and their on/off switches seemed to sync. Two root
causes, both fixed:

1. **The picker was fully deterministic.** `BEST_PART_SQL` ordered by
   `neighbours_done desc, neighbour_yield desc, state_cd, ac_no, part_no` - no
   device-varying key - so every device picked the *same* top part in the same
   second; one won the atomic claim and the rest logged skips. The last sort key
   before the stable `state/ac/part` keys is now a per-device hash:
   `md5(p.state_cd || ':' || p.ac_no::text || ':' || p.part_no::text || <device tag>)`.
   The real ranking keys still dominate, so the hash only spreads the
   *equally-good* frontier parts across devices. Implemented in all three makers
   (`worker.py`, `Worker.java`, `old_eci_collector.py`); the tag is appended to
   the query params.

2. **Settings were one global row-set.** Every knob now resolves
   `<key>@<device_tag>` -> shared `<key>` -> default. The phone's service switch
   and Settings screen write the scoped `@tag` row; the web dashboard writes the
   global fleet-default row (and `/api/summary` reports each device's
   `auto_overrides`). `auto_enabled` and `calibrate_offset` are per-device too,
   so the phone can opt out of auto without stopping the PC. Device tags:
   PC `ECI_DEVICE_TAG` env (else hostname), Android `android-<Build.MODEL>`,
   engine `OLD_ECI_DEVICE_TAG` (default `colab`).

### Evidence

**Picker independence (real code path, live DB).** For `S01 AC 26`,
`best_pending_parts` under three tags returned:

```
inspiron                     [140, 234, 281, 124, 136, 402]
android-sdk_gphone64_x86_64  [358, 304, 393,  68, 135, 232]
colab                        [253, 264,  59,  35, 236, 176]
identical top-N across tags? False
first picks: {140, 253, 358}   parts chosen by ALL tags: none
```

Before the fix all three would have been the identical ranking.

**Picker performance (found & fixed this session).** Once discovery grew
`old_parts` past ~30k pending, the two correlated neighbour subqueries
seq-scanned the AC for every pending row and the pick exceeded the 120 s
`statement_timeout` (`QueryCanceled` in the worker loop; the PC stopped
collecting). Added `old_parts_neigh_idx (state_cd, ac_no, status, part_no)` - a
covering index for the neighbour lookup - in all three makers (in the DDL *and*
as an unconditional `create index if not exists` so the `_schema_ok` fast path
cannot skip it on an already-migrated DB). Plan cost fell 7.7x (3.38M -> 0.44M)
and the pick now returns in **~5.5 s** instead of hard-timing out.

**Live coexistence (this session).** With the PC web app + the emulator + the
physical phone all collection: seven samples over ~90 s showed four distinct
claimants each holding their own parts -
`android-CPH2293-29997` (x2), `android-sdk_gphone64_x86_64-14354` (x2) and the
PC (x2) - and the last 5 min of `events` were only `info collect` successes
(S01 AC135 parts 195-210 and AC14 parts 27-28 sweeping in parallel), 0 errors.

**Sticky-error fix (found this session).** `worker_status()['error']` kept
showing a transient `QueryCanceled` after recovery, because the loop only set
`error` on failure and never cleared it. It is now cleared at the top of every
loop turn, so `alive:true` + `error:null` honestly reflects a recovered worker.

### Per-device independence - current state
- PC server port 8008 running with all fixes; `/api/summary` returns
  `settings.device_tag`, `settings.auto_overrides` and top-level `claimants`
  (`split_part(claimed_by,'-',1)` grouped). Verified live:
  `{"auto_enabled": true, "workers": 8, "device_tag": "inspiron",
  "auto_overrides": []}` and `claimants` showing `pc` + `android` simultaneously.
- APK rebuilt (`BUILD SUCCESSFUL`), installed on `emulator-5554`, service
  started; the emulator claims parts under tag `android-sdk_gphone64_x86_64`.
- The physical phone collects under tag `android-CPH2293-...` (claims visible in
  the DB) even though it is not currently visible to `adb`.

## Solo devices: own logs and own tasks (2026-10-07, latest)

The APK was never server-dependent - it has always talked straight to Postgres
over JDBC with its own host/port/name/user/pass in SharedPreferences, so it runs
with the web app stopped. What made it *look* like a remote of the web dashboard
was shared state: both read the same global `events` feed, and both drew from one
shared `jobs` queue (`either worker can pick it up`). Fixed:

- **`events.device text`** - every writer stamps its own tag
  (`db.event`, Android `Db.event`, engine `event`). The app shows **its own**
  feed by default; the `LOGS: mine / all` button switches to the fleet view,
  which prefixes each line with the producing device's tag.
- **`jobs.device text`** - a job carries the tag of the device that queued it,
  and `claim_job` in both makers now selects
  `where status='queued' and (device is null or device = <own tag>)`. A phone's
  manual job is only run by that phone. Rows queued before the change have no
  device and stay claimable by anyone, so nothing was stranded.
- **`events_device_idx (device, id desc)`** and
  **`jobs_device_idx (device, status, priority desc, id)`**, created in the DDL
  *and* unconditionally in each `initSchema`/`init` (the schema fast path skips
  DDL, so a migrated DB would otherwise never get them).

### Android screen (verified by `uiautomator dump` on the emulator)

```
THIS DEVICE
android-sdk_gphone64_x86_64
worker alive  ·  auto ON  ·  mine running 6
db eci_app@129.225.75.85:5432/old_eci
GLOBAL PARTS
2334 / 35774   (pend 33426 · run 14 · err 0)
free 33426  ·  this device runs 6
records 1,851,283 · epics 978,006 · unique 977,414
IN FLIGHT
S01 · AC 135 · part 220   400/670
MY TASKS
#226 seed_states [done]
#224 seed_states [done]
LOGS  [MINE]
15:05:45 worker: worker started
```

`MINE` showed only this device's events; tapping the button flipped it to `ALL`
and the same list gained `[inspiron] collect: S01 AC 135 part 155 ...`, i.e. the
tags are what separates the streams. `MY TASKS` listed only the two jobs queued
for the emulator's tag (the `inspiron` ones queued in the same test were absent).

### Evidence

**Task isolation, against the shipped claim SQL** (synthetic queued jobs,
transaction rolled back so live workers were untouched):

```
queued #228 device='inspiron'   +   #229 device=NULL (legacy)
  emulator claims -> (229, None)  emulator skipped #228
  colab claims    -> None
  pc claims       -> (228, 'inspiron')
```

**Event tagging, live**: `select device, count(*) from events group by 1` splits
cleanly - `inspiron` (the PC) and `android-sdk_gphone64_x86_64` (the emulator),
with only pre-change rows left as `NULL`.

**Claim lock cost** (`ZZTEST` synthetic rows, rolled back): winner 119 ms,
loser 102 ms - one round trip either way, i.e. the same cost as a bare `SELECT`.
The loser learns it lost from the same statement that tried to take the part, so
there is no second round trip and no wasted sweep (a check-then-claim would be
2 round trips and still racy).

**Free-parts pool**: `overall.free_parts` = pending + error = the exact set every
picker is allowed to scan (a running row already belongs to a device).

### Known limitation found live

The physical phone (`android-CPH2293`) still runs the **pre-change APK**. Because
its `claim_job` has no device filter it still steals queued jobs and writes
untagged (`device IS NULL`) events - that is how it was caught. It needs the new
APK installed to become a properly solo device.

## Per-device part reservations (2026-10-07, latest)

### The problem

Two devices ranking the same part both paid a pick and exactly one won the
claim. The loser wasted the pick - now a **1.2-9 s** full scan of 33k+ pending
rows - plus a slot's worth of attention, and then re-ranked from scratch. The
per-device `md5(... || tag)` tie-break already spread *equally* ranked parts
(`[140, 234, 281, ...]` vs `[358, 304, 393, ...]`), but the real ranking keys
(done neighbours, then yield) still dominate, so devices kept colliding on the
same frontier part.

### Design: a reservation, not a queue service

Two nullable columns on `old_parts` - `reserved_by` (device tag),
`reserved_at` - plus a partial index. Layers on the existing contract instead of
replacing it, so the atomic claim remains the only thing that decides who sweeps:

| piece | what it does |
| --- | --- |
| **hide** | the pickers skip **another device's live hold**, so two devices stop ranking the same part at all. Expired holds are visible again. |
| **guard** | `claim_part` / `_mark_running` / `claimPart` refuse a live **foreign** hold, so a pick made just before that device reserved cannot steal it. Own holds always pass - that is how the queue drains. Winning clears the hold. |
| **take** | `take_reserved(n)` moves this device's oldest holds to `running` in **one indexed statement** (partial index, a handful of rows). This replaces the ~5 s picker on the per-part critical path. FIFO on `reserved_at`, so a hold is never held past its turn. |
| **top up** | `reserve_parts(n)` = one candidate picker query (excluding its own live holds, `fresh=1`) + **one batch update**. A candidate another device stamped first simply does not match: a lost race costs a missing row, not a wasted sweep. |
| **expire** | expiry is a **time predicate in the SQL** (`now() - make_interval(secs => ttl)`), used by the picker, the claim and the count - so a crashed or stopped device frees its own queue with no reaper running. The reaper only tidies, and never touches its own or a live hold. |
| **give back** | released on a clean stop, when auto is off while holding parts, and when a pipeline turn yields to a queued job or collects nothing. |

Pipeline: a freed slot is filled from the device's own queue first and only falls
back to the shared picker when the queue is short; the top-up runs **after** the
slots are dispatched. A low-water mark (one spare per slot) keeps the picker at
once per `n_res - low` parts instead of once per part.

Settings (per-device, like every other knob): `part_reserve_n` = 5,
`part_reserve_ttl` = 900 s (floored at 60 s). The TTL must exceed
`N x part duration` - a part is ~5-60 s, so a 5-deep queue is read ~5 min after
it was held; below that a device would lose the back of its own queue.

Rejected alternatives: a separate `part_reservations` table (adds a join to the
already 30k-row picker); pre-claiming N parts as `running` (the known regression:
`collect_part` claims internally, so every part looked running and skipped); a
new `status='reserved'` value (must be handled by every counter, KPI and list
query in three codebases, for no extra guarantee).

Schema applied live in **0.7 s** (nullable columns = no rewrite; the partial
index covers a handful of rows). It is also in all three codebases' DDL,
MIGRATIONS, schema sentinels and the unconditional `init`/`initSchema` index
block, so an already-migrated DB still gets the index.

### Evidence

**1. Semantics - `work/old_eci/reserve_test.py` (27 checks, ALL PASS).** Runs in
its own schema (`rsvtest`) so the live fleet is never touched, and calls the real
functions: exactly-N holds; another device's picker cannot see them while still
seeing all 7 free parts; its claim is refused and `hold_reason` names the holder
(`reserved by rsv-A`); the held row is untouched; top-up adds only new parts and
never re-timestamps a live hold (no FIFO drift); `take_reserved(2)` returns the
two **oldest** holds and leaves them `running` under this worker with the hold
cleared; `collect_part(preclaimed=True)` sweeps instead of skipping; without the
flag it would skip its own claim; a foreign hold blocks a plain claim on the sweep
path; release frees exactly the remainder; a backdated hold is invisible/no longer
counted and can be claimed by another device **without any reaper**; the reaper
clears other devices' expired holds but leaves its own; `part_reserve_n=2`
(per-device setting) is honoured.

**2. Engine parity - `reserve_test.py --engine` (12 checks, ALL PASS)** through
`old_eci_collector`'s own `reserve_parts` / `take_reserved` / `_mark_running` /
`hold_reason` / `collect_part(preclaimed)` / `best_pending_parts(fresh=1)`.

**3. Cost - `reserve_test.py --live` against the real 33.9k-row backlog**
(round trip 67-81 ms; every number below is measured, not estimated):

| operation | cost |
| --- | --- |
| `best_pending_part` (whole backlog) | 1232-1679 ms |
| `take_reserved(1)` from own queue | **67-85 ms** = 0.8-1.3x a round trip (**14-25x** cheaper) |
| `take_reserved(4)` (drain the queue) | 80-102 ms - still one statement |
| claim refused on a foreign hold | 66-100 ms - one round trip, no wasted sweep |
| fleet-wide hold count | 76-121 ms |
| `reserve_parts` top-up | 1.19-2.67 s per 5 parts = 0.24-0.53 s/part amortised |

**4. Live fleet.** With the PC worker restarted on the new code:

- **11 of 11** parts it started came **from its own queue**, **0** from a shared
  picker pick - the per-part decision is now a queue pop.
- its queue oscillated 2->3->4->5 (refill at the low-water mark) and never fell
  below the 2 in-flight slots - a slot was never left without a queued part.
- 4 devices swept in parallel (PC, emulator, phone, plus one reaped orphan claim)
  with no duplicate sweeps.
- Cross-device evidence that holds are respected *between* new-code devices:
  the emulator (new APK) lost **0** holds to the PC and stole **0** of the PC's.

**4b. Android/Java path, observed live on the emulator** (the Java SQL is only
compile-checked by `gradle`, so it was watched in the database):

- `reserveParts` stamped a batch - five holds for the phone's tag, three of them
  with an identical `reserved_at` (one statement);
- the queue refilled in steps (`holds 0 -> 4`, then `-> 3`) whenever a slot
  freed, so a slot was never left without a queued part;
- `takeReserved` + `collectPart(preclaimed)`: `new running=[part 12] from QUEUE: 1`
  - the part was in the previous sample's hold set and came back `running` under
    this device's `WORKER_ID`;
- `releaseReservations` on stop: tapping **STOP** produced
  `worker released 1 part reservation(s) on stop` and the hold count went 1 -> 0;
- the emulator never pulled a part out of the PC's queue (the phone on the old
  APK did - see the limitation below).

**5. Visibility.** Dashboard KPI: `RESERVED (QUEUE) 5 / android-sdk_gphone64_x86_64 3 · inspiron 2 · this device 2`,
header `… · queue 2`; `free (unclaimed)` is now pending+error **minus live holds**
(33,539 of 33,544+0 with 5 held). Android card (verified by `uiautomator dump`):
`free 33555 · reserved 8 · my queue 3 · this device runs 4`.

### Known limitation found live (same class as the last one)

The **phone on the old APK** still ignores reservations - its picker does not skip
holds and its claim has no hold guard - and it took **3 of the PC's holds** during
the run above. Harmless (no duplicate sweep: the PC never started those parts, it
refills automatically, and the leftover `reserved_by` is ignored by every
reservation query because they all filter `status in ('pending','error')`), but
the full effect needs the new APK on it. A stale `reserved_by` left on a `running`
row by old code has one useful side effect: if that part is handed back to
pending, it returns to the holder's queue instead of vanishing.

### Reproducing

```bash
cd work/old_eci
python -X utf8 reserve_test.py            # 27 semantic checks, private schema
python -X utf8 reserve_test.py --engine   # 12 checks on the Colab script
python -X utf8 reserve_test.py --live     # cost vs the real backlog
```

Both sandbox modes drop their schema when they finish; `--live` deletes its
synthetic rows, hands back any real hold it took and requeues any real part it
claimed, so it leaves the fleet exactly as it found it.

## Zero-record guard (2026-10-07, after reservations)

Found while verifying background work: WAF challenges / rate-limit storms answer
the gateway with 200 and no parsable rows, so a sweep could *succeed* with
0 records and the part was marked `done` on the first attempt — permanently
lost. Fingerprint: all 63 such parts carried a degraded `roll_end=30` from the
failed roll-end probe (real neighbours sweep 500–1200 records).

Fix (all three codebases, one condition at finalisation): a completed sweep
with `records == 0` **and** `errors > 0` (evidence of transport trouble) is
handed back as `error` + `last_error='suspicious: 0 records…'` instead of done.
A genuinely empty part misses cleanly (`errors == 0`) and still finishes `done`
in one pass — no extra sweeps for phantoms. Finalisation also now clears
`reserved_by`/`reserved_at`, so a finished part can never look live-held to
another device's picker.

Recovery: the 63 lost parts were requeued (`last_error='requeued: was done
with 0 records…'`); re-sweeps returned 519–1040 records each (e.g. S01 AC20
P163: 0 → 820 records). Verified live: finish events show `res_cleared=true`,
PC `pc-inspiron-58568` and emulator `…-717` claiming independently after the
restart, `/api/health` `worker_error: null`.

Colab note: the engine (`old_eci_collector.py`) has the same guard, but a
running Colab runtime keeps its old copy — restart the runtime to pick it up.
