# Changelog

PR #2 review fixes: censor outcome paths first received after the declared window; reject duplicate, gapped or insufficient 5-minute derivative histories. Regression suite: 55 tests.

All versions belong only to the independent **Trading Decision Automation Lab** project.

## Unreleased — evaluation-first direction

- Enforce one normalized as-of availability contract before live/replay features and offline evaluation; preserve state on temporal failures and report `ASOF_INVALID` replay cycles.
- Add regressions for future evidence/receipts, strict timestamps, optional provenance and precomputed-feature bypass; document open-candle semantics and the local capture handoff.

- Add frozen-dataset-bound descriptive decision evaluation against simple selection baselines, with missing/censored outcomes and abstention trade-offs disclosed.
- Restore decimal fields when replaying saved JSONL/SQLite snapshots; verify persistence-to-replay parity.
- Document the constrained MVP, product assumptions, causal limitations and evaluation-driven advancement gates.

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

