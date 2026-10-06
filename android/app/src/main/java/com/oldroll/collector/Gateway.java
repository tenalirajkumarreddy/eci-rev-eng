package com.oldroll.collector;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Anonymous ECI API client - the Android twin of work/old_eci/client.py.
 *
 * Routes used (all verified, no token/captcha):
 *  - POST gateway-vha .../elastic-sir-citizen/get-eroll-data-2003  (old roll)
 *  - GET  gateway-vha .../common/states, .../citizen/sir/getAsmbly (catalogue)
 *  - GET  gateway-voters .../citizen/sir/getPartByAc               (AC part list)
 */
public final class Gateway {

    static final String BASE = "https://gateway-vha.eci.gov.in/api/v1/";
    static final String OLD_EROLL = BASE + "elastic-sir-citizen/get-eroll-data-2003";
    static final String STATES_API = BASE + "common/states";
    static final String ASM_API = BASE + "citizen/sir/getAsmbly";
    static final String PART_API = BASE + "common/part/get/bystatecd/districtcd/acNumber";
    static final String WEB_API = "https://gateway-voters.eci.gov.in/api/v1/";
    static final String PART_BY_AC = WEB_API + "citizen/sir/getPartByAc";
    static final String ASM_API_WEB = WEB_API + "citizen/sir/getAsmbly";

    static final Map<String, String> APP_HEADERS = new HashMap<>();
    static {
        APP_HEADERS.put("Content-Type", "application/json");
        APP_HEADERS.put("Accept", "application/json");
        APP_HEADERS.put("applicationName", "VHA");
        APP_HEADERS.put("appName", "VHA");
        APP_HEADERS.put("channelidobo", "VHA");
        APP_HEADERS.put("platform-type", "ANDROIDMOB");
        APP_HEADERS.put("currentRole", "citizen");
        APP_HEADERS.put("User-Agent", "okhttp/4.9.2");
    }

