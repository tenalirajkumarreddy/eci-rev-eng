package com.oldroll.collector;

import org.json.JSONException;
import org.json.JSONObject;

import java.sql.PreparedStatement;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.concurrent.CompletionService;
import java.util.concurrent.ExecutorCompletionService;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

/**
 * The worker loop - the Android twin of work/old_eci/worker.py.
 *
 * Same queue, same ledger, same job kinds (seed_states, seed_acs[,_all],
 * discover_parts, collect_part, collect_auto, epic_lookup), same auto mode:
 * the phone and the PC web app are interchangeable workers over one database.
 * Queued jobs use FOR UPDATE SKIP LOCKED, and a part is claimed inside
 * collect_part only, so two workers never both sweep the same part.
 *
 * Differences from the Python worker, both deliberate:
 *  - orphan recovery only reclaims rows stale for 10 min (parts) / 30 min
 *    (jobs): a live worker on the other machine keeps writing, so its work is
 *    never stolen (the Python version resets every running row, which is only
 *    safe with exactly one worker process);
 *  - offset calibration is best-effort: a failed EPIC lookup must not strand a
 *    freshly collected part in 'running'.
 */
public final class Worker {

    // ---------------------------------------------------------------- state
    public static volatile boolean running;
    public static volatile boolean stopRequested;
    public static volatile long tickAt;
    public static volatile long startedAt;
    public static volatile long jobId;
    public static volatile String note = "";
    public static volatile String lastError;

    // Part sweeps beat the heartbeat: collect_part bumps tick every 200 serials.
    static final long STALE_AFTER_MS = 90_000;

    static final int PART_CHUNK = 200;
    static final int PART_HARD_CAP = 3000;

    static Thread thread;

    private Worker() {}

    public static synchronized void start() {
        if (thread != null && thread.isAlive()) return;
        stopRequested = false;
        thread = new Thread(() -> {
            try {
                runForever();
            } catch (Throwable t) {
                // Last line of defence: an uncaught throwable on any Android
                // thread kills the whole process, so it dies here instead.
                lastError = "worker died: " + t;
                Db.event("worker", lastError, "error");
                running = false;
            }
        }, "old-eci-worker");
        thread.start();
    }

    public static synchronized void stop() {
        stopRequested = true;
    }

    public static synchronized boolean alive() {
        return thread != null && thread.isAlive() && !stopRequested;
    }

    public static Map<String, Object> status() {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("running", running);
        m.put("job_id", jobId == 0 ? null : jobId);
        m.put("note", note);
        m.put("error", lastError);
        long age = tickAt == 0 ? -1 : (System.currentTimeMillis() - tickAt) / 1000;
        m.put("tick_age", age < 0 ? null : Math.round(age * 10.0) / 10.0);
        boolean alive = running && age >= 0 && age * 1000 < STALE_AFTER_MS;
        m.put("alive", alive);
        return m;
    }

    public static boolean uiAlive() {
        return alive();
    }

    static long now() {
        return System.currentTimeMillis();
    }

    // ----------------------------------------------------------- job queue

    static Map<String, Object> claimJob() throws SQLException {
        return Db.q1("update jobs set status='running', started_at=now() "
                + "where id = (select id from jobs where status='queued' "
                + "order by priority desc, id limit 1 for update skip locked) "
                + "returning *");
    }

    static boolean jobCancelled(Long id) {
        if (id == null) return false;
        try {
            Map<String, Object> r = Db.q1("select cancel from jobs where id=?", id);
            return r != null && Boolean.TRUE.equals(r.get("cancel"));
        } catch (Exception e) {
            return false;
        }
    }

    static void finish(Long id, String status, JSONObject result, String error) {
        if (id == null) return;
        try {
            Db.upd("update jobs set status=?, finished_at=now(), result=?::jsonb, "
                    + "error=? where id=?",
                    status, result == null ? null : result.toString(), error, id);
        } catch (Exception ignored) {
            // job bookkeeping must not take the loop down
        }
    }

    static void progress(Long id, JSONObject prog) {
        if (id == null) return;
        try {
            Db.upd("update jobs set progress=?::jsonb where id=?",
                    prog.toString(), id);
        } catch (Exception ignored) { }
    }

