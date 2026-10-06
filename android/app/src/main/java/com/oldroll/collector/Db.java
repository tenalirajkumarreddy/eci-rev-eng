package com.oldroll.collector;

import android.content.Context;
import android.content.SharedPreferences;

import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.ResultSetMetaData;
import java.sql.SQLException;
import java.sql.Statement;
import java.sql.Timestamp;
import java.sql.Types;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Properties;

/**
 * PostgreSQL layer - the Android twin of work/old_eci/db.py.
 *
 * Same database (old_eci), same schema (public), same tables: the phone and the
 * PC web app are interchangeable workers over one ledger. Unlike the Python app
 * there is no pool - Android runs one UI connection (thread-local) plus the
 * worker thread's own connection, which is exactly what psycopg's
 * {@code connect()} is used for on the worker side.
 *
 * Keepalives / timeouts: the server is remote so a NAT can drop a connection
 * silently. tcpKeepAlive + socketTimeout + statement_timeout make a dead peer
 * raise instead of hanging a read forever (same reasoning as CONN_KWARGS).
 */
public final class Db {

    // Defaults = the Python app's ECI_PG_DSN (password already embedded there,
    // in old_eci_collector.py and db.py alike). Editable in Settings.
    public static final String DEF_HOST = "129.225.75.85";
    public static final String DEF_PORT = "5432";
    public static final String DEF_NAME = "old_eci";
    public static final String DEF_USER = "eci_app";
    public static final String DEF_PASS = "Raj@A2Nkufyg";
    public static final String GEO_NAME = "eci";     // legacy read-only catalogue

    public static final String SCHEMA = "public";

    public static volatile String host = DEF_HOST;
    public static volatile String port = DEF_PORT;
    public static volatile String name = DEF_NAME;
    public static volatile String user = DEF_USER;
    public static volatile String pass = DEF_PASS;

    public static volatile String lastError;
    public static volatile long lastOkAt;

    // Small borrow/return pool: UI code runs on short-lived threads, so a
    // thread-local connection would leak a socket per refresh. At most a few
    // physical connections exist; extras are closed on return.
    private static final java.util.concurrent.ArrayBlockingQueue<Connection> POOL =
            new java.util.concurrent.ArrayBlockingQueue<>(3);

    static {
        try {
            Class.forName("org.postgresql.Driver");
        } catch (Exception e) {
            throw new ExceptionInInitializerError(e);
        }
    }

    private Db() {}

    /** Borrow a live connection (opens a new one when the pool is empty). */
    public static Connection borrow() throws SQLException {
        Connection c = POOL.poll();
        if (c != null) {
            try {
                if (c.isValid(2)) return c;
            } catch (Exception ignored) {
                // replace it below
            }
            try { c.close(); } catch (Exception ignored) { }
        }
        return open(name);
    }

    /** Return a connection to the pool (or close it when the pool is full). */
    public static void give(Connection c) {
        if (c == null) return;
        try {
            if (c.isValid(2)) {
                if (!POOL.offer(c)) {
                    try { c.close(); } catch (Exception ignored) { }
                }
                return;
            }
        } catch (Exception ignored) {
            // dead - fall through to close
        }
        try { c.close(); } catch (Exception ignored) { }
    }

    /** Drop every pooled connection (used when the loop resets after an error). */
    public static void dropPool() {
        Connection c;
        while ((c = POOL.poll()) != null) {
            try { c.close(); } catch (Exception ignored) { }
        }
    }

    public static void loadPrefs(Context ctx) {
        SharedPreferences p = ctx.getSharedPreferences("db", Context.MODE_PRIVATE);
        host = p.getString("host", DEF_HOST);
        port = p.getString("port", DEF_PORT);
        name = p.getString("name", DEF_NAME);
        user = p.getString("user", DEF_USER);
        pass = p.getString("pass", DEF_PASS);
    }

    public static void savePrefs(Context ctx, String h, String po, String n,
                                 String u, String pa) {
        host = h; port = po; name = n; user = u; pass = pa;
        ctx.getSharedPreferences("db", Context.MODE_PRIVATE).edit()
                .putString("host", h).putString("port", po).putString("name", n)
                .putString("user", u).putString("pass", pa).apply();
    }

