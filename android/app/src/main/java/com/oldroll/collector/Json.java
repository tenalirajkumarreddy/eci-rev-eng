package com.oldroll.collector;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.List;
import java.util.Map;

/**
 * Recursive Map/List -> JSONObject/JSONArray conversion.
 *
 * org.json's JSONObject(Map) copies values as-is, so a nested Map inside a job
 * result would serialize as HashMap.toString() - a quoted Java dump instead of
 * JSON. Job results and progress payloads go through here so the web UI reads
 * phone-written rows exactly like Python-written ones.
 */
final class Json {

    private Json() {}

    static Object z(Object v) {
        if (v == null) return JSONObject.NULL;
        if (v instanceof Map) return map((Map<?, ?>) v);
        if (v instanceof List) {
            JSONArray a = new JSONArray();
            for (Object item : (List<?>) v) a.put(z(item));
            return a;
        }
        return v;
    }

    static JSONObject map(Map<?, ?> m) {
        JSONObject o = new JSONObject();
        for (Map.Entry<?, ?> e : m.entrySet()) {
            try {
                o.put(String.valueOf(e.getKey()), z(e.getValue()));
            } catch (Exception ignored) {
                // skip unparsable keys rather than fail the whole result
            }
        }
        return o;
    }
}
