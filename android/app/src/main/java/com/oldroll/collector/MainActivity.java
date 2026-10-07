package com.oldroll.collector;

import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Menu;
import android.view.MenuItem;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Dashboard - the Android twin of the web app's index screen: overall KPIs,
 * live in-flight progress with speed/ETA, the auto-mode switch (the shared
 * settings.auto_enabled row), collector start/stop, and the events feed.
 *
 * Refreshes every 3s while visible, exactly like the web page's poller.
 */
public class MainActivity extends Activity {

    TextView tvHealth, tvParts, tvKpis, tvCurrent, tvSpeed, tvDevice, tvTasks;
    ProgressBar pbParts, pbCurrent;
    Switch swAuto;
    Button btnService, btnLogScope;
    LinearLayout llEvents;
    // Logs default to THIS device's own stream; the toggle reveals the whole
    // fleet. 'mine' is what makes the app a solo collector rather than a remote
    // mirror of the web dashboard's feed.
    volatile boolean logsAll;

    final Handler main = new Handler(Looper.getMainLooper());
    boolean refreshing;
    final java.util.concurrent.atomic.AtomicBoolean heavyInFlight =
            new java.util.concurrent.atomic.AtomicBoolean(false);
    boolean updatingAuto;
    // After a user flips the switch, the DB write is async; a refresh reading
    // the OLD value before it lands would visually revert the user's tap (and
    // a second tap would then toggle from the wrong state). Skip programmatic
    // setChecked for a few seconds after a user toggle.
    volatile long autoPendingUntil;

    // heavy counts change slowly - cache like the web app (60s TTL)
    long cachedElectors = -1, cachedUnique = -1;
    long cachedAt;

    final CollectorService.Listener tickListener = () -> main.post(this::refreshLight);

