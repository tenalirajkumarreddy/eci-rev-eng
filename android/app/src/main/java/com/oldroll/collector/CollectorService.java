package com.oldroll.collector;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;

import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * The collector's home: a dataSync foreground service so collection survives
 * screen-off and app-switch, with a live progress notification.
 *
 * Start/stop semantics mirror the web app's worker lifecycle: schema init +
 * stale-orphan recovery on start, the loop finishes (or cleanly cancels) the
 * in-flight part on stop. A partial wake lock keeps the CPU from dozing
 * mid-sweep; network in deep doze is best handled by ignoring battery
 * optimizations (button in Settings).
 */
public class CollectorService extends Service {

    static final String CHANNEL = "collector";
    static final int NOTIFY_ID = 417;
    static final String ACTION_STOP = "com.oldroll.collector.STOP";

    public static volatile boolean active;
    public static volatile String progressLine = "starting…";

    /** Simple listener bus so activities can re-render when the ledger moves. */
    public interface Listener { void onTick(); }
    static final List<Listener> LISTENERS = new CopyOnWriteArrayList<>();

    PowerManager.WakeLock wakeLock;
    Thread ticker;

    public static void start(Context ctx) {
        Intent i = new Intent(ctx, CollectorService.class);
        if (Build.VERSION.SDK_INT >= 26) ctx.startForegroundService(i);
        else ctx.startService(i);
    }

    public static void stop(Context ctx) {
        Intent i = new Intent(ctx, CollectorService.class);
        i.setAction(ACTION_STOP);
        ctx.startService(i);
    }

    @Override
    public void onCreate() {
        super.onCreate();
        Db.loadPrefs(this);
        NotificationChannel ch = new NotificationChannel(CHANNEL, "Collector",
                NotificationManager.IMPORTANCE_LOW);
        ch.setShowBadge(false);
        NotificationManager nm = getSystemService(NotificationManager.class);
        nm.createNotificationChannel(ch);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            Worker.stop();
            active = false;
            stopForeground(true);
            stopSelf();
            return START_NOT_STICKY;
        }

        Notification n = notify("Old ECI: starting…");
        if (Build.VERSION.SDK_INT >= 34) {
            // specialUse: Android 15 caps a dataSync foreground service at
            // 6h/24h and kills it mid-sweep the moment the quota runs out, and
            // a collector's whole job is to run for hours unattended. There is
            // no time limit on specialUse, and this app is sideloaded, so the
            // Play-policy review that type implies does not apply.
            startForeground(NOTIFY_ID, n,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        } else if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIFY_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
        } else {
            startForeground(NOTIFY_ID, n);
        }

        if (!active) {
            active = true;
            PowerManager pm = getSystemService(PowerManager.class);
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "oldroll:worker");
            wakeLock.setReferenceCounted(false);
            acquireWakeLock();

            // Schema first, then the loop (both off the main thread).
            new Thread(() -> {
                try {
                    Db.initSchema();
                    Db.event("android", "schema ready");
                } catch (Throwable t) {   // Throwable: an Error must not kill the process
                    Db.event("android", "schema init failed: " + t, "error");
                }
                Worker.start();
            }, "oldroll-init").start();

