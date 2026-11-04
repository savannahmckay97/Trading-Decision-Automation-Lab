# Evaluation readiness: can TDAL's assessments be tested yet? (2026-10-11)

Update after the issue #7 fix: the live observer now aligns derivatives to the
completed candle and has emitted NO_TRADE with passing gates against live data.
The dataset assessment below describes the pre-fix recordings. Prospective
capture is still needed, and an independent replay-envelope timestamp limitation
was observed; see [verification and limits](DERIVATIVE_CANDLE_ALIGNMENT.md).


Priority 3 is establishing whether TDAL's trade assessments carry useful
predictive information. The harness can now label selections against baselines
(0.1) and the reward reference against invalidation (0.2). This note records
whether there is data to run it on, and roughly how much is needed.

## 1. Today no real dataset can produce a TDAL selection

- **Live recordings:** always `DATA_STALE` (issue #7: taker/OI freshness).
- **Historical import (`ingestion.py`):** candles only, so every replayed
  observation is blocked for missing derivative inputs.

Any evaluation result today would therefore come from synthetic fixtures,
which verify plumbing only.

## 2. Setup frequency on 29 days of real data (diagnostic)

`research/setup_frequency/` downloads public Binance USD-M TAOUSDT history
(15m klines; 5m open-interest and taker ratio, which Binance keeps for about 30
days) and replays the **production** feature, score and state-machine functions
bar by bar.

Assumptions, because this is not an as-of TDAL dataset:

- **Candle-aligned derivatives.** The taker window is the three buckets that
  make up the candle, and OI is the row at the candle's close boundary. This is
  the alignment proposed for the issue #7 fix, not what live code does today.
- **No historical Kraken quotes.** Spread is fixed at 3.35 bps, the live value
  observed on 2026-10-10, and divergence at 0, so the spread and venue gates
  never block. Freshness gates are not applied.
- **Possibly revised history.** Values are as served on 2026-10-11, not as
  published at the time; Binance revisions are unknown.
- **Decision time 240 s after the close.** This is later than the slowest
  taker publication observed live (194 s). The first outcome bar still
  includes those minutes before the decision.
- **Outcomes computed later, under preregistration.** Forward outcomes were
  not computed for this count. They were computed afterwards in a
  preregistered exploratory analysis: `research/first_touch_exploratory/`.

Result (2,779 bars, 28.95 days, ending 2026-10-11 UTC; no skipped bars):

| Measure | Count |
|---|---|
| Completed closes above resistance + buffer | 107 (quality score ≥ 1: 80) |
| Breakouts (`IDLE → BREAKOUT_OBSERVED`) | 58 |
| Failed (breakout or retest) / expired | 28 / 13 |
| `LONG_ELIGIBLE` | **17** (≈ 17.6 per 30 days) |
| … with a reward reference / `TARGET_UNAVAILABLE` | 9 / 8 |

This is one month, one asset and one market regime. The gate assumptions
probably overstate frequency somewhat.

## 3. What that implies

- **The baseline comparison will take months.** About 17 selections a month,
  with consecutive setups and overlapping horizons not independent, makes the
  current descriptive comparison (TDAL vs `SELECT_ALL` / momentum) exploratory
  for months.
- **The reward question is slower still.** Only about half of selections have a
  target, and some will end `NEITHER` or `AMBIGUOUS`. Even if every targeted
  selection resolved, that is about 9 decided cases a month. Estimating a
  target-first rate to about ±10 percentage points (95%, treating cases as
  independent) needs about 96 decided cases, roughly 11 months. About ±15 points
  needs about 43, roughly 5 months. Dependence makes both longer. These are rough
  lower bounds, not a power analysis.
- **Target coverage is a design weakness, now measured.** 8 of 17 eligible
  decisions had no confirmed overhead swing high within 36 h, which is the
  trade-off predicted in `REWARD_REFERENCE.md` §3. A longer or higher-timeframe
  level search would raise coverage, but it needs more history per snapshot (a
  collector change). This is about availability, not outcomes, so revisiting
  it does not tune anything to results. It is still a parameter change, and
  should be decided and frozen before any outcome is examined.

## 4. Recommended path

1. **Fix issue #7 (Codex) and start prospective as-of recording immediately.**
   This is the only source of genuinely as-observed evidence. The evaluation
   clock starts when it does.
2. **Optionally, a reconstructed derivative backfill as an *exploratory*
   dataset.** Extend ingestion to the 30-day 5m OI/taker history, mark it
   `RECONSTRUCTED_NOT_AS_OBSERVED`, and model publication lag using the delays
   measured in #7. It would let the harness run end to end on real prices now.
   It must never be presented as holdout or as-observed evidence. This overlaps
   Codex's ingestion and as-of work, so it should be coordinated there rather
   than built here.
3. **Preregister before anyone looks at outcomes:**
   - the horizon (for example 16 bars, 4 h, for first touch);
   - the stop rule reported as primary (close, matching the state machine);
   - the target-first vs breakeven comparison;
   - a holdout start date.

   Drafted for owner review in `research/prospective_v1/PREREGISTRATION.md`
   (holdout start 2026-10-11T06:08:13Z, the start of prospective recording).
4. **Do not widen to more assets just to gain sample size** until the
   single-asset loop works (`PRODUCT_DIRECTION.md`). Pooling would speed
   evaluation, but it would mix regimes and venues.

Reproduce (network required; outputs go outside the repo):

```sh
python -I research/setup_frequency/fetch_binance_history.py /tmp/tdal_backfill
python -I research/setup_frequency/count_setups.py /tmp/tdal_backfill
```
