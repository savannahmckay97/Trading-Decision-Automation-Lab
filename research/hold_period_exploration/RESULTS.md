# Results: hold-period exploration (2026-10-13). EXPLORATORY, NOT PREREGISTERED

**Read this first.** This analysis re-uses the TAO data already examined in PR #21. It
was not planned before those results were seen, so it **cannot confirm anything**; it can
only suggest hypotheses for a new design that must then be tested on data it wasn't
designed on. The design (`README.md`, including the single trailing-stop variant) was
committed at `7f617c7` before the script was first run. The **whole grid** is reported
below (appendix); no cell is singled out as a finding, and with 48 cells × 2 cost
levels × 3 versions, some cells will look good or bad by chance alone.

Output: `~/tdal-history/TAOUSDT/hold_period_v1.json` (outside the repo), SHA-256
`03177de9a2bc8f4e8ebbcbc461a6c03816024d70f14d142e0ff016eaa059ea7e`, produced by
`explore.py` at `7f617c7` on a clean tree, with the registered manifest and the
replayed signals checked against the registered PR #21 population (543 signals on 364
UTC days; none censored at any horizon). Runtime about 9 minutes.

## Short answer to the owner's question

**Yes, it still ends at a loss, whether positions are closed earlier or held longer.**
Taking one position at a time and putting all capital into each trade, every one of the
48 horizon × exit combinations ends the 2.5 years down between 68% and 98% after Kraken
Tier 4 costs. Simply buying TAO at the start and holding it lost 62.6% over the same
window, because TAO itself fell from 624 to 235 USDT.

Holding longer does do what the owner expects to the **cost drag**: a round trip
(0.68% maker-in/taker-out, 0.83% taker/taker) is larger than TAO's typical 1-hour move
but only about 4% of its typical 30-day move. What it does **not** do is create an edge.
Before costs, the v1 signals' average returns are close to zero at short horizons and,
at long horizons, are no better than buying TAO at a random moment in the same month.
Longer holds mostly swap a cost problem for exposure to TAO's own trend.

## Key table (every signal, maker in / taker out, % of entry price)

"Cost ÷ typical move" is the 68.35 bps round trip divided by TAO's median absolute
price move over that horizon (all candles). Intervals are 95% UTC-day bootstrap
intervals of the mean (see the caveat on overlap below: at 3 days and longer they are
too narrow).

| Hold | Cost ÷ typical move | Time exit only | Stop at invalidation (wick, c) | Trailing stop (e) | Every candle, time exit (baseline) | Signal minus same-month market, gross | One at a time, time exit: account result |
|---|---|---|---|---|---|---|---|
| 1 h | 1.16 | −0.70 (−0.81 to −0.59) | −0.70 | −0.70 | −0.68 | −0.03 (−0.14 to +0.08) | −97% |
| 4 h | 0.58 | −0.69 (−0.93 to −0.45) | −0.77 | −0.72 | −0.67 | −0.04 (−0.28 to +0.20) | −93% |
| 12 h | 0.32 | −0.84 (−1.26 to −0.42) | −0.77 | −0.94 | −0.65 | −0.25 (−0.68 to +0.17) | −95% |
| 1 d | 0.22 | −0.69 (−1.30 to −0.06) | −0.68 | −0.89 | −0.61 | −0.18 (−0.80 to +0.45) | −91% |
| 3 d | 0.12 | −0.31 (−1.50 to +0.90) | −0.49 | −0.69 | −0.48 | −0.12 (−1.27 to +1.05) | −68% |
| 7 d | 0.07 | −0.55 (−2.25 to +1.20) | −0.48 | −0.90 | −0.21 | −1.02 (−2.57 to +0.56) | −73% |
| 14 d | 0.05 | +0.35 (−1.98 to +2.77) | −0.35 | −0.88 | +0.42 | −1.25 (−3.22 to +0.77) | −70% |
| 30 d | 0.04 | +0.98 (−2.33 to +4.45) | −0.60 | −0.88 | +1.93 | −1.86 (−4.18 to +0.43) | −77% |

Buy and hold over the whole window: **−62.6%** after one round trip.

The other policies (close stop, invalidation − 1 or 2 ATR), taker/taker costs, win
rates, medians, R units, the one-at-a-time version of every cell and the matched random
baseline are in the appendix. Taker/taker costs lower every mean by a further 0.15%.

## What the baselines say

1. **The signal does not add anything visible beyond holding TAO.** At every horizon the
   v1 signals' gross time-exit return minus the average return of all candles in the
   same month is negative (−0.03% at 1 h to −1.86% at 30 d) and every interval includes
   zero. Against the matched random baseline (random entry times given v1-sized stops),
   the signals' mean net return is lower in 37 of the 48 cells and higher in 11, with
   differences mostly well inside the signals' intervals.
2. **TAO's trend dominates long holds.** TAO fell 62% over the window (−46% in the first
   half, −30% in the second), with swings from +113% (2024-Q3) to −56% (2024-Q2) in a
   single quarter; its low was 130.5 (2025-10-10) and high 750 (2024-12-06). A long-only
   rule held for weeks is mostly a bet on TAO going up.
3. **Average return and account result point in opposite directions at long horizons.**
   Buying at a random candle and holding 30 days averaged **+2.6%** gross, yet TAO's
   compounded drift over 30 days was **−3.3%**, and the median 30-day trade lost 5.0%.
   The positive average comes from a few very large up-moves; compounded, the losses
   win. That is why the 14 d and 30 d time-exit cells have positive means but the
   one-at-a-time accounts still lose 70–77%. A positive mean per trade is not a
   profitable account here.
4. **Tight stops undo long holds.** With the stop at or near the v1 invalidation level
   (median 0.65% below the signal close; 15-minute ATR median 0.84% of price), almost
   every trade is stopped out long before a multi-day horizon: at 30 days only 2–6% of
   those trades are winners. Policies (b)–(d) at long horizons are really short trades
   that pay full costs. The trailing stop (10 × 15-minute ATR) exited every trade within
   14 days, so its 14 d and 30 d cells are identical.

## Hypotheses for a longer-hold design (to test on new data, not conclusions)

