package com.oldroll.collector;

import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.ListView;
import android.widget.TextView;
import android.widget.Toast;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * EPIC processor - the Android twin of the web UI's EPIC screen: encrypted
 * national-display search (same wire contract as the garuda APK), cached in
 * the shared epic_lookups table, with the recent-lookup history.
 */
public class EpicActivity extends Activity {

    EditText etEpic;
    CheckBox cbForce;
    Button btnLookup;
    TextView tvResult;
    ListView lvHistory;

    final List<String> history = new ArrayList<>();
    ArrayAdapter<String> historyAdapter;
    volatile boolean busy;

    final Handler main = new Handler(Looper.getMainLooper());

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_epic);
        Db.loadPrefs(this);

        etEpic = findViewById(R.id.etEpic);
        cbForce = findViewById(R.id.cbForce);
        btnLookup = findViewById(R.id.btnLookup);
        tvResult = findViewById(R.id.tvResult);
        lvHistory = findViewById(R.id.lvHistory);

        historyAdapter = new ArrayAdapter<>(this, android.R.layout.simple_list_item_1,
                history);
        lvHistory.setAdapter(historyAdapter);

        btnLookup.setOnClickListener(v -> lookup());
        lvHistory.setOnItemClickListener((parent, view, pos, id) -> {
            String epic = history.get(pos).split(" ")[0];
            etEpic.setText(epic);
            lookup();
        });

        loadHistory();
    }

    void lookup() {
        final String epic = EpicCrypto.normalize(etEpic.getText().toString());
        if (epic.isEmpty()) {
            Toast.makeText(this, "enter an EPIC", Toast.LENGTH_SHORT).show();
            return;
        }
        if (busy) return;
        busy = true;
        btnLookup.setEnabled(false);
        tvResult.setText("searching " + epic + " …");

        final boolean force = cbForce.isChecked();
        new Thread(() -> {
            try {
                final Map<String, Object> out;
                if (!force) {
                    Map<String, Object> cached = Db.q1(
                            "select * from epic_lookups where epic=?", epic);
                    if (cached != null) {
                        out = cached;
                        runOnUiThread(() -> render(cached, true));
                        busy = false;
                        runOnUiThread(() -> btnLookup.setEnabled(true));
                        return;
                    }
                }
                Map<String, Object> res = Worker.epicLookupJob(null, epic);
                Map<String, Object> row = Db.q1(
                        "select * from epic_lookups where epic=?", epic);
                final Map<String, Object> shown = row != null ? row : res;
                runOnUiThread(() -> render(shown, false));
                loadHistory();
            } catch (Throwable e) {   // Throwable: an Error must not kill the process
                final String m = e.getMessage();
                runOnUiThread(() -> {
                    tvResult.setText("error: " + m);
                    busy = false;
                    btnLookup.setEnabled(true);
                });
                return;
            }
            runOnUiThread(() -> {
                busy = false;
                btnLookup.setEnabled(true);
            });
        }, "epic").start();
    }

    @SuppressWarnings("unchecked")
    void render(Map<String, Object> r, boolean cached) {
        boolean found = Boolean.TRUE.equals(r.get("found"));
        StringBuilder sb = new StringBuilder();
        sb.append(r.get("epic")).append("  ");
        if (found) sb.append("FOUND");
        else sb.append("not found");
        if (cached) sb.append("  (cached)");
        sb.append("\nhits ").append(r.get("hits"))
                .append(" · http ").append(r.get("http_status")).append('\n');

        if (found) {
            add(sb, "name", r.get("name"));
            add(sb, "name (L1)", r.get("name_local"));
            Object rt = r.get("relation_type");
            add(sb, "relation", (rt == null ? "" : r.get("relation"))
                    + (rt == null ? "" : " [" + Labels.relationLabel(String.valueOf(rt), false) + "]"));
            add(sb, "age", r.get("age"));
            add(sb, "gender", Labels.genderLabel(
                    r.get("gender") == null ? null : String.valueOf(r.get("gender"))));
            add(sb, "state", str(r.get("state_name")) + " " + str(r.get("state_cd")));
            add(sb, "district", r.get("district"));
            add(sb, "AC", str(r.get("ac_no")) + " " + str(r.get("ac_name")));
            add(sb, "part", str(r.get("part_no")) + " " + str(r.get("part_name")));
            add(sb, "serial", r.get("serial_no"));
            add(sb, "building", r.get("ps_building"));
        }
        if (r.get("raw") != null) {
            String raw = String.valueOf(r.get("raw"));
            sb.append("\nraw: ").append(raw.length() > 400
                    ? raw.substring(0, 400) + "…" : raw);
        }
        tvResult.setText(sb.toString());
    }

    static void add(StringBuilder sb, String k, Object v) {
        if (v == null || String.valueOf(v).isEmpty()) return;
        sb.append(k).append(": ").append(v).append('\n');
    }

    static String str(Object o) {
        return o == null ? "" : String.valueOf(o);
    }

    void loadHistory() {
        new Thread(() -> {
            try {
                List<Map<String, Object>> rows = Db.q(
                        "select epic, found, name, part_name, fetched_at from "
                                + "epic_lookups order by fetched_at desc limit 20");
                final List<String> out = new ArrayList<>();
                for (Map<String, Object> r : rows) {
                    out.add(str(r.get("epic")) + "  "
                            + (Boolean.TRUE.equals(r.get("found")) ? "✓ " : "✗ ")
                            + str(r.get("name"))
                            + (r.get("part_name") == null ? "" : " · " + r.get("part_name")));
                }
                runOnUiThread(() -> {
                    history.clear();
                    history.addAll(out);
                    historyAdapter.notifyDataSetChanged();
                });
            } catch (Throwable ignored) {
                // history is optional
            }
        }, "epic-hist").start();
    }

    @Override
    protected void onResume() {
        super.onResume();
        loadHistory();
    }
}