    /**
     * Put work left behind by a dead process back in the queue - but only work
     * that is actually stale. A part being swept right now by the PC worker is
     * written every 200 serials, so 'running + updated within 10 min' is live
     * and must stay untouched.
     */
    public static void recoverOrphans() throws SQLException {
        List<Map<String, Object>> parts = Db.updReturning(
                "update old_parts set status='pending', last_serial=0, "
                        + "updated_at=now() where status='running' "
                        + "and updated_at < now() - interval '10 minutes' "
                        + "returning state_cd, ac_no, part_no");
        List<Map<String, Object>> jobs = Db.updReturning(
                "update jobs set status='error', finished_at=now(), "
                        + "error='interrupted by restart' where status='running' "
                        + "and started_at < now() - interval '30 minutes' "
                        + "returning id");
        if (!parts.isEmpty()) {
            StringBuilder sb = new StringBuilder();
            for (Map<String, Object> p : parts) {
                if (sb.length() > 0) sb.append(", ");
                sb.append(p.get("state_cd")).append(" AC").append(p.get("ac_no"))
                        .append(" P").append(p.get("part_no"));
            }
            Db.event("worker", "requeued " + parts.size()
                    + " stale part(s): " + sb);
        }
        if (!jobs.isEmpty()) {
            StringBuilder sb = new StringBuilder();
            for (Map<String, Object> j : jobs) {
                if (sb.length() > 0) sb.append(", ");
                sb.append("#").append(j.get("id"));
            }
            Db.event("worker", "marked " + jobs.size()
                    + " interrupted job(s) as error: " + sb, "warn");
        }
    }

    // ------------------------------------------------------- best pending part

    static final String BEST_PART_SQL =
            "select p.state_cd, p.ac_no, p.part_no, p.name,\n"
          + "       (select count(*) from old_parts d\n"
          + "         where d.state_cd=p.state_cd and d.ac_no=p.ac_no and d.status='done'\n"
          + "           and abs(d.part_no - p.part_no) <= 2) as neighbours_done,\n"
          + "       coalesce((select avg(d.epics) from old_parts d\n"
          + "         where d.state_cd=p.state_cd and d.ac_no=p.ac_no and d.status='done'\n"
          + "           and abs(d.part_no - p.part_no) <= 2), 0) as neighbour_yield\n"
          + "from old_parts p\n"
          + "where p.status in ('pending','error') and coalesce(p.exists_, true)\n"
          + "  %s\n"
          + "order by neighbours_done desc, neighbour_yield desc, "
          + "p.state_cd, p.ac_no, p.part_no\n"
          + "limit 1";

    public static Map<String, Object> bestPendingPart(String stateCd, Integer acNo)
            throws SQLException {
        StringBuilder filters = new StringBuilder();
        List<Object> params = new ArrayList<>();
        if (stateCd != null) {
            filters.append(" and p.state_cd = ?");
            params.add(stateCd);
        }
        if (acNo != null) {
            filters.append(" and p.ac_no = ?");
            params.add(acNo);
        }
        return Db.q1(String.format(BEST_PART_SQL, filters),
                params.toArray(new Object[0]));
    }

    // ------------------------------------------------------------- catalog

    public static Map<String, Object> seedStates() throws SQLException {
        List<Map<String, Object>> rows = new ArrayList<>();
        List<Map<String, Object>> pub = Db.geoQuery(
                "select state_cd, name from public.states");
        String source = "db";
        if (!pub.isEmpty()) {
            for (Map<String, Object> r : pub) {
                Map<String, Object> m = new LinkedHashMap<>();
                m.put("state_cd", r.get("state_cd"));
                m.put("name", r.get("name"));
                m.put("source", "db");
                rows.add(m);
            }
        } else {
            source = "api";
            for (Gateway.StateRow s : Gateway.statesLive()) {
                Map<String, Object> m = new LinkedHashMap<>();
                m.put("state_cd", s.stateCd);
                m.put("name", s.name);
                m.put("source", "api");
                rows.add(m);
            }
        }
        for (Map<String, Object> r : rows) {
            Db.upd("insert into states(state_cd,name,source) values (?,?,?) "
                    + "on conflict (state_cd) do update "
                    + "set name=coalesce(excluded.name, states.name)",
                    r.get("state_cd"), r.get("name"), r.get("source"));
        }
        Db.event("catalog", "seeded " + rows.size() + " states from " + source);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("states", rows.size());
        return out;
    }

