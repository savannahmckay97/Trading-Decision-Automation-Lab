# Preregistration: prospective evaluation of BREAKOUT_RETEST_V1 on TAO (v1)

**DRAFT for owner review.** Items marked **[OWNER]** carry a proposed default
and must be confirmed or changed before this file is merged. The merge commit
freezes the plan. Until then nothing in it binds, and no outcome may be examined.

This is the step 3 of `docs/EVALUATION_READINESS.md` §4: it fixes the horizon,
the primary stop rule, the comparison and the holdout start before anyone looks
at outcomes. It also fixes the governed paper ledger (PR #11), because that
ledger computes outcomes too.

## 1. Status and what has been seen

- **Partition: holdout (confirmatory).** This is the first as-observed dataset.
  The exploratory first-touch study (`research/first_touch_exploratory/`) used
  reconstructed history and does not overlap this data in time.
- **Seen so far:** operational checks on `decisions.jsonl` only, namely
  timestamps, gate statuses, feature presence and collection errors
  (2026-10-11 and 2026-10-12). On 2026-10-11 live/replay parity compared full
  decisions on the first snapshots, before any forward path existed.
- **Not seen:** setup states, the number of `LONG_ELIGIBLE` decisions, reward
  references, and any price after a decision. The plan was written without
  them.

## 2. Frozen inputs

- **Data:** the prospective recording described in each segment's `MANIFEST.md`
  (segment 1: `~/tdal-recorder/data/` on the laptop; segment 2:
  `~/tdal-recorder/data/` on the cloud VM, backed up hourly to
  `OneDrive/TDAL-recorder-backup/segment2/`), from segment 1 (start **2026-10-11T06:08:13Z**, the holdout start) up to the
  analysis cut-off in §5. Raw snapshots in `observer.sqlite` are the source. Gaps
  are never backfilled.
- **Evaluator:** decisions are recomputed by replaying the recorded snapshots with
  the observer at **76dbb5d** (default `ObserverConfig`: TAO, 15m, k = 2,
  144-bar reward search). The recorded live decisions must match the replay
  exactly. Any mismatch halts the analysis until it's explained, and both are
  reported.
- **Later segments:** a new recorder segment is pooled only if its snapshots
  decode and replay under 76dbb5d unchanged. A collector change that 76dbb5d
  rejects (for example extra fields) ends the pooled holdout at that segment's
  start. Strategy or parameter changes are never pooled. They start a new
  experiment with its own holdout start.
- **Overlapping segments:** segment 2 (Oracle Cloud VM, Sydney, same code
  76dbb5d) started **2026-10-12T18:01:52Z** while segment 1 (the laptop) kept
  running, so some candles are recorded twice. The pooled series switches
  source once, at a fixed cutover: **segment 1 for candles closing before
  2026-10-13T00:00Z, segment 2 from then on.** Setup state carries across
  candles (a breakout, then a retest within 6 bars), so each stretch is taken
  whole from one recorder and the two are never mixed candle by candle. The
  cutover leaves segment 2 about six hours (24 candles) to warm up from its
  fresh start. After the cutover, segment 2's gaps are not filled from
  segment 1. Segment 2 is preferred because it is the long-running host. The
  rule is fixed before any outcome is examined and doesn't depend on what
  either segment decided. For the overlapping candles, both segments'
  decisions are compared as an operational parity check (state, signal, risk
  reason, breakout-quality score, ignoring timing-dependent `DATA_STALE`
  rows), and every difference is reported. **[OWNER]**
- **Outcome prices:** Binance USD-M TAOUSDT completed 15m candles, taken from
  the recorded snapshots (each carries 150 candles, so gaps shorter than about
  37 h are filled by later snapshots that observed those candles). Execution
  would be on Kraken TAOUSD. The venues differ by about 10 bps, and that's a
  stated limitation, not a correction.

## 3. Population

Every `LONG_ELIGIBLE` decision in the window, counting the first gate-passing
observation per completed candle (the harness rule in `DECISION_EVALUATION.md`).
Blocked cycles count towards coverage only. Decisions without a reward reference
(`TARGET_UNAVAILABLE`) and censored paths are counted and reported, never scored.

## 4. Analyses

### Primary

- **Outcome:** reward first touch under the **close rule**
  (`COMPLETED_CLOSE_BELOW`) at a **16-bar (4 h) horizon**, using the harness's
  `_first_touch` unchanged.
- **Statistic:** **mean realised gross R per decided case**: `+rᵢ` for
  `TARGET_FIRST`, `−1` for `STOP_FIRST`. The exploratory study found it the more
  direct per-case number, because it doesn't assume the hit rate is independent
  of the ratio. **[OWNER]**
- **Uncertainty:** a 95% percentile bootstrap that resamples **UTC days** with
  replacement (overlapping setups on one day aren't independent), 10,000 draws,
  seed 20261012.
- **Reading, fixed in advance:**
  - lower bound > 0: the setup shows a gross edge; proceed to the net question
    (ledger, below);
  - upper bound < 0: evidence against the rule as specified; stop treating it as
    a candidate and redesign on independent grounds;
  - otherwise: inconclusive. The analysis is not rerun on the same data with
    different settings.

### Co-reported with the primary (not used for the reading)

- Target-first fraction of decided cases with its Wilson 95% interval, against
  the breakeven rate at the mean ratio `1 / (1 + mean r)`.
- Every status count: `TARGET_FIRST`, `STOP_FIRST`, `AMBIGUOUS_SAME_BAR`,
  `NEITHER_WITHIN_HORIZON`, `TARGET_UNAVAILABLE`, censored.

### Secondary (all reported; none used to choose anything)

1. **Other horizons and rules:** 4 and 96 bars; wick rule at all three horizons.
2. **Close-exit R:** the close rule exits at a close below invalidation, so
   gross R of −1 is a best case. Report R using the actual breach close as the
   exit.
3. **Bar-1 sensitivity:** decisions arrive about 1–3 min after the close, but bar 1
   of the path starts at the close. Report the primary statistic both with and
   without the cases whose first event is on bar 1.
4. **Selection baselines** (harness 0.1, 16 bars): TDAL vs `SELECT_ALL` and
   `POSITIVE_PRIOR_RETURN`, mean and median forward close movement, worst
   adverse excursion. Descriptive.
5. **Governed paper ledger** (RiskGovernor, PR #11): one $1,000 paper account
   run chronologically over the same decisions, with parameters as fixed there
   (0.5% risk; −2% daily or 3-loss breaker; one position; no leverage;
   close-below-invalidation and 0.5 ATR emergency stop). `MAKER_STAGGERED` is
   reported first, `MARKET_BASELINE` alongside. Costs: Kraken taker 40 bps,
   maker 25 bps per side, slippage 5 bps, spread from the recorded quote at
   decision. **[OWNER]** Report ending equity, net R, maximum drawdown, entry
   fill rate, cost share of risk, breaker trips and every journal line. The
   maker fill model is optimistic (no queue, partial fills or adverse
   selection), and fills are priced on Binance candles. So a positive ledger
   alone is not a success, and it can't rescue a primary that isn't positive.
6. **Coverage:** recorder uptime, gap list, blocked-cycle share and the share of
   decisions that are `TARGET_UNAVAILABLE`.

## 5. When the analysis runs

**One analysis, no interim outcome looks.** **[OWNER]** It runs at the earlier of:

- **110 `LONG_ELIGIBLE` decisions that have a reward reference at observation**
  (expected about 90 decided cases, roughly ±10 points on the target-first rate;
  `EVALUATION_READINESS.md` §3 estimates about 11–12 months); or
- **2027-10-11T00:00Z**, one year after the holdout start.

The count uses decision fields only (setup state and the presence of
`risk.reward_reference`), never prices after the decision. So a script may
track it while recording continues. Operational monitoring (uptime, gates,
errors) is always allowed.

## 6. Code frozen before the look

Before the analysis runs, these are committed and tested only on synthetic
fixtures, the exploratory backfill, or a copy of the database with outcome
outputs suppressed:

- an export from `observer.sqlite` to canonical snapshot JSONL plus a dataset
  manifest (`dataset.py`; none exists today);
- a parity check of replay vs recorded decisions;
- the analysis script for §4 (the bootstrap, close-exit R and bar-1 sensitivity
  are new code);
- the ledger at the PR #11 commit used. If PR #11 changes before the look, this
  file is amended first, and the amendment says so.

## 7. Commitments

- **No parameter changes based on these results.** This covers breakout,
  retest, quality, reward k or window, horizon, stop rule and governor
  parameters. A changed rule is a new experiment on new data.
- **The 4h structural reference** recommended by PR #10 is not part of v1.
  Segment 1 tests the rule as recorded. If the reference is added, it's a
  separate experiment from its own start date. **[OWNER]**
- **No claims** of profitability, calibrated probability or demonstrated
  decision benefit. The reading in §4 is the only inference.
- **Everything is published**: every horizon, both stop rules, every
  decision and every journal line, including unfavourable or empty results.
  Deviations from this plan are listed in the results as deviations.
