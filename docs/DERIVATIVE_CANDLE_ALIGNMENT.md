# Live derivative candle alignment — issue #7

For the latest completed 15-minute Binance candle, let C be its open time and
E = C + 900000 its exclusive end. The stored close time must equal E - 1.

- Taker timestamps are bucket **opens**. Require exactly C, C + 300000 and
  C + 600000, and sum only those volumes. Later published buckets cannot move
  this window or change its buy share.
- OI timestamps are point observations. Require every 5-minute point from
  E - 3600000 through E, including the exact 15-minute start C. Compute changes
  from OI(E) versus OI(C) and OI(E - 3600000). Never use an earlier substitute.
- Required windows reject missing, duplicate, off-grid or invalid values.
  The shared as-of guard continues to reject future receipts/events, future
  series timestamps and discontinuous derivative histories.
- Derivative freshness now measures endpoint receipt age, with a 15-second
  collection budget (the same budget as market quotes), rather than age of
  bucket opens/point stamps. This is separate from exact candle coverage.
  Fresh receipts cannot rescue a missing boundary or an old completed candle.
- Require the latest expected completed candle at exchange time. Do not fall
  back to the previous candle while waiting for publication. Existing exchange
  clock, quote, spread, divergence and observation-only gates still apply.

Binance publication delays therefore produce DATA_STALE until all required
rows arrive. The first passing poll can advance state once; later polls and
restarts cannot reevaluate that candle. Publication lag is not itself a trading
rule and no arbitrary larger event-age allowance is introduced. If publication
never catches up before the next candle closes, that candle remains unevaluated;
existing continuity controls handle skipped decision candles.

The shared feature path applies this contract to live observations and snapshot
replay. Old saved snapshots with misaligned derivative windows will now block;
this is an intentional correctness change. No values, state, credentials or
thresholds were adopted from other projects. This change addresses findings 1–3
in [issue #7](https://github.com/savannahmckay97/Trading-Decision-Automation-Lab/issues/7).
The original evidence is in PR #6's `docs/LIVE_VERIFICATION_REPORT.md`; PR #6
has since merged into main; this branch integrates current main.

## Verification

154 unit tests pass, including 194/444/777-second post-close polling, missing
publication followed by arrival, restart/idempotence, later rows with opposing
flow, missing/duplicate/shifted windows, invalid values, stale receipts, and
missing latest candles. Specification validation passes.

Run the public, read-only probe from the repository root:

```sh
python3 -m scripts.verify_live_derivatives /tmp/tdal-issue7-live
```

The probe saves raw events, normalized snapshot, independent 5-minute klines and
its summary under the supplied directory, using an in-memory observer store.
It checks production observation, replay parity, missing-row rejection and taker
open-time volume semantics. See `ISSUE_7_LIVE_CHECK.json` for the recorded run.
Live checks verify plumbing and gates, not predictive value or trade performance.

On 2026-10-10 at 19:32:52 UTC, the 19:15–19:30 candle's final taker bucket
was missing: DATA_STALE. At 19:33:51 its window was present and all evaluation
gates passed. At 19:39:46, the full production ObserverService emitted NO_TRADE,
NO_ORDER and DENIED_OBSERVATION_ONLY with no collection errors. Both required-row
removal diagnostics rejected evaluation. The latest taker stamp was 586812 ms
old, and the latest OI stamp 286812 ms old; the selected OI endpoint was still
exactly 19:30 and taker opens were 19:15, 19:20 and 19:25. Later published rows
were excluded from those features.

Two provider caveats were observed and preserved, without altering evidence:

- Independent 5m kline comparison matched all three buy volumes and two total
  volumes exactly; the 19:20 total differed by 0.076 TAO. An earlier candle also
  had a 0.172 TAO buy/total difference. This check does not establish universal
  equality between Binance endpoints or validate decision performance.
- Actual saved-snapshot replay was rejected by the existing decoder at
  `market_observer/snapshots.py`: it compares Kraken's receipt-based event stamp
  against the earlier concurrently fetched Binance exchange clock. The shared
  live as-of guard uses observed time for envelope events and accepts the same
  evidence. This independent replay-envelope limitation is left unchanged in
  this issue's fix. Fixture parity passes; live saved-snapshot parity is **not**
  established. No clocks were rewritten to make replay pass.

The initial sandbox attempt could not resolve provider hosts; these reported
network checks used authorized public network access. Full raw evidence remains
in `/tmp/tdal-issue7-live` and the blocked run in `/tmp/tdal-issue7-waiting` on the
verification machine; the committed JSON contains inspectable check summaries.