    /**
     * Seed ACs: legacy catalogue merged with the live list (one batched write).
     * The legacy DB alone is NOT complete - it lists 175 ACs for S01 where the
     * live list serves 187, and the 12 extra have old-roll data - so live rows
     * are merged on top by (state_cd, ac_no), exactly like seed_acs_all.
     */
    public static Map<String, Object> seedAcsAll(String stateCd) throws SQLException {
        Map<String, Object[]> merged = new TreeMap<>();   // key "S01|1" -> {name,l1,dist,acType}
        String geoSql = "select state_cd, ac_number, ac_name, district_cd from public.acs"
                + (stateCd != null ? " where state_cd=?" : "")
                + " order by state_cd, ac_number";
        for (Map<String, Object> r : Db.geoQuery(geoSql,
                stateCd == null ? new Object[0] : new Object[]{stateCd})) {
            String sc = String.valueOf(r.get("state_cd"));
            int ac = ((Number) r.get("ac_number")).intValue();
            Object[] v = new Object[4];
            v[0] = r.get("ac_name");
            v[2] = r.get("district_cd");
            merged.put(sc + "|" + ac, v);
        }
        int fromLegacy = merged.size();
        int liveOnly = 0;
        // live list merge (below) fills {name, name_l1, district_cd, ac_type}

        List<String> stateRows = new ArrayList<>();
        if (stateCd != null) {
            stateRows.add(stateCd);
        } else {
            for (Map<String, Object> s : Db.q("select state_cd from states order by state_cd")) {
                stateRows.add(String.valueOf(s.get("state_cd")));
            }
        }
        for (String sc : stateRows) {
            List<Gateway.AcRow> rows;
            try {
                rows = Gateway.acsLive(sc);
            } catch (Exception e) {
                Db.event("worker", "live AC list failed for " + sc + ": " + e, "warn");
                continue;
            }
            for (Gateway.AcRow a : rows) {
                String key = sc + "|" + a.acNo;
                Object[] cur = merged.get(key);
                if (cur == null) {
                    cur = new Object[4];
                    merged.put(key, cur);
                    liveOnly++;
                }
                if (a.name != null) cur[0] = a.name;
                if (a.nameL1 != null) cur[1] = a.nameL1;
                if (a.acType != null) cur[3] = a.acType;
                if (a.districtCd != null) cur[2] = a.districtCd;
            }
        }

        if (merged.isEmpty()) {
            Map<String, Object> out = new LinkedHashMap<>();
            out.put("acs", 0);
            out.put("source", "none");
            return out;
        }
        batchUpsertAcs(merged);
        Set<String> states = new HashSet<>();
        for (String k : merged.keySet()) states.add(k.substring(0, k.indexOf('|')));
        Db.event("catalog", "seeded " + merged.size() + " ACs across " + states.size()
                + " states (" + fromLegacy + " from the legacy catalogue, "
                + liveOnly + " only in the live list)");
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("acs", merged.size());
        out.put("states", states.size());
        out.put("from_legacy", fromLegacy);
        out.put("live_only", liveOnly);
        return out;
    }

    static void batchUpsertAcs(Map<String, Object[]> merged) throws SQLException {
        String sql = "insert into acs(state_cd, ac_no, name, name_l1, district_cd, ac_type) "
                + "values (?,?,?,?,?,?) on conflict (state_cd, ac_no) do update set "
                + "name=coalesce(excluded.name, acs.name), "
                + "name_l1=coalesce(excluded.name_l1, acs.name_l1), "
                + "ac_type=coalesce(excluded.ac_type, acs.ac_type), "
                + "district_cd=coalesce(excluded.district_cd, acs.district_cd)";
        Db.withPs(sql, ps -> {
            for (Map.Entry<String, Object[]> e : merged.entrySet()) {
                String key = e.getKey();
                String sc = key.substring(0, key.indexOf('|'));
                int ac = Integer.parseInt(key.substring(key.indexOf('|') + 1));
                Object[] v = e.getValue();
                ps.setString(1, sc);
                ps.setInt(2, ac);
                ps.setString(3, v[0] == null ? null : String.valueOf(v[0]));
                ps.setString(4, v[1] == null ? null : String.valueOf(v[1]));
                ps.setString(5, v[2] == null ? null : String.valueOf(v[2]));
                ps.setString(6, v[3] == null ? null : String.valueOf(v[3]));
                ps.addBatch();
            }
            ps.executeBatch();
        });
    }

