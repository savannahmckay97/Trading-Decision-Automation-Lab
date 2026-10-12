# Archive fidelity: does Binance's public archive reproduce the live inputs?

Checked 2026-10-13, before any historical outcome was computed.

## Data

`fetch_archive.py` downloaded TAOUSDT from data.binance.vision: 15m and 5m klines
(monthly, daily for the current month) and daily `metrics` files. All 993 published
files matched Binance's SHA-256 checksums. The 10 metrics days 2024-04-01..10 aren't
published (the contract listed on 2024-04-11). Manifest SHA-256:
`bfc2ae4a9968c4872650877d8949e973327b7c8c1e91601ef1b78c55923da00c`.

Coverage after loading: 87,590 15m candles, 2024-04-11 14:30Z to 2026-10-10 23:45Z,
no missing candles; 262,765 open-interest points with 2 gaps totalling 5 points.

## Comparison with the live API (2026-09-11..10-10 backfill)

The 29-day API backfill in `~/tdal-livecheck/evidence/backfill_30d/` overlaps the archive.
`fidelity_check.py` (added after review) reproduces this comparison and the decision
parity check below: `python research/historical_archive/fidelity_check.py ARCHIVE_DIR BACKFILL_DIR`.

| Series | Points | Result |
|---|---|---|
| 15m klines | 2,944 | identical except the one candle still open when the API was queried |
| Open interest | 8,352 | identical on all points after relabelling archive timestamps +5 min |
| Taker buy/sell (5m klines vs `takerlongshortRatio`) | 8,352 | buy volume identical on 71% (5,955), sell volume on 71% (5,958), **both on 58% (4,851)**; in the other 3,501 total volume differs by a median 0.07% of the archive's volume (max 24%) |

The archive labels open interest at the start of each 5-minute period and the API at
its end; `archive.py` applies the +5 min relabelling to every point. The metrics file's own taker ratio
matched the API less well than the 5m klines did, so klines are the taker source.
Per 15m candle, the taker buy share differed by at most 0.046.

**Scope of this check.** The +5 min open-interest relabel and the choice of 5m klines as
the taker source are verified only for this one-month overlap (2026-09-11..10-10). For
the rest of the 2.5 years they are an extrapolated reconstruction assumption: nothing
here shows that Binance's archive labelling, publication timing or taker semantics were
the same throughout. (The 15m kline row in the table above counted 2,944 API candles
including the one still open; `fidelity_check.py` excludes it and finds 2,943 of 2,943
identical.)

## Does it change decisions?

`replay_archive.py` follows `research/setup_frequency/history.py` exactly. Checks:

1. On the API backfill, `replay_archive.replay` and `history.replay` give the same 17
   `LONG_ELIGIBLE` decisions.
2. Over the same 2,779 candles, API inputs and archive inputs give **identical state and
   signal on every candle and the same 17 `LONG_ELIGIBLE` decisions.** Nine candles
   differ only in breakout-quality score, never across the threshold.

So for BREAKOUT_RETEST_V1 the archive is a faithful stand-in for the live Binance inputs
over this month. It can't stand in for things the archive lacks: Kraken's spread and the
cross-venue divergence gate (a fixed 3.35 bps spread with zero divergence is assumed, as
in `history.py`), and real receipt timing (a 4-minute decision delay is assumed).

The replay calls the production feature, quality, state-machine and risk functions
(`calculate_features`, `breakout_quality`, `advance_breakout_retest`,
`assess_decision_risk`) but **bypasses the production evaluator** (`evaluate_snapshot`):
its spread, divergence and data-freshness gates are not run, and every candle is
treated as if they passed.

## Full replay (setups only, no outcomes)

2024-04-12..2026-10-10: 87,413 candles evaluated, **561 `LONG_ELIGIBLE` decisions**
(about 19 a month, 13 to 29 in every full month). 77 candles reset the setup and were
skipped, fail-closed: 63 where the archive records zero open interest, 8 misaligned
open-interest windows, 2 missing open-interest points, 4 zero-volume taker windows.
In counts: 87,490 candles considered after warm-up, 77 skipped, 87,413 evaluated, of
which 84,533 fall in the study window. `run.py` now writes these counts and the input
coverage (87,590 15m bars, 262,765 OI points, 262,770 5m taker buckets) to the
`.provenance.json` file beside each result.
