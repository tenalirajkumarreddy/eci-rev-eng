package com.oldroll.collector;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.KeyFactory;
import java.security.PublicKey;
import java.security.SecureRandom;
import java.security.spec.MGF1ParameterSpec;
import java.security.spec.X509EncodedKeySpec;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Random;

import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.OAEPParameterSpec;
import javax.crypto.spec.PSource;
import javax.crypto.spec.SecretKeySpec;

/**
 * The APK's own EPIC search: EPIC in, details out, no captcha.
 *
 * Exact port of work/app_search.py - the contract reconstructed from the
 * garuda smali (PollingStationSearchActivity.callSearchApiTrial):
 *
 *   inner = {"captchaData":"na","captchaId":"na","epicNumber":EPIC,
 *            "securityKey": gpk(EPIC, tc)}
 *   body  = {encryptedKey: b64(RSA-OAEP-SHA256(aesKey)),
 *            iv:           b64(12-byte GCM nonce),
 *            encryptedPayload: b64(AES-256-GCM(inner))}
 *
 * gpk:  plaintext "EPIC:yyyy-MM-dd-HH-mm-ss:rrrrrr", AES-GCM with the tc key
 *       and a 16-byte zero IV.
 * OAEP: digest SHA-256 AND MGF1 digest SHA-256 (the named Java/Android
 *       transformation defaults MGF1 to SHA-1, so the parameters are set
 *       explicitly to match cryptography's OAEP(SHA256, MGF1(SHA256))).
 *
 * Both key constants come from work/app_keys.json (device-verified: tc is the
 * live key, external the SPKI RSA-2048 public key of the gateway).
 */
public final class EpicCrypto {

    public static final String EPIC_TC =
            "P79vtNtk/WZaAXsQKCHClA";
    public static final String EPIC_EXTERNAL =
            "TUlJQklqQU5CZ2txaGtpRzl3MEJBUUVGQUFPQ0FROEFNSUlCQ2dLQ0FRRUFyYjcrK0J4TC9Z"
          + "TjhPSWxuKzZGTDlHbnc1RE5tUS9WRlpYc3MrSitUdVF5SmM4OTFKYnFiaWp4WVFORWluMmMy"
          + "dStDbnBYcG9HUS8xZ1VTekRNSmVOUzNzTlNsSVV5a3AyZHQ3eEltL2NtVjRzWi9jNzY5dkN4"
          + "VlJvc01mUmFaSm5CQWFoK20xWDI2bEVobk9vMHdwQUI5VHhyOFJJeUJlNmg3UGlRV3lrZUpl"
          + "aDZVYWNPQkJYMjhrZ2txNyt2SmhXOEhnQjM4bHQzMlhSb2N6blJZd1M5THFSN1p3ZUZtUWhU"
          + "cjErRUdycWlFS0NPQ3hNWWdIUjJTUWNrYjk2aFo5a1d6ZnpldW40YlVPNW9YS0pjaUxraVMx"
          + "SWdLaWVBREV2WUxndTEyOVpJcG4xSCs4SCs4aWtOTlZFVHFFRERNdHFjUWNRbVdwcEp2Y1dI"
          + "YVhBcytmOFFJREFRQUI";

    static final String ENDPOINT = Gateway.BASE
            + "elastic/search-by-epic-from-national-display-v1";
    static final String MOBILE_UA =
            "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 "
            + "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36";

    // ~1 req/s hard limit on this route (same lock as Python's _epic_lock).
    static final Object LOCK = new Object();
    static long lastAt;

    static final SecureRandom RNG = new SecureRandom();

    private EpicCrypto() {}

    // ----------------------------------------------------------------- b64

    static String b64(byte[] raw) {
        return android.util.Base64.encodeToString(raw, android.util.Base64.NO_WRAP);
    }

    /** Lenient decode - android.util.Base64.DEFAULT tolerates bad padding. */
    static byte[] unb64(String text) {
        String t = text.trim();
        t = t + "====".substring(0, (4 - t.length() % 4) % 4);
        return android.util.Base64.decode(t, android.util.Base64.DEFAULT);
    }

    // ----------------------------------------------------------------- gpk

    /**
     * KGn.gPK: primary:timestamp:6random digits, AES-GCM under the tc key with
     * a 16-byte zero IV. Values arrive wrapped by BaseActivity as
     * new String(Base64.decode(native)), so one or two unwraps are tolerated.
     */
    static byte[] unwrap(String text, int[] want) {
        String cur = text == null ? "" : text.trim();
        for (int i = 0; i < 2; i++) {
            byte[] raw;
            try {
                raw = unb64(cur);
            } catch (Exception e) {
                return null;
            }
            for (int w : want) {
                if (raw.length == w) return raw;
            }
            try {
                cur = new String(raw, StandardCharsets.US_ASCII).trim();
            } catch (Exception e) {
                return null;
            }
            if (!cur.matches("[A-Za-z0-9+/=]+")) return null;
        }
        return null;
    }

