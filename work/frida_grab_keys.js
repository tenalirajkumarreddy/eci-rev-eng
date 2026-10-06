/*
 * frida_grab_keys.js - dump the ECINET app's static native keys at runtime.
 *
 * These are the values behind BaseActivity's native getters, e.g.
 *     getOfficialDetailSecureKey() = new String(Base64.decode(native(...)))
 *     getECISITEAPIKEY() / getEciTechAPIKEY()   -> X-API-KEY header values
 * They are app constants, not account credentials: no login/signup required.
 *
 * Usage (rooted device or emulator with frida-server running):
 *     frida -U -f in.gov.eci.app -l work/frida_grab_keys.js --no-pause
 * Then open the electoral-search / EEPIC screen so the getters get called.
 *
 * The script hooks the named getters AND every method on ECI classes whose
 * name looks like a key getter, printing every value it sees.
 */

Java.perform(function () {
    var seen = {};

    function log(cls, method, value) {
        var tag = cls + "." + method + "()";
        if (seen[tag] === value) return;
        seen[tag] = value;
        console.log("[KEY] " + tag + " = " + value);
    }

    var NAMES = [
        "getOfficialDetailSecureKey",
        "getElectorDetailSecureKey",
        "getElectorDetailEpic",
        "getElectoralSearchSECURE_KEY",
        "getECISITEAPIKEY",
        "getEciTechAPIKEY",
        "getSveepAPIKEY",
        "getEepicHashNew",
        "getLocalLoginClientAPIKEY",
        "getLocalRegistrationClientAPIKEY",
        "getAuthenticationTokenCredentials",
        "getElectionResultTokenKey",
        "getEvpApiSecureEci",
        "getEvpDigitalSecureApi",
        "getNgspClientKey",
        "getSessionKey"
    ];

    var KEY_RE = /(api_?key|securekey|secure_key|hashnew|apikey)/i;
    var CLASS_RE = /(eci|garuda)/i;

    function shoot(clsName, methodName) {
        var cls;
        try {
            cls = Java.use(clsName);
        } catch (e) {
            return;
        }
        var method = cls[methodName];
        if (!method) return;
        try {
            method.overload().implementation = function () {
                var value = method.overload().call(this);
                log(clsName, methodName, value);
                return value;
            };
            console.log("[frida] hooked " + clsName + "." + methodName);
        } catch (e) {
            /* static method or overload mismatch - try each overload */
            try {
                var overloads = method.overloads;
                overloads.forEach(function (o) {
                    o.implementation = function () {
                        var value = o.call(this);
                        log(clsName, methodName, value);
                        return value;
                    };
                });
                console.log("[frida] hooked " + clsName + "." + methodName + " (overloads)");
            } catch (e2) { /* ignore */ }
        }
    }

    // 1) Explicit BaseActivity getters (they are plain instance methods).
    NAMES.forEach(function (name) {
        shoot("com.eci.citizen.BaseActivity", name);
    });

    // 2) Catch-all: hook every key-looking method on ECI classes as they load.
    Java.enumerateLoadedClasses({
        onMatch: function (name) {
            if (name.indexOf("com.eci") !== 0 && name.indexOf("in.gov.eci") !== 0) return;
            var nameLooksInteresting = CLASS_RE.test(name);
            if (!nameLooksInteresting) return;
            var methods;
            try {
                methods = Java.use(name).class.getDeclaredMethods();
            } catch (e) {
                return;
            }
            methods.forEach(function (m) {
                var mn = m.getName();
                if (KEY_RE.test(mn) && !seen[name + "." + mn]) {
                    shoot(name, mn);
                }
            });
        },
        onComplete: function () {
            console.log("[frida] hooks installed - open the search / EEPIC screen");
        }
    });
});
