# Changelog

PR #5 review fixes: preserve supported manifest versions and source metadata through outcome/paper validation; reject future auxiliary rows, invalid quantity ranges, malformed candle intervals and mismatched provenance. Added downstream CLI and snapshot regressions.

All versions belong only to the independent **Trading Decision Automation Lab** project.

## Unreleased — historical ingestion and persisted snapshot boundary

- Add bounded offline Binance USD-M 15m source-envelope ingestion and a synthetic runnable example.
- Preserve acquisition timestamps separately from explicitly unverified historical cutoffs; never invent missing market context.
- Add recursive snapshot field validation and exact JSONL/SQLite Decimal decoding.
- Reject backwards cutoff chronology and nested outcome/decision/fill fields.
- Use exclusive immutable dataset writes; preserve other writers' files on failure.
- Dataset implementation is now 0.2.0; replay implementation is 0.3.0. Strategy logic is unchanged.
- Add 21 regression/acceptance tests (60 total). Actual Binance integration remains unverified: HTTP 451 during the implementation check.

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
