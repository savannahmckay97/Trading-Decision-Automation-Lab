# Changelog

## Unreleased — issue #18, deferred review findings from the issue #12 PRs

- Retry truncated HTTP bodies and bad status lines as transport failures.
- Start the host cooldown for rate limits declared in HTTP 200 bodies (Kraken, Binance -1003).
- Require `binance_time`'s event time to equal `exchange_time_ms`, in both the live guard and the saved-snapshot decoder.
- Measure event-clocked freshness from the earlier of event and receipt; skewed genuine data is still accepted.
- Five regression tests (187 total).

## Unreleased — issue #12, skipped-candle state reset

- Reset stale hypotheses after unevaluated completed candles in the shared evaluator.
- Report continuity/reset evidence and preserve state through blocked cycles.
- Remove replay-only resets; add gap, restart and live/replay parity regressions.


## Unreleased — issue #7

- Align taker buckets and OI changes to exact completed 15-minute candle boundaries.
- Wait fail closed for delayed publication; keep fresh receipt and latest-candle requirements.
- Add realistic publication-delay tests and a public live verification probe with recorded evidence.

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
PR #2 review fixes: censor outcome paths first received after the declared window; reject duplicate, gapped or insufficient 5-minute derivative histories. Regression suite: 55 tests.

## Unreleased — evaluation-first direction

- Enforce one normalized as-of availability contract before live/replay features and offline evaluation; preserve state on temporal failures and report `ASOF_INVALID` replay cycles.
- Add regressions for future evidence/receipts, strict timestamps, optional provenance and precomputed-feature bypass; document open-candle semantics and the local capture handoff.
- Add a `risk` contract (`decision_risk/0.1`) to every live and replay decision: research-venue completed-close invalidation, per-unit distance and spread proxy, setup window, and explicit exclusions. No sizing or execution permission. See `docs/DECISION_RISK.md`.
- Add optional `fee_bps_per_side` / `slippage_bps_per_side` cost assumptions (observer and replay CLI flags). The risk contract (`decision_risk/0.2`) reports spread, fees, slippage and funding separately; unset costs are `NOT_SUPPLIED`, never zero.
- Document a reward-reference coverage study (`docs/REWARD_REFERENCE.md` §7, `research/reward_coverage/`): lengthening the 15m search fills coverage with trivial levels; 4h pivots are structural but overstate reward. Recommends a secondary structural reference after issue #7 lands; nothing implemented.
- Evaluation 0.2: label each TDAL selection by first touch of reward reference vs invalidation (wick and close rules separately; same-bar wick conflicts stay ambiguous) and report the target-first fraction, the breakeven rate at the mean ratio, and realised gross R. See `docs/DECISION_EVALUATION.md`.
- Add `docs/EVALUATION_READINESS.md` and a setup-frequency diagnostic (`research/setup_frequency/`): ~17 `LONG_ELIGIBLE` per 30 days on real TAOUSDT history, half with a reward reference. A preregistered exploratory first-touch analysis on the same 29 days (`research/first_touch_exploratory/`) is reported in full: n = 7 decided cases, within noise; no parameters changed.
- Add a descriptive reward reference (`decision_risk/0.3`): nearest confirmed overhead swing high (k=2, 144-bar causal search) with gross and, when costs are complete, partial cost-adjusted opportunity-to-risk; `TARGET_UNAVAILABLE` with no substitute target. Eligibility unchanged. See `docs/REWARD_REFERENCE.md`.
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
