# Proposal: reward reference for LONG assessments (`decision_risk/0.2`)

Status: **PROPOSED, not implemented.** Implementation waits until Codex has
integrated PRs #2, #3 and #5, because it touches shared files (see
Dependencies). Extends the contract in `DECISION_RISK.md`.

Purpose: a descriptive opportunity-to-risk measurement using the nearest
overhead resistance known at observation time. It is not a take-profit order,
not an exit rule, and not evidence of expected profitability.

## 1. Why the existing `resistance` feature is unsuitable

`features.resistance` is `max(high)` over the 32 completed bars *before* the
current bar (`features.py`, `prior_levels`). Used as a target it is misleading
in both setup phases:

1. **At breakout (`BREAKOUT_OBSERVED`) it can never be overhead.** A breakout
   requires `close > resistance + 0.10 × ATR`, and `resistance` is the maximum
   of every high in the window, so no high in that window exceeds the
   reference. The target would be `TARGET_UNAVAILABLE` on every breakout by
   construction. That reflects the definition, not the market.
2. **After breakout it becomes the setup's own excursion.** Once the breakout
   bar enters the window, `resistance` becomes the highest high since breakout,
   usually the breakout bar's own high. Reward would then be "breakout high
   minus retest close". That is a measured-move proxy in disguise, which the
   requirements exclude. The level is also unconfirmed: nothing shows price was
   rejected there.
3. **It yields one level, not a set.** "Nearest qualifying level above" needs
   a set of candidate levels. A rolling maximum only gives the highest one.

The ATR, the completed-candle selection and the OHLCV validation are reusable.
The level definition is not.

## 2. Proposed level: nearest confirmed swing high

**Candidate set.** Completed research-venue 15m bar `i` is a *swing high* with
confirmation width `k` when

- `high[i] > high[j]` for every `j` in `i−k … i−1`, and
- `high[i] ≥ high[j]` for every `j` in `i+1 … i+k`. Ties resolve to the
  earliest bar, so a flat top yields one pivot.

**Causality.** A pivot is usable only when its confirming bar `i+k` is a
completed bar at or before the decision's completed candle. "Completed" is the
same rule features use: `close_time_ms < exchange_time_ms`. PR #3 guarantees
`exchange_time_ms ≤ observed_at_ms`. A pivot whose confirmation would need the
open candle, or any later bar, does not exist yet. Confirmation therefore lags
by `k × 15m`, and that lag is reported.

**Qualifying filter.**

- `level > reference_price`, strictly.
- The pivot bar opened **before** `breakout_close_time_ms`. Swing highs formed
  during the setup are the setup's own path, not prior overhead supply. Their
  count is reported as `excluded_post_breakout_pivots`.
- The pivot lies inside a search window that is contiguous and passes OHLCV
  validation.

**Selection.** The minimum qualifying level. If several pivots share it,
provenance comes from the most recent one and `pivot_count_at_level` records
the rest.

**Search window.** Every contiguous completed candle in the snapshot, up to
`target_search_bars`. The live collector holds at most 149 completed bars, about
37 hours. A discontinuity anywhere in the window is a fail-closed condition, not
a reason to silently search a shorter span. "No level within 37 h" is not "no
resistance exists". The record states the window searched so the absence cannot
be over-read.

**Frozen parameters (new; chosen before any outcome is examined, never tuned):**

| Parameter | Proposed | Rationale |
|---|---|---|
| `target_pivot_confirmation_bars` (`k`) | 2 | Smallest symmetric width that still requires a reaction on both sides; common fractal convention. Not fitted. |
| `target_search_bars` | 148 | Uses the full window the collector already fetches (`candle_limit` 150, minus the open bar and one bar for the left side). No new data request. |

These enter `ObserverConfig.public_dict()`, so they change `configuration_id`
and are recorded with every replay.

## 3. Output contract (added to the existing `risk` object)

```jsonc
"reward_reference": {
  "status": "AVAILABLE" | "TARGET_UNAVAILABLE" | "WITHHELD_BLOCKED",
  "reason": "NO_QUALIFYING_LEVEL_IN_WINDOW" | "SEARCH_WINDOW_DISCONTINUOUS"
          | "INVALID_CANDLE_IN_WINDOW" | "RISK_UNAVAILABLE" | null,
  "role": "DESCRIPTIVE_OPPORTUNITY_NOT_TAKE_PROFIT",
  "level": "…",                       // research quote units
  "source": {
    "method": "CONFIRMED_SWING_HIGH",
    "confirmation_bars": 2,
    "pivot_open_time_ms": 0, "pivot_close_time_ms": 0,
    "confirmed_by_close_time_ms": 0,  // close of bar i+k
    "bars_before_reference": 0,
    "pivot_count_at_level": 1,
    "excluded_post_breakout_pivots": 0
  },
  "provenance": {
    "venue": "binance_usds_m_futures", "symbol": "TAOUSDT", "quote": "USDT",
    "observed_at_ms": 0, "exchange_time_ms": 0,
    "reference_close_time_ms": 0,
    "search_window": {"first_close_time_ms": 0, "last_close_time_ms": 0, "bars": 0}
  },
  "gross_reward": {"distance": "…", "bps_of_reference": "…", "atr": "…"},
  "gross_opportunity_to_risk": "…",  // gross reward ÷ gross stop distance
  "ratio_basis": "GROSS_PRICE_DISTANCES_NO_COSTS",
  // costs: reuse the sibling risk.costs block (spread / fees / slippage /
  // funding with separate statuses); not duplicated here.
  "cost_adjusted_opportunity_to_risk": null,
  "cost_adjusted_basis": "UNAVAILABLE_FEES_OR_SLIPPAGE_NOT_SUPPLIED"
}
```

