# Decision risk contract (`decision_risk/0.2`)

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
  units (USDT), in bps of reference and in breakout ATRs, plus
  `distance_plus_modelled_costs_bps` when every modelled cost is known.
  `gap_loss_bounded` is always `false`.
- `costs`: each component kept separate, with its own status:
  - `spread`: signal-time Kraken quote, round-trip bps (`OBSERVED_SIGNAL_TIME_PROXY`);
  - `fees`, `slippage`: per-side bps from `ObserverConfig.fee_bps_per_side` /
    `slippage_bps_per_side` (observer CLI `--fee-bps-per-side`,
    `--slippage-bps-per-side`). Unset means `NOT_SUPPLIED`, **never zero**; an
    explicit `0` is a `CONFIGURED_ASSUMPTION`;
  - `funding`: always `NOT_MODELLED`.

  `modelled_round_trip_bps = spread + 2 × fee + 2 × slippage`, matching the
  paper simulator (half spread plus per-side fee/slippage on each leg), and only
  when all three are known; otherwise it is `null` and `modelled_basis` names
  what is missing (e.g. `INCOMPLETE_MISSING_FEES_SLIPPAGE`). It is additive bps,
  excludes funding and gap-through, and is never labelled net. The cost
  assumptions are part of `ObserverConfig.public_dict()` and so of the
  replay `configuration_id`. Replay CLI flags wait for the PR #2/#3/#5
  integration because `replay.py` is under reconciliation.
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

## Agreed sequencing (10 October 2026)

Per-trade risk is useful now; account-exposure governance waits for validated
trade proposals.

1. **Complete the open PR integration** (#2 evaluation-first, then #3 as-of
   guard, then #5 historical ingestion). This branch builds on #2 and must be
   reconciled with #3/#5 after they merge.
2. **Complete the per-trade assessment.** Each assessment must explicitly
   report:
   - entry reference — present, but only as the completed research close; a
     causal arrival price needs the observation-time fix noted in
     `PRODUCT_DIRECTION.md` (paper entry vs `observed_at_ms`);
   - invalidation and stop distance — present (`invalidation`, `per_unit_risk`);
   - estimated costs — spread observed; fee and slippage configurable and
     reported `NOT_SUPPLIED` when unset (never zero); funding not modelled;
   - available reward — absent (`NOT_DEFINED_BY_STRATEGY`). A target definition
     must be chosen and frozen *before* examining outcomes, then reward and
     reward/risk follow mechanically.
3. **Establish predictive usefulness** of those assessments with the PR #2
   evaluation harness on real, provider-verified as-of data against baselines
   and unseen periods.
4. **Build the full RiskGovernor** (account equity, heat, breakers, sizing via
   `risk.maximum_quantity`) only once step 3 shows trade proposals reliable
   enough to govern. Live order submission remains out of scope throughout.