    /** Probe part numbers upward until a whole chunk comes back empty.
     *  The floor is a floor, not a limit (S01 AC 1 hid 141 parts once behind a
     *  cap of 12), so probing continues while the chunk top is live, up to 3000. */
    public static Map<String, Object> discoverParts(Long id, String stateCd, int acNo,
                                                    Integer maxPart)
            throws SQLException, JSONException {
        int floor = maxPart != null ? maxPart : Db.intSetting("discover_max_part", 400);
        Db.upd("update acs set discover_status='running', discover_max=?, "
                + "last_error=null where state_cd=? and ac_no=?", floor, stateCd, acNo);
        int workers = Math.max(1, Db.intSetting("workers", 6));

        Map<Integer, String> found = new TreeMap<>();
        int probedTo = 0;
        int start = 1;
        while (start <= PART_HARD_CAP) {
            int end = Math.min(start + PART_CHUNK - 1, PART_HARD_CAP);
            if (probedTo < floor) end = Math.min(Math.max(end, floor), PART_HARD_CAP);
            int hits = 0;
            ExecutorService pool = Executors.newFixedThreadPool(workers);
            try {
                CompletionService<Probe> cs = new ExecutorCompletionService<>(pool);
                for (int n = start; n <= end; n++) {
                    final int part = n;
                    cs.submit(() -> probe(stateCd, acNo, part));
                }
                for (int n = start; n <= end; n++) {
                    try {
                        Probe p = cs.take().get();
                        if (p.exists) {
                            found.put(p.part, p.name);
                            hits++;
                        }
                    } catch (Exception e) {
                        // a failed probe counts as a miss, as in Python's pool.map
                    }
                }
            } finally {
                pool.shutdownNow();
            }
            probedTo = end;
            JSONObject prog = new JSONObject();
            prog.put("phase", "discover");
            prog.put("probed_to", end);
            prog.put("found", found.size());
            progress(id, prog);
            Db.upd("update acs set old_parts_found=?, discover_max=? "
                    + "where state_cd=? and ac_no=?",
                    found.size(), probedTo, stateCd, acNo);
            if (hits == 0 && probedTo >= floor) break;
            start = end + 1;
        }

        boolean truncated = found.containsKey(probedTo) && probedTo >= PART_HARD_CAP;
        String sql = "insert into old_parts(state_cd, ac_no, part_no, name, exists_, status) "
                + "values (?,?,?,?,'pending') on conflict (state_cd, ac_no, part_no) "
                + "do update set name=coalesce(excluded.name, old_parts.name), "
                + "exists_=true, updated_at=now()";
        Db.withPs(sql, ps -> {
            for (Map.Entry<Integer, String> e : found.entrySet()) {
                ps.setString(1, stateCd);
                ps.setInt(2, acNo);
                ps.setInt(3, e.getKey());
                ps.setString(4, e.getValue());
                ps.addBatch();
            }
            ps.executeBatch();
        });
        Db.upd("update states set has_old_data=true, last_checked=now() where state_cd=?",
                stateCd);
        Db.upd("update acs set discover_status='done', old_parts_found=?, "
                        + "discover_max=?, discovered_at=now() "
                        + "where state_cd=? and ac_no=?",
                found.size(), probedTo, stateCd, acNo);
        Db.event("catalog", "discover " + stateCd + " AC " + acNo + ": " + found.size()
                + " old parts (probed 1.." + probedTo + (truncated
                ? " - TRUNCATED at the hard cap" : "") + ")");
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("found", found.size());
        out.put("probed_to", probedTo);
        out.put("truncated", truncated);
        return out;
    }

    static final class Probe {
        int part;
        boolean exists;
        String name;
        Probe(int p, boolean e, String n) { part = p; exists = e; name = n; }
    }

    static Probe probe(String state, int ac, int partNo) {
        Gateway.Eroll w = Gateway.eroll(state, ac, partNo, "");
        String name = null;
        for (Map<String, String> rec : w.payload) {
            String n = rec.get("oldPartName");
            if (n != null && !n.isEmpty()) {
                name = n;
                break;
            }
        }
        boolean exists = !w.payload.isEmpty() || w.status == 200;
        if (exists && w.payload.isEmpty()) {
            for (int serial : new int[]{1, 25, 100}) {
                Gateway.Eroll e = Gateway.eroll(state, ac, partNo, String.valueOf(serial));
                if (!e.payload.isEmpty()) {
                    String n = e.payload.get(0).get("oldPartName");
                    return new Probe(partNo, true, n);
                }
            }
            return new Probe(partNo, false, null);
        }
        return new Probe(partNo, exists, name);
    }

    // ------------------------------------------------------------ collection

    static final String ELECTOR_UPSERT =
            "insert into electors(source_id, state_cd, ac_no, part_no, serial_no,\n"
          + "        full_name, full_name_l1, relative_name, relative_name_l1, relation_type,\n"
          + "        gender, age_snapshot, epic_2003, marked_by_blo,\n"
          + "        cur_state_cd, cur_ac_no, cur_part_no, cur_epic, last_seen)\n"
          + "values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, now())\n"
          + "on conflict (source_id) do update set\n"
          + "  serial_no=excluded.serial_no, full_name=excluded.full_name,\n"
          + "  full_name_l1=excluded.full_name_l1, relative_name=excluded.relative_name,\n"
          + "  relative_name_l1=excluded.relative_name_l1, relation_type=excluded.relation_type,\n"
          + "  gender=excluded.gender, age_snapshot=excluded.age_snapshot,\n"
          + "  epic_2003=excluded.epic_2003, marked_by_blo=excluded.marked_by_blo,\n"
          + "  cur_state_cd=excluded.cur_state_cd, cur_ac_no=excluded.cur_ac_no,\n"
          + "  cur_part_no=excluded.cur_part_no, cur_epic=excluded.cur_epic,\n"
          + "  last_seen=now()";

    static Integer toInt(String v) {
        if (v == null || v.isEmpty()) return null;
        try {
            return (int) Double.parseDouble(v);
        } catch (Exception e) {
            return null;
        }
    }

