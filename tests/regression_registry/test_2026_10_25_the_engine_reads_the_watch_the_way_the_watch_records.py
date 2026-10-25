"""Feature pin (2026-10-25, Kipchoge S0.3): what eight real runs taught
the engine before it had goldens.

THE GAPS (found running the engine on fixtures extracted from a read-
only copy of the store, then checked against the thread's reads):
  * A workout's heart-rate series from the Watch's day data is sampled
    every ~5 s with gaps of 11–72 s where the pulse was lost (sweat, a
    loose strap, a stop the Watch did not pause for). Read as pauses,
    they cut a run's moving time by minutes and made every pace too
    fast, a minute a mile on one run.
  * The sleep bridge stores the Watch's staged sleep with `asleep_s` 0
    and the time in `core_s + deep_s + rem_s`; a night the Watch joined
    to an afternoon nap ends AFTER the next morning's run; and a night
    was on file twice at a 3-hour shifted clock. The night before read
    0.0 h for most runs.
  * A Zone 2 run whose main block drifted over the cap typed as a tempo
    (grade D, no fitness point) when the thread and the PRD call it a
    weak Zone 2 run (grade C).

THE PINS (agents/kipchoge/ingest.py, segments.py, grade.py, analyze.py):
  * A heart-rate gap is reported (`hr_gaps`, count, longest, total),
    never subtracted. A pause is standing still in the speed series, a
    gap in the route when the Watch's own duration says it paused, or
    the stop between a split run's parts. A heart-rate-only run's
    moving time is the Watch's active duration (its pauses already out).
  * The night before is the longest session starting within 20 h before
    the run, its hours from the stages when it has them; a shifted copy
    counts once; the night before that starts 20–44 h before.
  * The whole run's moving average types a run; tempo starts 3 bpm over
    LT1's ceiling (5 over the band when LT1 is not on file).
  * A run's elapsed span (end − start) and its active duration travel
    separately; a split run sums the parts' active durations.

The public cases are synthetic (tests/kipchoge/test_engine_synthetic.py,
re-exported here so the registry runs them); the private goldens on his
runs reproduce the thread's halves, pace, sleep, ramp and artifact
within tolerance when vault/kipchoge/ is present.
"""
from __future__ import annotations

from tests.kipchoge.test_engine_synthetic import (  # noqa: F401
    con,
    test_a_block_that_drifts_over_the_cap_is_a_weak_zone2_run_not_a_tempo,
    test_a_split_workout_is_read_as_one_session,
    test_heart_rate_gaps_are_reported_not_subtracted,
    test_the_night_before_comes_from_the_stages_and_may_end_after_the_run,
    test_the_watch_s_own_pause_is_the_gap_in_the_route,
)
