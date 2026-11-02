# Hold-period exploration (EXPLORATORY, not preregistered)

**Owner's question:** would the simulation of BREAKOUT_RETEST_V1 on TAO still end at a
loss if positions were held longer, or closed earlier?

**Status of this work.** This is exploratory analysis of data that has already been
examined (the PR #21 historical replay). It can suggest hypotheses for a new design; it
cannot confirm anything. Every horizon and exit policy below is reported, not just the
best-looking one, and none of them is a finding. Nothing here changes the v1 rule, its
parameters, or any frozen file (`research/historical_archive/*` is only imported).

This design was written and committed **before the script was first run**. The results
are in `RESULTS.md`, added in a later commit.

## Run

```sh
PYTHONPATH=. python research/hold_period_exploration/explore.py \
    ~/tdal-history/TAOUSDT ~/tdal-history/TAOUSDT/hold_period_v1.json \
    ~/tdal-history/TAOUSDT/results_v1.json
```

The archive must match the registered manifest (re-hashed on load). With the third
argument, the replayed signals are also checked against the registered PR #21
population. The output is created exclusively (never overwritten) and the script prints
markdown tables.

## Design

- **Signals:** the 543 `LONG_ELIGIBLE` decisions from the unchanged production replay
  (`replay_archive.replay`) whose signal candle closed between 2024-04-12 and
  2026-09-10 UTC.
- **Entry:** the open of the first 15-minute candle opening at or after the assumed
  decision time, 4 minutes after the signal candle closes (the candle after next).
  Binance USD-M prices stand in for Kraken spot.
- **Horizons:** 1 h, 4 h, 12 h, 1 d, 3 d, 7 d, 14 d, 30 d (4 to 2,880 candles) from the
  entry candle. The time exit is the close of the last candle of the hold. A trade whose
  horizon runs past the end of the data (2026-10-10) is censored for that horizon and
  counted.
- **Exit policies** (each also exits at the horizon):
  - (a) time exit only;
  - (b) close stop: exit at the first candle close below the v1 invalidation level;
  - (c) wick stop at invalidation: exit at the level when a candle's low touches it, or at
    the candle's open if it opened below (a gap fills at the worse open);
  - (d1) wick stop at invalidation − 1 × ATR; (d2) at invalidation − 2 × ATR (ATR is the
    setup's 15-minute breakout ATR, known at entry);
  - (e) **trailing stop, declared before running and the only variant tried:** start at
    invalidation − 2 × ATR; after each completed candle raise the stop to (highest high
    since entry − 10 × ATR) if higher; never lower it. Wick execution, gaps fill at the
    open. 10 × the 15-minute ATR is about a one-day range if moves scale with the square
    root of time (√96 ≈ 9.8).
- **Costs (Kraken Tier 4):** per side, fee + 5 bps slippage + half of a 3.35 bps spread,
  applied to that side's price. Maker in (20 bps) / taker out (35 bps): **68.35 bps**
  round trip. Taker in / taker out: **83.35 bps**. Spot, so no funding. A maker fill at
  the open is assumed, which is optimistic.
- **Units:** % of entry price. R = net return ÷ the v1 stop distance (signal close minus
  invalidation, as a fraction of the signal close), i.e. in units of v1's own risk
  whatever the exit policy; most meaningful for (b) and (c).
- **Overlap:** "every signal" counts each signal as a trade even when they overlap (they
  are not independent). "One position at a time" takes a signal only when flat, per cell,
  and reports the compounded account result and the share of time in the market.
- **Uncertainty:** 95% percentile interval of the mean net return, resampling UTC days
  with replacement (10,000 draws, seed 20261013), as in `run.py`.
- **Baselines** (same entry timing, exits and costs):
  1. every evaluated candle in the window as an entry, time exit only;
  2. a matched random baseline: 20,000 random window candles (fixed seed), each given the
     stop distance and ATR (as fractions of price) of a randomly drawn v1 signal, run
     through every policy;
  3. buy-and-hold TAO over the window (one round trip);
  4. TAO's drift per horizon: geometric over the window, and the every-candle mean.
  
  Plus a trend control: each signal's gross time-exit return minus the mean of every
  candle in the same UTC month, at the same horizon.
- **Cost drag:** round trip cost against the median absolute price move per horizon.
