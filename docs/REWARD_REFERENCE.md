# Reward reference (`risk.reward_reference`, `decision_risk/0.3`)

A descriptive opportunity-to-risk measurement for active LONG setups: the
distance from the entry reference to the nearest **confirmed** overhead swing
high, compared with the distance to invalidation. It is **not** a take-profit
order or exit rule, and it is not evidence of expected reward or of a
probability of reaching the level. Nothing in it feeds gates, score, state
transitions or eligibility (tested).

Implementation: `market_observer/reward_reference.py`, called from
`decision_risk.py` inside the shared evaluator, so live and replay are identical.

## 1. Why not the existing `features.resistance`

`resistance` is `max(high)` over the 32 completed bars before the current one.
It cannot serve causally and meaningfully as a target:

1. **At breakout it is never overhead.** A breakout needs
   `close > resistance + 0.10 × ATR`, and `resistance` is the maximum high in
   the window, so every high in the window is below the reference. The target
   would be absent on every breakout because of how the level is defined, not
   because of the market.
2. **After breakout it is the setup's own unconfirmed running high.** Once the
   breakout bar enters the window, `resistance` becomes the highest high since
   breakout, often just the breakout bar's wick. Nothing shows price was
   rejected there, and using it would make the reward a disguised measured move.
3. **It is one level, not a set.** "Nearest qualifying level" needs candidates.

Completed-candle selection and OHLCV/contiguity validation are reused through
`features.completed_candle_rows` and `features.validate_candle_window`.
Feature behaviour is unchanged.

## 2. Definition

- **Swing high.** Completed bar *i* with
  `high[i] > high[j]` for the *k* bars before it and `high[i] ≥ high[j]` for the
  *k* bars after it. Ties resolve to the earliest bar, so a flat top gives one
  pivot.
- **Causality.** Only candles with `close_time_ms < exchange_time_ms` are used.
  This is the same completed-candle rule as features, and the as-of guard
  ensures `exchange_time_ms ≤ observed_at_ms`. A pivot counts only once its
  *k*-th right-hand bar has completed, so the open candle and later rows can
  never create, confirm or move a level (tested). Confirmation lags by
  *k* × 15 min, and `confirmed_by_close_time_ms` records it.
- **Selection.** The lowest confirmed pivot high **strictly above** the entry
  reference, which is the completed research close. Ties report the most recent
  pivot and `pivot_count_at_level`.
- **Window.** The longest contiguous run of completed candles ending at the
  reference bar, capped at `reward_search_bars`. A gap shortens the search and
  sets `truncated_by_gap`. Duplicate or invalid candles fail closed. The searched
  span is always reported, so "no level" always means "no confirmed level in
  this many bars".
- **Unavailable.** If nothing qualifies: `TARGET_UNAVAILABLE` with a reason. No
  ATR multiple, R multiple, measured move or round number is substituted.
- **Blocked cycles.** `DATA_STALE`/`RISK_BLOCKED` withhold the whole risk object,
  including the reward.

## 3. Design decisions and trade-offs

These were decided before looking at any outcome data. They have not been, and
must not be, tuned against outcomes.

**Setup-internal pivots are included and tagged (`formed_during_setup`).** This
reverses the earlier proposal to exclude them. Excluding a confirmed rejection
just because it formed after breakout skips the nearest real obstacle. That
makes the reward look larger and the setup look more attractive, which is the
dangerous direction for a descriptive measure. The earlier concern was
measured-move contamination. That applies to the *unconfirmed* running high,
which confirmation already excludes; it does not apply to a confirmed rejection.

**Unconfirmed recent highs are disclosed, not used.** The last *k* bars cannot
be confirmed yet. If any of them traded above the reference, the highest is
reported as `unconfirmed_overhead_high`, and `target_beyond_unconfirmed_high` is
set when the selected target lies above it. Using them as the target would let
one bar's upper wick define the reward, so nearly every record would show a
trivially small reward. The real-data check below hit this case on its newest
bar.

