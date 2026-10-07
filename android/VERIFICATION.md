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