    public static String gpk(String primary, String tc) throws Exception {
        String ts = new SimpleDateFormat("yyyy-MM-dd-HH-mm-ss", Locale.US)
                .format(new Date());
        String rand6 = String.format(Locale.US, "%06d", new Random().nextInt(1_000_000));
        byte[] plain = (primary + ":" + ts + ":" + rand6)
                .getBytes(StandardCharsets.UTF_8);
        byte[] key = unwrap(tc, new int[]{16, 24, 32});
        if (key == null) throw new IllegalStateException("tc does not decode to an AES key");
        Cipher c = Cipher.getInstance("AES/GCM/NoPadding");
        c.init(Cipher.ENCRYPT_MODE, new SecretKeySpec(key, "AES"),
                new GCMParameterSpec(128, new byte[16]));
        return b64(c.doFinal(plain));
    }

    // ---------------------------------------------------------- encryptData

    /** Base64 unwrap layers of the native constant (1-3 levels, like Python). */
    static List<byte[]> decodeVariants(String text) {
        List<byte[]> out = new ArrayList<>();
        String cur = text == null ? "" : text.trim();
        for (int i = 0; i < 3; i++) {
            byte[] raw;
            try {
                raw = unb64(cur);
            } catch (Exception e) {
                break;
            }
            out.add(raw);
            try {
                cur = new String(raw, StandardCharsets.US_ASCII).trim();
            } catch (Exception e) {
                break;
            }
            if (!cur.matches("[A-Za-z0-9+/=]+")) break;
        }
        return out;
    }

    public static PublicKey loadPublicKey(String external) throws Exception {
        for (byte[] blob : decodeVariants(external)) {
            if (blob.length < 64) continue;
            try {
                return KeyFactory.getInstance("RSA")
                        .generatePublic(new X509EncodedKeySpec(blob));
            } catch (Exception ignored) {
                // try the next unwrap level
            }
        }
        throw new IllegalStateException("external is not a Base64 X.509/SPKI RSA key");
    }

    public static JSONObject encryptData(byte[] payload, PublicKey pub) throws Exception {
        byte[] aesKey = new byte[32];
        byte[] iv = new byte[12];
        RNG.nextBytes(aesKey);
        RNG.nextBytes(iv);
        Cipher g = Cipher.getInstance("AES/GCM/NoPadding");
        g.init(Cipher.ENCRYPT_MODE, new SecretKeySpec(aesKey, "AES"),
                new GCMParameterSpec(128, iv));
        byte[] ct = g.doFinal(payload);           // ciphertext || 128-bit tag

        Cipher r;
        OAEPParameterSpec oaep = new OAEPParameterSpec("SHA-256", "MGF1",
                MGF1ParameterSpec.SHA256, PSource.PSpecified.DEFAULT);
        try {
            r = Cipher.getInstance("RSA/ECB/OAEPWithSHA-256AndMGF1Padding");
        } catch (Exception e) {
            r = Cipher.getInstance("RSA/ECB/OAEPPadding");
        }
        r.init(Cipher.ENCRYPT_MODE, pub, oaep);
        byte[] wrapped = r.doFinal(aesKey);

        JSONObject out = new JSONObject();
        out.put("encryptedKey", b64(wrapped));
        out.put("iv", b64(iv));
        out.put("encryptedPayload", b64(ct));
        return out;
    }

    // -------------------------------------------------------------- lookup

    public static final class Result {
        public String epic;
        public int status;
        public JSONArray hits = new JSONArray();
        public String raw = "";
        public String error;
        public boolean cached;
        public JSONObject content = new JSONObject();
    }

    /** Builds the exact wire request (inner plaintext + encrypted body). */
    public static JSONObject buildBody(String epic) throws Exception {
        JSONObject inner = new JSONObject();
        inner.put("captchaData", "na");
        inner.put("captchaId", "na");
        inner.put("epicNumber", epic);
        inner.put("securityKey", gpk(epic, EPIC_TC));
        PublicKey pub = loadPublicKey(EPIC_EXTERNAL);
        return encryptData(inner.toString().getBytes(StandardCharsets.UTF_8), pub);
    }

