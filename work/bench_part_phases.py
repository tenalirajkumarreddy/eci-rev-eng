#!/usr/bin/env python3
"""Where does a part's wall time actually go, and what do extra workers buy it?

For one real old part, at several worker counts, this measures:

  1. probe_roll_end       SEQUENTIAL requests to find the last serial (fixed cost)
  2. HTTP sweep 1..roll_end  the parallel fetches, timed on their own
  3. collect_part(force=True)  the real thing, end to end
  4. overhead = 3 - 1 - 2      batched upserts, progress updates, mark-running,
                               the final update and calibration - all synchronous
                               on the main thread, so they never overlap the sweep

Run from the repo root:  python -X utf8 work/bench_part_phases.py
"""
from __future__ import annotations

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import old_eci_collector as m  # noqa: E402

STATE, AC, PART = "S02", 1, 6      # 220 serials: small, so fixed costs stand out
WORKERS = [4, 8, 16, 32]

_calls = [0]
_real_fetch = m.fetch_serial


def counting_fetch(*a, **kw):
    _calls[0] += 1
    return _real_fetch(*a, **kw)


def main():
    m.fetch_serial = counting_fetch          # collect_part resolves it at call time
    conn = m.connect()

    # the range is the same for every run, so find it once
    roll_end = m.probe_roll_end(STATE, AC, PART, hard_cap=3000)
    print("part %s AC %s P%s: roll_end=%d, re-collected at each worker count"
          % (STATE, AC, PART, roll_end))
    print()
    print("%8s %9s %9s %9s %9s %9s %9s"
          % ("workers", "probe", "http", "total", "overhead", "calls", "req/s"))
    rows = []
    for w in WORKERS:
        _calls[0] = 0
        t0 = time.time()
        m.probe_roll_end(STATE, AC, PART, hard_cap=3000)
        probe = time.time() - t0
        probe_calls = _calls[0]

        _calls[0] = 0
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=w) as tp:
            list(tp.map(lambda s: m.fetch_serial(STATE, AC, PART, s),
                        range(1, roll_end + 1)))
        http = time.time() - t0
        http_calls = _calls[0]

        _calls[0] = 0
        res = m.collect_part(conn, STATE, AC, PART, force=True, workers=w)
        total = res["seconds"]
        total_calls = _calls[0]

        overhead = total - probe - http
        rows.append((w, total, http, probe, overhead, res))
        print("%8d %9.2f %9.2f %9.2f %9.2f %9d %9.1f"
              % (w, probe, http, total, overhead, total_calls,
                 roll_end / max(total, 0.01)))
        time.sleep(1)
    conn.close()

    print()
    print("same part, scaling:")
    base = rows[0][1]
    for w, total, http, probe, overhead, res in rows:
        print("  %3d workers: %5.1fs total = %4.1fs probe + %5.1fs http + %4.1fs overhead"
              "   (%.2fx faster than %d workers, %d records)"
              % (w, total, probe, http, overhead, base / total, rows[0][0],
                 res.get("records", 0)))


if __name__ == "__main__":
    main()