    static String url(String dbName) {
        return "jdbc:postgresql://" + host + ":" + port + "/" + dbName
                + "?sslmode=disable&connectTimeout=10&socketTimeout=120"
                + "&tcpKeepAlive=true&ApplicationName=oldroll-android";
    }

    /** New standalone connection (the worker thread's, like psycopg.connect()). */
    public static Connection open(String dbName) throws SQLException {
        Properties pr = new Properties();
        pr.setProperty("user", user);
        pr.setProperty("password", pass);
        Connection c = DriverManager.getConnection(url(dbName), pr);
        try (Statement s = c.createStatement()) {
            s.execute("set statement_timeout=120000");
        }
        lastError = null;
        lastOkAt = System.currentTimeMillis();
        return c;
    }

    // ------------------------------------------------------------- queries

    public static List<Map<String, Object>> q(String sql, Object... params)
            throws SQLException {
        Connection c = borrow();
        try (PreparedStatement ps = c.prepareStatement(sql)) {
            bind(ps, params);
            try (ResultSet rs = ps.executeQuery()) {
                return rows(rs);
            }
        } catch (SQLException e) {
            lastError = e.getMessage();
            throw e;
        } finally {
            give(c);
        }
    }    /** Row callback for {@link #stream}: values in query column order, no
     *  Map materialization (exports pull 70k+ rows; a List<Map> would both
     *  balloon memory and double the transfer time on this slow uplink). */
    public interface RowSink {
        void row(Object[] vals) throws Exception;
    }

    /**
     * Stream a query through a server-side cursor (autoCommit off + fetchSize),
     * the JDBC twin of psycopg's named cursor - constant memory regardless of
     * result size. The per-transaction {@code set local statement_timeout=0}
     * lets a long export run while the connection's session default (120s,
     * same as CONN_KWARGS) reverts automatically at commit, so pooled
     * connections keep their dead-peer protection.
     */
    public static void stream(String sql, Object[] params, RowSink sink)
            throws Exception {
        Connection c = borrow();
        try {
            c.setAutoCommit(false);
            try (Statement ts = c.createStatement()) {
                ts.execute("set local statement_timeout=0");
            }
            try (PreparedStatement ps = c.prepareStatement(sql)) {
                bind(ps, params);
                ps.setFetchSize(5000);
                try (ResultSet rs = ps.executeQuery()) {
                    ResultSetMetaData md = rs.getMetaData();
                    int n = md.getColumnCount();
                    Object[] vals = new Object[n];
                    while (rs.next()) {
                        for (int i = 0; i < n; i++) vals[i] = rs.getObject(i + 1);
                        sink.row(vals);
                    }
                }
                c.commit();
            }
        } catch (Exception e) {
            try { c.rollback(); } catch (Exception ignored) { }
            lastError = e.getMessage();
            throw e;
        } finally {
            try { c.setAutoCommit(true); } catch (Exception ignored) { }
            give(c);
        }
    }

    public static Map<String, Object> q1(String sql, Object... params) throws SQLException {
        List<Map<String, Object>> r = q(sql, params);
        return r.isEmpty() ? null : r.get(0);
    }

    public static int upd(String sql, Object... params) throws SQLException {
        Connection c = borrow();
        try (PreparedStatement ps = c.prepareStatement(sql)) {
            bind(ps, params);
            return ps.executeUpdate();
        } catch (SQLException e) {
            lastError = e.getMessage();
            throw e;
        } finally {
            give(c);
        }
    }

    /** UPDATE ... RETURNING rows (used for orphan recovery). */
    public static List<Map<String, Object>> updReturning(String sql, Object... params)
            throws SQLException {
        Connection c = borrow();
        try (PreparedStatement ps = c.prepareStatement(sql)) {
            bind(ps, params);
            try (ResultSet rs = ps.executeQuery()) {
                return rows(rs);
            }
        } finally {
            give(c);
        }
    }