            ticker = new Thread(this::tick, "oldroll-ticker");
            ticker.start();
        }
        return START_STICKY;
    }

    /** Hold with a timeout (some OEM battery savers silently drop an
     *  unbounded lock) and re-assert it every tick, so a lock the system took
     *  back mid-sweep is re-acquired within 3s instead of idling the CPU in
     *  doze - which is exactly the 'stalls when the screen is off' symptom. */
    static final long WAKE_MS = 4L * 60 * 60 * 1000;   // 4h, renewed below

    void acquireWakeLock() {
        if (wakeLock != null && !wakeLock.isHeld()) wakeLock.acquire(WAKE_MS);
    }

    void tick() {
        while (active) {
            try {
                acquireWakeLock();
                watchdog();
                String line = buildProgressLine();
                progressLine = line;
                NotificationManager nm = getSystemService(NotificationManager.class);
                nm.notify(NOTIFY_ID, notify(line));
                for (Listener l : LISTENERS) l.onTick();
            } catch (Throwable t) {
                // a ticker failure must not kill the service
            }
            try {
                Thread.sleep(3000);
            } catch (InterruptedException e) {
                return;
            }
        }
    }

    /** The PC worker restarts a thread that died (ensure_worker); without the
     *  same thing here a single uncaught throwable ends an overnight run the
     *  moment the screen is off and nobody is watching. Debounced so a worker
     *  that dies instantly cannot spin into a restart loop. */
    long lastRestart;

    void watchdog() {
        if (!active || Worker.stopRequested || Worker.alive()) return;
        long now = android.os.SystemClock.elapsedRealtime();
        if (now - lastRestart < 30_000) return;
        lastRestart = now;
        String why = Worker.lastError == null ? "no tick" : Worker.lastError;
        Db.event("worker", "service watchdog: restarting worker thread (" + why + ")",
                "warn");
        Worker.start();
    }

    /** "auto ON · S01 AC1 P12 340/560 · 5.1 r/s" - the same facts the web dashboard shows. */
    String buildProgressLine() {
        try {
            java.util.Map<String, Object> cur = Db.q1(
                    "select state_cd, ac_no, part_no, last_serial, roll_end, records "
                            + "from old_parts where status='running' "
                            + "order by started_at desc limit 1");
            boolean auto = Db.boolSetting("auto_enabled", false);
            String head = auto ? "auto ON · " : "auto off · ";
            if (cur == null) {
                boolean alive = Worker.running;
                return head + (alive ? Worker.note : "worker stopped");
            }
            String sc = String.valueOf(cur.get("state_cd"));
            int ac = ((Number) cur.get("ac_no")).intValue();
            int p = ((Number) cur.get("part_no")).intValue();
            int done = cur.get("last_serial") == null ? 0
                    : ((Number) cur.get("last_serial")).intValue();
            Integer end = cur.get("roll_end") == null ? null
                    : ((Number) cur.get("roll_end")).intValue();
            StringBuilder sb = new StringBuilder(head)
                    .append(sc).append(" AC").append(ac).append(" P").append(p)
                    .append(" ").append(done);
            if (end != null) sb.append("/").append(end);
            java.util.Map<String, Object> sp = Db.q1(
                    "select coalesce(sum(extract(epoch from "
                            + "(coalesce(finished_at, now()) - started_at))),0) busy "
                            + "from old_parts where finished_at > now() "
                            + "- interval '1 minute'");
            double busy = sp == null || sp.get("busy") == null ? 0
                    : ((Number) sp.get("busy")).doubleValue();
            if (busy > 0.5) {
                long serials = Db.scalarLong("select coalesce(sum(last_serial),0) "
                        + "from old_parts where finished_at > now() - interval '1 minute'");
                sb.append(" · ").append(Math.round(serials / busy * 10.0) / 10.0)
                        .append(" r/s");
            }
            return sb.toString();
        } catch (Exception e) {
            return "db: " + e.getMessage();
        }
    }

    Notification notify(String text) {
        Intent open = new Intent(this, MainActivity.class)
                .setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent pi = PendingIntent.getActivity(this, 0, open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        Intent stop = new Intent(this, CollectorService.class).setAction(ACTION_STOP);
        PendingIntent stopPi = PendingIntent.getService(this, 1, stop,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        return new Notification.Builder(this, CHANNEL)
                .setSmallIcon(android.R.drawable.stat_sys_download)
                .setContentTitle("Old ECI collector")
                .setContentText(text)
                .setStyle(new Notification.BigTextStyle().bigText(text))
                .setContentIntent(pi)
                .addAction(android.R.drawable.ic_menu_close_clear_cancel,
                        "Stop", stopPi)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .build();
    }

    @Override
    public void onDestroy() {
        active = false;
        Worker.stop();
        if (wakeLock != null && wakeLock.isHeld()) wakeLock.release();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
