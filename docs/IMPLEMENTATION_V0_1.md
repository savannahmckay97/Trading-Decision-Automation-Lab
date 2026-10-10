# Implementation map — current through v0.2

“Implemented” means deterministic code and tests exist. It does not mean profitable, calibrated or ready for money.

| Layer | File | Present responsibility |
|---|---|---|
| Configuration | `market_observer/config.py` | Versioned symbols, thresholds and observation-only mode |
| Public collection | `market_observer/collectors.py` | Binance/Kraken public REST and provenance |
| Normalization | `market_observer/normalization.py` | Provider payloads to an explicit common snapshot |
| Features | `market_observer/features.py` | Causal candles, ATR, levels, volume, OI, taker flow and venue metrics |
| Rules/state | `market_observer/engine.py` | Veto gates, experimental score and breakout/retest transitions |
| Persistence | `market_observer/storage.py` | Raw events, snapshots, decisions, errors, heartbeat and setup state |
| Orchestration | `market_observer/service.py` | Fail-closed collection-to-decision cycle |
| Operator | `market_observer/cli.py` | One cycle or supervised polling loop |
| Risk arithmetic | `market_observer/risk.py` | Cost-aware maximum quantity; never authorization |
| Historical replay | `market_observer/replay.py` | Chronological reuse of production features, gates and setup state |
| Frozen dataset | `market_observer/dataset.py` | Identity/chronology validation and canonical content fingerprints |
| Post-hoc labels | `market_observer/outcomes.py` | Forward market movement, explicitly separated from decision inputs |
| Hypothetical fills | `market_observer/paper.py` | Research-venue, independent one-unit cost/stop scenarios |
| Decision evaluation | `market_observer/evaluation.py` | Dataset-bound descriptive selection/abstention comparisons |

## Implemented doctrine subset

| Rule group | Status |
|---|---|
| Required-input and freshness vetoes | Implemented |
| Kraken spread and cross-venue confirmation | Implemented |
| Completed candle, causal levels, Wilder ATR | Implemented |
| Volume percentile, OI change, taker flow | Implemented |
| Funding and positioning | Annotation only |
| Price×OI quadrant | Implemented |
| Breakout-quality score | Implemented but uncalibrated |
| Breakout→retest state machine | Implemented; observation only |
| Cost-aware quantity | Pure utility only |
| Event blackout, account risk, portfolio heat and daily breaker | Specification only; required before execution eligibility |
| Historical decision replay | Implemented; no performance claims |
| Gap accounting and restart-safe backup/restore | Implemented and tested |
| Outcome labels and hypothetical paper fills | Implemented research artifacts; no strategy or executable-venue validation |
| Dataset-bound benchmark evaluation | Implemented; descriptive comparisons, no significance or improvement claim |
| News sentiment and orders | Not implemented |

## Advancement gate

Next verify actual provider timing/identity and causal derivative windows, then capture real as-of snapshots. Labels and hypothetical fills already exist. Use the evaluation harness and a preregistered user pilot to test the decision mechanism before expanding it. The harness rejects detectable future rows but cannot establish provider publication semantics or synchronized windows. Account for multiple testing and dependent labels; preserve untouched chronological holdouts. See `PRODUCT_DIRECTION.md` for evidence, MVP and advancement/retirement gates.
