# Running the old-roll collector on more devices

Add a phone, a laptop or a Colab session and it works alongside whatever is
already running. **No device depends on another** — they all talk straight to the
same PostgreSQL database and each one claims its own parts atomically.

- Data cards (parts done, records, EPICs, electors) are **global** — every device
  contributes to and displays the same dataset.
- Logs, tasks and settings are **per device** — what you see and change stays
  yours.
- A part, once chosen, is **locked** by the claim in the database, so two devices
  can never sweep the same part.

---

## 0. Read this first: device tags

Every device gets a **tag**, and the tag is what keeps devices independent. It
scopes four things: your settings, your log stream, your task queue, and the
tie-break that spreads equally-good parts across devices.

| Device | Tag | How to set it |
| --- | --- | --- |
| Android phone | `android-<Build.MODEL>` | automatic — nothing to do |
| PC / laptop (web app) | `ECI_DEVICE_TAG`, else the hostname | environment variable |
| Colab / Linux engine | `OLD_ECI_DEVICE_TAG`, default `colab` | environment variable |

**Two devices must not share a tag.** If they do, they share settings and logs,
and the tie-break can no longer spread their picks apart.

> ⚠️ The one real trap: two phones of the **same model** produce the same tag
> (`android-Pixel6` and `android-Pixel6`). If you run two identical handsets,
> watch for it — both will appear as one device. Different models (the normal
> case) are always distinct.

---

## 1. Prerequisites for *every* device

1. **Network route to the database**: `129.225.75.85:5432`. A phone on mobile
   data is fine; a phone on a locked-down Wi-Fi may not be.
2. **The database credentials.** Already embedded as defaults in the APK, the web
   app and the Colab file (see `DEF_HOST`/`DEF_USER`/`DEF_PASS` in
   `android/app/src/main/java/com/oldroll/collector/Db.java`, and the `ECI_PG_DSN`
   default in `work/old_eci/db.py`). **You normally type nothing** — only change
   them if the database moves.
3. **A unique tag** (table above). Android is automatic.

The first device to connect also creates any missing tables and indexes. Every
later device detects the finished schema and skips that step, so you never have
to "prepare" the database for a new device.

---

## 2. Android phone

### 2a. Install the APK

Build it (2b) or use the existing artifact:

```
android/app/build/outputs/apk/debug/app-debug.apk
```

**Over USB** — enable Developer options → USB debugging, plug in, then:

```bash
adb devices                       # confirm the phone is listed and 'device'
adb install -r android/app/build/outputs/apk/debug/app-debug.apk
```

**Over Wi-Fi (no cable)** — on the phone: Developer options → **Wireless
debugging** → *Pair device with pairing code*:

```bash
adb pair <phone-ip>:<pairing-port>     # enter the pairing code when asked
adb connect <phone-ip>:<debug-port>
adb devices                            # now listed
adb install -r android/app/build/outputs/apk/debug/app-debug.apk
```

If you have no cable and no adb at all, copy the `.apk` to the phone (Drive,
chat, USB storage), tap it in Files, and allow "install unknown apps" for
whichever app you opened it from.

### 2b. Or build it yourself

Requirements: **JDK 17+**, **Android SDK with compileSdk 35** (minSdk 26),
**Gradle 8.x**.

```bash
cd android
gradle assembleDebug
```

> Do **not** swap the bundled `app/libs/postgresql-42.7.4-android.jar` for the
> stock pgjdbc jar. Two of its classes are recompiled in-repo — stock pgjdbc
> crashes on Android because it calls `java.lang.management.ManagementFactory`,
> which does not exist there. See `android/README.md` for the receipt.

### 2c. First run

1. Open **Old ECI**. On first launch it checks/creates the schema.
2. **Settings** → confirm the database fields → tap **Test**. You want
   `OK · old_parts <n> (done <n>)`.
3. Back on the main screen, tap **Start**. Grant the notification permission —
   the collector runs as a foreground service, which is what keeps it alive.
4. In **Settings**, tap the battery button (or Android Settings → Apps → Old ECI
   → Battery → **Unrestricted**). Without this, aggressive OEM battery managers
   will kill long runs.

That is the whole setup — the DSN ships embedded, so a new phone needs no typing
unless your database has moved.

### 2d. What you should see

| Card | Meaning |
| --- | --- |
| **THIS DEVICE** | your tag, your worker state, your auto switch, **how many parts *you* hold**, your DB endpoint |
| **GLOBAL PARTS** | the whole fleet's progress, plus **free** = unprocessed *and* unclaimed — exactly the pool your picker scans |
| **IN FLIGHT** | the part *this* device is currently sweeping, with speed and ETA |
| **MY TASKS** | jobs queued **on this device** (nobody else can run them) |
| **LOGS** | your own log lines. The `mine` / `all` button switches to the fleet view |

Turn on **Auto collect** to let the device pick and sweep parts by itself. That
switch is per device: switching it off on the phone does not stop the PC.

