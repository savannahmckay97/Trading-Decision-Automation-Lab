# Changelog

All versions belong only to the independent **Trading Decision Automation Lab** project.

## v0.2.0 — historical decision replay

- Added chronological replay from this project's SQLite snapshots or JSON Lines.
- Reused the production feature, gate, score and setup-state functions instead of creating a separate backtest strategy.
- Added reverse-order rejection, duplicate classification and explicit missing-bar accounting.
- Reset active setup state across replay gaps.
- Added deterministic configuration and decision identifiers.
- Made replay outputs create-only so an earlier result cannot be silently overwritten.
- Added transactionally consistent SQLite backup and guarded restore.
- Expanded deterministic verification from 13 to 18 tests.
- Added repository hygiene rules for runtime data, outputs and credentials.

## v0.1.0 — observation foundation

- Established the project boundary and controlled-adoption rules.
- Added the doctrine, 21-rule machine catalogue and data dictionary.
- Added public Binance/Kraken collection, normalization, causal features, gates and persistent breakout/retest state.
- Enforced observation-only behavior with no order route or credentials.