    /** Fire the national search (throttled to ~1 req/s). */
    public static Result fetch(String rawEpic) {
        Result out = new Result();
        out.epic = normalize(rawEpic);
        if (out.epic.isEmpty()) {
            out.error = "empty EPIC";
            return out;
        }
        JSONObject body;
        try {
            body = buildBody(out.epic);
        } catch (Exception e) {
            out.error = e.getClass().getSimpleName() + ": " + e.getMessage();
            return out;
        }
        synchronized (LOCK) {
            long wait = 1100 - (System.currentTimeMillis() - lastAt);
            if (wait > 0) {
                try { Thread.sleep(wait); } catch (InterruptedException ignored) { }
            }
            HttpURLConnection c = null;
            try {
                c = (HttpURLConnection) new URL(ENDPOINT).openConnection();
                c.setConnectTimeout(15000);
                c.setReadTimeout(40000);
                c.setRequestMethod("POST");
                c.setDoOutput(true);
                for (Map.Entry<String, String> e : Gateway.APP_HEADERS.entrySet()) {
                    c.setRequestProperty(e.getKey(), e.getValue());
                }
                c.setRequestProperty("device-id",
                        java.util.UUID.randomUUID().toString());
                c.setRequestProperty("User-Agent", MOBILE_UA);
                OutputStream os = c.getOutputStream();
                os.write(body.toString().getBytes(StandardCharsets.UTF_8));
                os.close();
                out.status = c.getResponseCode();
                InputStream in = out.status >= 400 ? c.getErrorStream()
                        : c.getInputStream();
                out.raw = readAll(in);
                lastAt = System.currentTimeMillis();
            } catch (Exception e) {
                out.status = 0;
                out.error = e.getClass().getSimpleName() + ": " + e.getMessage();
                lastAt = System.currentTimeMillis();
                return out;
            } finally {
                if (c != null) c.disconnect();
            }
        }
        // 200 + list = records; 200 + [] = not in the national display;
        // 400 + [] = rejected (bad key).
        String t = out.raw == null ? "" : out.raw.trim();
        try {
            if (t.startsWith("[")) out.hits = new JSONArray(t);
        } catch (Exception ignored) {
            out.hits = new JSONArray();
        }
        if (out.hits.length() > 0) {
            JSONObject h = out.hits.optJSONObject(0);
            if (h != null) out.content = h.optJSONObject("content");
            if (out.content == null) out.content = new JSONObject();
        }
        return out;
    }

    static String readAll(InputStream in) throws java.io.IOException {
        if (in == null) return "";
        ByteArrayOutputStream buf = new ByteArrayOutputStream();
        byte[] chunk = new byte[8192];
        int n;
        while ((n = in.read(chunk)) > 0) buf.write(chunk, 0, n);
        return buf.toString("UTF-8");
    }

    public static String normalize(String epic) {
        if (epic == null) return "";
        return epic.trim().replaceAll("\\s+", "").toUpperCase(Locale.ROOT);
    }

    /** Flat profile from a national-display record (client.profile_from_content). */
    public static Map<String, Object> profileFromContent(JSONObject content) {
        Map<String, Object> p = new HashMap<>();
        if (content == null) content = new JSONObject();
        p.put("name", g(content, "fullName", "applicantFirstName"));
        p.put("name_local", g(content, "fullNameL1", "applicantFirstNameL1"));
        p.put("relation", g(content, "relativeFullName", "relationName"));
        p.put("relation_local", g(content, "relativeFullNameL1", "relationNameL1"));
        String rt = g(content, "relationType");
        p.put("relation_type", rt);
        p.put("relation_label", Labels.relationLabel(rt, false));
        p.put("age", g(content, "age"));
        p.put("gender", g(content, "gender"));
        p.put("state_cd", g(content, "stateCd"));
        p.put("state_name", g(content, "stateName"));
        p.put("district", g(content, "districtValue"));
        p.put("ac_no", g(content, "acNumber"));
        p.put("ac_name", g(content, "asmblyName"));
        p.put("part_no", g(content, "partNumber"));
        p.put("part_name", g(content, "partName"));
        p.put("part_name_l1", g(content, "partNameL1"));
        p.put("part_id", g(content, "partId"));
        p.put("serial_no", g(content, "partSerialNumber"));
        p.put("section_no", g(content, "sectionNo"));
        p.put("ps_building", g(content, "psbuildingName", "buildingAddress"));
        p.put("ps_building_l1", g(content, "psBuildingNameL1", "buildingAddressL1"));
        p.put("record_id", g(content, "id"));
        return p;
    }

    static String g(JSONObject o, String... keys) {
        for (String k : keys) {
            Object v = o.opt(k);
            if (v != null && v != JSONObject.NULL) {
                String s = String.valueOf(v);
                if (!s.isEmpty()) return s;
            }
        }
        return null;
    }
}