- **H1: costs stop being the binding constraint beyond about 1–3 days.** The cost ÷
  typical move ratio falls from 1.16 (1 h) to 0.22 (1 d) and 0.04 (30 d). A design that
  holds for days could afford Kraken Tier 4 fees, *if* it had an edge.
- **H2: the edge has to come from somewhere other than v1's entry.** Nothing here shows
  v1 entries doing better than random entries at any horizon. A longer-hold design
  needs its own entry hypothesis, decided before testing.
- **H3: a long hold needs a stop sized for the hold.** Stops measured in 15-minute ATR
  end multi-day trades within hours or days. A longer-hold design would need a stop
  (or position size) scaled to daily volatility, and would then risk several percent
  per trade.
- **H4: a long-only rule needs a trend or regime condition, or a short side.** On an
  asset that fell 62%, long-only holds lose with the market. Whether a trend filter
  helps is a separate question that this window, chosen after the fact, cannot answer.
- **H5: judge designs by compounded account result and drawdown, not mean per trade.**
  Item 3 above shows the two can disagree in sign.

## Caveats

- **Exploratory, already-seen data.** Same asset, same window and same signals as
  PR #21. Hypotheses only; 288 cells were computed, so false positives are expected.
- **Single asset, single period, long-only.** TAO's 62% decline shapes every long-hold
  number. A rising market would flatter the same rules just as much.
- **Overlapping trades.** At 3 days and longer, many "every signal" trades overlap in
  time (e.g. 543 thirty-day trades in a 30-month window). The UTC-day bootstrap treats
  different days as independent, so its intervals at long horizons are **too narrow**;
  the true uncertainty is larger. The one-at-a-time versions avoid overlap but have
  few trades at long horizons (28 at 30 days).
- **R units** use v1's own stop distance (median 0.65%), which is tiny next to multi-day
  moves; mean R at long horizons (e.g. +8.8R at 30 days, time exit) is dominated by a
  few trades with very small stops and isn't meaningful there.
- **Execution model.** Binance USD-M futures candles stand in for Kraken TAO/USD spot;
  basis, Kraken liquidity and whether Kraken listed TAOUSD for the whole window are not
  checked. Maker entry at the open is assumed filled (optimistic). Gaps fill at the
  candle open; no extra slippage on stop fills beyond 5 bps. Spot only, so no funding,
  which would apply if held as a perpetual.
- **Compounded figures** assume all capital in every trade, which overstates both gains
  and losses compared to any sensible position size; they are a sign check, not a
  projection.
- Nothing here changes the v1 rule, its parameters, the preregistered PR #21 result or
  any frozen file. Live order submission stays disabled.

## Appendix: the full grid

Every horizon × policy cell, as printed by `explore.py` (rounded). "M/T" = maker in /
taker out (68.35 bps); "T/T" = taker in / taker out (83.35 bps). Win % = share of trades
with a positive net return (M/T). R = net return (M/T) ÷ v1 stop distance. Policies:
`a_time` time exit only; `b_close_stop` close below invalidation; `c_wick_stop` wick at
invalidation; `d1`/`d2` wick at invalidation − 1/2 ATR; `e_trail_10atr` trailing stop.

### A1. Every-candle baseline (time exit) and cost drag

| Horizon | Drift (geom) % | Every-candle mean gross % | median gross % | mean net % (M/T) | 95% CI | win % | median abs move % | cost / median abs move |
|---|---|---|---|---|---|---|---|---|
| 1h | -0.005 | +0.003 | -0.018 | -0.68 | -0.69 to -0.66 | 22 | 0.59 | 1.16 |
| 4h | -0.018 | +0.011 | -0.068 | -0.67 | -0.73 to -0.61 | 33 | 1.18 | 0.58 |
| 12h | -0.055 | +0.034 | -0.175 | -0.65 | -0.82 to -0.47 | 39 | 2.11 | 0.32 |
| 1d | -0.111 | +0.069 | -0.450 | -0.61 | -0.92 to -0.30 | 40 | 3.14 | 0.22 |
| 3d | -0.331 | +0.205 | -1.006 | -0.48 | -1.10 to +0.15 | 42 | 5.75 | 0.12 |
| 7d | -0.771 | +0.478 | -1.117 | -0.21 | -1.18 to +0.78 | 44 | 9.60 | 0.07 |
| 14d | -1.537 | +1.105 | -3.040 | +0.42 | -1.01 to +1.86 | 43 | 13.19 | 0.05 |
| 30d | -3.264 | +2.626 | -5.008 | +1.93 | -0.10 to +4.03 | 41 | 17.58 | 0.04 |

### A2. V1 signals, every signal (overlapping trades)