    /** Borrow, prepare, hand to the callback, always return. */
    public interface StmWork {
        void run(PreparedStatement ps) throws SQLException;
    }

    public static void withPs(String sql, StmWork work) throws SQLException {
        Connection c = borrow();
        try (PreparedStatement ps = c.prepareStatement(sql)) {
            work.run(ps);
        } finally {
            give(c);
        }
    }

    public static long scalarLong(String sql, Object... params) throws SQLException {
        Map<String, Object> r = q1(sql, params);
        if (r == null || r.isEmpty()) return 0L;
        Object v = r.values().iterator().next();
        if (v == null) return 0L;
        if (v instanceof Number) return ((Number) v).longValue();
        return Long.parseLong(v.toString());
    }

    static void bind(PreparedStatement ps, Object... params) throws SQLException {
        for (int i = 0; i < params.length; i++) {
            Object v = params[i];
            if (v == null) {
                ps.setNull(i + 1, Types.OTHER);
            } else if (v instanceof Boolean) {
                ps.setBoolean(i + 1, (Boolean) v);
            } else if (v instanceof Integer) {
                ps.setInt(i + 1, (Integer) v);
            } else if (v instanceof Long) {
                ps.setLong(i + 1, (Long) v);
            } else if (v instanceof Double) {
                ps.setDouble(i + 1, (Double) v);
            } else {
                ps.setString(i + 1, v.toString());
            }
        }
    }

    static List<Map<String, Object>> rows(ResultSet rs) throws SQLException {
        List<Map<String, Object>> out = new ArrayList<>();
        ResultSetMetaData md = rs.getMetaData();
        int n = md.getColumnCount();
        while (rs.next()) {
            Map<String, Object> row = new LinkedHashMap<>();
            for (int i = 1; i <= n; i++) {
                String label = md.getColumnLabel(i);
                Object v = rs.getObject(i);
                if (v instanceof Timestamp) {
                    row.put(label, ((Timestamp) v).getTime());
                } else {
                    row.put(label, v);
                }
            }
            out.add(row);
        }
        return out;
    }

    // ----------------------------------------------------- settings / events

    /** Reads a settings row. jsonb arrives as text: {@code 6}, {@code true}, {@code "x"}. */
    public static Object setting(String key, Object def) {
        try {
            Map<String, Object> r = q1("select value from settings where key=?", key);
            if (r == null) return def;
            String v = r.get("value") == null ? "" : r.get("value").toString().trim();
            if (v.isEmpty()) return def;
            if (def instanceof Boolean) return "true".equalsIgnoreCase(v);
            if (def instanceof Integer) return (int) Double.parseDouble(v);
            if (def instanceof Long) return (long) Double.parseDouble(v);
            if (v.length() > 1 && v.startsWith("\"") && v.endsWith("\""))
                return v.substring(1, v.length() - 1);
            return v;
        } catch (Exception e) {
            return def;
        }
    }

    public static boolean boolSetting(String key, boolean def) {
        Object v = setting(key, def);
        return v instanceof Boolean ? (Boolean) v : Boolean.parseBoolean(String.valueOf(v));
    }

    public static int intSetting(String key, int def) {
        Object v = setting(key, def);
        if (v instanceof Integer) return (Integer) v;
        try {
            return (int) Double.parseDouble(String.valueOf(v));
        } catch (Exception e) {
            return def;
        }
    }

    /** Writes a settings row as jsonb (same rows the web app reads). */
    public static void setSetting(String key, Object value) throws SQLException {
        String json;
        if (value instanceof Boolean) json = ((Boolean) value) ? "true" : "false";
        else if (value instanceof Number) json = value.toString();
        else json = "\"" + String.valueOf(value).replace("\\", "\\\\")
                .replace("\"", "\\\"") + "\"";
        upd("insert into settings(key, value) values (?, ?::jsonb) "
                + "on conflict (key) do update set value=excluded.value, "
                + "updated_at=now()", key, json);
    }

    public static void event(String source, String message) {
        event(source, message, "info");
    }