Rules:

- `WITHHELD_BLOCKED` and `RISK_UNAVAILABLE` follow the parent risk status. No
  target is reported when the stop side is not reported.
- `cost_adjusted_opportunity_to_risk` is computed only when fee **and**
  slippage are explicitly supplied (optional config fields, default `None`; no
  silent defaults). It is calculated as
  `(reward_bps − C) / (risk_bps + C)`, where `C = spread_bps + 2 × (fee + slippage)`.
  It is labelled `PARTIAL_EXCLUDES_FUNDING_AND_GAP`. Nothing in this contract
  is called "net".
- If no level qualifies, the result is `TARGET_UNAVAILABLE`. No ATR multiple,
  R multiple or measured move is substituted.
- Eligibility is untouched. The target is computed after the state machine and
  is never read by gates, score or state transitions.

## 4. Deterministic tests (to add with the implementation)

| Area | Test |
|---|---|
| Missing target | Reference at the window high → `TARGET_UNAVAILABLE` / `NO_QUALIFYING_LEVEL_IN_WINDOW`, window reported |
| Missing target | Every pivot above the reference formed after breakout → unavailable, and the exclusion count is reported |
| Stale evidence | `DATA_STALE` and `RISK_BLOCKED` with an active setup → `WITHHELD_BLOCKED`, no level |
| Stale evidence | Feature-error failure path → withheld |
| Invalid target | Pivot exactly equal to the reference is excluded (strict inequality) |
| Invalid target | Gap inside the search window → `SEARCH_WINDOW_DISCONTINUOUS`; invalid OHLCV → `INVALID_CANDLE_IN_WINDOW` |
| Invalid target | Malformed or non-positive values → fail closed |
| Look-ahead | Pivot whose `i+k` bar is the open candle is not used |
| Look-ahead | Appending future candles, or altering the open candle, leaves the result byte-identical |
| Look-ahead | Prefix parity: the target at bar *t* equals the target computed from history truncated at *t* |
| Selection | Nearest of several qualifying pivots chosen; tie provenance and count correct |
| Costs | Fee/slippage absent → cost-adjusted ratio null with reason; supplied → value and partial label |
| Invariance | Replay signal/state sequences identical before and after the change on all fixtures |
| Parity | Live and replay `reward_reference` byte-identical |

## 5. Dependencies and integration plan

Shared files touched, so this waits until Codex has finished reconciling #2, #3
and #5:

| File | Change | Interaction |
|---|---|---|
| `features.py` | Expose validated completed candles over the full search window. Today `_completed_candles` validates only the last 97 bars. | Reuse its completed-bar, contiguity and OHLCV rules; do not write a second validator |
| `evaluator.py` | Pass validated candles to the risk contract | PR #3 edits this file |
| `config.py` | Two frozen target parameters; optional fee/slippage fields | Changes `configuration_id` |
| `decision_risk.py`, new `reward_reference.py` | Contract and pure level selection | This branch only |
| `spec/data_dictionary.yaml` | New derived fields | Spec validation |
| `README.md`, `CHANGELOG.md`, `TEST_RESULTS.md`, `DECISION_RISK.md` | Documentation | Conflict with all three PRs today |

Integration sequence:

1. Codex merges #2 → #3 → #5.
2. Rebase `feat/decision-risk-contract` onto the integrated `main` and resolve
   the docs conflicts. `replay.py` currently conflicts with #5; the risk
   contract must run after #3's as-of validation.
3. Re-run the full suite and CI. Push only with explicit approval.
4. Implement this proposal as a separate commit with the tests above.

Other dependencies and caveats:

- **PR #5 historical snapshots** carry up to 150 candles and no derivatives, so
  replay over them blocks before any risk is computed. Snapshots shorter than
  the search window will report a shorter window rather than fail.
- **Entry reference** is still the completed research close. The paper entry
  vs `observed_at_ms` timing fix (`PRODUCT_DIRECTION.md` §4.3) is separate,
  but it changes what "reward from entry" would mean.
- **Venue.** Levels are Binance USD-M USDT prices. No Kraken USD translation,
  consistent with the stop side.
- **Evaluation (step 3)** may later report descriptively whether price reached
  the reference before invalidation. That is an outcome label and must never
  feed back into the evaluator.
- **Provenance of the method.** Swing-high pivots are proposed here from first
  principles. If an earlier project used the same method or parameters, record
  that in `ADOPTION_LOG.md` before implementation.

## 6. Decisions for the owner

1. **Exclude setup-internal pivots** (recommended, as above), or include them
   with a tag?
2. Accept `k = 2` and the roughly 37 h search window, which needs no new data?
   A longer horizon needs a larger kline request, which is a collector change.
