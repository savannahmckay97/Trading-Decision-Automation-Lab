# Verification — project zero v0.2

- **18/18 deterministic unit/integration tests passed.**
- Machine specifications validated: **21 rules, 49 raw/config fields, 18 derived fields.**
- Python package compiled successfully.
- Archive integrity checked after packaging.

The tests cover completed-candle causality, gap rejection, spread and venue evidence, breakout/retest progression, same-candle idempotence, restart recovery, stale-input freezing, observation-only enforcement, cost-aware sizing, and independent project/database identity.

The v0.2 additions verify historical replay through the production feature/rule functions, reverse-chronology rejection, completed-bar gap accounting, immutable output files, hard replay no-order enforcement, and transactionally consistent SQLite backup/restore.

One bounded live probe timed out against both public venues in the build environment. The program correctly emitted `DATA_STALE`, froze state and returned `NO_ORDER`. This verifies failure behavior only; live provider integration remains unverified.

No order was placed, no credentials were used and no process was left running.
