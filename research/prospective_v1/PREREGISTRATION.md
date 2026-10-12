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
- **Disclosure: the measure design was informed by history for this same rule.**
  The two amendments that define the current primary (`0ba6ade`, and the
  2026-10-13 amendment that added this paragraph) were written after the author
  had seen PR #21's historical replay results for BREAKOUT_RETEST_V1 on TAO
  (2024-04 to 2026-09) and the independent review in issue #22. Both shaped the
  outcome measure: the R denominator, the not-entered rules, the comparator's
  risk distance D and the sample-size rationale in §5 rest on design checks on
  that published, non-holdout history. Numbers from it appear below only where
  they justify a definition. The rule itself (breakout, retest, quality, reward
  reference, parameters) was not changed. The prospective holdout's outcomes
  remain unexamined.

## 2. Frozen inputs

- **Data:** the prospective recording described in each segment's `MANIFEST.md`
  (segment 1: `~/tdal-recorder/data/` on the laptop; segment 2:
  `~/tdal-recorder/data/` on the cloud VM, backed up hourly to
  `OneDrive/TDAL-recorder-backup/segment2/`), from segment 1 (start
  **2026-10-11T06:08:13Z**, the holdout start) up to the analysis cut-off in §5.
  Raw snapshots in `observer.sqlite` are the source. Gaps are never backfilled.
- **Evaluator:** decisions are recomputed by replaying every recorded snapshot,
  including those that produced blocked cycles, with the observer at **76dbb5d**
  (default `ObserverConfig`: TAO, 15m, k = 2, 144-bar reward search). The
  population and every outcome are built from the replay rows, mapped to the
  live decisions as in §3.
- **Live/replay parity:** for every snapshot, the replay row must equal the
  recorded live decision on the canonical parity contract in §6, which covers
  the decision's content including features and breakout-score components. That
  is what "match" means here, not literal equality of whole records, whose IDs,
  schemas and mode metadata legitimately differ. Any canonical mismatch halts the
  analysis until it's explained, and both versions are reported.
- **Times have different meanings.** A snapshot's `observed_at_ms` is when its
  collection completed; a live decision's `decision_time_ms` is when the decision
  was emitted, after evaluation. Neither is treated as harmless metadata: §3 fixes
  which one defines entry, and §6 validates both against the snapshot's cutoffs
  and receipts.
- **Later segments:** a new recorder segment is pooled only if its snapshots
  decode and replay under 76dbb5d unchanged. A collector change that 76dbb5d
  rejects (for example extra fields) ends the pooled holdout at that segment's
  start. Strategy or parameter changes are never pooled. They start a new
  experiment with its own holdout start.
- **Overlapping segments and the seam:** segment 2 (Oracle Cloud VM, Sydney,
  same code 76dbb5d) started **2026-10-12T18:01:52Z** while segment 1 (the
  laptop) kept running, so some candles are recorded twice. The pooled series
  switches source once, at a fixed cutover: **segment 1 for candles closing
  before 2026-10-13T00:00Z, segment 2 from then on.** Setup state carries across
  candles (a breakout, then a retest within 6 bars), so each stretch is taken
  whole from one recorder and the two are never mixed candle by candle. After the
  cutover, segment 2's gaps are not filled from segment 1. Segment 2 is preferred
  because it is the long-running host. The rule is fixed before any outcome is
  examined and doesn't depend on what either segment decided.
  Each segment is replayed independently from its own first snapshot, blocked
  cycles included. Warm-up alone doesn't guarantee equal state, so pooling
  requires seam parity: the canonical setup state that the two replays carry into
  the first candle closing at or after the cutover must be equal. If it differs,
  the cutover moves to the first later candle at which both replays' canonical
  setup states agree (a rule on decision fields only); if there is none while
  segment 1 is still recording, the analysis halts until it's explained. For all
  overlapping candles, both segments' first gate-passing observations are
  compared on the canonical fields of §6, and every difference is reported.
  Differences in which cycles were blocked come from host timing and are listed,
  not treated as mismatches. **[OWNER]**
- **Outcome prices:** Binance USD-M TAOUSDT completed 15m candles, taken from
  the recorded snapshots (each carries 150 candles, so gaps shorter than about
  37 h are filled by later snapshots that observed those candles). Execution
  would be on Kraken TAOUSD. The venues differ by about 10 bps, and that's a
  stated limitation, not a correction.