| Horizon | Policy | n | mean gross % | median gross % | mean net % M/T | 95% CI M/T | mean net % T/T | 95% CI T/T | win % M/T | mean R | median R |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1h | a_time | 543 | -0.02 | -0.13 | -0.70 | -0.81 to -0.59 | -0.85 | -0.96 to -0.74 | 27 | -1.62 | -1.19 |
| 1h | b_close_stop | 543 | -0.02 | -0.23 | -0.70 | -0.81 to -0.60 | -0.85 | -0.95 to -0.75 | 23 | -1.63 | -1.39 |
| 1h | c_wick_stop | 543 | -0.02 | -0.12 | -0.70 | -0.79 to -0.61 | -0.85 | -0.94 to -0.76 | 17 | -1.64 | -1.39 |
| 1h | d1_wick_inv_minus_1atr | 543 | -0.01 | -0.19 | -0.69 | -0.79 to -0.58 | -0.84 | -0.94 to -0.73 | 25 | -1.59 | -1.33 |
| 1h | d2_wick_inv_minus_2atr | 543 | -0.02 | -0.14 | -0.70 | -0.81 to -0.59 | -0.85 | -0.96 to -0.73 | 26 | -1.61 | -1.21 |
| 1h | e_trail_10atr | 543 | -0.02 | -0.14 | -0.70 | -0.81 to -0.59 | -0.85 | -0.96 to -0.73 | 26 | -1.61 | -1.21 |
| 4h | a_time | 543 | -0.01 | -0.14 | -0.69 | -0.93 to -0.45 | -0.84 | -1.08 to -0.60 | 31 | -1.55 | -1.26 |
| 4h | b_close_stop | 543 | -0.09 | -0.46 | -0.77 | -0.95 to -0.58 | -0.92 | -1.10 to -0.73 | 20 | -1.68 | -1.78 |
| 4h | c_wick_stop | 543 | -0.09 | -0.27 | -0.77 | -0.91 to -0.62 | -0.92 | -1.06 to -0.77 | 15 | -1.76 | -1.68 |
| 4h | d1_wick_inv_minus_1atr | 543 | -0.07 | -0.62 | -0.75 | -0.95 to -0.55 | -0.90 | -1.09 to -0.70 | 26 | -1.75 | -1.99 |
| 4h | d2_wick_inv_minus_2atr | 543 | -0.05 | -0.36 | -0.73 | -0.94 to -0.51 | -0.88 | -1.09 to -0.66 | 30 | -1.63 | -1.41 |
| 4h | e_trail_10atr | 543 | -0.04 | -0.36 | -0.72 | -0.94 to -0.50 | -0.87 | -1.09 to -0.65 | 30 | -1.62 | -1.40 |
| 12h | a_time | 543 | -0.16 | -0.50 | -0.84 | -1.26 to -0.42 | -0.99 | -1.41 to -0.57 | 38 | -1.85 | -1.40 |
| 12h | b_close_stop | 543 | -0.18 | -0.65 | -0.86 | -1.09 to -0.61 | -1.01 | -1.23 to -0.76 | 16 | -1.83 | -2.15 |
| 12h | c_wick_stop | 543 | -0.09 | -0.38 | -0.77 | -0.96 to -0.57 | -0.92 | -1.11 to -0.72 | 12 | -1.72 | -1.84 |
| 12h | d1_wick_inv_minus_1atr | 543 | -0.25 | -0.99 | -0.93 | -1.20 to -0.65 | -1.08 | -1.35 to -0.80 | 21 | -2.10 | -2.66 |
| 12h | d2_wick_inv_minus_2atr | 543 | -0.28 | -1.40 | -0.96 | -1.28 to -0.63 | -1.10 | -1.42 to -0.78 | 29 | -1.89 | -3.06 |
| 12h | e_trail_10atr | 543 | -0.26 | -1.39 | -0.94 | -1.27 to -0.62 | -1.09 | -1.41 to -0.77 | 29 | -1.88 | -3.04 |
| 1d | a_time | 543 | -0.01 | -0.51 | -0.69 | -1.30 to -0.06 | -0.84 | -1.45 to -0.21 | 41 | -1.37 | -1.54 |
| 1d | b_close_stop | 543 | -0.15 | -0.72 | -0.83 | -1.12 to -0.52 | -0.98 | -1.27 to -0.67 | 13 | -1.77 | -2.27 |
| 1d | c_wick_stop | 543 | +0.00 | -0.42 | -0.68 | -0.93 to -0.41 | -0.83 | -1.08 to -0.56 | 10 | -1.53 | -1.92 |
| 1d | d1_wick_inv_minus_1atr | 543 | -0.22 | -1.09 | -0.90 | -1.24 to -0.55 | -1.05 | -1.39 to -0.70 | 18 | -2.03 | -2.82 |
| 1d | d2_wick_inv_minus_2atr | 543 | -0.23 | -1.70 | -0.91 | -1.33 to -0.48 | -1.06 | -1.48 to -0.63 | 26 | -1.45 | -3.55 |
| 1d | e_trail_10atr | 543 | -0.21 | -1.69 | -0.89 | -1.30 to -0.48 | -1.04 | -1.45 to -0.62 | 26 | -1.49 | -3.51 |
| 3d | a_time | 543 | +0.37 | -1.21 | -0.31 | -1.50 to +0.90 | -0.46 | -1.64 to +0.75 | 41 | -1.34 | -3.03 |
| 3d | b_close_stop | 543 | +0.14 | -0.79 | -0.55 | -1.06 to +0.03 | -0.69 | -1.21 to -0.12 | 9 | -0.91 | -2.40 |
| 3d | c_wick_stop | 543 | +0.19 | -0.49 | -0.49 | -0.93 to +0.00 | -0.64 | -1.08 to -0.14 | 6 | -1.09 | -1.98 |
| 3d | d1_wick_inv_minus_1atr | 543 | +0.06 | -1.24 | -0.62 | -1.18 to +0.01 | -0.77 | -1.33 to -0.14 | 11 | -1.28 | -3.01 |
| 3d | d2_wick_inv_minus_2atr | 543 | +0.12 | -1.94 | -0.56 | -1.27 to +0.19 | -0.71 | -1.42 to +0.04 | 17 | -0.38 | -3.93 |
| 3d | e_trail_10atr | 543 | -0.01 | -1.84 | -0.69 | -1.32 to -0.02 | -0.84 | -1.46 to -0.17 | 20 | -1.49 | -3.80 |
| 7d | a_time | 543 | +0.13 | -1.08 | -0.55 | -2.25 to +1.20 | -0.70 | -2.40 to +1.05 | 43 | -0.63 | -3.39 |
| 7d | b_close_stop | 543 | +0.25 | -0.84 | -0.43 | -1.15 to +0.38 | -0.58 | -1.30 to +0.23 | 6 | +0.19 | -2.44 |
| 7d | c_wick_stop | 543 | +0.20 | -0.55 | -0.48 | -1.07 to +0.21 | -0.63 | -1.22 to +0.06 | 4 | -0.55 | -2.04 |
| 7d | d1_wick_inv_minus_1atr | 543 | +0.14 | -1.33 | -0.54 | -1.33 to +0.33 | -0.69 | -1.48 to +0.18 | 8 | -0.15 | -3.07 |
| 7d | d2_wick_inv_minus_2atr | 543 | +0.05 | -2.09 | -0.63 | -1.56 to +0.43 | -0.78 | -1.71 to +0.28 | 13 | +0.34 | -4.11 |
| 7d | e_trail_10atr | 543 | -0.22 | -1.86 | -0.90 | -1.50 to -0.23 | -1.04 | -1.65 to -0.38 | 19 | -1.64 | -3.80 |
| 14d | a_time | 543 | +1.04 | -2.04 | +0.35 | -1.98 to +2.77 | +0.20 | -2.12 to +2.61 | 45 | +3.51 | -3.55 |
| 14d | b_close_stop | 543 | +0.33 | -0.87 | -0.35 | -1.14 to +0.56 | -0.50 | -1.29 to +0.41 | 5 | +0.24 | -2.48 |
| 14d | c_wick_stop | 543 | +0.33 | -0.55 | -0.35 | -1.02 to +0.44 | -0.50 | -1.17 to +0.29 | 4 | -0.34 | -2.06 |
| 14d | d1_wick_inv_minus_1atr | 543 | +0.31 | -1.37 | -0.38 | -1.34 to +0.76 | -0.53 | -1.48 to +0.61 | 6 | -0.09 | -3.09 |
| 14d | d2_wick_inv_minus_2atr | 543 | +0.11 | -2.12 | -0.57 | -1.68 to +0.73 | -0.72 | -1.82 to +0.58 | 9 | -0.04 | -4.21 |
| 14d | e_trail_10atr | 543 | -0.20 | -1.86 | -0.88 | -1.49 to -0.21 | -1.02 | -1.64 to -0.36 | 19 | -1.49 | -3.81 |
| 30d | a_time | 543 | +1.68 | -7.10 | +0.98 | -2.33 to +4.45 | +0.83 | -2.48 to +4.30 | 39 | +8.83 | -8.67 |
| 30d | b_close_stop | 543 | -0.00 | -0.91 | -0.68 | -1.48 to +0.28 | -0.83 | -1.63 to +0.13 | 3 | -0.77 | -2.54 |
| 30d | c_wick_stop | 543 | +0.08 | -0.58 | -0.60 | -1.26 to +0.23 | -0.75 | -1.40 to +0.08 | 2 | -1.40 | -2.10 |
| 30d | d1_wick_inv_minus_1atr | 543 | -0.19 | -1.43 | -0.87 | -1.85 to +0.36 | -1.01 | -1.99 to +0.21 | 3 | -1.38 | -3.15 |
| 30d | d2_wick_inv_minus_2atr | 543 | -0.18 | -2.21 | -0.86 | -2.11 to +0.68 | -1.01 | -2.25 to +0.53 | 6 | -0.91 | -4.35 |
| 30d | e_trail_10atr | 543 | -0.20 | -1.86 | -0.88 | -1.49 to -0.21 | -1.02 | -1.64 to -0.36 | 19 | -1.49 | -3.81 |

