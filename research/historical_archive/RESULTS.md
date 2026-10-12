# Results: historical replay of BREAKOUT_RETEST_V1 on TAO (2026-10-13)

Run exactly as preregistered (`PREREGISTRATION.md`, frozen at `f166d3b`; script
`run.py` committed at `f7b9e74` before it was run). Retrospective evidence, not
confirmation. No parameter was or will be changed because of these results.
Full per-decision labels: `~/tdal-history/TAOUSDT/results_v1.json` (outside the repo), SHA-256 `0b915fa6541394973b3c10ca3227db5f4d851a28bb0be50a076b1bb21cd66e37`.

## Population

543 `LONG_ELIGIBLE` decisions on 364 UTC days, 2024-04-12 to 2026-09-10. 181 had no
reward reference (`TARGET_UNAVAILABLE`); 362 were labelled; none censored.

## Primary (as preregistered)

16-bar horizon, close rule, 358 decided cases on 288 days:

| Mean gross R per decided case | 95% CI (UTC-day bootstrap) | Reading |
|---|---|---|
| **+0.29R** | **+0.16 to +0.42** | **GROSS_EDGE_IN_PAST** |

Target first in 245 of 358 (68%, Wilson 63–73%), against a breakeven of 41% at the
mean ratio.

## The preregistered secondaries undercut it

The primary scores every close-rule stop as exactly −1R. The preregistration called
that a best case. The secondaries show how far from it reality is:

1. **Close-exit R: −0.02R** (358 cases, sum −6.1R). Exiting at the actual close that
   breaks invalidation, the average stop costs **−1.97R**, not −1R, and the edge is gone.
2. **Wick rule: −0.01R** (16 bars, 323 decided, 39 ambiguous). If an intrabar touch of
   invalidation stops the trade, there is no edge either.
3. **Bar 1:** 221 of the 358 decided cases resolve on the first bar after the signal
   (169 targets, 52 stops). The decision arrives about 4 minutes into that bar, so some
   of those touches may come before anyone could act. Excluding them gives +0.43R on
   137 cases, but that subset still assumes −1R losses.
4. **Selection baseline:** the 16-bar forward close move after a TDAL signal averages
   −0.004% (median −0.21%), against +0.011% (median −0.07%) for every candle and
   +0.0002% (median −0.10%) after a rising candle. The signals don't pick better
   upward moves than chance.
5. **Other horizons:** close rule +0.24R (4 bars) and +0.29R (96 bars); wick rule
   −0.03R and −0.01R.

Why the primary looks good: targets are often very close (median ratio of reached
targets 0.49R; a third are under 0.25R), so they're touched quickly and often, while
stops are scored at −1R even though the breaching close is typically about 2R away.

## Stability (primary cell)

Positive in both halves (+0.26R, n 174; +0.32R, n 184) and in all ten quarters
(+0.07R to +0.79R). It is consistently present, which is what to expect if it comes
from the −1R scoring convention rather than from a stretch of favourable markets.

## Reading

The preregistered primary reads **GROSS_EDGE_IN_PAST**, and that's recorded as such.
But under realistic exits (close-exit R, wick rule) the gross edge is about zero, and
the signals select no better forward moves than chance. Costs make it clearly negative:
the median stop distance is 0.65% of price, and a Kraken Tier 4 round trip (maker
entry 20 bps, taker exit 35, slippage 2 × 5, spread 3.35) is about 0.68%, so costs
alone are about **1R per trade**.
**On this evidence, BREAKOUT_RETEST_V1 as specified has no usable edge on TAO.** The
prospective study can still run as preregistered, but there is little reason to expect
a different answer, and the rule should be redesigned on independent grounds, with the
redesign tested on data it wasn't designed on.

## Not covered

No paper ledger (PR #11 was a draft at freeze). Kraken spread, divergence and
freshness gates were assumed passed. Whether Kraken listed TAOUSD for the whole window
was not checked.

## Post-run note: the primary inherited known design flaws (added 2026-10-13, after the results)

This plan copied its primary measure from PR #20. An independent review of PR #20
(issue #22, posted 2026-10-12, before this plan froze but not consulted when freezing it)
had already identified four flaws in that measure. All four are visible here:

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
conclusion above, no usable edge, rests on the secondaries and is unchanged. Future
plans use the corrected estimand being written into PR #20.
