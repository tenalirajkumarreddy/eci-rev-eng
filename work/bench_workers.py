#!/usr/bin/env python3
"""Why does adding workers stop helping? Measure it instead of guessing.

Sweeps the same serial range of one old part at increasing concurrency and
reports throughput, per-request latency and the HTTP status mix. If throughput
flattens while per-request latency climbs, the ceiling is on the server side
(the gateway queueing us), not in this client.

Also measures the client's own costs so they can be ruled in or out:
  * a pooled round trip (`select 1`)
  * a batched write (executemany of 500 upsert rows) into a TEMP table, so no
    real row is touched

Run from the repo root:  python -X utf8 work/bench_workers.py
"""
from __future__ import annotations

import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import old_eci_collector as m  # noqa: E402

STATE, AC, PART = "S01", 1, 1
N = 200                     # serials per configuration
WORKERS = [1, 2, 4, 8, 16, 32, 56]


def sweep(workers, n=N):
    """Fetch serials 1..n with `workers` threads -> (seconds, latencies, statuses)."""
    lat, statuses = [], []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as tp:
        for st, _payload in tp.map(lambda s: m.fetch_serial(STATE, AC, PART, s),
                                   range(1, n + 1)):
            statuses.append(st)
    return time.time() - t0, lat, statuses


def percentile(values, pct):
    if not values:
        return 0.0
    values = sorted(values)
    k = max(0, min(len(values) - 1, int(round((pct / 100.0) * len(values) + 0.5)) - 1))
    return values[k]


def main():
    print("=" * 78)
    print("HTTP sweep of %s AC %s part %s, serials 1..%d" % (STATE, AC, PART, N))
    print("=" * 78)
    print("%8s %9s %9s %9s %9s %9s  %s"
          % ("workers", "secs", "req/s", "per-req", "p95", "429/err", "status mix"))
    rows = []
    for w in WORKERS:
        secs, lat, statuses = sweep(w)
        rps = N / secs
        per_req = secs / N * w          # wall time a caller sees per request
        bad = sum(1 for s in statuses if s != 200)
        mix = {}
        for s in statuses:
            mix[s] = mix.get(s, 0) + 1
        print("%8d %9.2f %9.1f %9.3f %9s %9d  %s"
              % (w, secs, rps, per_req, "-", bad,
                 " ".join("%s:%d" % kv for kv in sorted(mix.items()))))
        rows.append((w, secs, rps))
        time.sleep(2)

    base = rows[0][2]
    print()
    print("scaling vs 1 worker:")
    for w, secs, rps in rows:
        print("  %3d workers: %6.1f req/s  (%4.2fx, %5.2f req/s per worker)"
              % (w, rps, rps / base, rps / w))

    # ---------------------------------------------------------------- DB costs
    print()
    print("=" * 78)
    print("client-side costs (to rule the DB path in or out)")
    print("=" * 78)
    conn = m.connect()
    with conn.cursor() as cur:
        t0 = time.time()
        for _ in range(20):
            cur.execute("select 1")
            cur.fetchone()
        print("  pooled round trip      : %.1f ms" % ((time.time() - t0) / 20 * 1000))

        cur.execute("""create temp table bench_el (
                         source_id text primary key, a text, b text, c integer,
                         d text, e text, f integer)""")
        rows_to_write = [("bench-%d" % i, "n%d" % i, "r%d" % i, 30 + i % 40,
                          None, None, None) for i in range(500)]
        sql = """insert into bench_el(source_id,a,b,c,d,e,f) values (%s,%s,%s,%s,%s,%s,%s)
                 on conflict (source_id) do update set a=excluded.a, b=excluded.b"""
        t0 = time.time()
        with conn.cursor() as cur2:
            cur2.executemany(sql, rows_to_write)
        dt = time.time() - t0
        print("  executemany 500 rows   : %.0f ms total, %.2f ms/row"
              % (dt * 1000, dt * 1000 / 500))
    conn.close()
    print()
    print("For reference, one old part is ~670 serials and ~670 records, so the")
    print("client-side costs above are a small fraction of a part's wall time.")


if __name__ == "__main__":
    main()
