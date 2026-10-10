# Verification — decision risk contract, 10 October 2026

- **116/116 deterministic tests passed locally on Python 3.13.5 after rebasing onto integrated `main` (`813224c`)**; specification validation passed (21 rules, 49 raw/config fields, 18 derived fields).
- 17 new tests (including as-of rejection withholding risk) in `tests/test_decision_risk.py`: close-based invalidation from frozen breakout levels, setup window, separate spread/fee/slippage/funding components with unknown costs never treated as zero, not-applicable/withheld/unavailable fail-closed states, persisted-string parity, live/replay parity, and absence of any quantity, notional or execution permission.
- Fixtures are synthetic. The contract describes setup geometry only; it is not evidence of stop quality, bounded loss or predictive value. Python 3.10/3.12 CI must still pass.

# Verification — integrated PR review fixes, 10 October 2026

The combined branches contain 99 deterministic tests. Verification covers late-received outcome censoring, derivative continuity before live/replay features, strict persisted snapshot ranges/intervals/provenance, supported legacy manifest fingerprints, and imported source metadata through outcome/paper CLIs. Candle-only imports remain DATA_STALE/NO_ORDER; chronology is checked even for incomplete inputs.

Specification validation passes: 21 rules, 49 raw/config fields, 18 derived fields. GitHub Actions must pass on Python 3.10 and 3.12 before merging. Synthetic fixtures establish plumbing and failure behavior; real provider integration and predictive benefit remain unverified.

## Previous milestone evidence

### Historical ingestion

- **60/60 deterministic tests passed** on Python 3.12 in the implementation environment.
- Specification validation passed: **21 rules, 49 raw/config fields, 18 derived fields**.
- CLI source import → immutable JSONL/manifest → replay is tested with synthetic data.
- Full breakout/retest fixtures preserve complete results through both JSONL and SQLite; signals remain `WATCH_LONG` then `LONG_ELIGIBLE`.
- Candle-only imports remain `DATA_STALE`/`NO_ORDER`, including after 97-bar warmup.
- Tests reject wrong identity/units/interval, malformed values, gaps, duplicates, reversed order, truncated coverage, future bars and nested research results.
- Determinism, immutable outputs and competing-writer file preservation are tested.
- Independent secondary review confirmed the original failures and found no blocking issues in the implementation. Its first patch pass independently ran the then-current 58 tests; two additional immutable-write tests brought the suite to 60.
- An actual Binance public historical-kline request returned **HTTP 451**. This is a verification limit: no successful real-export or live-provider integration is claimed.

Run `python -m unittest discover -s tests -v` and `python spec/validate_specifications.py` from the repository root. CI remains configured for Python 3.10 and 3.12; local execution alone does not establish a remote CI result.

No live orders, credentials, prediction-performance claims or RiskGovernor changes are part of this milestone. See `docs/HISTORICAL_INGESTION_V0_1.md` for exact contracts and remaining work.

## Earlier verification — project zero v0.2
### Shared as-of guard

- **68/68 deterministic unit/integration tests passed locally on Python 3.12.14.**
- Machine specifications validated: **21 rules, 49 raw/config fields, 18 derived fields.**
- Python package compiled successfully.
- Saved canonical JSONL and SQLite snapshots reproduce in-memory replay results.

The inspected baseline at `043c50d8bc1316eb3f0e06efd704de64dcc5602c` had 39 tests. Its GitHub Actions run `36647903497` passed installation, unit tests and specification validation on both Python 3.10 and 3.12. The new intervention must pass that same matrix; local verification alone does not establish the new Python 3.10 result.

The tests cover completed-candle causality, gap rejection, spread and venue evidence, breakout/retest progression, same-candle idempotence, restart recovery, stale-input freezing, observation-only enforcement, cost-aware sizing, and independent project/database identity.

Historical tests verify shared feature/rule replay, reverse-chronology rejection, completed-bar gap accounting, immutable output files, replay no-order enforcement and SQLite backup/restore. Dataset, outcome and paper tests verify frozen identity/hash, unavailable future paths, conflicting candles and disclosed hypothetical cost assumptions.

The evaluation tests cover a common baseline universe, duplicate-poll accounting, censoring at the evaluation boundary, unavailable outcomes, future derivative/receipt rejection, manifest mismatch, blocked observations, close versus wick invalidation, causal prefix parity, state warmup, declared holdout limitations, immutable CLI output and invalid evaluation designs. Two saved-input regressions cover JSONL/SQLite decimal round trips and rejection of non-finite numbers.

All evaluation fixtures are synthetic. Passing these tests establishes behavior, not decision benefit, predictive edge or commercial demand. Live observation, replay and offline evaluation now share detectable as-of contradiction checks. Provider semantics, derivative alignment/continuity and live gap handling still need validation. Current paper fills remain hypothetical research scenarios rather than obtainable Kraken fills.

The archived v0.2 verification recorded a bounded live probe that timed out against both providers. That historical claim is not a successful integration test, and it is not evidence of successful integration. This guard follow-up made one bounded public time/ticker request per provider (4-second timeout, no retry); both timed out in this workspace. Live provider integration remains unverified.

No order was placed, no credentials were used and no process was left running.


The as-of regressions verify unchanged valid fixtures, future rows/events/receipts, strict timestamp types, matching optional provenance, optional unavailable annotations, normal open-candle exclusion, precomputed-feature bypass rejection and live/replay state preservation. PR #2 is the dependency (53 tests); this follow-up adds 15 tests. Remote matrix status is reported in the PR/delivery rather than inferred from local results.