## 3. Population and decision time

**Mapping replay rows to live decisions.** Within each segment, the export (§6)
lists `normalized_snapshots` in the order the replay reads them
(`observed_at_ms`, then rowid). A snapshot's `snapshot_index` is its position in
that list, and the replay row with that `snapshot_index` is its decision. The
snapshot's ID is recomputed from its payload (`stable_id("snapshot", payload)`)
and must equal the stored `snapshot_id`. Its live decision is the `decisions` row
whose payload carries that `snapshot_id`:

- exactly one: matched, and that row's `decision_time_ms` is the decision time;
- none (for example the process stopped between storing the snapshot and the
  decision): `NO_LIVE_DECISION`; the decision time falls back to the snapshot's
  `observed_at_ms`, and these rows are counted and listed;
- more than one: the analysis halts until it's explained.

Live failure decisions with no snapshot (`snapshot_id` null) have no replay row.
They count as blocked cycles, for coverage only.

**First gate-passing observation per completed candle**, as in the harness
(`market_observer/evaluation.py`, `DECISION_EVALUATION.md`): take the pooled
replay rows in segment and `snapshot_index` order whose `observed_at_ms` lies
between the holdout start and the cut-off. Rows whose signal is `DATA_STALE` or
`RISK_BLOCKED` are blocked cycles and are skipped. The first remaining row for
each `completed_candle_close_time_ms` is that candle's observation. Later rows
for the same candle are duplicates and are counted; a duplicate whose signal
differs from the first is also listed. Only the first row is used.

**Targeted population:** candles whose first gate-passing observation has signal
`LONG_ELIGIBLE` and `risk.reward_reference.status == AVAILABLE`. The **signal
candle** is that completed candle. Its **reference** is the risk contract's
completed close (`risk.reference_price.value`); **invalidation** and the **reward
level** are `risk.invalidation.level` and `risk.reward_reference.level`. Eligible
decisions without an available reward reference are counted and reported
separately (`TARGET_UNAVAILABLE`), scored only in secondary 2. Blocked cycles
count towards coverage only.

**Decision time** of a case is the mapped live `decision_time_ms` (or the
`observed_at_ms` fallback above). It defines entry in §4.

## 4. Analyses

Amended twice on 2026-10-13 after the independent review in issue #22 (see §1
and §8), before any prospective outcome was examined. The original primary
(mean first-touch R over resolved cases, losses scored −1R, paths starting at the
signal close) is kept only as a descriptive statistic below, because it
conditions on resolution, flatters close-rule losses and lets prices before the
decision decide the result.

### Primary: observation-aligned gross trade outcome

For each case in the targeted population, a hypothetical long trade, frozen as:

- **Entry candle:** the first 15m candle that opens at or after the decision time.
  Decisions arrive 1–3 min after the signal close, so this is normally the second
  candle after the signal candle; the first is already under way when the decision
  exists. No price before the entry open decides an exit.
- **Pre-entry candles** are those after the signal candle and before the entry
  candle (normally one).
- **Not entered (0R, stays in the population)**, checked in this order:
  1. `PRE_ENTRY_INVALIDATED`: a pre-entry candle closes below invalidation (the
     setup failed before the trade could start);
  2. `PRE_ENTRY_TARGET_TOUCHED`: a pre-entry candle's high reaches the reward level;
  3. `OPEN_AT_OR_ABOVE_TARGET`: the entry open is at or above the reward level;
  4. `OPEN_AT_OR_BELOW_INVALIDATION`: the entry open is at or below invalidation.

  Counts are reported by reason. Any other case that isn't censored is
  `ENTERED` at the entry open.
- **Exits**, checked candle by candle from the entry candle for **16 candles
  (4 h)**: the reward level is touched (high ≥ level), exit at the level;
  otherwise a candle closes below invalidation, exit at that close; if both happen
  in one candle, the target counts first (the close rule acts only at the close).
  Otherwise exit at the close of the 16th candle.