---

## 3. A second PC or laptop (web app)

Requirements: Python 3.11+ with `psycopg`, `psycopg_pool`, `fastapi`, `uvicorn`,
`jinja2`.

```bash
# A UNIQUE tag for this machine - do not reuse another device's
export ECI_DEVICE_TAG=laptop2          # Windows: set ECI_DEVICE_TAG=laptop2

cd work/old_eci
python -X utf8 -m uvicorn app:app --host 127.0.0.1 --port 8008
```

Open <http://127.0.0.1:8008/>. The dashboard shows the global cards, and **this
machine's** logs and tasks — not the phones'.

Optional overrides (only if the database moves):

| env | default | meaning |
| --- | --- | --- |
| `ECI_PG_DSN` | the `old_eci` store | read/write connection |
| `ECI_PG_SCHEMA` | `public` | schema to work in |
| `ECI_GEO_DSN` | the legacy `eci` DB | read-only catalogue seed |

`--host 0.0.0.0` instead of `127.0.0.1` only if you want to open the dashboard
from another machine on your network. It is an **unauthenticated dashboard** — do
not expose it to the internet.

---

## 4. Colab, or any Linux box (single-file engine)

`old_eci_collector.py` is the whole app in one file with no local imports.

```bash
OLD_ECI_DEVICE_TAG=colab python old_eci_collector.py --parts 20
```

Useful flags:

| Flag | Purpose |
| --- | --- |
| `--workers N` | parallel requests *within one part* |
| `--parts-parallel N` | collect N parts at once |
| `--parts N` / `--minutes N` | stop after N parts / N minutes |
| `--state S01 --ac 26` | restrict to one AC |
| `--status` | print DB state and exit |
| `--no-init` | skip schema DDL (faster restart) |
| `--no-recover` | never requeue anything (see the note below) |
| `--force` | re-collect already-done parts |
| `--idle-exit N` | exit after N idle minutes |

**`--no-recover` is now optional.** The reaper is *stale-only*: it requeues a
part only if the holder has not touched it for **10 minutes**, so it can no
longer disturb the PC worker's live sweep the way it used to. Pass `--no-recover`
only if you want to guarantee this process never requeues anything at all.

---

## 5. Sizing the fleet (avoid this mistake)

Two independent knobs multiply together:

```
requests this device sends ≈ workers × parts_parallel
```

The gateway tops out around **~80 requests/sec per source**. That number is
*empirical* — the gateway advertises no rate-limit header on this route, and it
is not per-account (the route is anonymous). Measured peaks across the fleet have
exceeded 80 req/s in total, so it is a per-source budget rather than a global
cap. **Whether the gateway keys it on the source IP or on the connection is not
established**, so treat devices behind one NAT (two phones on the same home
Wi-Fi) as *possibly* sharing a budget, and devices on separate networks as
certainly independent. So:

- Keep `workers × parts_parallel` at or below **~32 per device**.
- Beyond that you add latency, not throughput.
- To scale, add **devices on different networks** rather than more handsets on
  one router.

Every device has its own `workers` / `parts_parallel` / `auto_enabled` /
`calibrate_offset`. Change them on the phone and the PC is unaffected.

---

## 6. Verify a new device joined

Run this from any machine that can reach the database:

```sql
-- who is holding parts right now (one row per live device)
select claimed_by, count(*) from old_parts where status = 'running'
group by 1 order by 1;

-- each device's own log stream
select device, count(*), max(ts) from events group by 1 order by 2 desc;
```

You should see one `claimed_by` row per active device, each holding its own
parts, and a separate `device` row per tag in `events`.

---

## 7. Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| Settings → Test shows `FAIL · connection refused` / timeout | The device cannot reach `129.225.75.85:5432`. Try mobile data; check the host value. |
| `worker stopped` on the main screen | Tap **Start**. If it dies again, apply the battery exemption (2c step 4). |
| Collecting stops ~1 minute after leaving the app | Aggressive foreground-service limits. Grant the battery exemption; keep the notification visible. |
| `free` is high but nothing is collected | **Auto collect** is off, or the container is already claimed by others (check `claimants`). |
| Logs show other devices' lines | You are on the `all` view — tap the button to return to `mine`. |
| Two phones look like one device | Same `Build.MODEL`. Give one a distinct tag if you need them separate. |
| A part sits `running` on a device that died | Expected: the stale reaper reclaims it after ~10 minutes. No action needed. |
| Everything stalls at once | Check the database itself — it is the single shared dependency. |

---

## 8. Operating notes

- **Never commit or share the dashboard publicly** — it has no login.
- **The database is the one shared dependency.** If it goes down every device
  stalls; nothing is lost, claims are just requeued.
- **Re-collection is safe.** Writes are upserts keyed on the record id, so a
  re-swept part overwrites rather than duplicates.
- **Adding a device never requires touching the others** — no registration, no
  coordination, no restart.
