# Saved live snapshot replay — issue #12 item 5

Public endpoint requests run concurrently. Binance's time response establishes
`exchange_time_ms`; Kraken has no provider event timestamp, so its envelope
`event_time_ms` is the local receipt time. That receipt commonly occurs after the
Binance clock sample. Tickers can also update while other requests are pending.

Previously, snapshot decoding rejected these valid envelope events by comparing
them with the earlier exchange clock. The live shared as-of guard accepted the
same data, so recorded observations could not enter JSONL/SQLite replay.

The decoder now matches the live guard's two timestamp boundaries:

- Envelope event timestamps and receipts must not exceed `observed_at_ms`.
  Equality is allowed. Missing provider timestamps are not fabricated.
- Derivative, funding and positioning row timestamps must not exceed
  `exchange_time_ms`, even when a later receipt would make them look available.
- Kline envelope events remain scheduled close boundaries, including the open
  candle. Completed-candle selection still uses the exchange cutoff. Future
  receipt times remain invalid.

Strict timestamp types, endpoint/venue/symbol identity, unknown-field rejection,
chronology, series continuity, candle alignment, and freshness gates are retained.
No timestamp or payload is rewritten. This change does not solve collection-end
freshness (issue #12 item 2) or certify provider authenticity.

## Verification

159 tests and specification validation pass on this independent main-based branch.
Five new tests cover actual collector timestamp semantics with deterministic
public responses, collector → service → persisted JSONL/SQLite → replay parity,
future envelopes/receipts, future series rows, and both loaders' rejection paths.
They compare the serialized decision fields because SQLite restores persisted
setup decimals as strings while in-memory replay uses Decimal values.

The original saved live capture from issue #7 now replays as NO_TRADE identically
through JSONL and SQLite; Kraken's receipt was 340 ms after the Binance clock.

A fresh public Binance/Kraken probe at 2026-10-10 23:10:43 UTC also returned
NO_TRADE / NO_ORDER / DENIED_OBSERVATION_ONLY, with all gates passing and no
collection errors. Its real raw events were fed through the production service
and persisted. JSONL and SQLite replay results were identical, payload/timestamps
were preserved, and signal, setup, features, risk and quality matched the live
service. See `ISSUE_12_REPLAY_CHECK.json`; full raw evidence is retained locally
at `/tmp/tdal-issue12-live`. All three taker bucket volume comparisons matched
Binance 5m klines in this capture. Missing required-row diagnostics blocked.

These checks establish replay plumbing, not predictive benefit. The skipped-
candle reset is a separate PR for issue #12 item 1. Collection-completion freshness,
HTTP retries and empty-series diagnostics remain filed in issue #12 items 2–4.
Both fixes need review before prospective recordings are relied upon as evidence.
