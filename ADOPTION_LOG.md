# Controlled adoption log

This log exists to distinguish deliberate engineering choices from accidental project merging.

## Accepted concepts in v0.1

| Adopted concept | Why it improves this project | Revalidation performed | Explicitly not inherited |
|---|---|---|---|
| Completed-candle gating | Prevents look-ahead and unstable intrabar decisions | Gap, completion and causality tests | Prior signals, levels and results |
| Raw provenance with event and receipt time | Makes latency, staleness and source errors inspectable | Persistence and failure-closed tests | Prior databases and observations |
| Modular collector → normalizer → feature → rule separation | Makes each calculation testable and replaceable | Independent module tests and compile check | Prior project identity and documentation |
| Observation-only execution boundary | Prevents research code from becoming an accidental broker | Live-mode rejection and endpoint inspection tests | Credentials and order code |
| Persistent state-machine storage | Prevents restart from inventing or forgetting a setup | Close/reopen database test | Any prior active setup state |
| SQLite WAL for the prototype | Improves local durability and works without extra services | Restart persistence test | Prior schemas and database files |

## Original to this project/request

- The doctrine hierarchy and current 21-rule machine catalogue.
- The current data dictionary and missing-data policy.
- The specific `BREAKOUT_RETEST_V1` parameters encoded in `spec/rule_catalog.yaml`.
- The Binance-research/Kraken-confirmation contract.
- The current implementation map and advancement gates.

## Rejected inheritance

- Existing Market Research Lab documents and 64-rule registry.
- Existing SOL or TAO standalone observer files.
- Previous backtest outcomes, model versions, thresholds and confidence claims.
- Existing Library artifact identity.

Future adoptions must add a new dated entry before code or data is imported.