    static final Map<String, String> WEB_HEADERS = new HashMap<>();
    static {
        WEB_HEADERS.put("Accept", "*/*");
        WEB_HEADERS.put("applicationname", "VSP");
        WEB_HEADERS.put("channelidobo", "VSP");
        WEB_HEADERS.put("currentrole", "citizen");
        WEB_HEADERS.put("platform-type", "ECIWEB");
        WEB_HEADERS.put("Origin", "https://voters.eci.gov.in");
        WEB_HEADERS.put("Referer", "https://voters.eci.gov.in/");
        WEB_HEADERS.put("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                + "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36");
    }

    private Gateway() {}

    public static final class Resp {
        public int status;
        public String body = "";
        public JSONObject json;
        public JSONArray array;
    }

    static void sleepMs(long ms) {
        try { Thread.sleep(ms); } catch (InterruptedException ignored) { }
    }

    static void applyHeaders(HttpURLConnection c, Map<String, String> headers) {
        if (headers == null) return;
        for (Map.Entry<String, String> e : headers.entrySet()) {
            c.setRequestProperty(e.getKey(), e.getValue());
        }
    }

    static String readAll(InputStream in) throws IOException {
        if (in == null) return "";
        ByteArrayOutputStream buf = new ByteArrayOutputStream();
        byte[] chunk = new byte[8192];
        int n;
        while ((n = in.read(chunk)) > 0) buf.write(chunk, 0, n);
        return buf.toString("UTF-8");
    }

    /** POST with the client.py retry policy: 3 attempts, 429 gets a longer backoff. */
    public static Resp post(String url, String body, Map<String, String> headers,
                            int timeoutMs) {
        Resp last = new Resp();
        for (int attempt = 0; attempt < 3; attempt++) {
            HttpURLConnection c = null;
            try {
                c = (HttpURLConnection) new URL(url).openConnection();
                c.setConnectTimeout(15000);
                c.setReadTimeout(timeoutMs);
                c.setRequestMethod("POST");
                c.setDoOutput(true);
                applyHeaders(c, headers != null ? headers : APP_HEADERS);
                if (headers == null || !headers.containsKey("Content-Type")) {
                    c.setRequestProperty("Content-Type", "application/json");
                }
                OutputStream os = c.getOutputStream();
                os.write(body.getBytes(StandardCharsets.UTF_8));
                os.close();
                int st = c.getResponseCode();
                String text = readAll(st >= 400 ? c.getErrorStream() : c.getInputStream());
                if (st == 429) {
                    sleepMs(2000L * (attempt + 1));
                    last.status = st;
                    last.body = text;
                    continue;
                }
                return parse(st, text);
            } catch (IOException e) {
                last.status = 0;
                last.body = e.getClass().getSimpleName() + ": " + e.getMessage();
                sleepMs(1000L * (attempt + 1));
            } finally {
                if (c != null) c.disconnect();
            }
        }
        return last;
    }

    public static Resp get(String url, Map<String, String> headers, int timeoutMs) {
        Resp last = new Resp();
        for (int attempt = 0; attempt < 3; attempt++) {
            HttpURLConnection c = null;
            try {
                c = (HttpURLConnection) new URL(url).openConnection();
                c.setConnectTimeout(15000);
                c.setReadTimeout(timeoutMs);
                c.setRequestMethod("GET");
                applyHeaders(c, headers);
                int st = c.getResponseCode();
                String text = readAll(st >= 400 ? c.getErrorStream() : c.getInputStream());
                if (st == 429) {
                    sleepMs(2000L * (attempt + 1));
                    continue;
                }
                return parse(st, text);
            } catch (IOException e) {
                last.status = 0;
                last.body = e.getClass().getSimpleName() + ": " + e.getMessage();
                sleepMs(1000L * (attempt + 1));
            } finally {
                if (c != null) c.disconnect();
            }
        }
        return last;
    }

    static Resp parse(int status, String text) {
        Resp r = new Resp();
        r.status = status;
        r.body = text == null ? "" : text;
        String t = r.body.trim();
        try {
            if (t.startsWith("{")) r.json = new JSONObject(t);
            else if (t.startsWith("[")) r.array = new JSONArray(t);
        } catch (Exception ignored) {
            // unparsable body stays a raw string, like Python's payload=None
        }
        return r;
    }

    /** One call to the old-roll route: status + payload (both matter for discovery). */
    public static final class Eroll {
        public int status;
        public List<Map<String, String>> payload = new ArrayList<>();
    }

    public static Eroll eroll(String state, int ac, int part, String serial) {
        Eroll out = new Eroll();
        try {
            JSONObject body = new JSONObject();
            body.put("oldStateCd", state);
            body.put("oldAcNo", String.valueOf(ac));
            body.put("oldPartNo", String.valueOf(part));
            body.put("oldPartSerialNo", serial == null ? "" : serial);
            Resp r = post(OLD_EROLL, body.toString(), null, 30000);
            out.status = r.status;
            if (r.status == 200 && r.json != null) {
                out.payload = toJsonMaps(r.json.optJSONArray("payload"));
            }
        } catch (Exception ignored) {
            // network/parse failure surfaces as an empty payload, as in Python
        }
        return out;
    }

    /** One serial of an old part -> payload list (empty when absent). */
    public static List<Map<String, String>> fetchSerial(String state, int ac, int part,
                                                        String serial) {
        return eroll(state, ac, part, serial).payload;
    }

    /** A ~50-record window of an old part (also carries oldPartName). */
    public static List<Map<String, String>> fetchWindow(String state, int ac, int part) {
        return fetchSerial(state, ac, part, "");
    }

    /**
     * Highest serial that answers, +20 margin (30 if the part is empty).
     * Candidates mirror client.probe_roll_end exactly.
     */
    public static int probeRollEnd(String state, int ac, int part, int hardCap) {
        int last = 0;
        for (int cand : new int[]{50, 100, 200, 300, 400, 500, 650, 800, 1000,
                1200, 1500, 2000, 2500}) {
            if (cand > hardCap) break;
            List<Map<String, String>> payload = fetchSerial(state, ac, part,
                    String.valueOf(cand));
            if (!payload.isEmpty()) {
                last = cand;
            } else if (last > 0 && cand > last + 120) {
                break;
            }
        }
        return Math.min(hardCap, last > 0 ? last + 20 : 30);
    }

    static List<Map<String, String>> toJsonMaps(JSONArray arr) {
        List<Map<String, String>> out = new ArrayList<>();
        if (arr == null) return out;
        for (int i = 0; i < arr.length(); i++) {
            JSONObject o = arr.optJSONObject(i);
            if (o == null) continue;
            Map<String, String> m = new HashMap<>();
            java.util.Iterator<String> it = o.keys();
            while (it.hasNext()) {
                String k = it.next();
                Object v = o.opt(k);
                m.put(k, v == null || v == JSONObject.NULL ? null : String.valueOf(v));
            }
            out.add(m);
        }
        return out;
    }

    static JSONArray rowsArray(Resp r) {
        if (r.array != null) return r.array;
        if (r.json != null) {
            for (String key : new String[]{"payload", "data", "result"}) {
                JSONArray a = r.json.optJSONArray(key);
                if (a != null) return a;
            }
        }
        return new JSONArray();
    }

    // ------------------------------------------------------------ catalogue

    public static final class StateRow {
        public String stateCd, name;
        public StateRow(String cd, String n) { stateCd = cd; name = n; }
    }

    public static List<StateRow> statesLive() {
        List<StateRow> out = new ArrayList<>();
        Resp r = get(STATES_API, APP_HEADERS, 30000);
        JSONArray arr = rowsArray(r);
        for (int i = 0; i < arr.length(); i++) {
            JSONObject o = arr.optJSONObject(i);
            if (o == null) continue;
            String cd = first(o, "stateCd", "stateCode", "state_cd");
            String nm = first(o, "stateName", "name");
            if (cd != null && !cd.isEmpty()) out.add(new StateRow(cd, nm));
        }
        return out;
    }

    public static final class AcRow {
        public int acNo;
        public String name, nameL1, acType, districtCd;
    }

    public static List<AcRow> acsLive(String state) {
        List<AcRow> out = new ArrayList<>();
        // The web gateway is preferred: same list plus acType (GEN/SC/ST).
        // The state travels as a custom HEADER on both gateways (client.py).
        Map<String, String> wh = new HashMap<>(WEB_HEADERS);
        wh.put("state", state);
        Resp r = get(ASM_API_WEB, wh, 30000);
        JSONArray arr = rowsArray(r);
        if (arr.length() == 0) {
            Map<String, String> vh = new HashMap<>(APP_HEADERS);
            vh.put("state", state);
            arr = rowsArray(get(ASM_API, vh, 30000));
        }
        for (int i = 0; i < arr.length(); i++) {
            JSONObject o = arr.optJSONObject(i);
            if (o == null) continue;
            String ac = first(o, "acNo", "asmblyNo", "ac_number", "assemblyNo");
            if (ac == null || ac.isEmpty()) continue;
            AcRow a = new AcRow();
            try {
                a.acNo = Integer.parseInt(ac.trim());
            } catch (NumberFormatException e) {
                continue;
            }
            a.name = first(o, "acNameV1", "acName", "ac_name");
            a.nameL1 = first(o, "acName");
            a.acType = Labels.acTypeLabel(first(o, "acType"));
            String dist = first(o, "distNo", "districtCd");
            a.districtCd = dist == null || dist.isEmpty() ? null : dist;
            out.add(a);
        }
        return out;
    }

    public static final class PartRow {
        public int acNumber, partNumber;
        public String partName, partNameL1, districtCd, psType, psCaty, oldPdfUrl;
        public Long partId;
    }

    /**
     * Current-roll parts of one AC. Prefers the web app's AC-scoped
     * getPartByAc; the VHA fallback answers for the whole district, so its rows
     * are discarded unless every row names an AC number (the filter that stopped
     * AC 1 from looking like it had 319 parts when it has 153).
     */
    public static List<PartRow> currentParts(String state, int ac) {
        Map<String, String> wh = new HashMap<>(WEB_HEADERS);
        wh.put("state", state);
        Resp r = get(PART_BY_AC + "?Asmbly=" + ac, wh, 30000);
        List<PartRow> out = normParts(rowsArray(r), ac, true);
        if (!out.isEmpty()) return out;

        Map<String, String> vh = new HashMap<>(APP_HEADERS);
        vh.put("state", state);
        Resp r2 = get(PART_API + "?stateCd=" + enc(state) + "&acNumber=" + ac, vh, 30000);
        JSONArray arr = rowsArray(r2);
        boolean allHaveAc = true;
        for (int i = 0; i < arr.length(); i++) {
            JSONObject o = arr.optJSONObject(i);
            if (o == null || first(o, "acNumber") == null) { allHaveAc = false; break; }
        }
        if (!allHaveAc) return new ArrayList<>();
        return normParts(arr, ac, false);
    }

    static List<PartRow> normParts(JSONArray arr, int ac, boolean strict) {
        List<PartRow> out = new ArrayList<>();
        for (int i = 0; i < arr.length(); i++) {
            JSONObject o = arr.optJSONObject(i);
            if (o == null) continue;
            String pn = first(o, "partNumber");
            if (pn == null || pn.isEmpty()) continue;
            PartRow p = new PartRow();
            try {
                p.partNumber = Integer.parseInt(pn.trim());
            } catch (NumberFormatException e) {
                continue;
            }
            String acn = first(o, "acNumber");
            p.acNumber = acn == null ? ac : Integer.parseInt(acn.trim());
            p.partName = first(o, "partName");
            p.partNameL1 = first(o, "partNameV1", "partNameL1");
            String pid = first(o, "id", "partId");
            p.partId = pid == null ? null : tryLong(pid);
            String dist = first(o, "distNo", "districtCd");
            p.districtCd = dist == null || dist.isEmpty() ? null : dist;
            p.psType = first(o, "psType");
            p.psCaty = first(o, "psCaty");
            p.oldPdfUrl = first(o, "oldPdfUrl");
            out.add(p);
        }
        return out;
    }

    // ------------------------------------------------------------- helpers

    static String first(JSONObject o, String... keys) {
        for (String k : keys) {
            Object v = o.opt(k);
            if (v != null && v != JSONObject.NULL) {
                String s = String.valueOf(v);
                if (!s.isEmpty()) return s;
            }
        }
        return null;
    }

    static Long tryLong(String s) {
        try {
            return (long) Double.parseDouble(s);
        } catch (Exception e) {
            return null;
        }
    }

    static String enc(String s) {
        try {
            return URLEncoder.encode(s, "UTF-8");
        } catch (Exception e) {
            return s;
        }
    }
}
