# Paper-only RiskGovernor (`governor 0.1`)

`market_observer/governor.py` turns `LONG_ELIGIBLE` decisions into either a
sized **paper** trade or a block with explicit reasons, against a simulated
account. It never submits or authorizes an order: every record carries
`execution_permission: NONE`, and the only thing it can approve is a paper
simulation (`APPROVED_FOR_PAPER`). Live order submission remains disabled.

## Why now, and why paper only

`DECISION_RISK.md` sequenced the governor after predictive validation.
`docs/EVALUATION_READINESS.md` shows that validation needs months of
prospective recordings. On 2026-10-11 the repository owner chose to build the
governor now, **restricted to paper**, so that sizing, breakers and the
protective stop are designed on first principles and tested before any
evidence arrives. This does not authorize live trading and does not change the
validation requirement for it.

## Parameters (owner-chosen 2026-10-11, before any governed run)

| Parameter | Value | Meaning |
|---|---|---|
| `starting_equity_usd` | 1000 | Paper account size |
| `risk_fraction` | 0.005 | Loss at the emergency stop, including modelled costs: 0.5% of equity |
| `max_daily_loss_fraction` | 0.02 | Block new trades once the UTC day's realised loss reaches 2% of start-of-day equity |
| `max_consecutive_losses` | 3 | Block new trades after 3 losses in a row (resets at the UTC day boundary) |
| `emergency_stop_atr` | 0.5 | Emergency stop distance below setup invalidation, in the setup's frozen breakout ATR |
| `max_open_positions` | 1 | One position at a time (TAO only, so this is also the heat cap) |
| `max_notional_fraction` | 1 | Notional never exceeds equity: no leverage |
| `kill_switch` | false | `--kill-switch` blocks every new paper trade |

None of these were fitted to outcomes. Changing them needs an independent
justification and new prospective data, not results from a governed run.

## Decision rules

`assess_trade(decision, account, config, round_trip_cost_bps=None)`:

- Non-`LONG_ELIGIBLE` signals are `NOT_A_PROPOSAL` and are not journaled.
- Otherwise **every** failed check is reported, not just the first:
  `KILL_SWITCH_ENGAGED`, `POSITION_LIMIT_REACHED`, `DAILY_LOSS_LIMIT_REACHED`,
  `CONSECUTIVE_LOSS_LIMIT_REACHED`, `RISK_CONTRACT_UNAVAILABLE`,
  `RISK_CONTRACT_MALFORMED`, `INVALID_STOP_GEOMETRY`, `COSTS_INCOMPLETE`.
- An unknown cost blocks the trade: it is never treated as zero.
- If approved: quantity = `equity × risk_fraction / (reference − emergency_stop + reference × round_trip_cost_bps / 10000)`,
  capped so notional ≤ `equity × max_notional_fraction`
  (`risk.maximum_quantity`). `notional_cap_binding` reports when the cap applied.

### Protective stop

The setup's invalidation is a **completed-close** rule, so it cannot be a resting
stop order. RESULTS.md §7 (first-touch analysis) concluded that a protective
stop needs its own definition. The governor uses two exits:

1. **Setup invalidation:** a completed 15m close below the invalidation level
   exits at that close.
2. **Emergency stop:** `invalidation − 0.5 × breakout_atr`. An intrabar low at or
   below it exits at the stop, or at the bar open if price gapped through.

Size is computed on the emergency-stop distance, so a stop-out costs about
1R. A close-rule exit usually costs less. A gap through the emergency stop costs
more: loss is not bounded (`gap_loss_bounded: false`).

There is **no take-profit**. Winners exit only at the max-hold close. Choosing a
profit exit is an open owner decision; the reward reference stays descriptive.

## Governed paper replay

```sh
python -m market_observer.governor \
  --jsonl snapshots.jsonl \
  --dataset-manifest snapshots.manifest.json \
  --replay results/tao_replay.json \
  --fee-bps-per-side "$FEE_BPS" \
  --slippage-bps-per-side "$SLIPPAGE_BPS" \
  --max-hold-bars "$MAX_HOLD_BARS" \
  --output results/tao_governed_paper.json
```

Decisions are processed in completed-close order on one account:

- **Entry:** next bar open after `LONG_ELIGIBLE`. If that open is at or below
  invalidation, the trade is not entered.
- **Exits, checked in this order within each bar:** emergency stop, close below
  invalidation, then the max-hold close.
- **Costs:** paper conventions (half the signal spread plus slippage per side,
  and the fee on effective notional each side).
- **Account updates:** a position's realised P&L applies at its exit bar. A new
  UTC day resets the daily loss and the loss streak.
- **Censoring:** a missing future path ends the simulation. Later proposals are
  `NOT_EVALUATED`, because account state after that point is unknown.

The artifact is immutable, deterministic (`governed_paper_id`) and states
`performance_claim_available: false`.

## Exploratory plumbing check on real data (2026-10-11)

`research/governed_backfill/run.py` ran the default governor over the 29-day
TAOUSDT backfill used by the first-touch analysis, under that replay's
assumptions, with an assumed Kraken spot taker fee of 40 bps per side, 5 bps
slippage and a 16-bar max hold:

- **Proposals:** 17. 15 were approved and filled; 2 were blocked by the position limit.
- **Exits:** 7 emergency stop, 4 close below invalidation, 4 max hold.
- **Account:** $1,000 → $957.64. Sum of realised R −8.6; maximum drawdown $44.14.
- **Costs:** the modelled round-trip cost (93.35 bps) was on average **50% of each
  trade's risk budget.**

This shows the governor runs end to end on real candles. It is **not** evidence
about the strategy: n = 15, reconstructed history, overlapping sample with the
first-touch study. The cost share is the structural finding. At taker fees
for this account size, costs consume about half of every risk budget, which
squeezes stop distance and size. How to address it (maker entries, a minimum
stop distance relative to costs, or a different venue tier) is an owner
decision for a later version and must not be tuned on this sample.

## Not yet included

- Live or shadow integration. The observer does not call the governor and keeps
  no paper ledger. The live path also depended on issue #7.
- Take-profit and trailing exits.
- Event blackout (no event calendar input exists) and multi-asset correlation
  (one asset only).
- Funding, depth, latency and partial fills.
