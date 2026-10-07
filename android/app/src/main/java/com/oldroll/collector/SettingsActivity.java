package com.oldroll.collector;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.provider.Settings;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.TextView;
import android.widget.Toast;

/**
 * Settings - worker knobs (shared settings table: workers / discover floor /
 * serial cap / offset calibration, read live by both workers) plus the
 * database endpoint (SharedPreferences, defaults = the embedded DSN).
 */
public class SettingsActivity extends Activity {

    EditText etWorkers, etDiscover, etCap, etHost, etPort, etDb, etUser, etPass;
    CheckBox cbCalibrate;
    TextView tvStatus;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_settings);
        Db.loadPrefs(this);

        etWorkers = findViewById(R.id.etWorkers);
        etDiscover = findViewById(R.id.etDiscover);
        etCap = findViewById(R.id.etCap);
        cbCalibrate = findViewById(R.id.cbCalibrate);
        etHost = findViewById(R.id.etHost);
        etPort = findViewById(R.id.etPort);
        etDb = findViewById(R.id.etDb);
        etUser = findViewById(R.id.etUser);
        etPass = findViewById(R.id.etPass);
        tvStatus = findViewById(R.id.tvStatus);

        etHost.setText(Db.host);
        etPort.setText(Db.port);
        etDb.setText(Db.name);
        etUser.setText(Db.user);
        etPass.setText(Db.pass);

        new Thread(() -> {
            // settings table may not exist yet on a fresh install
            try { Db.initSchema(); } catch (Throwable ignored) { }
            // THIS device's own row first ('<key>@<tag>'), falling back to the
            // shared global value when the device has not set its own yet.
            final String tag = Db.myTag();
            final Object w = Db.settingExact("workers@" + tag,
                    Db.setting("workers", 6));
            final Object d = Db.settingExact("discover_max_part@" + tag,
                    Db.setting("discover_max_part", 400));
            final Object c = Db.settingExact("collect_serial_cap@" + tag,
                    Db.setting("collect_serial_cap", 3000));
            final Object cal = Db.settingExact("calibrate_offset@" + tag,
                    Db.setting("calibrate_offset", true));
            runOnUiThread(() -> {
                etWorkers.setText(String.valueOf(w));
                etDiscover.setText(String.valueOf(d));
                etCap.setText(String.valueOf(c));
                cbCalibrate.setChecked(Boolean.TRUE.equals(cal));
            });
        }, "settings-load").start();

        findViewById(R.id.btnSave).setOnClickListener(v -> {
            Db.savePrefs(this, etHost.getText().toString().trim(),
                    etPort.getText().toString().trim(),
                    etDb.getText().toString().trim(),
                    etUser.getText().toString().trim(),
                    etPass.getText().toString());
            final int w = parseInt(etWorkers, 6);
            final int d = parseInt(etDiscover, 400);
            final int c = parseInt(etCap, 3000);
            final boolean cal = cbCalibrate.isChecked();
            new Thread(() -> {
                try {
                    // Every value the settings screen owns is per-device
                    // ('<key>@<tag>'): the phone's numbers never touch the
                    // PC's or another phone's.
                    final String tag = Db.myTag();
                    Db.setSetting("workers", w, tag);
                    Db.setSetting("discover_max_part", d, tag);
                    Db.setSetting("collect_serial_cap", c, tag);
                    Db.setSetting("calibrate_offset", cal, tag);
                    Db.event("api", "settings saved for " + tag
                            + ": workers=" + w + " floor=" + d
                            + " cap=" + c + " calibrate=" + cal);
                    runOnUiThread(() -> Toast.makeText(this, "Saved",
                            Toast.LENGTH_SHORT).show());
                } catch (Throwable e) {
                    final String m = e.getMessage();
                    runOnUiThread(() -> Toast.makeText(this, "save: " + m,
                            Toast.LENGTH_LONG).show());
                }
            }, "settings-save").start();
        });

        findViewById(R.id.btnTest).setOnClickListener(v -> {
            // test uses the edited values without persisting them first
            final String h = etHost.getText().toString().trim();
            final String p = etPort.getText().toString().trim();
            final String n = etDb.getText().toString().trim();
            final String u = etUser.getText().toString().trim();
            final String pw = etPass.getText().toString();
            tvStatus.setText("testing…");
            new Thread(() -> {
                String oldHost = Db.host, oldPort = Db.port, oldName = Db.name;
                String oldUser = Db.user, oldPass = Db.pass;
                String msg;
                try {
                    Db.host = h; Db.port = p; Db.name = n;
                    Db.user = u; Db.pass = pw;
                    long c = Db.scalarLong("select count(*) from old_parts");
                    long done = Db.scalarLong(
                            "select count(*) from old_parts where status='done'");
                    msg = "OK · old_parts " + c + " (done " + done + ")";
                } catch (Throwable e) {
                    msg = "FAIL · " + e.getMessage();
                } finally {
                    Db.host = oldHost; Db.port = oldPort; Db.name = oldName;
                    Db.user = oldUser; Db.pass = oldPass;
                }
                final String m = msg;
                runOnUiThread(() -> tvStatus.setText(m));
            }, "db-test").start();
        });

        findViewById(R.id.btnBattery).setOnClickListener(v -> {
            try {
                Intent i = new Intent(
                        android.provider.Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS,
                        Uri.parse("package:" + getPackageName()));
                startActivity(i);
            } catch (Exception e) {
                Toast.makeText(this, "not supported: " + e.getMessage(),
                        Toast.LENGTH_LONG).show();
            }
        });
    }

    int parseInt(EditText et, int def) {
        try {
            return Integer.parseInt(et.getText().toString().trim());
        } catch (Exception e) {
            return def;
        }
    }
}
