package com.oldroll.collector;

import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.util.Log;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStream;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;

/**
 * CSV export - the Android twin of GET /api/export.csv.
 *
 * Same query, same column order, same filename scheme
 * (epics_<state>_AC<ac>[_P<part>].csv), same UTF-8 BOM so Excel opens it, same
 * default of mapped EPICs only. API 29+ writes through MediaStore into
 * Downloads (no permission); API 26-28 writes the file directly (the
 * WRITE_EXTERNAL_STORAGE permission covers it, maxSdkVersion 28).
 *
 * Rows are STREAMED through Db.stream() (server-side cursor) straight into the
 * output file - the link to the remote Postgres is only ~0.3MB/s and a big AC
 * is 70k+ rows, so materializing a List&lt;Map&gt; first ran past the 120s
 * statement timeout and risked OOM. The MediaStore row is inserted before the
 * query runs, so the file appears in Downloads while it fills (the web app's
 * streaming response behaves the same way); a failed export deletes the
 * partial file.
 */
public final class CsvExport {

    static final String[] COLS = {
            "epic", "name", "name_local", "relation_type", "relation",
            "relation_local", "gender", "age_2003", "epic_2003", "old_serial",
            "old_part", "cur_ac", "cur_part"
    };

    private CsvExport() {}

    static String field(Object v) {
        if (v == null) return "";
        String s = String.valueOf(v);
        if (s.contains(",") || s.contains("\"") || s.contains("\n") || s.contains("\r")) {
            return "\"" + s.replace("\"", "\"\"") + "\"";
        }
        return s;
    }

    /** Returns the filename (Downloads) the CSV was written to. */
    public static String export(Context ctx, String state, int ac, Integer part,
                                boolean epicsOnly) throws Exception {
        StringBuilder where = new StringBuilder("state_cd=? and ac_no=?");
        int n = 2;
        if (part != null) {
            where.append(" and part_no=?");
            n++;
        }
        if (epicsOnly) {
            where.append(" and cur_epic is not null and cur_epic <> ''");
        }
        Object[] params = new Object[n];
        params[0] = state;
        params[1] = ac;
        if (part != null) params[2] = part;

        String sql = "select cur_epic, full_name, full_name_l1, relative_name, "
                + "relative_name_l1, relation_type, gender, age_snapshot, "
                + "epic_2003, serial_no, part_no, cur_ac_no, cur_part_no "
                + "from electors where " + where
                + " order by cur_ac_no, cur_part_no, cur_epic";

        String name = "epics_" + state + "_AC" + ac
                + (part != null ? "_P" + part : "") + ".csv";
        long t0 = System.currentTimeMillis();
        long rows;

        if (Build.VERSION.SDK_INT >= 29) {
            ContentResolver cr = ctx.getContentResolver();
            Uri uri = insertDownload(cr, name);
            // MediaStore renames on conflict (" (1)" suffix) - report the name
            // the row actually got, not the one we asked for.
            try (Cursor cur = cr.query(uri,
                    new String[]{MediaStoreCompat.DISPLAY_NAME_RAW},
                    null, null, null)) {
                if (cur != null && cur.moveToFirst() && cur.getString(0) != null) {
                    name = cur.getString(0);
                }
            } catch (Exception ignored) {
                // keep the requested name
            }
            boolean ok = false;
            try (OutputStream os = cr.openOutputStream(uri)) {
                if (os == null) throw new IllegalStateException("openOutputStream failed");
                rows = write(sql, params, os);
                ok = true;
            } finally {
                if (!ok) {
                    try { cr.delete(uri, null, null); } catch (Exception ignored) { }
                }
            }
            String path = Environment.DIRECTORY_DOWNLOADS + "/" + name;
            done(state, ac, part, name, rows, t0);
            return path;
        }

        File dir = Environment.getExternalStoragePublicDirectory(
                Environment.DIRECTORY_DOWNLOADS);
        if (!dir.exists() && !dir.mkdirs()) {
            throw new IllegalStateException("cannot create Downloads dir");
        }
        File f = new File(dir, name);
        boolean ok = false;
        try (OutputStream os = new FileOutputStream(f)) {
            rows = write(sql, params, os);
            ok = true;
        } finally {
            if (!ok) { try { f.delete(); } catch (Exception ignored) { } }
        }
        done(state, ac, part, name, rows, t0);
        return f.getAbsolutePath();
    }