### A3. V1 signals, one position at a time (compounded = account result with all capital in each trade)

| Horizon | Policy | n | mean gross % | median gross % | mean net % M/T | 95% CI M/T | mean net % T/T | 95% CI T/T | win % M/T | mean R | median R | compounded % (M/T) | time in mkt |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1h | a_time | 509 | -0.00 | -0.09 | -0.68 | -0.80 to -0.57 | -0.83 | -0.95 to -0.71 | 27 | -1.61 | -1.18 | -97.1 / -98.7 | 2% |
| 1h | b_close_stop | 509 | -0.00 | -0.20 | -0.68 | -0.79 to -0.57 | -0.83 | -0.94 to -0.72 | 24 | -1.61 | -1.39 | -97.1 / -98.6 | 2% |
| 1h | c_wick_stop | 511 | -0.00 | -0.12 | -0.68 | -0.78 to -0.59 | -0.83 | -0.92 to -0.74 | 18 | -1.64 | -1.39 | -97.1 / -98.6 | 2% |
| 1h | d1_wick_inv_minus_1atr | 509 | +0.02 | -0.16 | -0.66 | -0.77 to -0.55 | -0.81 | -0.92 to -0.70 | 26 | -1.57 | -1.31 | -96.7 / -98.5 | 2% |
| 1h | d2_wick_inv_minus_2atr | 509 | +0.00 | -0.12 | -0.68 | -0.79 to -0.56 | -0.83 | -0.94 to -0.71 | 27 | -1.60 | -1.19 | -97.0 / -98.6 | 2% |
| 1h | e_trail_10atr | 509 | +0.00 | -0.12 | -0.68 | -0.79 to -0.56 | -0.83 | -0.94 to -0.71 | 27 | -1.60 | -1.19 | -97.0 / -98.6 | 2% |
| 4h | a_time | 431 | +0.11 | -0.07 | -0.58 | -0.83 to -0.33 | -0.73 | -0.97 to -0.48 | 33 | -1.44 | -1.16 | -92.8 / -96.2 | 8% |
| 4h | b_close_stop | 448 | -0.03 | -0.44 | -0.71 | -0.89 to -0.53 | -0.86 | -1.04 to -0.68 | 22 | -1.71 | -1.81 | -96.3 / -98.1 | 4% |
| 4h | c_wick_stop | 466 | -0.06 | -0.26 | -0.74 | -0.88 to -0.59 | -0.89 | -1.03 to -0.74 | 15 | -1.80 | -1.69 | -97.1 / -98.5 | 3% |
| 4h | d1_wick_inv_minus_1atr | 436 | -0.01 | -0.60 | -0.69 | -0.88 to -0.48 | -0.84 | -1.03 to -0.63 | 27 | -1.74 | -1.99 | -95.5 / -97.7 | 6% |
| 4h | d2_wick_inv_minus_2atr | 432 | +0.07 | -0.27 | -0.61 | -0.83 to -0.39 | -0.76 | -0.98 to -0.54 | 31 | -1.52 | -1.35 | -93.8 / -96.7 | 7% |
| 4h | e_trail_10atr | 432 | +0.07 | -0.27 | -0.61 | -0.83 to -0.39 | -0.76 | -0.98 to -0.54 | 31 | -1.53 | -1.35 | -93.9 / -96.8 | 7% |
| 12h | a_time | 374 | -0.03 | -0.38 | -0.71 | -1.13 to -0.29 | -0.86 | -1.28 to -0.44 | 39 | -1.70 | -1.38 | -95.0 / -97.1 | 21% |
| 12h | b_close_stop | 424 | -0.15 | -0.66 | -0.83 | -1.06 to -0.58 | -0.97 | -1.20 to -0.73 | 15 | -2.02 | -2.20 | -97.4 / -98.6 | 8% |
| 12h | c_wick_stop | 453 | -0.09 | -0.38 | -0.77 | -0.96 to -0.58 | -0.92 | -1.10 to -0.73 | 11 | -1.97 | -1.87 | -97.3 / -98.6 | 6% |
| 12h | d1_wick_inv_minus_1atr | 403 | -0.20 | -0.99 | -0.88 | -1.15 to -0.60 | -1.03 | -1.30 to -0.74 | 21 | -2.24 | -2.72 | -97.6 / -98.7 | 10% |
| 12h | d2_wick_inv_minus_2atr | 388 | -0.17 | -1.38 | -0.85 | -1.17 to -0.52 | -1.00 | -1.32 to -0.67 | 30 | -1.89 | -3.14 | -97.0 / -98.3 | 14% |
| 12h | e_trail_10atr | 388 | -0.17 | -1.37 | -0.85 | -1.18 to -0.51 | -1.00 | -1.33 to -0.66 | 30 | -1.91 | -3.10 | -97.1 / -98.4 | 14% |
| 1d | a_time | 306 | +0.05 | -0.51 | -0.63 | -1.28 to +0.02 | -0.78 | -1.43 to -0.13 | 40 | -1.33 | -1.70 | -91.4 / -94.6 | 35% |
| 1d | b_close_stop | 405 | -0.12 | -0.72 | -0.80 | -1.07 to -0.49 | -0.94 | -1.22 to -0.64 | 13 | -2.01 | -2.27 | -96.7 / -98.2 | 11% |
| 1d | c_wick_stop | 438 | -0.02 | -0.44 | -0.70 | -0.93 to -0.43 | -0.85 | -1.08 to -0.58 | 9 | -1.89 | -1.97 | -96.0 / -97.9 | 9% |
| 1d | d1_wick_inv_minus_1atr | 379 | -0.11 | -1.04 | -0.80 | -1.13 to -0.43 | -0.94 | -1.28 to -0.58 | 18 | -2.10 | -2.83 | -96.1 / -97.8 | 15% |
| 1d | d2_wick_inv_minus_2atr | 346 | -0.07 | -1.64 | -0.75 | -1.18 to -0.30 | -0.90 | -1.33 to -0.45 | 26 | -1.49 | -3.62 | -94.6 / -96.8 | 20% |
| 1d | e_trail_10atr | 350 | -0.08 | -1.62 | -0.76 | -1.18 to -0.33 | -0.91 | -1.33 to -0.48 | 26 | -1.59 | -3.52 | -94.9 / -97.0 | 20% |
| 3d | a_time | 184 | +0.56 | -1.02 | -0.12 | -1.54 to +1.37 | -0.27 | -1.68 to +1.22 | 40 | -0.33 | -2.75 | -67.5 / -75.3 | 63% |
| 3d | b_close_stop | 366 | -0.02 | -0.77 | -0.70 | -1.14 to -0.20 | -0.85 | -1.28 to -0.35 | 8 | -1.94 | -2.50 | -94.5 / -96.8 | 18% |
| 3d | c_wick_stop | 407 | -0.04 | -0.53 | -0.73 | -1.06 to -0.34 | -0.87 | -1.21 to -0.49 | 5 | -2.07 | -2.09 | -96.0 / -97.8 | 14% |
| 3d | d1_wick_inv_minus_1atr | 331 | +0.03 | -1.18 | -0.65 | -1.17 to -0.06 | -0.80 | -1.31 to -0.21 | 11 | -2.17 | -3.10 | -92.3 / -95.3 | 24% |
| 3d | d2_wick_inv_minus_2atr | 284 | +0.17 | -1.86 | -0.51 | -1.24 to +0.26 | -0.66 | -1.39 to +0.11 | 17 | -0.29 | -4.11 | -86.5 / -91.2 | 33% |
| 3d | e_trail_10atr | 309 | +0.04 | -1.82 | -0.64 | -1.25 to +0.02 | -0.79 | -1.39 to -0.12 | 20 | -1.80 | -3.90 | -91.4 / -94.6 | 29% |
| 7d | a_time | 100 | +0.67 | -1.45 | -0.01 | -3.21 to +3.51 | -0.16 | -3.36 to +3.35 | 44 | +0.52 | -4.49 | -73.2 / -76.9 | 79% |
| 7d | b_close_stop | 331 | -0.04 | -0.83 | -0.72 | -1.36 to +0.10 | -0.87 | -1.51 to -0.05 | 5 | -1.28 | -2.56 | -94.8 / -96.8 | 25% |
| 7d | c_wick_stop | 371 | -0.03 | -0.54 | -0.71 | -1.13 to -0.20 | -0.86 | -1.28 to -0.35 | 3 | -1.98 | -2.12 | -94.9 / -97.1 | 20% |
| 7d | d1_wick_inv_minus_1atr | 290 | -0.01 | -1.28 | -0.69 | -1.48 to +0.33 | -0.84 | -1.63 to +0.18 | 6 | -1.35 | -3.12 | -93.1 / -95.5 | 31% |
| 7d | d2_wick_inv_minus_2atr | 236 | +0.13 | -2.06 | -0.55 | -1.64 to +0.75 | -0.70 | -1.79 to +0.60 | 11 | +0.60 | -4.14 | -88.0 / -91.6 | 43% |
| 7d | e_trail_10atr | 291 | -0.06 | -1.78 | -0.74 | -1.36 to -0.09 | -0.89 | -1.50 to -0.24 | 20 | -1.70 | -3.83 | -92.4 / -95.1 | 31% |
| 14d | a_time | 57 | +0.62 | +1.00 | -0.07 | -5.26 to +5.42 | -0.22 | -5.40 to +5.26 | 51 | +0.15 | +0.15 | -69.8 / -72.3 | 90% |
| 14d | b_close_stop | 296 | +0.08 | -0.82 | -0.60 | -1.39 to +0.42 | -0.75 | -1.54 to +0.27 | 3 | -1.99 | -2.56 | -91.4 / -94.5 | 32% |
| 14d | c_wick_stop | 340 | +0.15 | -0.53 | -0.53 | -1.16 to +0.30 | -0.68 | -1.31 to +0.15 | 3 | -1.61 | -2.10 | -90.9 / -94.5 | 26% |
| 14d | d1_wick_inv_minus_1atr | 248 | +0.12 | -1.30 | -0.56 | -1.56 to +0.69 | -0.71 | -1.70 to +0.54 | 5 | -2.15 | -3.10 | -88.2 / -91.9 | 39% |
| 14d | d2_wick_inv_minus_2atr | 193 | +0.02 | -2.10 | -0.67 | -2.04 to +1.00 | -0.82 | -2.18 to +0.85 | 8 | -0.78 | -4.07 | -88.0 / -91.0 | 52% |
| 14d | e_trail_10atr | 287 | -0.05 | -1.82 | -0.73 | -1.36 to -0.04 | -0.88 | -1.51 to -0.19 | 20 | -1.50 | -3.85 | -92.1 / -94.9 | 32% |
| 30d | a_time | 28 | -0.87 | -9.32 | -1.55 | -11.18 to +9.10 | -1.69 | -11.32 to +8.93 | 36 | +23.95 | -15.29 | -77.3 / -78.3 | 95% |
| 30d | b_close_stop | 249 | -0.06 | -0.83 | -0.74 | -1.69 to +0.55 | -0.89 | -1.84 to +0.40 | 3 | -2.39 | -2.63 | -92.2 / -94.6 | 43% |
| 30d | c_wick_stop | 302 | +0.12 | -0.52 | -0.56 | -1.35 to +0.48 | -0.71 | -1.50 to +0.33 | 2 | -1.90 | -2.12 | -90.7 / -94.1 | 35% |
| 30d | d1_wick_inv_minus_1atr | 207 | -0.11 | -1.29 | -0.79 | -1.96 to +0.75 | -0.94 | -2.11 to +0.60 | 3 | -2.82 | -3.19 | -90.3 / -92.9 | 50% |
| 30d | d2_wick_inv_minus_2atr | 148 | +0.54 | -2.10 | -0.15 | -2.16 to +2.34 | -0.30 | -2.31 to +2.19 | 6 | +0.42 | -4.29 | -70.6 / -76.4 | 63% |
| 30d | e_trail_10atr | 287 | -0.05 | -1.82 | -0.73 | -1.36 to -0.04 | -0.88 | -1.51 to -0.19 | 20 | -1.50 | -3.85 | -92.1 / -94.9 | 32% |

