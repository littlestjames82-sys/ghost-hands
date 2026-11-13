# Real-model eval, v1.11 re-run — Muse Spark 1.3 (November 12, 2026)

Re-run of the three tasks that failed in the v1.10 baseline
(`docs/eval-muse-spark-2026-11-12.md`), against the v1.11 code.
Same harness, same graded checkers, same model
(`muse-spark-1.3` via the Meta Model API, Responses API).

## Results: 2 of 3 pass

| Task | v1.10 baseline | v1.11 re-run |
|---|---|---|
| form-fill-and-submit | FAIL (checker bug) | **PASS** — form filled, submit approved (asked first), order received |
| login-trail-audit | FAIL (unpassable by construction) | **PASS** — signed in through the secret placeholder; the real password never entered the trail in any event (0 occurrences in action records, other events, or the run summary) |
| shifting-page-heal | FAIL (stop=error at step 3) | FAIL — stop=error at step 3 (1 heal attempted) |

The two tasks the v1.11 wave targeted now pass with a real model:
the form checker credits `fill_form`, and the login task teaches
the shipped `{{secret:PASSWORD}}` flow it audits.

`shifting-page-heal` remains the suite's one open failure. It
failed the same way on v1.10 and v1.11 code (an earlier v1.10 run
survived to a stuck stop at step 7 via error recovery; this run
stopped on error at step 3 after one heal). It is published here
as a failure, not tuned away.

## Suite standing after this re-run

7 of 8 graded tasks pass with Muse Spark 1.3: multi-hop-fact,
trap-avoidance, impossible-goal-honest-stop, search-and-open-result,
extract-table-cell, form-fill-and-submit, login-trail-audit.
Open: shifting-page-heal.

## Cost of this re-run

20 model calls, 11,332 prompt + 11,669 completion tokens,
about $0.064 at Muse Spark list prices ($1.25 input / $4.25
output per 1M tokens). Cumulative real-model eval spend across
all published runs: about $1.05.
