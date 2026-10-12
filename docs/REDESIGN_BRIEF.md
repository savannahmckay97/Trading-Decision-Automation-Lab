# Redesign brief: from one rule to a layered, hypothesis-driven observer

For Codex (and any agent) picking up the decision-logic redesign. Written 2026-10-13
after the historical replay (PR #21), revised after the independent review on PR #23
(evidence matrix, sources and replacement wording in issue #22). Read `AGENTS.md` and
`CLAUDE.md` first; every rule there still applies. This is a design brief, not a
validated strategy: nothing here establishes that any layer or threshold has edge.

## 1. Why a redesign

PR #21 replayed the current rule, BREAKOUT_RETEST_V1, over Binance's public archive for
TAO (543 `LONG_ELIGIBLE` decisions, 2024-04-12..2026-09-10, preregistered; independently
reproduced). **This retrospective reconstruction does not demonstrate usable edge for v1
under the examined conventions:**

- Exiting at the close that breaks invalidation: −0.017R mean over 358 resolved cases
  (those stops average −1.97R, not −1R). Wick-stop rule: −0.01R. No uncertainty was
  reported for these secondaries.
- The signals' 16-bar forward close moves were no better than all candles'
  (descriptive; no uncertainty reported).
- Median stop distance was 0.65% of price; an assumed Kraken Tier 4 round trip (maker
  entry, taker exit, slippage, spread) is about 0.68%, so under that entry and venue
  model costs alone are about 1R. Half the reached targets were under 0.5R away.
- Assumptions remain: Binance USDT candles stand in for Kraken USD; spread, divergence
  and freshness gates were assumed passed; entry timing is approximate.

That result is about v1 only. It is not evidence that every TAO pattern, every data
family, or the unimplemented layered design fails. v1 stays as an auditable comparator.

What v1 actually is (`market_observer/features.py`, `engine.py`, default config):

- **One long-only pattern decides.** "Resistance" is the maximum high of the previous
  32 completed 15m candles, an eight-hour rolling range extreme ("support", the minimum
  low, is not a trigger). A close above resistance + 0.10 ATR forms a breakout and
  freezes that level. Within 6 bars, a bar whose low comes within 0.20 ATR of it, that
  closes at or above it, with taker buy share ≥ 0.50, makes it `LONG_ELIGIBLE`. This is
  a causal, reproducible range-breakout baseline; nothing in the repo shows the level is
  defended supply or a reaction zone, or that 32 bars is a privileged lookback.
- **An uncalibrated score gates formation only.** Volume percentile, 15m OI change,
  taker buy share, spread and staleness add up to a clipped −5..+5 score that must be
  ≥ 1 when the breakout forms. The retest does not re-check it, so a retest can become
  eligible while the current bar scores −2.
- **Much collected data has no decision authority.** Funding rate and percentile, the
  three long/short shares, 60m OI change and the price/OI quadrant are recorded but
  never read by `engine.py`. The confirmed swing high in `reward_reference.py` is
  descriptive and does not gate eligibility.
- **No context**: no higher timeframe, no other asset (not even BTC), no regime, no
  calendar, no short side.

## 2. What the owner wants (the target behaviour)

The observer should take a proper observation of everything available, then reason in
layers, the way a careful discretionary trader would, but recorded and testable:

- Notice patterns, repeated movements, synchronicity between assets and timeframes, and
  behavioural cues (positioning extremes, funding stress, flow imbalances, volume shifts).
- Use technical-analysis principles (structure, trend, levels, momentum, volatility) as
  inputs, not as the whole decision.
- **Several independent layers either validate or invalidate a decision.** A setup in one
  layer is a candidate; other layers confirm, veto, or size it down. WAIT is a normal answer.
- Form **hypotheses**, make educated guesses, and keep score. A hypothesis that turns out
  wrong is not deleted on the spot: it is revised into a new version and kept being tested
  on candles that come *after* the revision.
- Cover more than TAO. Add **BTC** first (strongly cyclical, and it drives the rest of the
  market), then others. Market-wide context matters: BTC's cycle and halving phase,
  alt-season vs BTC-led phases, seasonal tendencies.

The owner may add an earlier conversation that sets out the layers in more detail.
**Section 7 is reserved for it**; where it conflicts with this brief, it wins.

## 3. Non-negotiable requirements

1. **Causality.** Every input is as-of decision time, counting receipt or publication,
   not only the event timestamp. Higher-timeframe candles count only once closed.
   Cross-asset inputs are aligned on the same as-of clock. Normalizers, percentiles,
   regime boundaries and cost estimates use only data available before the decision.
2. **Hypothesis ledger with versioning, no look-ahead.** Each hypothesis is an explicit,
   machine-evaluable rule with an ID, version, parent version, declared asset scope and
   direction, required and optional evidence, and the prediction it makes (direction,
   horizon, invalidation, target reference). A revision creates a new version; old
   versions keep their record and are never rescored or redefined with hindsight. Every
   variant ever tried is listed, including discarded ones. Records are append-only and
   keep **two clocks apart**: real time (`created_at_utc`, author and reason,
   code/config hash, which results the author had already seen) and simulated time
   (experiment and fold ID, training cutoff, `simulation_effective_from`, test
   interval). A hypothesis written in October 2026 did not exist in April 2024; never
   backdate it.
3. **Walk-forward evaluation is the core method** (owner's choice). Step through the
   archive month by month. At each step, only hypothesis versions effective before that
   month may predict it; revisions made after seeing a month apply from the next month
   on. A hypothesis's score is the concatenation of its forward predictions only. Fit
   each fold on data and outcome labels that had matured before its test month; purge
   training events whose horizons overlap it. Each month is held out from its own
   fitting step, but that alone does not make it independent of the researcher: a
   frozen automated procedure may revise between folds, while a manual revision made
   after reading later months is a new experiment, recorded with that exposure.
4. **Guard against false discoveries.** A system that watches many patterns will find
   some that "worked" by chance. The trial ledger counts every asset, version,
   direction, horizon and discarded variant. Require a minimum forward sample before
   promoting, report uncertainty by dependent episodes or time blocks (not raw rows),
   and (recommended) correct for the number tried with a method that fits the metric:
   deflated Sharpe for Sharpe-type selection, a false-discovery-rate adjustment for
   per-hypothesis tests. A minimum count alone is not discovery control.
5. **Cross-asset evidence counts one way only** (owner's rule). A hypothesis that also
   holds on assets it wasn't designed on gains credibility. One that fails elsewhere is
   **not** invalidated for its own asset; its claim is scoped to that asset ("works on
   TAO", not "works in crypto"). An asset-specific hypothesis gets no support from other
   assets, so it must earn promotion on its own asset's walk-forward record alone. For
   credibility from other assets to count, the asset panel is declared before the run
   and every result on it, failures included, is reported; citing only the assets that
   passed is selection. Crypto assets share common shocks, so they are not independent
   replications.
6. **Live recording is the final judge.** A promoted hypothesis is frozen, preregistered
   and confirmed prospectively, as in `research/prospective_v1/`. Revisions after that
   are new versions.
7. **Costs first.** State R, the instrument, the entry model and the round-trip cost
   assumptions (fees, spread, slippage; funding or borrow where they apply, otherwise
   marked missing). A target's distance is an opportunity reference, not expected reward
   or a probability of reaching it. A cost-to-risk admission cap is a policy candidate
   to evaluate, not an established threshold (proposal to test: costs ≤ 0.25R at the
   owner's tier); widening stops only to shrink that ratio is not an improvement unless
   outcomes show it. Evaluate with realistic exits (close-exit R, wick stops), never a
   −1R floor; resolve ambiguous intrabar events conservatively or mark them unresolved.
   Unknown or failing economics blocks a paper intent but keeps the descriptive
   hypothesis record.
8. **Long and short**, plus WAIT. Long and short hypotheses are specified and tested
   separately; a failed long does not create a short. Research direction, paper
   feasibility and execution permission are separate statuses; a short research
   conclusion does not imply shorting is available on Kraken spot. Observation only: no
   order route, no credentials.
9. **Fail closed, scoped by requirement.** Data every hypothesis needs (clock, price
   candles, identity, units) unusable → WAIT / DATA_BLOCKED for all. Data one hypothesis
   requires unusable → that hypothesis is blocked. An optional annotation missing →
   UNKNOWN with the reason; never imputed, never silently zero, never counted as
   opposition (missing funding does not veto a price-only hypothesis). Requiredness is
   declared with the hypothesis and cannot change after outcomes are seen.

## 4. Layers, evidence authority and levels

### 4.1 Candidate layers

Each layer is a pure function of an as-of snapshot. It emits observations with
provenance (rule/version, instrument and venue, window, latest available time, input
IDs, quality status) and, per hypothesis, an interpretation: supports, opposes or
unknown. The same observation can support one hypothesis and oppose another; it is not a
global bullish or bearish vote. The authority column is the starting default; a layer
gains more only through a hypothesis whose incremental value has been tested (§6).

| Layer | Inputs (already collected unless marked) | Default authority |
|---|---|---|
| Structure and trend | 15m/1h/4h/1d candles: swing structure, levels, trend, momentum (1h+ is new, aggregated from contiguous closed 15m bars) | Support/opposition; thesis invalidation |
| Volatility regime | ATR percentile, range expansion or compression | Sizing and exit geometry |
| Derivatives positioning | funding rate and percentile, OI change, global/top long-short shares | Annotation |
| Flow | taker buy share, volume percentile | Support/opposition only under a declared test |
| Cross-asset | BTC (and later ETH) trend, lead-lag, synchrony across assets (new) | Annotation; contextual opposition under a declared hypothesis |
| Market cycle and calendar | halving phase, BTC dominance or alt/BTC strength (new source needed), month and weekday | Annotation |
| Execution quality | Kraken spread, cross-venue divergence, data freshness (existing gates) | Hard veto |

**What the inputs measure.** Frame conditional mechanisms and competing explanations,
not a point per indicator. OI is matched outstanding contracts, not a count of longs:
rising OI does not say which side is informed, and falling OI can be either side
closing. Taker share is aggressor direction, not opening or closing intent, and omits
limit orders and cancellations. Account ratios count accounts, not capital. ATR scales
historical range and has no direction. High funding is not by itself a reversal signal.
Price breakout, momentum and MA slope share price inputs; funding and positioning
overlap; account cohorts overlap. Record dependency groups: correlated measurements
with different names are not independent confirmations.

### 4.2 Authority and conflict resolution

Every piece of evidence has one authority: **annotation** (describes state, no effect on
eligibility), **support/opposition** (tests a named premise of one hypothesis; no
unbounded additive voting), **hard veto** (blocks a hypothesis or capability whatever
else says), **thesis invalidation** (the hypothesis's own predeclared failure event),
**sizing input**, or **exit input**. Statuses are kept separate:
`direction = LONG | SHORT | WAIT`, `research_status = CANDIDATE | ELIGIBLE | BLOCKED |
EXPIRED | INVALIDATED`, `paper_status = AVAILABLE | UNAVAILABLE | BLOCKED`,
`execution_permission = NONE`.

Deterministic order, applied per decision:

1. Validate identity, units, clocks and availability; apply §3.9 missingness.
2. Apply invalidation and expiry to open hypotheses. Across a data gap, reset or suspend
   per recorded policy; never infer what happened in the gap.
3. Apply hard vetoes. No score offsets a veto or a failed premise. An invalidated
   hypothesis resumes only with a new formation event and setup ID.
4. Evaluate long and short hypotheses independently. A mandatory confirmation must be
   true, not unknown.
5. Both directions eligible on conflicting premises → WAIT / CONFLICT, unless a frozen,
   tested arbitration rule exists. Neither eligible → WAIT with the failing or absent
   premises.
6. Only then compute paper geometry and hypothetical size.

Every supporting, opposing, unknown, veto and invalidation reason stays in the record,
even when one status is displayed. Output is readable: long case, short case,
objections, vetoes, invalidation/expiry, economics, unavailable evidence.

### 4.3 Levels: provenance before sophistication

- Keep v1's level as the baseline under an honest method name, `PRIOR_RANGE_EXTREME`
  (previous 32 completed 15m bars, max high / min low).
- Any alternative level or zone records: method and version, source bar IDs, timeframe,
  formation time and confirmation/availability time, zone bounds, separate reaction
  episodes (not every candle sitting in the zone), lifetime and invalidation. Candidates
  such as confirmed-pivot or rejection zones (prior swing points confirmed only after
  their right-side bars close, clustered by a versioned rule) are plausible methods to
  test, not proven upgrades. `reward_reference.py` shows the causal pivot pattern.
- The breakout candle cannot create the level it breaks. The level, zone and
  invalidation freeze when the setup forms; later evidence updates state, not the level.
- Price structure constructs a level; flow, volume and context may test the response at
  it. Not every feed has to go into the level's price.
- Lookback, width, confirmation bars and tolerance are versioned candidate parameters,
  chosen on training folds only. Never pick whichever level would have worked best.
- Volume-at-price needs actual trade prices and sizes; assigning a candle's volume to
  its close invents a profile. Aggregate taker share cannot confirm absorption or
  passive defence: describe it (`HIGH_BUY_SHARE_WEAK_PRICE_RESPONSE`), don't diagnose it.

## 5. Honest cautions

- **Cycles are few.** BTC has had about four halving cycles, and published halving
  studies disagree. Enough to use as context, far too few to prove a cycle "works like
  clockwork". Cycle and calendar stay annotations until incremental evidence says
  otherwise.
- **Recurrence can be hindsight.** TAO was chosen for behaving in a recurring way; the
  current rule tried to exploit that and the archive didn't support it.
- **What has been seen.** The whole TAO archive 2024-04..2026-09 has been examined
  through v1. That tests one rule under its assumptions; it says nothing about which
  other patterns work. Use the history freely for exploratory design, falsification,
  robustness and retrospective walk-forward, and record which periods and results
  informed each design. Don't delete the v1 results: they are the audit trail.
- **Locked final test period, with its exposure disclosed.** Walk forward through TAO up
  to 2025-09-30 while designing. Keep 2025-10-01..2026-09-30 locked: no new idea is
  scored on it during design. Finished, written-down hypotheses get one walk-forward run
  through it, then prospective confirmation on the recorder. **Disclosure:** that period
  is not unseen. BREAKOUT_RETEST_V1's outcomes on it were computed and published in
  PR #21. That is acceptable for new rules designed without fitting to it, but results
  on it are reported as retrospective, not independent confirmation; only the
  recorder's forward data is.

## 6. Suggested sequence

1. Review the repo and add explanatory comments (owner request); no behaviour change.
   Repair the open data and paper-evaluation defects (issue #22) before adding layers.
2. Evidence schema with explicit authority and requiredness alongside the current
   features and gates; v1 kept as the comparator, its level named `PRIOR_RANGE_EXTREME`.
3. The hypothesis ledger (two clocks, append-only) and a walk-forward driver with
   matured-label cutoffs, with the test period in §5 locked before any hypothesis is
   scored on it. Both live in the existing local pure evaluator: no microservices, no
   LLM choosing authority at runtime.
4. Multi-asset configuration; fetch the BTCUSDT archive with
   `research/historical_archive/fetch_archive.py` (PR #21) and check fidelity the same
   way. Persist closed higher-timeframe history only where a declared hypothesis needs it.
5. Incremental, matched tests, one change at a time, on the same entry/exit/cost model
   and population, reporting common-coverage results and deployment missingness and
   recording every variant:
   - rolling range extreme vs causal zone candidates (and vs matched arbitrary levels);
   - plain breakout vs breakout then retest;
   - structure-only vs added participation (volume, flow, OI), and renewed confirmation
     at the retest;
   - higher timeframe, BTC context, funding and positioning cohorts, each added separately;
   - with vs without the cost-admission cap (net outcome, coverage, opportunity lost).

   Don't assume that wiring in the unused inputs rescues v1 until these tests show
   incremental value.
6. First hypotheses, written down before scoring. Then preregister the survivors and run
   them prospectively on the recorder.

## 7. Owner's layer design (to be added)

_Reserved for the owner's earlier discussion of decision layers._