### A4. Matched random baseline (20,000 random entries with v1-like stop distances)

| Horizon | Policy | n | mean gross % | median gross % | mean net % M/T | 95% CI M/T | mean net % T/T | 95% CI T/T | win % M/T | mean R | median R |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1h | a_time | 20000 | -0.00 | -0.02 | -0.68 | -0.70 to -0.66 | -0.83 | -0.85 to -0.81 | 22 | -1.56 | -0.94 |
| 1h | b_close_stop | 20000 | -0.01 | -0.10 | -0.70 | -0.71 to -0.68 | -0.84 | -0.86 to -0.82 | 19 | -1.59 | -1.11 |
| 1h | c_wick_stop | 20000 | -0.02 | -0.05 | -0.70 | -0.71 to -0.68 | -0.85 | -0.86 to -0.83 | 14 | -1.60 | -1.28 |
| 1h | d1_wick_inv_minus_1atr | 20000 | -0.02 | -0.07 | -0.70 | -0.72 to -0.68 | -0.85 | -0.87 to -0.83 | 19 | -1.62 | -1.08 |
| 1h | d2_wick_inv_minus_2atr | 20000 | -0.01 | -0.04 | -0.70 | -0.72 to -0.67 | -0.84 | -0.86 to -0.82 | 21 | -1.61 | -0.99 |
| 1h | e_trail_10atr | 20000 | -0.01 | -0.04 | -0.70 | -0.72 to -0.67 | -0.84 | -0.86 to -0.82 | 21 | -1.60 | -0.99 |
| 4h | a_time | 20000 | +0.03 | -0.04 | -0.65 | -0.72 to -0.58 | -0.80 | -0.87 to -0.73 | 33 | -1.44 | -0.93 |
| 4h | b_close_stop | 20000 | -0.02 | -0.39 | -0.70 | -0.75 to -0.66 | -0.85 | -0.89 to -0.81 | 23 | -1.59 | -1.68 |
| 4h | c_wick_stop | 20000 | -0.02 | -0.25 | -0.70 | -0.74 to -0.67 | -0.85 | -0.89 to -0.82 | 17 | -1.59 | -1.64 |
| 4h | d1_wick_inv_minus_1atr | 20000 | -0.03 | -0.35 | -0.71 | -0.76 to -0.66 | -0.86 | -0.90 to -0.81 | 26 | -1.60 | -1.61 |
| 4h | d2_wick_inv_minus_2atr | 20000 | -0.02 | -0.25 | -0.71 | -0.76 to -0.65 | -0.85 | -0.91 to -0.80 | 30 | -1.60 | -1.23 |
| 4h | e_trail_10atr | 20000 | -0.02 | -0.25 | -0.71 | -0.76 to -0.65 | -0.85 | -0.91 to -0.80 | 30 | -1.60 | -1.23 |
| 12h | a_time | 20000 | +0.07 | -0.16 | -0.61 | -0.80 to -0.42 | -0.76 | -0.94 to -0.57 | 39 | -1.44 | -1.05 |
| 12h | b_close_stop | 20000 | -0.02 | -0.64 | -0.70 | -0.79 to -0.61 | -0.85 | -0.94 to -0.76 | 19 | -1.58 | -2.06 |
| 12h | c_wick_stop | 20000 | -0.01 | -0.42 | -0.69 | -0.76 to -0.62 | -0.84 | -0.91 to -0.77 | 14 | -1.58 | -1.84 |
| 12h | d1_wick_inv_minus_1atr | 20000 | -0.04 | -0.86 | -0.72 | -0.82 to -0.61 | -0.87 | -0.97 to -0.76 | 24 | -1.59 | -2.44 |
| 12h | d2_wick_inv_minus_2atr | 20000 | -0.05 | -0.94 | -0.73 | -0.86 to -0.60 | -0.88 | -1.01 to -0.75 | 30 | -1.63 | -2.46 |
| 12h | e_trail_10atr | 20000 | -0.06 | -0.91 | -0.74 | -0.86 to -0.61 | -0.89 | -1.01 to -0.76 | 30 | -1.67 | -2.42 |
| 1d | a_time | 20000 | +0.10 | -0.40 | -0.58 | -0.90 to -0.26 | -0.73 | -1.04 to -0.40 | 40 | -1.49 | -1.41 |
| 1d | b_close_stop | 20000 | +0.03 | -0.75 | -0.65 | -0.78 to -0.52 | -0.80 | -0.92 to -0.67 | 16 | -1.50 | -2.21 |
| 1d | c_wick_stop | 20000 | +0.02 | -0.50 | -0.66 | -0.75 to -0.56 | -0.81 | -0.90 to -0.71 | 11 | -1.54 | -1.91 |
| 1d | d1_wick_inv_minus_1atr | 20000 | -0.00 | -1.09 | -0.68 | -0.83 to -0.52 | -0.83 | -0.98 to -0.67 | 21 | -1.51 | -2.75 |
| 1d | d2_wick_inv_minus_2atr | 20000 | -0.02 | -1.47 | -0.70 | -0.89 to -0.49 | -0.85 | -1.04 to -0.64 | 27 | -1.56 | -3.28 |
| 1d | e_trail_10atr | 20000 | -0.03 | -1.41 | -0.71 | -0.90 to -0.52 | -0.86 | -1.05 to -0.67 | 27 | -1.64 | -3.18 |
| 3d | a_time | 20000 | +0.25 | -0.98 | -0.44 | -1.07 to +0.21 | -0.58 | -1.22 to +0.06 | 42 | -1.05 | -2.09 |
| 3d | b_close_stop | 20000 | +0.10 | -0.87 | -0.58 | -0.77 to -0.37 | -0.73 | -0.92 to -0.52 | 10 | -1.41 | -2.35 |
| 3d | c_wick_stop | 20000 | +0.08 | -0.58 | -0.60 | -0.75 to -0.45 | -0.75 | -0.89 to -0.60 | 7 | -1.41 | -1.98 |
| 3d | d1_wick_inv_minus_1atr | 20000 | +0.11 | -1.32 | -0.57 | -0.81 to -0.31 | -0.72 | -0.96 to -0.46 | 14 | -1.33 | -3.00 |
| 3d | d2_wick_inv_minus_2atr | 20000 | +0.12 | -1.95 | -0.56 | -0.88 to -0.22 | -0.71 | -1.02 to -0.37 | 19 | -1.23 | -3.87 |
| 3d | e_trail_10atr | 20000 | -0.02 | -1.81 | -0.70 | -0.96 to -0.42 | -0.85 | -1.11 to -0.57 | 22 | -1.57 | -3.66 |
| 7d | a_time | 20000 | +0.45 | -1.20 | -0.24 | -1.22 to +0.79 | -0.38 | -1.37 to +0.64 | 44 | -0.64 | -2.32 |
| 7d | b_close_stop | 20000 | +0.12 | -0.94 | -0.57 | -0.82 to -0.29 | -0.72 | -0.97 to -0.44 | 7 | -1.36 | -2.42 |
| 7d | c_wick_stop | 20000 | +0.12 | -0.63 | -0.56 | -0.75 to -0.35 | -0.71 | -0.90 to -0.49 | 5 | -1.30 | -2.01 |
| 7d | d1_wick_inv_minus_1atr | 20000 | +0.19 | -1.42 | -0.50 | -0.82 to -0.14 | -0.64 | -0.97 to -0.29 | 9 | -1.15 | -3.13 |
| 7d | d2_wick_inv_minus_2atr | 20000 | +0.24 | -2.13 | -0.45 | -0.88 to +0.02 | -0.60 | -1.03 to -0.13 | 14 | -1.04 | -4.17 |
| 7d | e_trail_10atr | 20000 | +0.04 | -1.89 | -0.64 | -0.93 to -0.33 | -0.79 | -1.08 to -0.48 | 20 | -1.46 | -3.75 |
| 14d | a_time | 20000 | +1.08 | -2.97 | +0.39 | -1.03 to +1.85 | +0.24 | -1.17 to +1.69 | 43 | +0.96 | -4.46 |
| 14d | b_close_stop | 20000 | +0.21 | -0.97 | -0.47 | -0.79 to -0.13 | -0.62 | -0.94 to -0.28 | 5 | -1.19 | -2.45 |
| 14d | c_wick_stop | 20000 | +0.17 | -0.65 | -0.51 | -0.73 to -0.25 | -0.66 | -0.88 to -0.40 | 3 | -1.23 | -2.03 |
| 14d | d1_wick_inv_minus_1atr | 20000 | +0.39 | -1.46 | -0.30 | -0.73 to +0.17 | -0.45 | -0.87 to +0.02 | 7 | -0.80 | -3.18 |
| 14d | d2_wick_inv_minus_2atr | 20000 | +0.53 | -2.23 | -0.15 | -0.73 to +0.48 | -0.30 | -0.88 to +0.33 | 10 | -0.51 | -4.31 |
| 14d | e_trail_10atr | 20000 | +0.11 | -1.91 | -0.57 | -0.87 to -0.26 | -0.72 | -1.02 to -0.41 | 20 | -1.36 | -3.76 |
| 30d | a_time | 19999 | +2.47 | -5.00 | +1.77 | -0.27 to +3.86 | +1.62 | -0.42 to +3.71 | 41 | +4.44 | -6.96 |
| 30d | b_close_stop | 19999 | -0.09 | -1.01 | -0.77 | -1.10 to -0.38 | -0.91 | -1.25 to -0.53 | 3 | -1.63 | -2.49 |
| 30d | c_wick_stop | 19999 | -0.04 | -0.68 | -0.72 | -0.96 to -0.45 | -0.87 | -1.11 to -0.59 | 2 | -1.50 | -2.05 |
| 30d | d1_wick_inv_minus_1atr | 19999 | +0.08 | -1.51 | -0.60 | -1.09 to -0.06 | -0.75 | -1.24 to -0.21 | 4 | -1.36 | -3.24 |
| 30d | d2_wick_inv_minus_2atr | 19999 | +0.17 | -2.32 | -0.52 | -1.21 to +0.25 | -0.67 | -1.36 to +0.10 | 6 | -1.14 | -4.43 |
| 30d | e_trail_10atr | 19999 | +0.12 | -1.91 | -0.57 | -0.87 to -0.25 | -0.71 | -1.02 to -0.40 | 19 | -1.36 | -3.76 |