**k = 2.** *k* sets both the minimum evidence of rejection and the confirmation
lag. *k* = 1 accepts almost every wiggle on 15-minute bars. Larger *k* adds lag,
leaves more recent highs unconfirmed and pushes targets further away, which
makes the reward look larger. 2 is the smallest width that needs more than one
bar of evidence on each side. It is a conventional prior, not an optimum, and no
sensitivity sweep is reported, because a sweep invites selection.

**Search window = 144 bars (36 h).** The bound is the data the collector already
fetches: `candle_limit` 150 means at most 149 completed bars. 144 is a round
1.5 days that fits with margin. It is an explicit configuration value, so
raising `candle_limit` later cannot silently change results. Consequence:
after strong trends, a price above every 36 h pivot reports `TARGET_UNAVAILABLE`
even though older resistance may exist. A longer or higher-timeframe search
(1h/4h pivots) would probably give more meaningful levels, but it needs a
collector change. Deferred.

**Gaps shorten the search instead of failing it.** A gap far back in history
does not make recent levels unknowable. Shortening the window and reporting it
is honest. Silently searching across the gap is not.

Both parameters are in `ObserverConfig.public_dict()` and so in the replay
`configuration_id`.

## 4. Output (when `AVAILABLE`)

| Field | Meaning |
|---|---|
| `level` | Selected pivot high, research quote units (USDT) |
| `source` | Pivot open/close time, `confirmed_by_close_time_ms`, `bars_before_reference`, `formed_during_setup`, `pivot_count_at_level` |
| `search_window` | First/last close time, bars, `truncated_by_gap` |
| `gross_reward` | Distance to level in USDT, bps of reference, and ATRs |
| `gross_opportunity_to_risk` | Gross reward ÷ gross stop distance (`ratio_basis: GROSS_PRICE_DISTANCES_NO_COSTS`) |
| `cost_adjusted_opportunity_to_risk` | `(reward_bps − C) / (risk_bps + C)` with `C = risk.costs.modelled_round_trip_bps`; only when spread, fees and slippage are all known; basis `PARTIAL_EXCLUDES_FUNDING_AND_GAP`. Can be ≤ 0 when costs exceed the opportunity |
| `unconfirmed_overhead_high`, `target_beyond_unconfirmed_high` | Disclosure described in §3 |

`TARGET_UNAVAILABLE` carries `reason`, plus `search_window` and
`unconfirmed_overhead_high` when the window was valid. Nothing in this contract
is called "net".

## 5. Verification

`tests/test_reward_reference.py` covers:

- **Selection:** nearest level chosen, tie and flat-top handling, setup-internal tagging.
- **Costs:** the adjusted ratio is null until spread, fees and slippage are all known.
- **Missing target:** no level, level equal to the reference, and no substitute value.
- **Invalid input:** a gap shortens the search and is disclosed; duplicate and invalid candles fail closed; the window must end at the reference bar.
- **Look-ahead:** a pivot that needs the open candle is not used; open and future candles cannot change the result; prefix parity with truncated history.
- **Integration:** blocked cycles withhold the reward; reward inputs cannot change signal, state, gates or features; invalid configuration is rejected.

Real-data plumbing check, not evaluation: on 149 completed live TAOUSDT 15m bars
captured 2026-10-10, the search found 19 confirmed pivots in 144 bars. Treating
each of the last 40 closes as a reference, as of that bar, a target was
available for 30. On the newest bar the only overhead high, 282.89, was one bar
old and unconfirmed: reported as `TARGET_UNAVAILABLE` with the unconfirmed high
disclosed. These counts describe the plumbing. They are not evidence about setup
quality.

## 6. What it does not establish, and next steps

- No claim that price reaches the level, or that a higher ratio means better
  decisions. Testing that is priority 3: use the evaluation harness, frozen
  definition, baselines and unseen periods, recording first-touch of target vs
  invalidation as an outcome label that never feeds back into decisions.
- Entry reference is the completed research close, not a causal arrival price.
- No Kraken translation of the level (USD/USDT parity is not assumed).
- Live output cannot be demonstrated until issue #7 (taker/OI freshness) is fixed.
