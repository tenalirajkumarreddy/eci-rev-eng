#!/usr/bin/env python3
"""What does raising `discover_max_part` actually change?

Re-discovers the SAME already-catalogued AC at several caps and reports what each
cap cost and what it found. Discovery is idempotent (the part upsert is keyed on
(state, ac, part)), so re-running it changes no elector data.

How the cap works, from the code:
  * part numbers are probed upward in chunks of PART_CHUNK (200)
  * the cap is a FLOOR: the first chunk is widened to reach it
  * probing stops only when a whole chunk comes back EMPTY, so the cap is not a
    hard limit - real parts beyond it are still found
  * PART_HARD_CAP (3000) is the real ceiling, and `truncated` reports hitting it

So the cap trades probe requests for robustness against gaps in the numbering.

Run from the repo root:  python -X utf8 work/bench_discover_cap.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import old_eci_collector as m  # noqa: E402

STATE, AC = "S01", 8
CAPS = [200, 400, 1200, 2000]

_calls = [0]
_real_window = m.fetch_window
_real_serial = m.fetch_serial


def counting_window(*a, **kw):
    _calls[0] += 1
    return _real_window(*a, **kw)


def counting_serial(*a, **kw):
    _calls[0] += 1
    return _real_serial(*a, **kw)


def main():
    m.fetch_window = counting_window
    m.fetch_serial = counting_serial
    conn = m.connect()

    known = m.q("select count(*) c from old_parts where state_cd=%s and ac_no=%s",
                (STATE, AC), fetch="one")["c"]
    print("%s AC %s already has %d catalogued old parts (ground truth)" % (STATE, AC, known))
    print()
    print("%9s %9s %9s %9s %9s %9s  %s"
          % ("cap", "found", "probed_to", "requests", "secs", "req/s", "truncated"))
    for cap in CAPS:
        _calls[0] = 0
        t0 = time.time()
        res = m.discover_parts(conn, STATE, AC, max_part=cap, workers=28)
        secs = time.time() - t0
        print("%9d %9d %9d %9d %9.1f %9.1f  %s"
              % (cap, res["found"], res["probed_to"], _calls[0], secs,
                 _calls[0] / max(secs, 0.01), res["truncated"]))
        time.sleep(1)
    conn.close()

    print()
    print("Extra requests per AC vs 400 (the old default):")
    print("  cap  400 -> baseline ... see the request column above")
    print("Same `found` at every cap means the cap changed nothing but the cost.")
    print()
    print("What the app's `maybe_truncated` flag can and cannot see: it fires when")
    print("old_parts_found >= discover_max (the highest number probed), i.e. when")
    print("the AC was still producing parts at the point probing stopped.")


if __name__ == "__main__":
    main()