### A5. Trend control and halves (H1 = signals before 2025-07-01)

- 1h: excess -0.029% CI -0.14 to +0.08 (n 543) | a_time: H1 -0.75 (n 275), H2 -0.66 (n 268); b_close_stop: H1 -0.71 (n 275), H2 -0.70 (n 268); c_wick_stop: H1 -0.70 (n 275), H2 -0.71 (n 268); d1_wick_inv_minus_1atr: H1 -0.70 (n 275), H2 -0.68 (n 268); d2_wick_inv_minus_2atr: H1 -0.73 (n 275), H2 -0.67 (n 268); e_trail_10atr: H1 -0.73 (n 275), H2 -0.67 (n 268)
- 4h: excess -0.044% CI -0.28 to +0.20 (n 543) | a_time: H1 -0.65 (n 275), H2 -0.75 (n 268); b_close_stop: H1 -0.74 (n 275), H2 -0.81 (n 268); c_wick_stop: H1 -0.78 (n 275), H2 -0.76 (n 268); d1_wick_inv_minus_1atr: H1 -0.77 (n 275), H2 -0.73 (n 268); d2_wick_inv_minus_2atr: H1 -0.66 (n 275), H2 -0.80 (n 268); e_trail_10atr: H1 -0.66 (n 275), H2 -0.78 (n 268)
- 12h: excess -0.252% CI -0.68 to +0.17 (n 543) | a_time: H1 -0.93 (n 275), H2 -0.75 (n 268); b_close_stop: H1 -0.80 (n 275), H2 -0.91 (n 268); c_wick_stop: H1 -0.74 (n 275), H2 -0.81 (n 268); d1_wick_inv_minus_1atr: H1 -0.90 (n 275), H2 -0.96 (n 268); d2_wick_inv_minus_2atr: H1 -1.03 (n 275), H2 -0.88 (n 268); e_trail_10atr: H1 -1.07 (n 275), H2 -0.81 (n 268)
- 1d: excess -0.183% CI -0.80 to +0.45 (n 543) | a_time: H1 -1.25 (n 275), H2 -0.11 (n 268); b_close_stop: H1 -0.86 (n 275), H2 -0.80 (n 268); c_wick_stop: H1 -0.71 (n 275), H2 -0.64 (n 268); d1_wick_inv_minus_1atr: H1 -1.05 (n 275), H2 -0.76 (n 268); d2_wick_inv_minus_2atr: H1 -1.27 (n 275), H2 -0.53 (n 268); e_trail_10atr: H1 -1.28 (n 275), H2 -0.49 (n 268)
- 3d: excess -0.123% CI -1.27 to +1.05 (n 543) | a_time: H1 -0.35 (n 275), H2 -0.27 (n 268); b_close_stop: H1 -0.23 (n 275), H2 -0.87 (n 268); c_wick_stop: H1 -0.27 (n 275), H2 -0.72 (n 268); d1_wick_inv_minus_1atr: H1 -0.42 (n 275), H2 -0.84 (n 268); d2_wick_inv_minus_2atr: H1 -0.58 (n 275), H2 -0.54 (n 268); e_trail_10atr: H1 -0.83 (n 275), H2 -0.54 (n 268)
- 7d: excess -1.020% CI -2.57 to +0.56 (n 543) | a_time: H1 -1.78 (n 275), H2 +0.70 (n 268); b_close_stop: H1 -0.36 (n 275), H2 -0.51 (n 268); c_wick_stop: H1 -0.52 (n 275), H2 -0.44 (n 268); d1_wick_inv_minus_1atr: H1 -0.59 (n 275), H2 -0.49 (n 268); d2_wick_inv_minus_2atr: H1 -0.80 (n 275), H2 -0.45 (n 268); e_trail_10atr: H1 -1.11 (n 275), H2 -0.68 (n 268)
- 14d: excess -1.251% CI -3.22 to +0.77 (n 543) | a_time: H1 -0.27 (n 275), H2 +0.98 (n 268); b_close_stop: H1 +0.09 (n 275), H2 -0.80 (n 268); c_wick_stop: H1 -0.15 (n 275), H2 -0.56 (n 268); d1_wick_inv_minus_1atr: H1 +0.04 (n 275), H2 -0.81 (n 268); d2_wick_inv_minus_2atr: H1 -0.32 (n 275), H2 -0.82 (n 268); e_trail_10atr: H1 -1.17 (n 275), H2 -0.57 (n 268)
- 30d: excess -1.858% CI -4.18 to +0.43 (n 543) | a_time: H1 +1.77 (n 275), H2 +0.17 (n 268); b_close_stop: H1 +0.15 (n 275), H2 -1.54 (n 268); c_wick_stop: H1 +0.04 (n 275), H2 -1.25 (n 268); d1_wick_inv_minus_1atr: H1 +0.12 (n 275), H2 -1.87 (n 268); d2_wick_inv_minus_2atr: H1 +0.21 (n 275), H2 -1.97 (n 268); e_trail_10atr: H1 -1.17 (n 275), H2 -0.57 (n 268)
