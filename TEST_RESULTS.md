# Verification — evaluation-first intervention, 9 October 2026

- **53/53 deterministic unit/integration tests passed locally on Python 3.12.14.**
- Machine specifications validated: **21 rules, 49 raw/config fields, 18 derived fields.**
- Python package compiled successfully.
- Saved canonical JSONL and SQLite snapshots reproduce in-memory replay results.

The inspected baseline at `043c50d8bc1316eb3f0e06efd704de64dcc5602c` had 39 tests. Its GitHub Actions run `36647903497` passed installation, unit tests and specification validation on both Python 3.10 and 3.12. The new intervention must pass that same matrix; local verification alone does not establish the new Python 3.10 result.

The tests cover completed-candle causality, gap rejection, spread and venue evidence, breakout/retest progression, same-candle idempotence, restart recovery, stale-input freezing, observation-only enforcement, cost-aware sizing, and independent project/database identity.

Historical tests verify shared feature/rule replay, reverse-chronology rejection, completed-bar gap accounting, immutable output files, replay no-order enforcement and SQLite backup/restore. Dataset, outcome and paper tests verify frozen identity/hash, unavailable future paths, conflicting candles and disclosed hypothetical cost assumptions.

The evaluation tests cover a common baseline universe, duplicate-poll accounting, censoring at the evaluation boundary, unavailable outcomes, future derivative/receipt rejection, manifest mismatch, blocked observations, close versus wick invalidation, causal prefix parity, state warmup, declared holdout limitations, immutable CLI output and invalid evaluation designs. Two saved-input regressions cover JSONL/SQLite decimal round trips and rejection of non-finite numbers.

All evaluation fixtures are synthetic. Passing these tests establishes behavior, not decision benefit, predictive edge or commercial demand. The evaluation boundary detects future rows; the live evaluator's derivative alignment/causality still needs hardening. Current paper fills remain hypothetical research scenarios rather than obtainable Kraken fills.

The archived v0.2 verification recorded a bounded live probe that timed out against both providers. That historical claim is not a successful integration test, and this intervention did not rerun a provider probe. Live provider integration remains unverified.

No order was placed, no credentials were used and no process was left running.
