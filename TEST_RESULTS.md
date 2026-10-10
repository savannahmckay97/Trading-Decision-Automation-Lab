# Verification — historical ingestion (10 October 2026)

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

- **18/18 deterministic unit/integration tests passed.**
- Machine specifications validated: **21 rules, 49 raw/config fields, 18 derived fields.**
- Python package compiled successfully.
- Archive integrity checked after packaging.

The tests cover completed-candle causality, gap rejection, spread and venue evidence, breakout/retest progression, same-candle idempotence, restart recovery, stale-input freezing, observation-only enforcement, cost-aware sizing, and independent project/database identity.

The v0.2 additions verify historical replay through the production feature/rule functions, reverse-chronology rejection, completed-bar gap accounting, immutable output files, hard replay no-order enforcement, and transactionally consistent SQLite backup/restore.

One bounded live probe timed out against both public venues in the build environment. The program correctly emitted `DATA_STALE`, froze state and returned `NO_ORDER`. This verifies failure behavior only; live provider integration remains unverified.

No order was placed, no credentials were used and no process was left running.