    static String pick(String a, String b) {
        if (a != null && !a.isEmpty()) return a;
        if (b != null && !b.isEmpty()) return b;
        return null;
    }

    /** One elector row -> the 18 bound params of ELECTOR_UPSERT (_row_tuple). */
    static Object[] rowParams(Map<String, String> rec, String state, int ac, int part) {
        return new Object[]{
                rec.get("id"), state, ac, part, toInt(rec.get("oldPartSerialNo")),
                pick(rec.get("oldFullName"), rec.get("firstName")),
                rec.get("oldFullNameL1"),
                pick(rec.get("oldRelativeFullName"), rec.get("relativeFName")),
                rec.get("oldRelativeFullNameL1"),
                rec.get("relationType"), rec.get("gender"), toInt(rec.get("age")),
                rec.get("epicNumber"), rec.get("markedByBlo"),
                rec.get("bloMappedStateCd"), toInt(rec.get("bloMappedAcNo")),
                toInt(rec.get("bloMappedPartNo")), rec.get("bloMappedEpicNo")
        };
    }

    static void flush(List<Object[]> batch) throws SQLException {
        if (batch.isEmpty()) return;
        Db.withPs(ELECTOR_UPSERT, ps -> {
            for (Object[] row : batch) {
                Db.bind(ps, row);
                ps.addBatch();
            }
            ps.executeBatch();
        });
        batch.clear();
    }

