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

## Profit exit and maker-first execution (owner decision 2026-10-11)

`execution_model=MAKER_STAGGERED` (the default; `MARKET_BASELINE` keeps the
original next-open market entry with no target, for comparison). The shape was
chosen by the owner: entries and profit exits as maker limit orders, stops as
taker. The numbers below are my a priori defaults, not fitted to outcomes, and
are the owner's to change before any prospective evidence exists.

- **Entry:** a post-only buy limit at `min(signal close, next bar open)`,
  resting up to `entry_ttl_bars` (2). It fills only if price trades *strictly
  below* the limit; touching is not a fill. A fill is processed before anything
  known only at the bar's close: a bar that trades through the limit fills, and a
  close below invalidation afterwards exits the position. Only a bar that doesn't
  fill and closes below invalidation cancels the order; so does a lapsed TTL. Then
  the account is untouched (`NOT_FILLED_*`). The committed quantity is rechecked at
  the limit against cash (no leverage, fee included) and the risk budget. Fill rate is therefore an output, and unfilled winners are
  a real cost of maker entries.
- **Take-profits:** staggered sell limits (maker) at +1R (30%) and +2R (30%),
  where R = committed limit price − emergency stop (reported per trade as
  `price_r_unit` and `take_profit_levels`). They fill when price trades strictly
  above the level, at the level, and are not credited on the entry bar.
- **Trailing stop:** the remainder (40%) trails. The stop level is
  `max(emergency stop, highest completed high before this bar − 2 × breakout ATR)`;
  it only ratchets up. On the entry bar only the emergency stop applies; the
  trail uses highs of bars completed after entry, never the entry bar's own high.
  Taker, fills at the level or the bar open if price gapped through.
- **Other exits (taker):** close below invalidation, max-hold close.
- **Within a bar a stop beats a target** (intrabar order is unknown).
- **Costs:** maker legs pay only the maker fee (no spread, no slippage); taker
  legs pay the taker fee, half the signal spread and slippage. Sizing assumes
  the worst case, a stop-out: maker entry + taker exit + half spread + slippage.
  `maker_fee_bps` has no default: unknown is never zero.

Maker savings here are an upper bound: the tape has no queue position, partial
fills or adverse selection (limits fill precisely when price is moving through
them). Only prospective recording against real order-book data can price that.

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

- **Proposals:** `LONG_ELIGIBLE` decisions only, first observation per completed
  candle (repeated polls are counted as `duplicate_polls`, not traded). Other
  records, including `DATA_STALE` failures without a candle anchor, are counted;
  observation order is checked by `observed_at_ms` and `snapshot_index`.
- **Entry:** next bar open after `LONG_ELIGIBLE`. If that open is at or below
  invalidation, the trade is not entered. The quantity committed at the fill is
  at most the reference-sized quantity, reduced if needed so notional plus entry
  fee fits the cash cap and the loss to the emergency stop plus costs, measured
  from the actual entry, fits the risk budget (`resize_reasons`).
- **Exits, checked in this order within each bar:** emergency stop, close below
  invalidation, then the max-hold close.
- **Costs:** paper conventions (half the signal spread plus slippage per side,
  and the fee on effective notional each side).
- **Account updates:** each leg (entry fee, each take-profit, the final exit) is
  booked at its own bar, on its own UTC day. A new UTC day resets the daily loss
  and the loss streak; the streak counts whole positions. Drawdown
  (`max_realized_drawdown_usd`) is on realised equity after each leg, not marked
  to market.
- **Censoring:** bars are consumed one at a time until the order or position
  ends. A missing bar while an order or position is open ends the simulation, and
  later proposals are `NOT_EVALUATED`; bars missing after the exit don't matter.
- **Malformed input blocks explicitly:** non-finite or non-positive ATR or prices,
  stop geometry other than `0 < emergency stop ≤ invalidation < reference`,
  negative or non-finite costs, a missing spread, and invalid account state.

The artifact is immutable, deterministic (`governed_paper_id`) and states
`performance_claim_available: false`.

## Exploratory plumbing check on real data (2026-10-11)

`research/governed_backfill/run.py` ran the default governor over the 29-day
TAOUSDT backfill used by the first-touch analysis, under that replay's
assumptions, with an assumed Kraken spot taker fee of 40 bps per side, 5 bps
slippage and a 16-bar max hold:

- **Proposals:** 17. 15 were approved and filled; 2 were blocked by the position limit.
- **Exits:** 7 emergency stop, 4 close below invalidation, 4 max hold.
- **Account:** $1,000 → $957.64. Sum of realised R −8.6; maximum drawdown $44.14
  (first run, before the issue #22 fixes; see the rerun below).
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
- Event blackout (no event calendar input exists) and multi-asset correlation
  (one asset only).
- Funding, depth, latency and partial fills.

## Rebased plumbing check with maker-first execution (same 29-day sample)

Same backfill, same assumptions, plus an assumed Kraken spot **maker fee of 25 bps**
(taker 40). Run once with the defaults above; not tuned.

| | MARKET_BASELINE | MAKER_STAGGERED |
|---|---|---|
Rerun 2026-10-13 after fixing the issue #22 findings G1–G7 and per-leg
settlement, with the same assumptions. The first run's numbers came from the
faulty model and are superseded: G1 had cancelled three maker entries that had
in fact filled (two of them losers), and G2 had trailed stops from the entry
bar's own high.

| | MARKET_BASELINE | MAKER_STAGGERED |
|---|---|---|
| Approved / filled | 15 / 15 | 15 / 15 (first run: 12) |
| Exits | 7 emergency, 4 invalidation, 4 hold | 5 emergency, 6 trailing, 3 invalidation, 1 hold |
| Sum realised R | −8.33 (first run −8.63) | −7.75 (first run −6.21) |
| Ending equity | $959.09 (first run $957.64) | $961.90 (first run $969.37) |
| Max realised drawdown | $43.69 | $41.29 |
| Mean cost share of risk budget | 50% | 43% |

Maker-first helps only marginally (about $3 here, not the $12 first reported)
and does not fix the cost problem at the lowest fee tier: 25 bps maker is still
50 bps round trip before the exit. The structural lever is
a lower fee tier, a venue with near-zero or rebated maker fees, or stops wide
enough that costs are a small share of risk. n = 15 on reconstructed history:
this is plumbing, not evidence about the strategy.
