package com.oldroll.collector;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.view.View;
import android.widget.AdapterView;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.ListView;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * Browse + collect - the Android twin of the web UI's browse screen:
 * pick state -> pick AC (seed/discover) -> parts list (tap to collect,
 * long-press for force), Auto-AC queueing, CSV export.
 *
 * Long actions (discover/collect) are QUEUED in the shared jobs table exactly
 * like /api/collect and /api/acs/discover, then the collector service is
 * started if it is not already running - so the job also shows up in the web
 * UI's feed and either worker can pick it up.
 */
public class SelectActivity extends Activity {

    Spinner stState, stAc;
    TextView tvAcInfo;
    ListView lvParts;

    final List<String> stateLabels = new ArrayList<>();
    final List<String> stateCds = new ArrayList<>();
    final List<String> acLabels = new ArrayList<>();
    final List<Integer> acNos = new ArrayList<>();
    final List<PartRow> partRows = new ArrayList<>();

    ArrayAdapter<String> stateAdapter, acAdapter;
    PartsAdapter partsAdapter;

    String curState;
    Integer curAc;
    boolean updatingSpinners;
    int pendingExportPerm;

    final Handler main = new Handler(Looper.getMainLooper());
    final Runnable refresher = new Runnable() {
        @Override public void run() {
            loadParts();
            main.postDelayed(this, 4000);
        }
    };

    static final class PartRow {
        int partNo, records, rollEnd;
        String name, status, curName;
        Integer offset;
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_select);
        Db.loadPrefs(this);

        stState = findViewById(R.id.stState);
        stAc = findViewById(R.id.stAc);
        tvAcInfo = findViewById(R.id.tvAcInfo);
        lvParts = findViewById(R.id.lvParts);

        stateAdapter = new ArrayAdapter<>(this, android.R.layout.simple_spinner_item,
                stateLabels);
        stateAdapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        stState.setAdapter(stateAdapter);

        acAdapter = new ArrayAdapter<>(this, android.R.layout.simple_spinner_item, acLabels);
        acAdapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        stAc.setAdapter(acAdapter);

        partsAdapter = new PartsAdapter();
        lvParts.setAdapter(partsAdapter);