    /** Full serial sweep of one old part -> electors rows; marks it done so it
     *  is never repeated (unless force). The claim lives HERE and only here. */
    public static Map<String, Object> collectPart(Long id, String stateCd, int acNo,
                                                  int partNo, boolean force)
            throws SQLException, JSONException {
        Map<String, Object> row = Db.q1("select * from old_parts where state_cd=? "
                + "and ac_no=? and part_no=?", stateCd, acNo, partNo);
        if (row != null && "done".equals(row.get("status")) && !force) {
            Map<String, Object> out = new LinkedHashMap<>();
            out.put("skipped", true);
            out.put("reason", "already done");
            out.put("records", row.get("records"));
            out.put("epics", row.get("epics"));
            return out;
        }

        Db.upd("insert into old_parts(state_cd, ac_no, part_no, status, started_at, attempts)\n"
                        + "values (?,?,?,'running', now(), 1)\n"
                        + "on conflict (state_cd, ac_no, part_no)\n"
                        + "do update set status='running', started_at=now(), "
                        + "attempts=old_parts.attempts+1, last_error=null, updated_at=now()",
                stateCd, acNo, partNo);

        long t0 = now();
        int workers = Math.max(1, Db.intSetting("workers", 6));
        int cap = Db.intSetting("collect_serial_cap", 3000);
        int rollEnd = Gateway.probeRollEnd(stateCd, acNo, partNo, cap);
        // Publish roll_end before sweeping: it is the denominator the dashboard
        // uses for live speed and ETA, and last_serial only lands every 200.
        Db.upd("update old_parts set roll_end=?, updated_at=now() "
                + "where state_cd=? and ac_no=? and part_no=?",
                rollEnd, stateCd, acNo, partNo);

        int hits = 0, misses = 0, errors = 0, records = 0, epics = 0;
        Set<String> seen = new HashSet<>();
        Map<String, String> meta = new HashMap<>();
        List<Object[]> batch = new ArrayList<>();
        boolean cancelled = false;
        int completed = 0;

        ExecutorService pool = Executors.newFixedThreadPool(workers);
        try {
            CompletionService<Gateway.Eroll> cs = new ExecutorCompletionService<>(pool);
            final int re = rollEnd;
            for (int s = 1; s <= re; s++) {
                final int serial = s;
                cs.submit(() -> Gateway.eroll(stateCd, acNo, partNo,
                        String.valueOf(serial)));
            }
            for (int i = 0; i < re; i++) {
                Gateway.Eroll r;
                try {
                    r = cs.take().get();
                } catch (InterruptedException ie) {
                    cancelled = true;
                    break;
                } catch (Exception e) {
                    errors++;
                    completed++;
                    continue;
                }
                completed++;
                if (r.status == 200 && !r.payload.isEmpty()) {
                    hits++;
                    for (Map<String, String> rec : r.payload) {
                        String rid = rec.get("id");
                        if (rid == null || rid.isEmpty()) continue;   // source_id is the PK
                        if (!seen.add(rid)) continue;
                        records++;
                        if (rec.get("bloMappedEpicNo") != null
                                && !rec.get("bloMappedEpicNo").isEmpty()) epics++;
                        if (meta.isEmpty()) {
                            meta.put("old_state_name", rec.get("oldStateName"));
                            String d = rec.get("oldDistNo");
                            meta.put("old_dist_no",
                                    d == null || d.isEmpty() ? null : d);
                            meta.put("old_dist_name", rec.get("oldDistName"));
                            meta.put("old_ac_name", rec.get("oldAcName"));
                        }
                        batch.add(rowParams(rec, stateCd, acNo, partNo));
                    }
                } else if (r.status == 404) {
                    misses++;
                } else {
                    errors++;
                }
                if (completed % 200 == 0) {
                    flush(batch);
                    tickAt = now();
                    Db.upd("update old_parts set last_serial=?, records=?, epics=?, "
                                    + "old_state_name=coalesce(?, old_state_name), "
                                    + "old_dist_no=coalesce(?, old_dist_no), "
                                    + "old_dist_name=coalesce(?, old_dist_name), "
                                    + "old_ac_name=coalesce(?, old_ac_name), "
                                    + "exists_=true, updated_at=now() "
                                    + "where state_cd=? and ac_no=? and part_no=?",
                            completed, records, epics,
                            meta.get("old_state_name"), meta.get("old_dist_no"),
                            meta.get("old_dist_name"), meta.get("old_ac_name"),
                            stateCd, acNo, partNo);
                    JSONObject prog = new JSONObject();
                    prog.put("phase", "collect");
                    prog.put("serial", completed);
                    prog.put("roll_end", rollEnd);
                    prog.put("records", records);
                    prog.put("epics", epics);
                    progress(id, prog);
                    if (jobCancelled(id) || stopRequested) {
                        cancelled = true;
                        break;
                    }
                }
            }
        } finally {
            pool.shutdownNow();
            try {
                pool.awaitTermination(5, TimeUnit.SECONDS);
            } catch (InterruptedException ignored) { }
            try {
                flush(batch);
            } catch (SQLException ignored) { }
        }

        // Calibration: how far does the mapping's numbering lag the live roll?
        // Best effort by design - a failed lookup must not strand the part.
        Integer offset = null, curPartMode = null;
        if (Db.boolSetting("calibrate_offset", true) && epics > 0) {
            try {
                List<Map<String, Object>> sample = Db.q(
                        "select cur_epic, cur_part_no from electors "
                                + "where state_cd=? and ac_no=? and part_no=? "
                                + "and cur_epic is not null and cur_epic <> '' "
                                + "order by random() limit 3", stateCd, acNo, partNo);
                List<Integer> deltas = new ArrayList<>();
                List<Integer> parts = new ArrayList<>();
                for (Map<String, Object> s : sample) {
                    Object cep = s.get("cur_epic");
                    Object cpart = s.get("cur_part_no");
                    if (cpart != null) parts.add(((Number) cpart).intValue());
                    if (cep == null) continue;
                    EpicCrypto.Result res = EpicCrypto.fetch(String.valueOf(cep));
                    String livePart = EpicCrypto.g(res.content, "partNumber");
                    if (livePart != null && cpart != null) {
                        try {
                            deltas.add(Integer.parseInt(livePart.trim())
                                    - ((Number) cpart).intValue());
                        } catch (NumberFormatException ignored) { }
                    }
                }
                if (!deltas.isEmpty()) {
                    java.util.Collections.sort(deltas);
                    offset = deltas.get(deltas.size() / 2);      // median
                }
                if (!parts.isEmpty()) {
                    Map<Integer, Integer> freq = new HashMap<>();
                    for (int p : parts) freq.put(p, freq.getOrDefault(p, 0) + 1);
                    int best = -1, bestN = -1;
                    for (Map.Entry<Integer, Integer> e : freq.entrySet()) {
                        if (e.getValue() > bestN) {
                            bestN = e.getValue();
                            best = e.getKey();
                        }
                    }
                    curPartMode = best;
                }
            } catch (Throwable t) {
                Db.event("collect", "offset calibration skipped for " + stateCd
                        + " AC" + acNo + " P" + partNo + ": " + t, "warn");
            }
        }

        String status = cancelled ? "pending" : "done";
        Db.upd("update old_parts set status=?, finished_at=now(), records=?, epics=?, "
                        + "unmapped=?, roll_end=?, mapping_offset=?, cur_part_mode=?, "
                        + "old_state_name=coalesce(?, old_state_name), "
                        + "old_dist_no=coalesce(?, old_dist_no), "
                        + "old_dist_name=coalesce(?, old_dist_name), "
                        + "old_ac_name=coalesce(?, old_ac_name), updated_at=now() "
                        + "where state_cd=? and ac_no=? and part_no=?",
                status, records, epics, records - epics, rollEnd, offset, curPartMode,
                meta.get("old_state_name"), meta.get("old_dist_no"),
                meta.get("old_dist_name"), meta.get("old_ac_name"),
                stateCd, acNo, partNo);

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("hits", hits);
        out.put("misses", misses);
        out.put("errors", errors);
        out.put("records", records);
        out.put("epics", epics);
        out.put("roll_end", rollEnd);
        out.put("offset", offset);
        out.put("cur_part_mode", curPartMode);
        out.put("seconds", Math.round((now() - t0) / 100.0) / 10.0);
        out.put("cancelled", cancelled);
        if (!cancelled) {
            Db.event("collect", stateCd + " AC " + acNo + " part " + partNo + ": "
                    + records + " records, " + epics + " EPICs (" + out.get("seconds")
                    + "s)");
        }
        return out;
    }