    final Runnable refresher = new Runnable() {
        @Override public void run() {
            refresh();
            main.postDelayed(this, 3000);
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        Db.loadPrefs(this);

        tvHealth = findViewById(R.id.tvHealth);
        tvParts = findViewById(R.id.tvParts);
        tvKpis = findViewById(R.id.tvKpis);
        tvCurrent = findViewById(R.id.tvCurrent);
        tvSpeed = findViewById(R.id.tvSpeed);
        pbParts = findViewById(R.id.pbParts);
        pbCurrent = findViewById(R.id.pbCurrent);
        swAuto = findViewById(R.id.swAuto);
        btnService = findViewById(R.id.btnService);
        llEvents = findViewById(R.id.llEvents);
        tvDevice = findViewById(R.id.tvDevice);
        tvTasks = findViewById(R.id.tvTasks);
        btnLogScope = findViewById(R.id.btnLogScope);
        btnLogScope.setOnClickListener(v -> {
            logsAll = !logsAll;
            btnLogScope.setText(logsAll ? "all" : "mine");
            refresh();
        });

        swAuto.setOnCheckedChangeListener((btn, checked) -> {
            if (updatingAuto) return;
            autoPendingUntil = System.currentTimeMillis() + 5000;
            bg(() -> {                        // Scoped to THIS device: the phone's switch starts/stops
                        // the phone only; the PC's dashboard toggle (global row)
                        // stays the fleet default.
                        Db.setSetting("auto_enabled", checked, Db.myTag());
                Db.event("api", "auto mode " + (checked ? "on" : "off"));
                if (checked) runOnUiThread(() -> CollectorService.start(this));
                return null;
            });
        });

        btnService.setOnClickListener(v -> {
            if (CollectorService.active) {
                CollectorService.stop(this);
                Toast.makeText(this, "Collector stopping (current part finishes cleanly)",
                        Toast.LENGTH_SHORT).show();
            } else {
                ensureNotifPermission();
                CollectorService.start(this);
                Toast.makeText(this, "Collector started", Toast.LENGTH_SHORT).show();
            }
        });

        findViewById(R.id.btnBrowse).setOnClickListener(
                v -> startActivity(new Intent(this, SelectActivity.class)));
        findViewById(R.id.btnEpic).setOnClickListener(
                v -> startActivity(new Intent(this, EpicActivity.class)));

        // Make the schema exist regardless of whether the service was started
        // (idempotent fast path - cheap when the web app already built it).
        new Thread(() -> {
            try {
                Db.initSchema();
            } catch (Throwable t) {
                Db.lastError = "schema: " + t.getMessage();
            }
        }, "schema-init").start();
    }

    void ensureNotifPermission() {
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission("android.permission.POST_NOTIFICATIONS")
                != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{"android.permission.POST_NOTIFICATIONS"}, 1);
        }
    }

    /** Service ticker already queries the ledger - rebind the UI without a full refresh. */
    void refreshLight() {
        main.post(() -> tvHealth.setText(healthLine()));
    }

    @Override
    protected void onResume() {
        super.onResume();
        main.postDelayed(refresher, 100);
        CollectorService.LISTENERS.add(tickListener);
    }

    @Override
    protected void onPause() {
        super.onPause();
        main.removeCallbacks(refresher);
        CollectorService.LISTENERS.remove(tickListener);
    }

    @Override
    public boolean onCreateOptionsMenu(Menu menu) {
        getMenuInflater().inflate(R.menu.main, menu);
        return true;
    }

    @Override
    public boolean onOptionsItemSelected(MenuItem item) {
        int id = item.getItemId();
        if (id == R.id.menu_browse) {
            startActivity(new Intent(this, SelectActivity.class));
            return true;
        }
        if (id == R.id.menu_epic) {
            startActivity(new Intent(this, EpicActivity.class));
            return true;
        }
        if (id == R.id.menu_settings) {
            startActivity(new Intent(this, SettingsActivity.class));
            return true;
        }
        return super.onOptionsItemSelected(item);
    }

    // ------------------------------------------------------------ refresh

    interface Work<T> { T run() throws Exception; }

    <T> void bg(Work<T> work) {
        new Thread(() -> {
            try {
                work.run();
            } catch (Throwable e) {   // Throwable: an Error must not kill the process
                runOnUiThread(() -> Toast.makeText(this,
                        "db: " + e.getMessage(), Toast.LENGTH_LONG).show());
            }
        }, "ui-db").start();
    }

    void refresh() {
        if (refreshing) return;
        refreshing = true;
        bg(() -> {
            try {
                doRefresh();
            } catch (Throwable e) {
                refreshing = false;
                Db.lastError = e.getMessage();
                // Rebind anyway so the health line explains WHY instead of
                // sitting on the initial "connecting…" for ever.
                runOnUiThread(() -> tvHealth.setText(healthLine()));
            }
            return null;
        });
    }

    void doRefresh() throws Exception {
        {
            Map<String, Object> parts = Db.q1("select count(*) old_parts, "
                    + "count(*) filter (where status='done') done_parts, "
                    + "count(*) filter (where status='pending') pending_parts, "
                    + "count(*) filter (where status='running') running_parts, "
                    + "count(*) filter (where status='error') error_parts, "
                    + "coalesce(sum(records),0) records, coalesce(sum(epics),0) epics "
                    + "from old_parts");
            long states = Db.scalarLong("select count(*) from states");
            long acs = Db.scalarLong("select count(*) from acs");
            long lookups = Db.scalarLong("select count(*) from epic_lookups");

            // Heavy electors/unique-EPIC aggregates: refresh at most every 2
            // minutes, and only inside a lock so the app polls at most ONE copy
            // of `count(distinct cur_epic)` (a minute-scale, 40 MB temp-spill
            // query; several concurrent copies starved the workers). Positive:
            // something is refreshing right now; Negative: first boot, not yet
            // loaded.
            if (cachedElectors < 0 || System.currentTimeMillis() - cachedAt > 120_000) {
                if (heavyInFlight.compareAndSet(false, true)) {
                    try {
                        cachedElectors = Db.scalarLong("select count(*) from electors");
                        cachedUnique = Db.scalarLong("select count(distinct cur_epic) "
                                + "from electors where cur_epic is not null and cur_epic <> ''");
                        cachedAt = System.currentTimeMillis();
                    } finally {
                        heavyInFlight.set(false);
                    }
                }
                // Not the leader this tick: serve the (possibly stale) cached
                // numbers - they feed a KPI card, nothing depends on freshness.
            }

            // The current-part card follows THIS device's claim ('claimed_by'
            // starts with this install's tag) instead of whichever device won
            // the last claim - two phones used to show the same part.
            Map<String, Object> cur = Db.q1("select state_cd, ac_no, part_no, "
                    + "last_serial, roll_end, records, "
                    + "extract(epoch from now()-started_at) elapsed "
                    + "from old_parts where status='running' "
                    + "and claimed_by like ? || '%' "
                    + "order by started_at desc limit 1", Db.myTag());
            boolean lastFinished = false;
            if (cur == null) {
                cur = Db.q1("select state_cd, ac_no, part_no, roll_end, records, "
                        + "roll_end last_serial, "
                        + "extract(epoch from (finished_at - started_at)) elapsed "
                        + "from old_parts where finished_at is not null "
                        + "order by finished_at desc limit 1");
                lastFinished = cur != null;
            }

            // 15-minute window, same split as the web app's speed_stats
            Map<String, Object> win = Db.q1("select count(*) parts, "
                    + "coalesce(sum(records),0) records, "
                    + "coalesce(sum(coalesce(roll_end, records)),0) serials, "
                    + "coalesce(sum(extract(epoch from (coalesce(finished_at, now()) "
                    + "- started_at))),0) busy from old_parts "
                    + "where finished_at > now() - interval '15 minutes'");

            boolean auto = Db.boolSetting("auto_enabled", false);  // this device

            // This device's own work and its own task queue - the app is a solo
            // collector, not a thin remote for the web dashboard.
            long myRunning = Db.scalarLong(
                    "select count(*) from old_parts where status='running' "
                            + "and claimed_by like ? || '%'", Db.myTag());
            List<Map<String, Object>> myTasks = Db.q(
                    "select id, kind, status from jobs where device = ? "
                            + "order by id desc limit 8", Db.myTag());

            // Own logs by default (every event carries a device tag); the button
            // switches to the whole fleet.
            List<Map<String, Object>> events = logsAll
                    ? Db.q("select ts, level, source, message, device from events "
                            + "order by id desc limit 25")
                    : Db.q("select ts, level, source, message, device from events "
                            + "where device = ? order by id desc limit 25", Db.myTag());

            final Map<String, Object> fParts = parts;
            final long fStates = states, fAcs = acs, fLookups = lookups;
            final Map<String, Object> fCur = cur;
            final boolean fLast = lastFinished;
            final Map<String, Object> fWin = win;
            final boolean fAuto = auto;
            final long fMyRunning = myRunning;
            final List<Map<String, Object>> fTasks = myTasks;
            final List<Map<String, Object>> fEvents = events;
            runOnUiThread(() -> bind(fParts, fStates, fAcs, fLookups, fCur, fLast,
                    fWin, fAuto, fMyRunning, fTasks, fEvents));
        }
    }

    String healthLine() {
        StringBuilder sb = new StringBuilder();
        sb.append(Db.host).append(':').append(Db.port).append('/').append(Db.name);
        sb.append("  ·  worker ").append(Worker.uiAlive() ? "alive" : "stopped");
        if (Db.boolSetting("auto_enabled", false)) sb.append("  ·  AUTO");
        if (Db.lastError != null) {
            sb.append("\nerr: ").append(Db.lastError);
        }
        if (Worker.note != null && !Worker.note.isEmpty()) {
            sb.append("\n").append(Worker.note);
        }
        return sb.toString();
    }

    void bind(Map<String, Object> parts, long states, long acs, long lookups,
              Map<String, Object> cur, boolean lastFinished, Map<String, Object> win,
              boolean auto, long myRunning, List<Map<String, Object>> myTasks,
              List<Map<String, Object>> events) {
        refreshing = false;
        tvHealth.setText(healthLine());

        long total = num(parts, "old_parts");
        long done = num(parts, "done_parts");
        long pend = num(parts, "pending_parts");
        long run = num(parts, "running_parts");
        long err = num(parts, "error_parts");
        // Free = nobody has processed it and nobody holds it (running parts are
        // already claimed) - the pool every device's picker scans.
        long free = pend + err;
        tvParts.setText(done + " / " + total
                + "   (pend " + pend + " · run " + run + " · err " + err + ")"
                + "\nfree " + free + "  ·  this device runs " + myRunning);
        pbParts.setProgress(total == 0 ? 0 : (int) (done * 100 / total));

        tvDevice.setText(Db.myTag()
                + "\nworker " + (Worker.uiAlive() ? "alive" : "stopped")
                + "  ·  auto " + (auto ? "ON" : "off")
                + "  ·  mine running " + myRunning
                + "\ndb " + Db.user + "@" + Db.host + ":" + Db.port + "/" + Db.name);

        StringBuilder tb = new StringBuilder();
        if (myTasks.isEmpty()) {
            tb.append("no tasks queued on this device");
        } else {
            for (Map<String, Object> t : myTasks) {
                if (tb.length() > 0) tb.append('\n');
                tb.append('#').append(num(t, "id")).append(' ').append(str(t.get("kind")))
                  .append("  [").append(str(t.get("status"))).append(']');
            }
        }
        tvTasks.setText(tb.toString());

        tvKpis.setText(String.format(Locale.US,
                "records %,d · epics %,d · unique %,d\nelectors %,d · lookups %,d · states %d · acs %d",
                num(parts, "records"), num(parts, "epics"), cachedUnique,
                cachedElectors, lookups, states, acs));

        if (cur == null) {
            tvCurrent.setText("idle");
            pbCurrent.setProgress(0);
            tvSpeed.setText("—");
        } else {
            String sc = str(cur.get("state_cd"));
            int ac = (int) num(cur, "ac_no");
            int p = (int) num(cur, "part_no");
            int serial = (int) num(cur, "last_serial");
            Integer end = cur.get("roll_end") == null ? null : (int) num(cur, "roll_end");
            tvCurrent.setText((lastFinished ? "last: " : "")
                    + sc + " · AC " + ac + " · part " + p
                    + "   " + serial + (end != null ? "/" + end : ""));
            if (end != null && end > 0) {
                pbCurrent.setProgress(Math.min(100, serial * 100 / end));
            } else {
                pbCurrent.setProgress(0);
            }
            double elapsed = cur.get("elapsed") == null ? 0
                    : ((Number) cur.get("elapsed")).doubleValue();
            StringBuilder sp = new StringBuilder();
            if (elapsed > 0.1 && serial > 0) {
                double rps = serial / elapsed;
                sp.append(String.format(Locale.US, "%.1f r/s", rps));
                if (end != null && end > serial && rps > 0) {
                    long eta = Math.round((end - serial) / rps);
                    sp.append(" · eta ").append(fmtDur(eta));
                }
            }
            tvSpeed.setText(sp.length() == 0 ? "—" : sp.toString());
        }

        double busy = win == null || win.get("busy") == null ? 0
                : ((Number) win.get("busy")).doubleValue();
        long wParts = num(win, "parts");
        long wRecords = num(win, "records");
        long wSerials = num(win, "serials");
        String speedLine = String.format(Locale.US,
                "15m: %d parts · %,d rec · %.1f parts/h%s",
                wParts, wRecords, wParts / 15.0 * 3600,
                busy > 0.5 ? String.format(Locale.US, " · %.1f rec/busy-s · %.1f req/s",
                        wRecords / busy, wSerials / busy) : "");
        tvSpeed.setText((tvSpeed.getText() + "\n" + speedLine).trim());

        if (System.currentTimeMillis() > autoPendingUntil) {
            updatingAuto = true;
            swAuto.setChecked(auto);
            updatingAuto = false;
        }
        btnService.setText(CollectorService.active ? "Stop" : "Start");

        llEvents.removeAllViews();
        SimpleDateFormat df = new SimpleDateFormat("HH:mm:ss", Locale.US);
        for (Map<String, Object> e : events) {
            TextView t = new TextView(this);
            long ts = e.get("ts") == null ? 0 : ((Number) e.get("ts")).longValue();
            String level = str(e.get("level"));
            // In fleet view, prefix each line with the producing device's tag so
            // 'mine' and 'all' are visually distinct.
            String dev = logsAll && e.get("device") != null
                    ? "[" + e.get("device") + "] " : "";
            t.setText(String.format(Locale.US, "%s %s%s%s: %s",
                    df.format(new Date(ts)), dev, str(e.get("source")),
                    "error".equals(level) ? "!" : "", str(e.get("message"))));
            t.setTextSize(11);
            if ("error".equals(level)) t.setTextColor(0xFFC62828);
            else if ("warn".equals(level)) t.setTextColor(0xFFF9A825);
            llEvents.addView(t);
        }
    }

    static long num(Map<String, Object> m, String k) {
        if (m == null || m.get(k) == null) return 0;
        return ((Number) m.get(k)).longValue();
    }

    static String str(Object o) {
        return o == null ? "" : String.valueOf(o);
    }

    static String fmtDur(long secs) {
        if (secs < 60) return secs + "s";
        if (secs < 3600) return (secs / 60) + "m" + (secs % 60) + "s";
        return (secs / 3600) + "h" + ((secs % 3600) / 60) + "m";
    }
}