    /** Best-effort event row - the web UI's activity feed shows these. */
    public static void event(String source, String message, String level) {
        try {
            upd("insert into events(level, source, message) values (?,?,?)",
                    level == null ? "info" : level, source, message);
        } catch (Exception ignored) {
            // events must never break collection
        }
    }

    // ------------------------------------------------------------- schema

    /** Every table the app owns (schema fast-path check, see initSchema). */
    static final String[] TABLES = {
            "states", "acs", "old_parts", "electors", "current_parts",
            "epic_lookups", "jobs", "events", "settings"
    };

    /** Sentinel columns added by MIGRATIONS - when they exist, DDL must be skipped. */
    static final String[][] SENTINELS = {
            {"acs", "ac_type"},
            {"current_parts", "old_pdf_url"},
            {"old_parts", "old_ac_name"},
    };

    static final Map<String, Object> DEFAULTS = new LinkedHashMap<>();
    static {
        DEFAULTS.put("auto_enabled", false);
        DEFAULTS.put("workers", 6);
        DEFAULTS.put("request_pause_ms", 0);
        DEFAULTS.put("discover_max_part", 400);
        DEFAULTS.put("calibrate_offset", true);
        DEFAULTS.put("collect_serial_cap", 3000);
    }

    static boolean schemaReady(Connection c) throws SQLException {
        try (PreparedStatement ps = c.prepareStatement(
                "select count(*) from information_schema.tables "
                        + "where table_schema = ? and table_name in ("
                        + "'states','acs','old_parts','electors','current_parts',"
                        + "'epic_lookups','jobs','events','settings')")) {
            ps.setString(1, SCHEMA);
            try (ResultSet rs = ps.executeQuery()) {
                rs.next();
                if (rs.getInt(1) != TABLES.length) return false;
            }
        }
        try (PreparedStatement ps = c.prepareStatement(
                "select count(*) from information_schema.columns "
                        + "where table_schema = ? and table_name = ? and column_name = ?")) {
            for (String[] s : SENTINELS) {
                ps.setString(1, SCHEMA);
                ps.setString(2, s[0]);
                ps.setString(3, s[1]);
                try (ResultSet rs = ps.executeQuery()) {
                    rs.next();
                    if (rs.getInt(1) != 1) return false;
                }
            }
        }
        return true;
    }

    /**
     * Create/migrate the schema, idempotently.
     *
     * The catalogue is checked first: the DDL below takes ACCESS EXCLUSIVE locks
     * which deadlock against a busy writer (observed for real with the web app's
     * worker running), so an existing schema is left untouched - same fast path
     * as old_eci_collector.py's _schema_ready().
     */
    public static synchronized void initSchema() throws SQLException {
        Connection c = open(name);
        try {
            boolean ready = schemaReady(c);
            if (!ready) {
                for (String stmt : DDL.split(";")) {
                    String s = stmt.trim();
                    if (s.isEmpty()) continue;
                    try (Statement st = c.createStatement()) {
                        st.execute(s);
                    }
                }
                for (String stmt : MIGRATIONS.split(";")) {
                    String s = stmt.trim();
                    if (s.isEmpty()) continue;
                    try (Statement st = c.createStatement()) {
                        st.execute(s);
                    }
                }
            }
            for (Map.Entry<String, Object> e : DEFAULTS.entrySet()) {
                String json = (e.getValue() instanceof Boolean)
                        ? (Boolean) e.getValue() ? "true" : "false"
                        : e.getValue().toString();
                try (PreparedStatement ps = c.prepareStatement(
                        "insert into settings(key, value) values (?, ?::jsonb) "
                                + "on conflict (key) do nothing")) {
                    ps.setString(1, e.getKey());
                    ps.setString(2, json);
                    ps.executeUpdate();
                }
            }
        } finally {
            try { c.close(); } catch (Exception ignored) { }
        }
    }

    /** Read-only probe of the legacy catalogue DB ([] when unavailable). */
    public static List<Map<String, Object>> geoQuery(String sql, Object... params) {
        try (Connection c = open(GEO_NAME);
             PreparedStatement ps = c.prepareStatement(sql)) {
            bind(ps, params);
            try (ResultSet rs = ps.executeQuery()) {
                return rows(rs);
            }
        } catch (Exception e) {
            return new ArrayList<>();
        }
    }

