# Results: historical replay of BREAKOUT_RETEST_V1 on TAO (2026-10-13)

Run exactly as preregistered (`PREREGISTRATION.md`, frozen at `f166d3b`; script
`run.py` committed at `f7b9e74` before it was run). Retrospective evidence, not
confirmation. No parameter was or will be changed because of these results.
Full per-decision labels: `~/tdal-history/TAOUSDT/results_v1.json` (outside the repo), SHA-256 `0b915fa6541394973b3c10ca3227db5f4d851a28bb0be50a076b1bb21cd66e37`.
That file is unchanged. Rerunning `run.py` at `3180b42` (which now enforces the archive
manifest, see below) reproduces it byte for byte.

**Wording revised 2026-10-13 after review (PR #21, items A5 and A1–A6):** the registered
numbers and label below are as published. What changed: statements that the rule has
"no edge" are reworded as *not demonstrated*, the post-run note's date error is
corrected, and figures computed after the run are in a separate, labelled post-hoc
section, produced by a committed script instead of by hand.

## Population

543 `LONG_ELIGIBLE` decisions on 364 UTC days, 2024-04-12 to 2026-09-10. 181 had no
reward reference (`TARGET_UNAVAILABLE`); 362 were labelled; none censored. **Every
first-touch conclusion below covers only the 362 of 543 decisions with a reward
reference**; nothing here says how the rule's other 181 decisions would have fared.

## Primary (as preregistered)

16-bar horizon, close rule, 358 decided cases on 288 days:

| Mean gross R per decided case | 95% CI (UTC-day bootstrap) | Reading |
|---|---|---|
| **+0.29R** | **+0.16 to +0.42** | **GROSS_EDGE_IN_PAST** |

Target first in 245 of 358 (68%, Wilson 63–73%), against a breakeven of 41% at the
mean ratio.

## The preregistered secondaries undercut it

The secondaries were registered without intervals; the intervals quoted for them below
are post-hoc (see that section).

The primary scores every close-rule stop as exactly −1R. The preregistration called
that a best case. The secondaries show how much that convention matters:

1. **Close-exit R: −0.02R** (358 cases, sum −6.1R). Exiting at the actual close that
   breaks invalidation, the average stop costs **−1.97R**, not −1R, and the gross
   result is about zero (post-hoc 95% interval −0.21 to +0.17R).
2. **Wick rule: −0.01R** (16 bars, 323 decided, 39 ambiguous). If an intrabar touch of
   invalidation stops the trade, the gross result is again about zero; counting the 39
   ambiguous bars as stops (post-hoc) gives −0.12R.
3. **Bar 1:** 221 of the 358 decided cases resolve on the first bar after the signal
   (169 targets, 52 stops). The decision arrives about 4 minutes into that bar, so some
   of those touches may come before anyone could act. Excluding them gives +0.43R on
   137 cases, but that subset still assumes −1R losses.
4. **Selection baseline:** the 16-bar forward close move after a TDAL signal averages
   −0.004% (median −0.21%), against +0.011% (median −0.07%) for every candle and
   +0.0002% (median −0.10%) after a rising candle. These descriptive means don't show
   the signals selecting better forward moves than the baselines. They don't show the
   opposite either: the TDAL mean's post-hoc 95% interval is −0.25% to +0.25%, which
   contains both baselines.
5. **Other horizons:** close rule +0.24R (4 bars) and +0.29R (96 bars); wick rule
   −0.03R and −0.01R.

Why the primary looks good: targets are often very close (median ratio of reached
targets 0.49R; a third are under 0.25R), so they're touched quickly and often, while
stops are scored at −1R even though the breaching close is on average 1.97R away
(median 1.61R).

## Stability (primary cell)

Positive in both halves (+0.26R, n 174; +0.32R, n 184) and in all ten quarters
(+0.07R to +0.79R). It is consistently present, which is what to expect if it comes
from the −1R scoring convention rather than from a stretch of favourable markets.

## Reading

The preregistered primary reads **GROSS_EDGE_IN_PAST**, and that's recorded as such.
But when stops are priced less favourably (close-exit R, wick rule) there is **no
evidence of a gross edge**: the estimates are about zero and their post-hoc intervals
include zero (or, for the wick rule with ambiguous bars as stops, sit just below it).
The forward-move comparison doesn't show better selection either.
These are still modelled exits on Binance candles, not simulated Kraken fills, with
the venue and freshness gates assumed passed.

Costs are large against these stops. The median stop distance is 0.65% of price over
all 543 decisions (0.56% over the 358 decided cases), and a Kraken Tier 4 round trip
(maker entry 20 bps, taker exit 35, slippage 2 × 5, spread 3.35 = 68.35 bps) is
about 0.68%, so costs alone are about **1R per trade at the median stop**, and about
1.56R on average over all 543 decisions (1.77R over the 358 decided cases), post-hoc.

**On this evidence, this retrospective model does not demonstrate a usable edge for
BREAKOUT_RETEST_V1 as specified on TAO: no usable edge after costs is demonstrated,
since the gross estimates are near zero and costs are about 1R or more per trade.**
That is an absence of demonstrated benefit, not a proof that no benefit exists: no
analysis here was designed to establish that. The prospective study can still run as
preregistered, but there is little reason to expect a different answer, and the rule
should be redesigned on independent grounds, with the redesign tested on data it
wasn't designed on.

## Not covered

No paper ledger (PR #11 was a draft at freeze). Kraken spread, divergence and
freshness gates were assumed passed: the replay bypasses the production evaluator
(`FIDELITY.md`). The +5 min open-interest relabel and taker sourcing are verified only
for the one-month overlap with the live API and are assumed elsewhere. Whether Kraken
listed TAOUSD for the whole window was not checked.

## Post-run note: the primary inherited known design flaws (added 2026-10-13, after the results)

This plan copied its primary measure from PR #20. An independent review of PR #20,
issue #22, identifies four flaws in that measure. Issue #22 was opened at
2026-10-12T18:53Z, after this plan was frozen (`f166d3b`, 18:43Z) and after the results
were committed (`aab4404`, 18:48Z): **the plan was frozen before the review existed**.
(An earlier version of this note wrongly said the issue was posted before the freeze.)
All four flaws are visible here:

1. **Resolved cases only.** The mean excludes `NEITHER_WITHIN_HORIZON` (4 cases here,
   so a small effect in this run, but the estimand is conditional).
2. **Every close-rule loss scored −1R.** Actual breach-close exits average −1.97R
   (close-exit R above).
3. **Touches before the decision existed.** Bar 1 starts at the signal close; the
   decision arrives about 4 minutes later. 221 of 358 cases resolve on bar 1.
4. **Baseline not matched.** The selection baselines use forward close moves, not the
   primary's first-touch payoff.

So the primary's **GROSS_EDGE_IN_PAST** should be read only as a positive *conditional,
descriptive first-touch statistic*, not as evidence of a gross trading edge. The
conclusion above, that no usable edge is demonstrated, rests on the secondaries and is
unchanged. Future plans use the corrected estimand being written into PR #20.

## Post-hoc descriptive additions (NOT preregistered; added 2026-10-13 after review)

None of these was planned, and none changes the registered primary or its reading.
They are descriptive. Every figure is printed by `posthoc.py` at `3180b42`, which
checks `results_v1.json`'s SHA-256 and the registered manifest, reruns the replay,
confirms its 16-bar labels equal the registered `primary_rows`, and writes
`~/tdal-history/TAOUSDT/posthoc_v1.json` (new file, exclusive creation; SHA-256
`32ed7f6fd6145add9b796b0318c4210c92c347bb9ee6e3c76dff82f786503550`). Intervals use the
registered day-cluster bootstrap (10,000 draws, seed 20261013). Rounded from the
script's output:

| Figure (16 bars) | Value |
|---|---|
| Close-exit R, 358 cases on 288 days | −0.017R, 95% CI **−0.208 to +0.166** |
| Wick rule, the 39 `AMBIGUOUS_SAME_BAR` counted as stops, 362 cases on 289 days | **−0.115R**, 95% CI −0.227 to −0.0004 |
| Breaching-close distance of the 113 close-rule stops | mean **1.97R**, median **1.61R** |
| Reached-target ratio, 245 target-first cases | median **0.49R**; 33.9% under 0.25R |
| Decided cases resolved on bar 1 (close rule) | 221: 169 targets, 52 stops |
| Median stop distance | 0.648% of price (all 543); 0.561% (358 decided) |
| Round-trip cost 68.35 bps, in R | mean **1.56R**, median 1.05R (all 543); mean 1.77R, median 1.22R (358 decided) |
| TDAL 16-bar forward close move, 543 decisions | mean −0.004%, 95% CI −0.251% to +0.248% |
| Decisions with a reward reference | 362 of 543 |

The 0.49R, 169/52 and 0.65% figures quoted earlier in this file were first computed by
hand; the script reproduces them. The cost figures treat both legs as a fraction of
the reference price, which is approximate.

## Provenance and input enforcement (added after review)

`archive.py` now loads only the files `fetch_manifest.json` lists as `OK`, re-hashes
each against the manifest, and rejects missing, unlisted or wrong-symbol files.
`run.py` checks the registered manifest SHA-256 before analysing, refuses to
overwrite an existing output, and writes `OUT.provenance.json` beside the result with
the input fingerprint, git revision, configuration, replay skip counts and coverage.
The registered output format is unchanged. Rerun at `3180b42` (clean tree): output
SHA-256 `0b915fa6…66e37`, identical to `results_v1.json`; 87,490 candles considered,
77 skipped fail-closed, 87,413 evaluated (84,533 in the study window).
