# Historical candle ingestion — v0.1

## Scope and rationale

Import one offline Binance USD-M futures 15-minute kline export for one explicit
USDT instrument over an exact bounded range. Produce immutable JSONL normalized
snapshots and a hash-linked manifest. This connects raw historical candles to the
existing dataset and replay compartments; it does not download, paginate, poll,
predict, size positions or submit orders.

Independent review confirmed two prerequisite defects: the persisted Decimal
representation was not decoded by replay, and the dataset manifest claimed no
labels while accepting arbitrary input fields. `snapshots.py` now provides a
shared recursive allowlist and typed decoder used by dataset construction, both
replay loaders and direct `ReplayRunner.run()` calls. Existing live normalization
and strategy calculations remain unchanged. Unknown structured fields, including
nested labels/decisions/fills, raise a boundary error rather than being discarded.
This guards the input schema; it cannot authenticate a source or detect someone
deliberately encoding future information in an otherwise valid price field.

## Source envelope

See `examples/binance_15m_synthetic.json` for a complete **synthetic**, three-bar
example. It is not exchange evidence and does not meet feature warmup.

| Field | Contract |
|---|---|
| `schema_version` | `binance_kline_source/0.1` |
| `venue`, `market_type` | `binance`, `usds_m_futures` |
| `symbol`, `base_asset`, `quote_asset` | Explicit instrument matching `--base`; USDT quote only |
| `interval` | `15m` |
| `price_unit`, `volume_unit` | `USDT_per_base`, `base_asset` |
| `start_open_time_ms`, `end_open_time_ms` | Inclusive start, exclusive end; UTC Unix integer milliseconds, aligned to 900,000 ms |
| `acquired_at_ms` | Actual acquisition/receipt time recorded with the saved source, at or after the final bar completes |
| `klines` | Complete, ordered 12-field provider-shaped row array covering exactly the declared range |

All envelope fields are required; extras are rejected. Identity and acquisition
time are assertions supplied with the source, not independently authenticated
facts. A naked positional kline response has no symbol or market identity. Do not
guess these from the prices or use file modification time as download time. If
the saved source lacks trustworthy acquisition metadata, this importer cannot
establish it retrospectively. Preserve the original source envelope alongside
the generated artifacts; its exact bytes are SHA-256 fingerprinted in the manifest.

Rows follow the public `/fapi/v1/klines` layout: open time, OHLC, base volume,
close time, quote volume, trade count, taker-buy base/quote volumes, ignored field.
See the [Binance market-data reference](https://developers.binance.info/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data).
Decimal fields must be exact strings or integers; floating-point source values
are rejected. Times and trade counts must be nonnegative integers (not booleans).
OHLC must be finite and positive, within low/high bounds; volume must be finite
and nonnegative. Auxiliary volumes and trade counts are validated but are not
repurposed into missing five-minute taker history.

Close time must equal open time + 899,999 ms. Wrong order, gaps, all duplicates
(including identical duplicates), wrong row length, incomplete range coverage,
out-of-range bars, malformed values and unknown fields reject the whole import.
No interpolation, silent trimming, deduplication or sorting repairs the source.

## Snapshot and time contract

One snapshot is emitted per completed bar, including warmup prefixes. Each has
at most 150 bars by default and contains nothing beyond its own cutoff. The
Python API permits an explicit positive `window_size`, recorded in the manifest.
Default features require 97 completed bars; shorter windows remain feature-invalid.

- `observed_at_ms` is the actual source acquisition time, equal across the batch.
- `exchange_time_ms` is a **reconstructed cutoff**, bar close + 1 ms. It is not a
  captured exchange-server clock. This interpretation is explicitly marked by
  `historical_context.kind = candle_only_reconstruction`.
- `historical_context` records cutoff, acquisition time and `availability: unverified`.
- Candle provenance uses bar close as event time and actual acquisition as receipt
  time. `raw_id` identifies the reconstructed rolling window, not a saved live
  `RawEvent`. The full source hash is separate manifest metadata.
- Publication delays and historical revisions are not reconstructed. A slice of
  a later download is not evidence that those values were available at the cutoff.

The manifest separately records source range, first/last cutoff, acquisition,
window length, candle count, importer version, source hash, and asserted identity.
Its first/last *observed* timestamps describe acquisition, not historical coverage.
Changing a future source candle changes the source/dataset hashes but cannot
change an earlier normalized window or its reconstruction identifier.

All current Binance/Kraken quote fields stay null. OI, five-minute taker history,
funding and positioning remain empty. Required-input flags include the unavailable
inputs and unverified historical availability. No historical close is relabeled
as a contemporaneous quote. The decoder rejects attempts to remove these flags,
add context to this candle-only schema or mark availability verified.

## Outputs and failure behavior

Imported snapshots intentionally replay to `DATA_STALE`, `NO_ORDER` and
`DENIED_HISTORICAL_REPLAY`. Early rows fail candle warmup; later rows fail missing
OI. No trade eligibility is demonstrated. Their replay decisions lack a feature
anchor and remain unlabelable by the existing outcome-label path.

Dataset and replay inputs reject backwards acquisition or cutoff chronology,
including reconstructed rows with identical acquisition times. Numeric strings
are restored to Decimal only in declared numeric fields; identifiers remain text.
Invalid JSON, duplicate JSON keys, nonfinite values and incompatible schemas are
rejected. Minimal legacy candle-only dataset objects remain supported for dataset
and outcome work, while replay requires the complete normalized structure.

Both dataset outputs must be new, distinct paths. Exclusive creation prevents
clobbering another writer; failure removes only files created by that invocation.
The JSONL and manifest pair is not a filesystem transaction: an abrupt process
termination can leave an incomplete pair. Treat outputs as usable only after
successful completion and keep them immutable. Import returns exit code 2 with
an explanation on validation or file errors. Replay outputs also refuse overwrite.

## Run, inspect and stop

From the repository root:

```sh
python -m market_observer.ingestion \
  --source examples/binance_15m_synthetic.json --base TAO \
  --output data/example_candles.jsonl --manifest data/example_candles.manifest.json
python -m market_observer.replay \
  --jsonl data/example_candles.jsonl --base TAO --output results/example_replay.json
python -m json.tool data/example_candles.manifest.json
python -m json.tool results/example_replay.json
```

Both commands finish on their own; Ctrl-C interrupts them. Use new output names
for another run. For a real saved export, replace the source path with an envelope
containing its original rows and truthful metadata. No credentials are needed.

## Verification and remaining work

Tests cover strict source validation, exact decimal mapping, chronological window
isolation, truthful acquisition times, missing context, CLI import/replay,
determinism, immutable writes including a competing writer, nested contamination
rejection, and complete breakout/retest JSONL **and SQLite** replay parity. The
complete fixtures matter: all-stale candle imports alone would conceal decoder
errors in OI, funding or other fields.

The implementation session's actual Binance public historical-candle request
returned HTTP 451. Integrated verification therefore uses explicitly synthetic
fixtures, not a verified live exchange export. Continuous capture, downloadable
historical exports, derivatives/confirmation availability, useful candle-only
research and predictive validation remain separate next steps. RiskGovernor and
live order submission are outside this milestone.