    // ------------------------------------------------------------- DDL

    static final String DDL = ""
            + "create table if not exists public.states (\n"
            + "  state_cd      text primary key,\n"
            + "  name          text,\n"
            + "  has_old_data  boolean default false,\n"
            + "  source        text default 'api',\n"
            + "  last_checked  timestamptz,\n"
            + "  created_at    timestamptz default now()\n"
            + ");\n"
            + "create table if not exists public.acs (\n"
            + "  state_cd        text not null,\n"
            + "  ac_no           integer not null,\n"
            + "  name            text,\n"
            + "  name_l1         text,\n"
            + "  district_cd     text,\n"
            + "  old_parts_found integer default 0,\n"
            + "  discover_status text default 'pending',\n"
            + "  discover_max    integer,\n"
            + "  discovered_at   timestamptz,\n"
            + "  last_error      text,\n"
            + "  created_at      timestamptz default now(),\n"
            + "  primary key (state_cd, ac_no)\n"
            + ");\n"
            + "create table if not exists public.old_parts (\n"
            + "  state_cd       text not null,\n"
            + "  ac_no          integer not null,\n"
            + "  part_no        integer not null,\n"
            + "  name           text,\n"
            + "  exists_        boolean default true,\n"
            + "  status         text not null default 'pending',\n"
            + "  priority       integer default 100,\n"
            + "  attempts       integer default 0,\n"
            + "  last_serial    integer default 0,\n"
            + "  roll_end       integer,\n"
            + "  records        integer default 0,\n"
            + "  epics          integer default 0,\n"
            + "  unmapped       integer default 0,\n"
            + "  cur_part_mode  integer,\n"
            + "  mapping_offset integer,\n"
            + "  started_at     timestamptz,\n"
            + "  finished_at    timestamptz,\n"
            + "  last_error     text,\n"
            + "  created_at     timestamptz default now(),\n"
            + "  updated_at     timestamptz default now(),\n"
            + "  primary key (state_cd, ac_no, part_no)\n"
            + ");\n"
            + "create index if not exists old_parts_queue_idx\n"
            + "  on public.old_parts (status, priority desc, state_cd, ac_no, part_no);\n"
            + "create index if not exists old_parts_cur_idx on public.old_parts (cur_part_mode);\n"
            + "create table if not exists public.electors (\n"
            + "  source_id     text primary key,\n"
            + "  state_cd      text not null,\n"
            + "  ac_no         integer not null,\n"
            + "  part_no       integer not null,\n"
            + "  serial_no     integer,\n"
            + "  full_name     text,\n"
            + "  full_name_l1  text,\n"
            + "  relative_name text,\n"
            + "  relative_name_l1 text,\n"
            + "  relation_type text,\n"
            + "  gender        text,\n"
            + "  age_snapshot  integer,\n"
            + "  epic_2003     text,\n"
            + "  marked_by_blo text,\n"
            + "  cur_state_cd  text,\n"
            + "  cur_ac_no     integer,\n"
            + "  cur_part_no   integer,\n"
            + "  cur_epic      text,\n"
            + "  first_seen    timestamptz default now(),\n"
            + "  last_seen     timestamptz default now()\n"
            + ");\n"
            + "create index if not exists electors_epic_idx on public.electors (cur_epic);\n"
            + "create index if not exists electors_old_idx on public.electors (state_cd, ac_no, part_no, serial_no);\n"
            + "create index if not exists electors_cur_idx on public.electors (cur_state_cd, cur_ac_no, cur_part_no);\n"
            + "create index if not exists electors_name_idx on public.electors (lower(full_name));\n"
            + "create table if not exists public.current_parts (\n"
            + "  state_cd    text not null,\n"
            + "  ac_no       integer not null,\n"
            + "  part_no     integer not null,\n"
            + "  part_name   text,\n"
            + "  part_name_l1 text,\n"
            + "  part_id     bigint,\n"
            + "  district_cd text,\n"
            + "  fetched_at  timestamptz default now(),\n"
            + "  primary key (state_cd, ac_no, part_no)\n"
            + ");\n"
            + "create table if not exists public.epic_lookups (\n"
            + "  epic          text primary key,\n"
            + "  found         boolean,\n"
            + "  http_status   integer,\n"
            + "  hits          integer,\n"
            + "  name          text,\n"
            + "  name_local    text,\n"
            + "  relation      text,\n"
            + "  relation_local text,\n"
            + "  relation_type text,\n"
            + "  age           integer,\n"
            + "  gender        text,\n"
            + "  state_cd      text,\n"
            + "  state_name    text,\n"
            + "  district      text,\n"
            + "  ac_no         integer,\n"
            + "  ac_name       text,\n"
            + "  part_no       integer,\n"
            + "  part_name     text,\n"
            + "  part_name_l1  text,\n"
            + "  part_id       bigint,\n"
            + "  serial_no     integer,\n"
            + "  section_no    integer,\n"
            + "  ps_building   text,\n"
            + "  ps_building_l1 text,\n"
            + "  record_id     text,\n"
            + "  raw           jsonb,\n"
            + "  fetched_at    timestamptz default now()\n"
            + ");\n"
            + "create table if not exists public.jobs (\n"
            + "  id          bigserial primary key,\n"
            + "  kind        text not null,\n"
            + "  payload     jsonb not null default '{}',\n"
            + "  mode        text default 'manual',\n"
            + "  status      text default 'queued',\n"
            + "  priority    integer default 100,\n"
            + "  progress    jsonb default '{}',\n"
            + "  result      jsonb,\n"
            + "  error       text,\n"
            + "  cancel      boolean default false,\n"
            + "  created_at  timestamptz default now(),\n"
            + "  started_at  timestamptz,\n"
            + "  finished_at timestamptz\n"
            + ");\n"
            + "create index if not exists jobs_queue_idx on public.jobs (status, priority desc, id);\n"
            + "create table if not exists public.events (\n"
            + "  id      bigserial primary key,\n"
            + "  ts      timestamptz default now(),\n"
            + "  level   text default 'info',\n"
            + "  source  text,\n"
            + "  message text\n"
            + ");\n"
            + "create table if not exists public.settings (\n"
            + "  key        text primary key,\n"
            + "  value      jsonb,\n"
            + "  updated_at timestamptz default now()\n"
            + ");\n"
            + "create or replace view public.v_overall as\n"
            + "select\n"
            + "  (select count(*) from public.states)                              as states,\n"
            + "  (select count(*) from public.acs)                                 as acs,\n"
            + "  (select count(*) from public.old_parts)                           as old_parts,\n"
            + "  (select count(*) from public.old_parts where status = 'done')     as done_parts,\n"
            + "  (select count(*) from public.old_parts where status = 'pending')  as pending_parts,\n"
            + "  (select count(*) from public.old_parts where status = 'running')  as running_parts,\n"
            + "  (select count(*) from public.old_parts where status = 'error')    as error_parts,\n"
            + "  (select coalesce(sum(records),0) from public.old_parts)           as records,\n"
            + "  (select coalesce(sum(epics),0) from public.old_parts)             as epics,\n"
            + "  (select count(*) from public.electors)                            as electors,\n"
            + "  (select count(distinct cur_epic) from public.electors\n"
            + "     where cur_epic is not null and cur_epic <> '')                 as unique_epics,\n"
            + "  (select count(*) from public.epic_lookups)                        as epic_lookups;\n";

    static final String MIGRATIONS = ""
            + "alter table public.old_parts add column if not exists old_state_name text;\n"
            + "alter table public.old_parts add column if not exists old_dist_no    text;\n"
            + "alter table public.old_parts add column if not exists old_dist_name  text;\n"
            + "alter table public.old_parts add column if not exists old_ac_name    text;\n"
            + "alter table public.acs add column if not exists ac_type text;\n"
            + "alter table public.current_parts add column if not exists ps_type     text;\n"
            + "alter table public.current_parts add column if not exists ps_caty    text;\n"
            + "alter table public.current_parts add column if not exists old_pdf_url text;\n";
}