        stState.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            @Override public void onItemSelected(AdapterView<?> parent, View view,
                                                 int pos, long id) {
                if (updatingSpinners || stateCds.isEmpty()) return;
                curState = stateCds.get(pos);
                loadAcs();
            }
            @Override public void onNothingSelected(AdapterView<?> parent) { }
        });
        stAc.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            @Override public void onItemSelected(AdapterView<?> parent, View view,
                                                 int pos, long id) {
                if (updatingSpinners || acNos.isEmpty()) return;
                curAc = acNos.get(pos);
                loadParts();
                loadAcInfo();
            }
            @Override public void onNothingSelected(AdapterView<?> parent) { }
        });

        lvParts.setOnItemClickListener((parent, view, pos, id) -> {
            PartRow p = partRows.get(pos);
            new AlertDialog.Builder(this)
                    .setTitle("Part " + p.partNo)
                    .setItems(new String[]{"Collect", "Force re-collect", "Cancel"},
                            (d, which) -> {
                                if (which == 0) {
                                    queue("collect_part", collectPayload(p.partNo, false),
                                            "manual", 100);
                                } else if (which == 1) {
                                    queue("collect_part", collectPayload(p.partNo, true),
                                            "manual", 100);
                                }
                            })
                    .show();
        });

        findViewById(R.id.btnSeedStates).setOnClickListener(v -> bg(() -> {
            Worker.seedStates();
            return null;
        }, "states seeded"));
        findViewById(R.id.btnSeedAcs).setOnClickListener(v -> {
            if (curState == null) return;
            final String sc = curState;
            bg(() -> Worker.seedAcsAll(sc), "ACs seeded");
        });
        findViewById(R.id.btnDiscover).setOnClickListener(v -> {
            if (curState == null || curAc == null) return;
            JSONObjectPayload p = new JSONObjectPayload();
            p.put("state_cd", curState);
            p.put("ac_no", curAc);
            p.put("max_part", Db.intSetting("discover_max_part", 400));
            queue("discover_parts", p, "manual", 100);
        });
        findViewById(R.id.btnAutoAc).setOnClickListener(v -> {
            if (curState == null || curAc == null) return;
            JSONObjectPayload p = new JSONObjectPayload();
            p.put("state_cd", curState);
            p.put("ac_no", curAc);
            p.put("max_parts", 25);
            queue("collect_auto", p, "auto", 100);
        });
        findViewById(R.id.btnExport).setOnClickListener(v -> exportCsv());
        findViewById(R.id.btnRefresh).setOnClickListener(v -> {
            loadStates();
            loadAcs();
            loadParts();
            loadAcInfo();
        });

        loadStates();
    }

    /** Minimal payload holder so lambdas stay readable. */
    static final class JSONObjectPayload extends java.util.LinkedHashMap<String, Object> {
        JSONObjectPayload put2(String k, Object v) { put(k, v); return this; }
    }

    JSONObjectPayload collectPayload(int partNo, boolean force) {
        JSONObjectPayload p = new JSONObjectPayload();
        p.put("state_cd", curState);
        p.put("ac_no", curAc);
        p.put("part_no", partNo);
        p.put("force", force);
        return p;
    }

    // ---------------------------------------------------------- actions

    interface Work { Object run() throws Exception; }

    void bg(Work work, String okToast) {
        new Thread(() -> {
            try {
                final Object r = work.run();
                runOnUiThread(() -> {
                    if (okToast != null) {
                        Toast.makeText(this, okToast + (r == null ? "" : " " + r),
                                Toast.LENGTH_SHORT).show();
                    }
                    loadStates();
                    loadAcs();
                    loadParts();
                    loadAcInfo();
                });
            } catch (Throwable e) {   // Throwable: an Error must not kill the process
                final String m = e.getMessage();
                runOnUiThread(() -> Toast.makeText(this, m, Toast.LENGTH_LONG).show());
            }
        }, "ui-db").start();
    }

    /** Queue a job in the shared jobs table (port of app.enqueue) and make sure
     *  a worker is around to run it. */
    void queue(String kind, java.util.Map<String, Object> payload, String mode,
               int priority) {
        final String body = Json.map(payload).toString();
        bg(() -> {
            Map<String, Object> row = Db.q1(
                    "insert into jobs(kind, payload, mode, priority) "
                            + "values (?,?::jsonb,?,?) returning id, kind, status",
                    kind, body, mode, priority);
            Db.event("api", "queued #" + row.get("id") + " " + kind + " "
                    + (body.length() > 120 ? body.substring(0, 120) : body));
            return row.get("id");
        }, "queued");
        if (!CollectorService.active) {
            CollectorService.start(this);
            Toast.makeText(this, "Collector started to run the job",
                    Toast.LENGTH_SHORT).show();
        }
    }

    void exportCsv() {
        if (Build.VERSION.SDK_INT < 29
                && checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE)
                != PackageManager.PERMISSION_GRANTED) {
            pendingExportPerm = 1;
            requestPermissions(
                    new String[]{Manifest.permission.WRITE_EXTERNAL_STORAGE}, 2);
            return;
        }
        runExport();
    }

    void runExport() {
        if (curState == null || curAc == null) return;
        final String sc = curState;
        final int ac = curAc;
        new Thread(() -> {
            try {
                final String path = CsvExport.export(this, sc, ac, null, true);
                runOnUiThread(() -> Toast.makeText(this,
                        "CSV saved: " + path, Toast.LENGTH_LONG).show());
            } catch (Throwable e) {
                final String m = String.valueOf(e.getMessage());
                android.util.Log.e("oldroll", "csv export failed: " + sc + " AC" + ac, e);
                Db.event("export", sc + " AC" + ac + " export failed: " + m, "error");
                runOnUiThread(() -> Toast.makeText(this,
                        "export: " + m, Toast.LENGTH_LONG).show());
            }
        }, "csv").start();
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] perms,
                                           int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, perms, grantResults);
        if (requestCode == 2 && grantResults.length > 0
                && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
            runExport();
        }
    }

    // ------------------------------------------------------------ loaders

    void bgLoad(LoadWork work) {
        new Thread(() -> {
            try {
                work.run();
            } catch (Throwable ignored) {
                // load failures leave the previous list on screen
            }
        }, "ui-load").start();
    }

    interface LoadWork { void run() throws Exception; }

    void loadStates() {
        bgLoad(() -> {
            List<Map<String, Object>> rows = Db.q(
                    "select s.state_cd, s.name, s.has_old_data, "
                            + "(select count(*) from acs a where a.state_cd=s.state_cd) acs, "
                            + "(select count(*) from old_parts p where p.state_cd=s.state_cd) parts, "
                            + "(select count(*) from old_parts p where p.state_cd=s.state_cd "
                            + " and p.status='done') done from states s order by s.state_cd");
            final List<String> labels = new ArrayList<>();
            final List<String> cds = new ArrayList<>();
            for (Map<String, Object> r : rows) {
                String cd = String.valueOf(r.get("state_cd"));
                cds.add(cd);
                labels.add(cd + "  " + (r.get("name") == null ? "" : r.get("name"))
                        + (Boolean.TRUE.equals(r.get("has_old_data")) ? " ★" : "")
                        + "  · " + num(r, "acs") + " AC · "
                        + num(r, "done") + "/" + num(r, "parts") + " parts");
            }
            runOnUiThread(() -> {
                String keep = curState;
                updatingSpinners = true;
                stateCds.clear();
                stateCds.addAll(cds);
                stateLabels.clear();
                stateLabels.addAll(labels);
                stateAdapter.notifyDataSetChanged();
                if (keep != null) {
                    int i = stateCds.indexOf(keep);
                    if (i >= 0) stState.setSelection(i);
                    else if (!stateCds.isEmpty()) {
                        curState = stateCds.get(0);
                    }
                } else if (!stateCds.isEmpty()) {
                    curState = stateCds.get(0);
                }
                updatingSpinners = false;
                if (curAc == null) loadAcs();
            });
        });
    }

    void loadAcs() {
        if (curState == null) return;
        final String sc = curState;
        bgLoad(() -> {
            List<Map<String, Object>> rows = Db.q(
                    "select a.ac_no, a.name, a.ac_type, a.discover_status, "
                            + "a.old_parts_found, a.discover_max, a.last_error, "
                            + "(a.discover_max is not null and a.old_parts_found >= a.discover_max) maybe_truncated, "
                            + "(select count(*) from old_parts p where p.state_cd=a.state_cd "
                            + " and p.ac_no=a.ac_no) parts, "
                            + "(select count(*) from old_parts p where p.state_cd=a.state_cd "
                            + " and p.ac_no=a.ac_no and p.status='done') done "
                            + "from acs a where a.state_cd=? order by a.ac_no", sc);
            final List<String> labels = new ArrayList<>();
            final List<Integer> nos = new ArrayList<>();
            for (Map<String, Object> r : rows) {
                int ac = (int) num(r, "ac_no");
                nos.add(ac);
                String type = r.get("ac_type") == null ? "" : " [" + r.get("ac_type") + "]";
                String disc = "discover:" + (r.get("discover_status") == null
                        ? "-" : r.get("discover_status"));
                if (num(r, "discover_max") > 0) {
                    disc += " " + num(r, "old_parts_found") + "/" + num(r, "discover_max");
                }
                if (Boolean.TRUE.equals(r.get("maybe_truncated"))) disc += " ⚠trunc";
                labels.add(ac + "  " + (r.get("name") == null ? "" : r.get("name"))
                        + type + "\n     " + disc + " · parts "
                        + num(r, "done") + "/" + num(r, "parts"));
            }
            runOnUiThread(() -> {
                Integer keep = curAc;
                updatingSpinners = true;
                acNos.clear();
                acNos.addAll(nos);
                acLabels.clear();
                acLabels.addAll(labels);
                acAdapter.notifyDataSetChanged();
                if (keep != null) {
                    int i = acNos.indexOf(keep);
                    if (i >= 0) stAc.setSelection(i);
                    else if (!acNos.isEmpty()) curAc = acNos.get(0);
                } else if (!acNos.isEmpty()) {
                    curAc = acNos.get(0);
                }
                updatingSpinners = false;
                loadParts();
                loadAcInfo();
            });
        });
    }

    void loadParts() {
        if (curState == null || curAc == null) return;
        final String sc = curState;
        final int ac = curAc;
        bgLoad(() -> {
            List<Map<String, Object>> rows = Db.q(
                    "select p.part_no, p.name, p.status, p.records, p.roll_end, "
                            + "p.mapping_offset, "
                            + "coalesce(cp.part_name, '') cur_name "
                            + "from old_parts p left join current_parts cp "
                            + " on cp.state_cd=p.state_cd and cp.ac_no=p.ac_no "
                            + " and cp.part_no=p.cur_part_mode "
                            + "where p.state_cd=? and p.ac_no=? "
                            + "order by p.part_no limit 2000", sc, ac);
            final List<PartRow> out = new ArrayList<>();
            for (Map<String, Object> r : rows) {
                PartRow p = new PartRow();
                p.partNo = (int) num(r, "part_no");
                p.name = r.get("name") == null ? "" : String.valueOf(r.get("name"));
                p.status = r.get("status") == null ? "" : String.valueOf(r.get("status"));
                p.records = (int) num(r, "records");
                p.rollEnd = r.get("roll_end") == null ? 0 : (int) num(r, "roll_end");
                p.offset = r.get("mapping_offset") == null
                        ? null : (int) num(r, "mapping_offset");
                p.curName = r.get("cur_name") == null ? "" : String.valueOf(r.get("cur_name"));
                out.add(p);
            }
            runOnUiThread(() -> {
                partRows.clear();
                partRows.addAll(out);
                partsAdapter.notifyDataSetChanged();
            });
        });
    }

    void loadAcInfo() {
        if (curState == null || curAc == null) return;
        final String sc = curState;
        final int ac = curAc;
        bgLoad(() -> {
            Map<String, Object> r = Db.q1(
                    "select a.name, a.ac_type, a.discover_status, a.discover_max, "
                            + "a.old_parts_found, a.discovered_at, "
                            + "(a.discover_max is not null "
                            + " and a.old_parts_found >= a.discover_max) trunc, "
                            + "(select count(*) from old_parts p where p.state_cd=a.state_cd "
                            + " and p.ac_no=a.ac_no) parts, "
                            + "(select count(*) from old_parts p where p.state_cd=a.state_cd "
                            + " and p.ac_no=a.ac_no and p.status='done') done, "
                            + "(select count(*) from old_parts p where p.state_cd=a.state_cd "
                            + " and p.ac_no=a.ac_no and p.status='pending') pending "
                            + "from acs a where a.state_cd=? and a.ac_no=?", sc, ac);
            final String text;
            if (r == null) {
                text = sc + " AC" + ac + " - not seeded (tap ACs)";
            } else {
                StringBuilder sb = new StringBuilder();
                sb.append(sc).append(" AC").append(ac).append("  ")
                        .append(r.get("name") == null ? "" : r.get("name"));
                if (r.get("ac_type") != null) sb.append(" [").append(r.get("ac_type")).append(']');
                sb.append("\nparts ").append(num(r, "done")).append('/')
                        .append(num(r, "parts")).append(" done · ")
                        .append(num(r, "pending")).append(" pending");
                if (r.get("discover_status") != null) {
                    sb.append(" · discover ").append(r.get("discover_status"));
                    if (r.get("discover_max") != null) {
                        sb.append(" ").append(num(r, "old_parts_found")).append('/')
                                .append(num(r, "discover_max"));
                    }
                }
                if (Boolean.TRUE.equals(r.get("trunc"))) sb.append(" ⚠ TRUNCATED");
                text = sb.toString();
            }
            runOnUiThread(() -> tvAcInfo.setText(text));
        });
    }

    class PartsAdapter extends android.widget.BaseAdapter {
        @Override public int getCount() { return partRows.size(); }
        @Override public Object getItem(int pos) { return partRows.get(pos); }
        @Override public long getItemId(int pos) { return partRows.get(pos).partNo; }
        @Override public View getView(int pos, View convertView, android.view.ViewGroup parent) {
            View v = convertView;
            if (v == null) {
                v = getLayoutInflater().inflate(R.layout.row_part, parent, false);
            }
            PartRow p = partRows.get(pos);
            TextView t1 = v.findViewById(R.id.tvTitle);
            TextView t2 = v.findViewById(R.id.tvSub);
            String mark = "done".equals(p.status) ? "✓" :
                    "running".equals(p.status) ? "▶" :
                    "error".equals(p.status) ? "✗" : "·";
            t1.setText(mark + "  P" + p.partNo + "  " + p.name);
            StringBuilder s = new StringBuilder(p.status);
            s.append(" · ").append(p.records);
            if (p.rollEnd > 0) s.append('/').append(p.rollEnd);
            s.append(" rec");
            if (p.offset != null) s.append(" · off ").append(p.offset);
            if (!p.curName.isEmpty()) s.append(" · → ").append(p.curName);
            t2.setText(s.toString());
            return v;
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        main.postDelayed(refresher, 2000);
    }

    @Override
    protected void onPause() {
        super.onPause();
        main.removeCallbacks(refresher);
    }

    static long num(Map<String, Object> m, String k) {
        if (m == null || m.get(k) == null) return 0;
        return ((Number) m.get(k)).longValue();
    }
}