- **R** = (exit − entry) / (reference − invalidation), gross of costs. The
  denominator is the decision's original risk distance
  (`risk.per_unit_risk.distance`), fixed when the decision exists. Dividing by
  (entry − invalidation) instead would let the gap between a later open and
  invalidation set the scale, and it explodes when the entry opens just above
  invalidation. In a design check on PR #21's history (non-holdout), that
  version gave results from −31.5R to +23R and an SD of about 3.0R over all
  targeted cases; this one gives an SD of about 1.3R.
- **Censored:** a missing pre-entry candle, a missing entry candle, a missing
  candle before the exit, or a horizon ending after the cut-off. Counted, listed
  and not scored in the primary.

**Statistic:** mean gross R over every scored case, entered and not entered
(pooled: total R / scored cases, each case weight 1). Reported alongside, with
the same bootstrap: the **entered-only mean** (total R / entered trades).

**Censoring sensitivity**, reported alongside: the same statistic with censored
cases scored 0R, and with censored cases scored −1R.

**Uncertainty:** 95% percentile bootstrap, 10,000 draws, seed 20261012, with a
fresh `random.Random(20261012)` for each interval. The resampling unit is the UTC
day of the signal candle's close, and only days with at least one scored case
are units. Each draw samples as many units as there are, with replacement, and
computes total R / scored cases over the drawn units. The interval runs from the
250th to the 9,750th of the 10,000 sorted draw values. Every unit holds a scored
case, so no draw is empty; with fewer than 2 units no interval is computed.

**Block sensitivity:** the same with fixed, non-overlapping 2-day and 7-day
calendar blocks. Block *b* covers [T₀ + *b*·L, T₀ + (*b*+1)·L), with
T₀ = 2026-10-11T00:00Z and L = 2 or 7 days. A case belongs to the block containing
its signal close, blocks with no scored case are dropped, and the draw, seed and
interval rules are those above. This covers dependence across midnight and
between adjacent days.

**Reading, fixed in advance** (on the primary interval only: UTC days, censored
cases excluded; the sensitivities are reported beside it):
- fewer than 30 ENTERED trades, or fewer than 15 UTC days containing an entered
  trade: **INCONCLUSIVE_SPARSE**;
- lower bound > 0: the signals produced positive gross trade-like outcomes before
  costs; proceed to the net question (ledger). This is not a claim of selection value
  (secondary 1) or of net profitability;
- upper bound < 0: evidence against the rule as specified; redesign on independent grounds;
- otherwise: inconclusive. The analysis is not rerun with different settings.

### Secondary (all reported; none used to choose anything)

