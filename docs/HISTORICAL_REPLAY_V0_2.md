# Historical replay contract — v0.2

## Purpose

Replay reconstructs the observer's decision state from a chronological sequence of normalized snapshots. It exists to detect causal, sequencing and state-machine errors before outcome labels or simulated fills are introduced.

It does **not** estimate profitability.

## Accepted inputs

- `normalized_snapshots` stored by this project's SQLite event store; or
- UTF-8 JSON Lines containing one complete `normalized_snapshot/0.1` object per line.

The source must retain event time, receipt time, venue, symbol, quote currency and the rolling data windows that were available at each as-of point. A later full-history download sliced after the fact is not equivalent unless publication delays and revisions are reconstructed.

## Causality and sequence rules

1. Snapshots are consumed in supplied order; the runner does not sort away evidence of bad ordering.
2. `observed_at_ms` may not move backwards.
3. Completed-candle time may not move backwards or fall off the configured interval grid.
4. Repeated completed candles are classified as duplicates and cannot advance setup state twice.
5. Missing completed bars are counted explicitly. Active setup state is reset before evaluating the first post-gap snapshot.
6. Feature-window gaps, malformed OHLCV or unavailable critical inputs fail closed as `DATA_STALE`.
7. Research and executable symbols must match the frozen replay configuration.

## Frozen baseline

Every result records:

- replay implementation version;
- full public configuration;
- deterministic configuration ID;
- deterministic decision IDs;
- signal and sequence counts;
- detected gaps and missing-bar totals;
- explicit unavailable claims.

The output path must be new. The runner refuses to overwrite an earlier replay result.

## Forbidden claims

A replay result does not establish:

- that a signal could have filled;
- entry or exit price;
- gross or net return;
- expected value;
- parameter stability;
- out-of-sample performance;
- live readiness.

Those claims require later, separately versioned compartments for outcome labeling, cost and fill simulation, multiple-testing control, chronological validation and paper reconciliation.
