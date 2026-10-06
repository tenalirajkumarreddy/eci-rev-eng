package com.oldroll.collector;

import java.util.HashMap;
import java.util.Locale;
import java.util.Map;

/**
 * Code -> label decoding, ported from client.py.
 *
 * Unknown codes pass through unchanged, so a new code the API starts returning
 * shows up instead of disappearing. The relation mapping was verified 12/12
 * against the national search (F->Father, H->Husband, M->Mother, O->Other).
 */
public final class Labels {

    static final Map<String, String[]> RELATION = new HashMap<>();
    static {
        RELATION.put("F", new String[]{"FTHR", "Father"});
        RELATION.put("H", new String[]{"HSBN", "Husband"});
        RELATION.put("M", new String[]{"MTHR", "Mother"});
        RELATION.put("O", new String[]{"OTHR", "Other"});
    }

    static final Map<String, String> GENDER = new HashMap<>();
    static {
        GENDER.put("M", "Male");
        GENDER.put("F", "Female");
        GENDER.put("T", "Third gender");
        GENDER.put("O", "Other");
    }

    // Reserved-seat category arrives spelled differently per state: 'GEN' /
    // 'General' / 'G', '(ST)', Devanagari abbreviations. Unmapped values pass
    // through so a new spelling shows up rather than being silently coerced.
    static final Map<String, String> AC_TYPES = new HashMap<>();
    static {
        AC_TYPES.put("GEN", "GEN");   AC_TYPES.put("GENERAL", "GEN");
        AC_TYPES.put("G", "GEN");     AC_TYPES.put("UR", "GEN");
        AC_TYPES.put("UNRESERVED", "GEN");
        AC_TYPES.put("SC", "SC");     AC_TYPES.put("(SC)", "SC");
        AC_TYPES.put("अ.ज.जा.", "SC");
        AC_TYPES.put("ST", "ST");     AC_TYPES.put("(ST)", "ST");
        AC_TYPES.put("अ.जा.", "ST");
    }

    private Labels() {}

    public static String relationLabel(String code, boolean shortForm) {
        if (code == null || code.isEmpty()) return null;
        String[] hit = RELATION.get(code.trim().toUpperCase(Locale.ROOT));
        if (hit == null) return code;
        return shortForm ? hit[0] : hit[1];
    }

    public static String genderLabel(String code) {
        if (code == null || code.isEmpty()) return null;
        String hit = GENDER.get(code.trim().toUpperCase(Locale.ROOT));
        return hit != null ? hit : code;
    }

    public static String acTypeLabel(String code) {
        if (code == null || code.trim().isEmpty()) return null;
        String raw = code.trim();
        String hit = AC_TYPES.get(raw.toUpperCase(Locale.ROOT));
        return hit != null ? hit : raw;
    }
}
