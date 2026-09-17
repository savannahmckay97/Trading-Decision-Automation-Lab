# Trading Decision Automation Lab — project zero, v0.2

This is a **new and independent project**. It is not a continuation, refactor, release, or renamed copy of the Market Research Lab, SOL observer, TAO observer, prediction-model project, or any other prior trading thread.

The objective is broader: convert a researched trading doctrine into explicit data contracts, deterministic rules, explainable state, and eventually a continuously running decision-support application. The first implementation compartment observes public market data and emits structured eligibility states. It cannot trade.

## Non-negotiable project boundary

- Separate project name, package, database, documentation, tests, release history, and future repository.
- No prior rule, parameter, feature, result, or architecture is inherited automatically.
- A past idea may be adopted only when it improves correctness, safety, measurement, or performance.
- Every such adoption must be recorded in `ADOPTION_LOG.md`, including why it was selected and what was deliberately not inherited.
- Similar-looking projects remain separate unless the user explicitly authorizes a merger.

See `PROJECT_BOUNDARY.md` for the full rule.

## Included compartments

1. `spec/Trading_System_Doctrine_v0.1.md` — researched doctrine and rule hierarchy.
2. `spec/rule_catalog.yaml` — 21 machine-readable foundation rules.
3. `spec/data_dictionary.yaml` — 49 raw/config fields and 18 derived fields.
4. `market_observer/` — public collectors, normalization, features, gates, state and storage.
5. `market_observer/replay.py` — chronological replay over saved normalized snapshots.
6. `tests/` — deterministic failure, progression, replay and recovery tests.

## First runnable behavior

The observer uses Binance USD-M futures as the research-context venue and Kraken spot as the executable-price confirmation venue. TAO is the initial instrument, not the permanent limit of the project.

It currently calculates:

- completed 15-minute candles only;
- causal support and resistance;
- Wilder ATR(14);
- volume percentile;
- 15-minute and 60-minute open-interest change;
- taker buy/sell ratio and buy share;
- funding and positioning annotations;
- Kraken executable spread;
- USD/USDT cross-venue divergence;
- price×OI classification;
- an explicitly uncalibrated breakout-quality score;
- a persistent breakout→retest state machine.

Every output contains `action: NO_ORDER` and `execution_permission: DENIED_OBSERVATION_ONLY`.

## Run locally

Python 3.10+; the observer has no third-party runtime dependency. Specification validation additionally uses PyYAML:

```sh
python -m unittest discover -s tests -v
python -m pip install '.[dev]'
python spec/validate_specifications.py
python observer.py --base TAO --pretty
python observer.py --base TAO --loop --poll-seconds 60
```

The default database is `data/trading_decision_lab.sqlite`. Use Ctrl-C to stop loop mode.

## Historical decision replay

Replay deliberately uses the same `calculate_features`, gate, score and state-machine functions as the live observer. It does not contain a second strategy implementation.

From an observer database:

```sh
python -m market_observer.replay \
  --db data/trading_decision_lab.sqlite \
  --base TAO \
  --output results/tao_replay.json
```

Or from a JSON Lines file containing one complete normalized snapshot per line:

```sh
python -m market_observer.replay \
  --jsonl snapshots.jsonl \
  --base TAO \
  --output results/tao_replay.json
```

Replay input must already be chronological and must preserve the data actually available at each historical instant. The runner rejects reversed chronology, records duplicate and missing completed bars, resets an active setup across a detected gap, and refuses to overwrite a prior result file.

This is **decision replay, not a profitability backtest**. It does not yet simulate fills, fees, slippage, funding, borrow, liquidation, returns or expectancy.

## Meaning of signals

| Signal | Meaning |
|---|---|
| `NO_TRADE` | No qualifying setup |
| `WATCH_LONG` | Breakout observed; retest still required |
| `LONG_ELIGIBLE` | Retest conditions passed; research eligibility only |
| `DATA_STALE` | Required data is missing, malformed, discontinuous or old |
| `RISK_BLOCKED` | Spread, venue-confirmation or mode veto failed |

Blocked or stale cycles freeze the state machine. Repeated polling of the same completed candle cannot advance it.

## Current verification boundary

The 18-test deterministic suite passes. Live Binance and Kraken requests timed out from the original build environment, so real provider integration is **not** claimed. No ongoing process was started, no credential was used, and no order route exists.

The next compartment is acquisition of a causally reconstructable historical dataset, followed by outcome labeling and a cost-aware paper-fill model. Parameter optimization and live execution remain out of scope.