1. **Comparator, for selection value.** Does TDAL select better trades than
   comparable geometry applied to the candles it didn't select?
   - *Population:* every other candle in the window whose first gate-passing
     observation (§3, same mapping and seam) is not `LONG_ELIGIBLE`. TDAL's
     selections are excluded. (A `TARGET_UNAVAILABLE` eligible candle would get
     the same unavailable reference here anyway.)
   - *Levels, computed causally from that observation's own snapshot:*
     reference = `features.completed_close`; ATR = its own `features.atr_15m`;
     invalidation = reference − D × ATR; reward level from `assess_reward_reference`
     (`market_observer/reward_reference.py`) called on the snapshot with that
     reference and the candle's close time as `reference_close_time_ms`. The level
     depends only on the snapshot's candles, the reference and its close time. The
     inputs a non-setup candle lacks are supplied as `stop_distance` = D × ATR,
     `atr` = ATR, `breakout_close_time_ms` = the candle's own close time and
     `modelled_round_trip_bps` = None; they affect only descriptive ratios and
     `formed_during_setup`, never the level. A `TARGET_UNAVAILABLE` result
     excludes the candle, and those are counted.
   - *D = 0.6797*, fixed from declared prior data, not the holdout: the median
     `risk.per_unit_risk.distance_atr` of the 362 historical TAO targeted cases
     (`LONG_ELIGIBLE`, reward reference `AVAILABLE`, 2024-04-12 to 2026-09-10) in
     PR #21's archive replay (`research/historical_archive/replay_archive.py` at
     `611a4c8`), computed once on 2026-10-13. It is not re-estimated.
   - *Trade:* decision time, entry, not-entered rules, exits, censoring and R
     exactly as the primary, so the denominator is reference − invalidation =
     D × ATR. Each comparator case has weight 1.
   - *Contrast:* TDAL mean R − comparator mean R, over the calendar blocks that
     contain at least one scored TDAL case and at least one scored comparator
     case. Uncertainty by **joint, calendar-aligned resampling**: each draw samples
     those blocks with replacement (UTC days; 2-day and 7-day blocks as in the
     primary's sensitivity) and takes all TDAL and all comparator cases of each
     sampled block, so shared price paths and time dependence stay together.
     Draws, seed and percentiles as in the primary. No draw is empty, by
     construction. The contrast is **INCONCLUSIVE_SPARSE** if its TDAL cases fail
     the primary's sparse rule (30 entered trades, 15 days with one) or it has
     fewer than 30 entered comparator trades. The all-window comparator mean is
     reported descriptively.
   - *Limits:* TDAL's stop sits at its structural level and the comparator's at
     a fixed ATR multiple, so the two are comparable in scale, not identical in
     geometry. Only a positive lower bound here would support a claim that TDAL
     selects better trades than this geometry applied to unselected candles.
2. **TARGET_UNAVAILABLE cases:** the same trade with only the invalidation and
   horizon exits; not entered only by rules 1 and 4.
3. **Other horizons and stop rules:** 4 and 96 candles with the close rule; and a
   **gap-aware wick stop** at 4, 16 and 96 candles, with the primary's population,
   entry and not-entered rules. Candle by candle from the entry candle: a candle
   opening at or below invalidation exits at that (worse) open; otherwise a candle
   whose low touches invalidation exits at invalidation; otherwise a high reaching
   the reward level exits at the level. When target and stop fall in the same
   candle, the stop counts first.
4. **Descriptive first-touch statistic** (the original primary): mean R over
   resolved cases with losses at −1R and paths from the signal close, with all status
   counts. A mechanism description, not an edge measure.
5. **Forward-move baselines** (harness 0.1, 16 bars): TDAL vs `SELECT_ALL` and
   `POSITIVE_PRIOR_RETURN`. Descriptive.
6. **Governed paper ledger** (RiskGovernor, PR #11): one $1,000 paper account
   run chronologically over the same decisions, with parameters as fixed there
   (0.5% risk; −2% daily or 3-loss breaker; one position; no leverage;
   close-below-invalidation and 0.5 ATR emergency stop). `MAKER_STAGGERED` is
   reported first, `MARKET_BASELINE` alongside. The pinned ledger must use the
   primary's entry timing: no order rests, fills or is priced on a price from
   before the decision time of §3. Costs are **dated assumptions, read
   2026-10-13** from kraken.com/features/fee-schedule, and are used as stated
   even if the schedule changes: Kraken Pro spot **Tier 4, taker 35 bps and maker
   20 bps per side** (the owner's tier on that date), slippage 5 bps, spread
   from the recorded quote at decision. A sensitivity at Tier 1 (taker 80, maker
   40 bps, the tier for an account without the owner's volume or assets) is
   reported alongside and labelled as such. Results restate the fees as dated
   assumptions. **[OWNER]** Report ending equity, net R, maximum drawdown, entry
   fill rate, cost share of risk, breaker trips and every journal line. The
   maker fill model is optimistic (no queue, partial fills or adverse
   selection), and fills are priced on Binance candles. So a positive ledger
   alone is not a success, and it can't rescue a primary that isn't positive.
7. **Coverage:** recorder uptime, gap list, blocked-cycle share, the share of
   decisions that are `TARGET_UNAVAILABLE`, `NO_LIVE_DECISION` rows, duplicate
   and changed-signal re-polls, and seam differences.

## 5. When the analysis runs

**One analysis, no interim outcome looks.** **[OWNER]** It runs at the earlier of:

- **110 cases in the targeted population** (§3: first gate-passing observation per
  candle, `LONG_ELIGIBLE`, `reward_reference.status == AVAILABLE`); or
- **2027-10-11T00:00Z**, one year after the holdout start.

Why 110, for the current primary (design checks on PR #21's history, which is
not holdout data): 39% of targeted cases were entered (141 of 362; most of the
rest resolved before the entry candle opened), so 110 cases should give about 43
entered trades, above the 30 the sparse rule needs. The primary R had an SD of
about 1.3R (1.25R) over all targeted cases, so a 95% interval on 110 cases would
be about ±0.23R if cases were independent, and wider with day clustering. History
had about 12.5 targeted cases a month (362 in 29 months), so 110 should take
**about 9 months** (around mid-2027), inside the deadline. This replaces the
earlier rationale (about 96 decided cases under the original primary and 11–12
months).

The count uses decision fields only (signal, completed candle and
`risk.reward_reference.status`), never prices after the decision. The entered
count needs prices, so it isn't tracked before the look; if the trigger or the
deadline arrives with too few entered trades, the reading is INCONCLUSIVE_SPARSE
(§4). A script may track the targeted count while recording continues.
Operational monitoring (uptime, gates, errors) is always allowed.

## 6. Code frozen before the look

Before the analysis runs, these are committed and tested only on synthetic
fixtures, the exploratory backfill, or a copy of the database with outcome
outputs suppressed:

- **Export:** `observer.sqlite` to canonical snapshot JSONL plus a dataset
  manifest (`dataset.py` builds the manifest; no export from `observer.sqlite`
  exists today), per segment, together with the **decision-time mapping** of §3:
  one row per snapshot with segment, `snapshot_index`, stored and recomputed
  `snapshot_id`, `observed_at_ms`, the matched live `decision_id` and
  `decision_time_ms` (or `NO_LIVE_DECISION`), the time source used, completed
  candle, signal, whether it is the candle's first gate-passing observation, and
  whether the seam pools it.
- **Parity checker,** live vs replay per snapshot, and segment 1 vs segment 2 per
  overlapping candle. The canonical contract covers: completed candle close time;
  setup state and transition (the full `setup` object); signal; risk status and
  reason, invalidation and reward levels (the full `risk` object); gate rule IDs,
  statuses and evidence; the full `features` object; and `breakout_quality`, score
  and components. Excluded as legitimately different: decision IDs, schema and
  version strings, mode, action and permission metadata, live-only fields
  (`decision_time_ms`, `collection_errors`, `unknown_or_unimplemented`) and
  replay-only fields (`sequence`, `snapshot_index`). Validated separately rather
  than compared: each snapshot's as-of audit, its `observed_at_ms` against its
  event receipt times and the matching `raw_events` rows, `observed_at_ms` ≤
  `decision_time_ms` for each matched decision, and the snapshot ID
  recomputation. The seam rule of §2 is enforced here before pooling.
- **Analysis script** for §4: entry, not-entered, exit, censoring and R rules for
  the primary, the comparator and the secondaries; the bootstrap, joint contrast
  resampling and sparse rules. Synthetic tests include each not-entered reason; a
  missing pre-entry, entry and mid-path candle; target and close stop in one
  candle; a horizon exit; an entry just above invalidation (R uses the original
  distance); a **gap-through** case for the wick stop (a candle opening below
  invalidation exits at its open); blocks with no cases; fewer than 30 entered
  trades; a contrast block with only one arm; and the `NO_LIVE_DECISION` fallback.
- **Ledger** at a pinned PR #11 commit with the issue #22 findings G1–G7 fixed
  (`55b974b` fixes those cases) and PR #11's open follow-ups also fixed in that
  commit: pending-order reservation that depends on future fills or expiry, a
  censored path erasing an already-realised prefix, and missing input lineage. It
  must use the primary's entry timing (§4 secondary 6). Where the pinned commit
  falls short of any of these, the gap is listed as a deviation. The ledger
  results published in PR #11 so far come from the unrepaired model and are not
  used. This file is amended to name the pinned commit before the look.

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

## 8. Amendment record

No prospective outcome had been examined at any of these.

- `836a930` (2026-10-13): segment 2 data source and the overlap cutover rule.
- `6c355cd` (2026-10-13): amendments allowed before the look.
- `4968ff8` (2026-10-13): ledger priced at the owner's Kraken tier.
- `0ba6ade` (2026-10-13): primary redefined as an observation-aligned trade
  outcome after the issue #22 review.
- Second issue #22 amendment (2026-10-13): R against the original risk distance;
  pre-entry not-entered rules and a sparse rule on entered trades; the
  replay-to-live decision-time mapping; canonical and seam parity; the comparator
  fully specified with D fixed from history; joint calendar-aligned and
  fixed-block bootstrap; a gap-aware wick stop; ledger timing and follow-up
  requirements; a censoring sensitivity; §5 rationale restated; the history
  disclosure in §1; fees marked as dated assumptions.
