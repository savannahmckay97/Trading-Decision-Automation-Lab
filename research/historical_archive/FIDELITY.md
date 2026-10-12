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

| Series | Points | Result |
|---|---|---|
| 15m klines | 2,944 | identical except the one candle still open when the API was queried |
| Open interest | 8,352 | identical on all points after relabelling archive timestamps +5 min |
| Taker buy/sell (5m klines vs `takerlongshortRatio`) | 8,352 | 71% identical; the API reports slightly less total volume in the rest (median 0.07%, max 31%) |

The archive labels open interest at the start of each 5-minute period and the API at
its end; `archive.py` applies the +5 min relabelling. The metrics file's own taker ratio
matched the API less well than the 5m klines did, so klines are the taker source.
Per 15m candle, the taker buy share differed by at most 0.046.

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

## Full replay (setups only, no outcomes)

2024-04-12..2026-10-10: 87,413 candles evaluated, **561 `LONG_ELIGIBLE` decisions**
(about 19 a month, 13 to 29 in every full month). 77 candles reset the setup and were
skipped, fail-closed: 63 where the archive records zero open interest, 8 misaligned
open-interest windows, 2 missing open-interest points, 4 zero-volume taker windows.