    /**
     * Insert a pending Downloads row. Standard devices accept the public
     * {@code display_name} column; some builds (the API 35 image verified here)
     * validate ContentValues against the raw schema and reject it with
     * {@code IllegalArgumentException: Invalid column display_name} - the raw
     * column is {@code _display_name}, so fall back to that.
     */
    private static Uri insertDownload(ContentResolver cr, String name) {
        ContentValues cv = new ContentValues();
        cv.put(MediaStoreCompat.MIME_TYPE, "text/csv");
        cv.put(MediaStoreCompat.RELATIVE_PATH,
                Environment.DIRECTORY_DOWNLOADS);
        cv.put(MediaStoreCompat.DISPLAY_NAME, name);
        try {
            Uri uri = cr.insert(MediaStoreCompat.EXTERNAL_CONTENT_URI, cv);
            if (uri != null) return uri;
        } catch (IllegalArgumentException ignored) {
            // fall through to the raw-column retry below
        }
        cv.clear();
        cv.put(MediaStoreCompat.MIME_TYPE, "text/csv");
        cv.put(MediaStoreCompat.RELATIVE_PATH,
                Environment.DIRECTORY_DOWNLOADS);
        cv.put(MediaStoreCompat.DISPLAY_NAME_RAW, name);
        Uri uri = cr.insert(MediaStoreCompat.EXTERNAL_CONTENT_URI, cv);
        if (uri == null) throw new IllegalStateException("MediaStore insert failed");
        return uri;
    }

    /** Streams BOM + header + rows into os; returns the row count. */
    private static long write(String sql, Object[] params, OutputStream os)
            throws Exception {
        final long[] n = {0};
        Writer w = new OutputStreamWriter(os, StandardCharsets.UTF_8);
        StringBuilder sb = new StringBuilder();
        sb.append('\uFEFF');   // UTF-8 BOM, like utf-8-sig in the web export
        for (String c : COLS) {
            if (sb.length() > 1) sb.append(',');
            sb.append(c);
        }
        sb.append("\r\n");
        w.write(sb.toString());
        w.flush();

        Db.stream(sql, params, vals -> {
            StringBuilder line = new StringBuilder(256);
            Object rel = vals[5];   // relation_type
            Object gen = vals[6];   // gender
            Object[] out = {
                    vals[0], vals[1], vals[2],
                    Labels.relationLabel(rel == null ? null : String.valueOf(rel), false),
                    vals[3], vals[4],
                    Labels.genderLabel(gen == null ? null : String.valueOf(gen)),
                    vals[7], vals[8], vals[9], vals[10], vals[11], vals[12]
            };
            for (int i = 0; i < out.length; i++) {
                if (i > 0) line.append(',');
                line.append(field(out[i]));
            }
            line.append("\r\n");
            w.write(line.toString());
            if (++n[0] % 2000 == 0) w.flush();  // bounded buffer + visible growth
        });
        w.flush();
        return n[0];
    }

    private static void done(String state, int ac, Integer part, String name,
                             long rows, long t0) {
        long ms = System.currentTimeMillis() - t0;
        Log.i("oldroll", "csv " + name + ": " + rows + " rows in " + ms + "ms");
        Db.event("export", state + " AC" + ac
                + (part != null ? " P" + part : "")
                + ": " + rows + " rows -> " + name);
    }

    /** MediaStore.Downloads only exists on API 29+ (guarded by the caller's SDK
     *  check); the URI/column strings are stable wire constants. */
    static final class MediaStoreCompat {
        static final String DISPLAY_NAME = "display_name";
        static final String DISPLAY_NAME_RAW = "_display_name";
        static final String MIME_TYPE = "mime_type";
        static final String RELATIVE_PATH = "relative_path";
        static final Uri EXTERNAL_CONTENT_URI =
                Uri.parse("content://media/external/downloads");
    }
}