    public static Map<String, Object> collectAuto(Long id, String stateCd, Integer acNo,
                                                  int maxParts, boolean force)
            throws SQLException, JSONException {
        List<Object> done = new ArrayList<>();
        for (int i = 0; i < maxParts; i++) {
            if (jobCancelled(id) || stopRequested) break;
            Map<String, Object> nxt = bestPendingPart(stateCd, acNo);
            if (nxt == null) break;
            String sc = String.valueOf(nxt.get("state_cd"));
            int a = ((Number) nxt.get("ac_no")).intValue();
            int p = ((Number) nxt.get("part_no")).intValue();
            note = "auto: " + sc + " AC" + a + " P" + p;
            Map<String, Object> res = collectPart(id, sc, a, p, force);
            Map<String, Object> one = new LinkedHashMap<>();
            one.put("state_cd", sc);
            one.put("ac_no", a);
            one.put("part_no", p);
            one.put("result", res);
            one.put("cur_part_mode", res.get("cur_part_mode"));
            done.add(one);
            JSONObject prog = new JSONObject();
            prog.put("phase", "auto");
            prog.put("finished", done.size());
            prog.put("last", Json.z(one));
            progress(id, prog);
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("parts", done.size());
        out.put("done", done);
        return out;
    }

    // ---------------------------------------------------------------- EPIC

    public static Map<String, Object> epicLookupJob(Long id, String epic)
            throws SQLException {
        EpicCrypto.Result res = EpicCrypto.fetch(epic);
        Map<String, Object> prof = EpicCrypto.profileFromContent(res.content);
        boolean found = res.hits.length() > 0;
        Db.upd("insert into epic_lookups(epic, found, http_status, hits, name, name_local,\n"
                        + "  relation, relation_local, relation_type, age, gender, state_cd,\n"
                        + "  state_name, district, ac_no, ac_name, part_no, part_name,\n"
                        + "  part_name_l1, part_id, serial_no, section_no, ps_building,\n"
                        + "  ps_building_l1, record_id, raw)\n"
                        + "values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?::jsonb)\n"
                        + "on conflict (epic) do update set\n"
                        + "  found=excluded.found, http_status=excluded.http_status,\n"
                        + "  hits=excluded.hits, name=excluded.name,\n"
                        + "  name_local=excluded.name_local, relation=excluded.relation,\n"
                        + "  relation_local=excluded.relation_local,\n"
                        + "  relation_type=excluded.relation_type, age=excluded.age,\n"
                        + "  gender=excluded.gender, state_cd=excluded.state_cd,\n"
                        + "  state_name=excluded.state_name, district=excluded.district,\n"
                        + "  ac_no=excluded.ac_no, ac_name=excluded.ac_name,\n"
                        + "  part_no=excluded.part_no, part_name=excluded.part_name,\n"
                        + "  part_name_l1=excluded.part_name_l1, part_id=excluded.part_id,\n"
                        + "  serial_no=excluded.serial_no, section_no=excluded.section_no,\n"
                        + "  ps_building=excluded.ps_building,\n"
                        + "  ps_building_l1=excluded.ps_building_l1,\n"
                        + "  record_id=excluded.record_id, raw=excluded.raw,\n"
                + "  fetched_at=now()",
                res.epic, found, res.status, res.hits.length(),
                prof.get("name"), prof.get("name_local"), prof.get("relation"),
                prof.get("relation_local"), prof.get("relation_type"),
                toInt(String.valueOf(prof.get("age") == null ? "" : prof.get("age"))),
                prof.get("gender"), prof.get("state_cd"), prof.get("state_name"),
                prof.get("district"),
                toInt(String.valueOf(prof.get("ac_no") == null ? "" : prof.get("ac_no"))),
                prof.get("ac_name"),
                toInt(String.valueOf(prof.get("part_no") == null ? "" : prof.get("part_no"))),
                prof.get("part_name"), prof.get("part_name_l1"),
                toInt(String.valueOf(prof.get("part_id") == null ? "" : prof.get("part_id"))),
                toInt(String.valueOf(prof.get("serial_no") == null ? "" : prof.get("serial_no"))),
                toInt(String.valueOf(prof.get("section_no") == null ? "" : prof.get("section_no"))),
                prof.get("ps_building"), prof.get("ps_building_l1"), prof.get("record_id"),
                res.raw == null || res.raw.isEmpty() ? "{}" : res.raw);
        Db.event("epic", "lookup " + res.epic + ": "
                + (found ? "found" : "no record (" + res.status + ")"));
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("epic", res.epic);
        out.put("found", found);
        out.put("status", res.status);
        out.put("profile", prof);
        if (res.error != null) out.put("error", res.error);
        return out;
    }

    // ------------------------------------------------------------ job runner

    public static JSONObject runJob(Map<String, Object> job) {
        long id = ((Number) job.get("id")).longValue();
        String kind = String.valueOf(job.get("kind"));
        String payloadStr = String.valueOf(job.get("payload") == null
                ? "{}" : job.get("payload"));
        jobId = id;
        note = kind + " " + (payloadStr.length() > 80
                ? payloadStr.substring(0, 80) : payloadStr);
        Db.event("worker", "job #" + id + " " + kind + " "
                + (payloadStr.length() > 120 ? payloadStr.substring(0, 120) : payloadStr));
        try {
            JSONObject payload = new JSONObject(payloadStr);
            Map<String, Object> out;
            switch (kind) {
                case "seed_states":
                    out = seedStates();
                    break;
                case "seed_acs":
                    out = seedAcsAll(payload.optString("state_cd", null));
                    break;
                case "seed_acs_all":
                    out = seedAcsAll(null);
                    break;
                case "discover_parts":
                    out = discoverParts(id, payload.getString("state_cd"),
                            payload.getInt("ac_no"),
                            payload.has("max_part") && !payload.isNull("max_part")
                                    ? payload.getInt("max_part") : null);
                    break;
                case "collect_part":
                    out = collectPart(id, payload.getString("state_cd"),
                            payload.getInt("ac_no"), payload.getInt("part_no"),
                            payload.optBoolean("force", false));
                    break;
                case "collect_auto":
                    out = collectAuto(id, payload.optString("state_cd", null),
                            payload.has("ac_no") && !payload.isNull("ac_no")
                                    ? payload.getInt("ac_no") : null,
                            payload.optInt("max_parts", 25),
                            payload.optBoolean("force", false));
                    break;
                case "epic_lookup":
                    out = epicLookupJob(id, payload.getString("epic"));
                    break;
                default:
                    throw new IllegalArgumentException("unknown job kind " + kind);
            }
            JSONObject resJson = out == null ? new JSONObject() : Json.map(out);
            finish(id, "done", resJson, null);
            return resJson;
        } catch (Throwable e) {   // Throwable: record Errors too, never crash the loop
            String msg = e.getClass().getSimpleName() + ": " + e.getMessage();
            finish(id, "error", null, msg);
            Db.event("worker", "job #" + id + " failed: " + msg, "error");
            lastError = msg;
            JSONObject err = new JSONObject();
            try {
                err.put("error", msg);
            } catch (JSONException ignored) {
                // constant key/value - cannot fail
            }
            return err;
        } finally {
            jobId = 0;
        }
    }

    // ----------------------------------------------------------- the loop

    static void runForever() {
        running = true;
        startedAt = now();
        tickAt = now();
        lastError = null;
        Db.event("worker", "worker started");
        try {
            recoverOrphans();
        } catch (Throwable t) {
            Db.event("worker", "orphan recovery failed: " + t, "error");
        }
        while (!stopRequested) {
            tickAt = now();
            try {
                Map<String, Object> job = claimJob();
                if (job != null) {
                    runJob(job);
                    continue;
                }
                if (Db.boolSetting("auto_enabled", false)) {
                    Map<String, Object> nxt = bestPendingPart(null, null);
                    if (nxt != null) {
                        String sc = String.valueOf(nxt.get("state_cd"));
                        int a = ((Number) nxt.get("ac_no")).intValue();
                        int p = ((Number) nxt.get("part_no")).intValue();
                        note = "auto: " + sc + " AC" + a + " P" + p;
                        collectPart(null, sc, a, p, false);
                        continue;
                    }
                    note = "idle - nothing pending";
                } else {
                    note = "idle";
                }
            } catch (Throwable e) {
                // Throwable: a NoClassDefFoundError here would otherwise kill the process
                lastError = e.getClass().getSimpleName() + ": " + e.getMessage();
                Db.event("worker", "loop error: " + lastError, "error");
                Db.dropPool();
                try {
                    Thread.sleep(2000);
                } catch (InterruptedException ignored) { }
            }
            try {
                Thread.sleep(2000);
            } catch (InterruptedException ignored) { }
        }
        running = false;
        Db.event("worker", "worker stopped");
    }
}
