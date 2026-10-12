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

The **targeted population** is the first observation per completed candle whose
signal is `LONG_ELIGIBLE` and whose `risk.reward_reference.status` is `AVAILABLE`
(the harness's first-observation rule in `DECISION_EVALUATION.md`; repeated polls of
one candle count once). Eligible decisions without an available reward reference are
counted and reported separately (`TARGET_UNAVAILABLE`), scored only in secondary 2.
Blocked cycles count towards coverage only.

## 4. Analyses

Amended 2026-10-13 after the independent review in issue #22, before any
prospective outcome was examined. The earlier primary (mean first-touch R over
resolved cases, losses scored −1R, paths starting at the signal close) is kept only
as a descriptive statistic below, because it conditions on resolution, flatters
close-rule losses and lets prices before the decision decide the result.

### Primary: observation-aligned gross trade outcome

For each case in the targeted population, a hypothetical long trade, frozen as:

- **Entry:** the open of the first 15m candle that opens at or after the recorded
  `decision_time_ms`. Decisions arrive 1–3 min after the signal close, so this is
  normally the second candle after the signal candle; the first is already under way
  when the decision exists. Price before that open is never used.
- **Not entered:** if that entry open is already at or above the reward level, or at
  or below invalidation, the trade isn't taken and scores **0R**. It stays in the
  population.
- **Exits**, checked bar by bar from the entry candle for **16 candles (4 h)**:
  the reward level is touched (high ≥ level), exit at the level; otherwise a candle
  closes below invalidation, exit at that close; if both happen in one candle, the
  target counts first (the close rule acts only at the close). Otherwise exit at the
  close of the 16th candle.
- **R** = (exit − entry) / (entry − invalidation), gross of costs.
- **Censored:** a missing candle before the exit. Counted and reported, not scored.

**Statistic:** mean gross R over every scored case (pooled: total R / cases).

**Uncertainty:** 95% percentile bootstrap resampling clusters with replacement,
10,000 draws, seed 20261012. A cluster is the UTC day of the signal close, and only
days with at least one scored case are clusters, so no draw is empty. Sensitivity:
the same with 2-day and 7-day blocks, for dependence across midnight and adjacent days.

**Reading, fixed in advance:**
- fewer than 30 scored cases or fewer than 15 clusters: **INCONCLUSIVE_SPARSE**;
- lower bound > 0: the signals produced positive gross trade-like outcomes before
  costs; proceed to the net question (ledger). This is not a claim of selection value
  (secondary 1) or of net profitability;
- upper bound < 0: evidence against the rule as specified; redesign on independent grounds;
- otherwise: inconclusive. The analysis is not rerun with different settings.

### Secondary (all reported; none used to choose anything)

1. **Matched comparator, for selection value.** The same entry, exit and R
   definitions applied to every other evaluated candle in the window, with the
   reward level from the same reward-reference function at that candle and
   invalidation at entry − D × ATR, where D is the median `distance_atr` of the
   targeted population. Report TDAL minus comparator mean R with the same
   bootstrap. Only a positive lower bound here would support a claim that TDAL
   selects better trades than its own geometry applied indiscriminately.
2. **TARGET_UNAVAILABLE cases:** the same trade with only the invalidation and
   horizon exits.
3. **Other horizons and stop rules:** 4 and 96 candles; a wick stop (exit at the
   invalidation level when a low touches it, and stop-first when target and stop
   share a candle).
4. **Descriptive first-touch statistic** (the pre-amendment primary): mean R over
   resolved cases with losses at −1R and paths from the signal close, with all status
   counts. A mechanism description, not an edge measure.
5. **Forward-move baselines** (harness 0.1, 16 bars): TDAL vs `SELECT_ALL` and
   `POSITIVE_PRIOR_RETURN`. Descriptive.
6. **Governed paper ledger** (RiskGovernor, PR #11): one $1,000 paper account
   run chronologically over the same decisions, with parameters as fixed there
   (0.5% risk; −2% daily or 3-loss breaker; one position; no leverage;
   close-below-invalidation and 0.5 ATR emergency stop). `MAKER_STAGGERED` is
   reported first, `MARKET_BASELINE` alongside. Costs: Kraken Pro spot
   **Tier 4, taker 35 bps and maker 20 bps per side** (the owner's actual
   tier), slippage 5 bps, spread from the recorded quote at decision. Source:
   kraken.com/features/fee-schedule, read 2026-10-13; it replaced the older
   schedule this draft first assumed. A sensitivity at Tier 1 (taker 80, maker
   40 bps, the tier for an account without the owner's volume or assets) is
   reported alongside and labelled as such. **[OWNER]** Report ending equity, net R, maximum drawdown, entry
   fill rate, cost share of risk, breaker trips and every journal line. The
   maker fill model is optimistic (no queue, partial fills or adverse
   selection), and fills are priced on Binance candles. So a positive ledger
   alone is not a success, and it can't rescue a primary that isn't positive.
7. **Coverage:** recorder uptime, gap list, blocked-cycle share and the share of
   decisions that are `TARGET_UNAVAILABLE`.

## 5. When the analysis runs

**One analysis, no interim outcome looks.** **[OWNER]** It runs at the earlier of:

- **110 cases in the targeted population** (§3: first observation per candle,
  `LONG_ELIGIBLE`, `reward_reference.status == AVAILABLE`; `EVALUATION_READINESS.md`
  §3 estimates about 11–12 months); or
- **2027-10-11T00:00Z**, one year after the holdout start.

The count uses decision fields only (signal, completed candle and
`risk.reward_reference.status`), never prices after the decision. If the deadline
arrives first with too few cases, the reading is INCONCLUSIVE_SPARSE (§4). So a script may
track it while recording continues. Operational monitoring (uptime, gates,
errors) is always allowed.

## 6. Code frozen before the look

Before the analysis runs, these are committed and tested only on synthetic
fixtures, the exploratory backfill, or a copy of the database with outcome
outputs suppressed:

- an export from `observer.sqlite` to canonical snapshot JSONL plus a dataset
  manifest (`dataset.py`; none exists today);
- a parity check of replay vs recorded decisions on canonical fields: completed
  candle, setup state and transition, signal, risk status and reason, invalidation
  and reward levels, gate statuses. Decision IDs, mode and permission metadata and
  observation timestamps legitimately differ between live and replay and are excluded;
- the analysis script for §4, including the matched comparator and the sparse and
  bootstrap edge cases, tested on synthetic fixtures;
- the ledger at a pinned PR #11 commit with the issue #22 findings G1–G7 fixed. The
  ledger results published in PR #11 so far come from the unrepaired model and are
  not used. This file is amended to name the pinned commit before the look.

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
- **Amendments before the look are allowed, on the record.** Until the one
  analysis in §5 runs, any part of this plan may be changed if a reasoned
  argument for the change comes up. Each amendment is its own commit to this
  file. It states what changed, why, the date, and that no outcome had been
  examined. The results list every amendment. Once the analysis has run,
  changes are deviations, not amendments.
