# Decision risk contract (`decision_risk/0.1`)

Every live (`market_decision/0.3`) and replay (`replay_decision/0.2`) decision
now carries a `risk` object, produced by `market_observer/decision_risk.py`
inside the shared evaluator, so live and replay values are identical for the
same inputs.

## Why this, and not a RiskGovernor

`docs/PRODUCT_DIRECTION.md` postpones an account-aware governor until proposed
trade intents are a validated need, and asks for a complete decision record with
an explicit research-quote completed-close invalidation. This contract is that
record field. It deliberately computes **no quantity, notional, equity fraction
or reward target**. `risk.maximum_quantity` remains an unconnected utility.

## Status values

| Status | When | Figures present |
|---|---|---|
| `AVAILABLE` | Active setup (`BREAKOUT_OBSERVED`, `RETEST_PENDING`, `LONG_ELIGIBLE`) on unblocked inputs | yes |
| `NOT_APPLICABLE` | No active setup (`IDLE`, `FAILED`, `EXPIRED`) | no |
| `WITHHELD_BLOCKED` | Signal is `DATA_STALE` or `RISK_BLOCKED`, including fail-closed feature errors | no |
| `UNAVAILABLE` | Setup or feature values malformed, non-positive, or reference at/below invalidation | no; `reason` given |

Blocked cycles withhold figures even when a setup is active, because they would
otherwise be derived from inputs that failed a gate.

## Fields (when `AVAILABLE`)

- `reference_price`: the research-venue completed close. It is **not an entry
  price**; no fill is implied.
- `invalidation.level`: `breakout_resistance − failure_buffer_atr × breakout_atr`,
  using the values frozen in the setup at breakout, not the current bar's.
  `rule: COMPLETED_CLOSE_BELOW` — the state machine's setup-failure meaning. The
  paper simulator exits on an intrabar low touch instead; the record states this
  so the two meanings are not conflated.
- `per_unit_risk`: distance from reference to invalidation in research quote
  units (USDT), in bps of reference and in breakout ATRs; the signal-time Kraken
  spread in bps as a round-trip cost proxy; and their bps sum. Fees, slippage,
  funding and gap-through are excluded and listed in `not_included`.
  `gap_loss_bounded` is always `false`.
- `setup_window.last_evaluable_close_time_ms`: last completed close at which the
  setup can still qualify (breakout close + `expiry_bars` intervals), or the
  current close once `LONG_ELIGIBLE` (terminal).
- `venue`: prices are Binance USD-M research prices. No USD/USDT parity is
  assumed and no Kraken stop price is derived; that translation stays manual.
- `sizing: NOT_COMPUTED_ACCOUNT_STATE_UNKNOWN`, `execution_permission: NONE`,
  `reward_reference: NOT_DEFINED_BY_STRATEGY`.

## Not established

These numbers describe the setup's own geometry. They are not evidence that the
invalidation level is a good stop, that losses are bounded at it, or that the
setup has predictive value.
