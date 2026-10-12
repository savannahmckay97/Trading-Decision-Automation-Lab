# Preregistration: historical replay of BREAKOUT_RETEST_V1 on TAO, 2024-04 to 2026-09

**FROZEN 2026-10-13.** The owner confirmed every **[OWNER]** default on 2026-10-13.
This commit freezes the plan and is pushed before any historical outcome is computed.

## 1. Purpose and standing

The prospective study (`research/prospective_v1/`) needs about a year to reach its
sample. This study asks the same question of the past: on 2.5 years of Binance's
public archive, replayed candle by candle through the production rule, does
BREAKOUT_RETEST_V1 show a gross edge?

**It's retrospective evidence, not confirmation.** The rule's parameters were fixed
before this data was downloaded and aren't tuned here, but nobody can show that
whoever chose them never looked at TAO charts from this period. A positive result
makes the prospective study worth running to its end; it doesn't replace it. A
negative or inconclusive result is reported just as fully.

## 2. Frozen inputs

- **Data:** the archive described in `FIDELITY.md` (manifest SHA-256
  `bfc2ae4a9968c4872650877d8949e973327b7c8c1e91601ef1b78c55923da00c`), loaded by
  `archive.py` at this commit. `FIDELITY.md` shows it reproduces the live API's
  decisions exactly over the one month where both exist.
- **Rule:** `ObserverConfig` defaults at 76dbb5d, unchanged.
- **Replay:** `replay_archive.py` at this commit: the assumptions of
  `research/setup_frequency/history.py` (4-minute decision delay, fixed 3.35 bps
  Kraken spread, zero cross-venue divergence, setup reset on missing inputs).
- **Window:** candles closing **2024-04-12 to 2026-09-10T23:59:59Z**.
  2026-09-11 onwards is excluded: the exploratory study
  (`research/first_touch_exploratory/`) already examined outcomes there.
  **[OWNER]**
- **Population:** every `LONG_ELIGIBLE` decision the replay makes in the window.
  The count, read before this plan, is **543** on 364 UTC days (275 before
  2025-07-01). Outcomes are unseen.
- **Outcome prices:** archive 15m candles (Binance USD-M TAOUSDT).

## 3. Analyses

Identical to `research/prospective_v1/PREREGISTRATION.md` §4 so the two studies can
be compared, with these choices:

### Primary
- **Outcome:** reward first touch, close rule (`COMPLETED_CLOSE_BELOW`), 16-bar
  (4 h) horizon, the harness's `_first_touch` unchanged.
- **Statistic:** mean realised gross R per decided case (`+rᵢ` for `TARGET_FIRST`,
  `−1` for `STOP_FIRST`).
- **Uncertainty:** 95% percentile bootstrap resampling UTC days, 10,000 draws,
  seed 20261013.
- **Reading, fixed in advance:** lower bound > 0, a gross edge in the past;
  upper bound < 0, evidence against the rule as specified; otherwise inconclusive.
  The analysis is not rerun with different settings.

### Co-reported and secondary (none used to choose anything)
- Target-first fraction with Wilson 95% interval against the breakeven rate; every
  status count, including `TARGET_UNAVAILABLE` and censored.
- 4- and 96-bar horizons; the wick rule; close-exit R; bar-1 sensitivity; the
  `SELECT_ALL` and `POSITIVE_PRIOR_RETURN` baselines.
- **Stability:** the primary statistic separately for 2024-04-12..2025-06-30 and
  2025-07-01..2026-09-10, and by calendar quarter. Reported, not used for the
  reading; a result carried by one stretch is described as such.
- **Paper ledger** (RiskGovernor, PR #11, if merged before the run; it is an open
  draft at freeze, so this run omits it; a later ledger run is reported as a separate analysis): one $1,000
  account, Kraken Tier 4 (taker 35, maker 20 bps), slippage 5 bps, the assumed
  3.35 bps spread; Tier 1 (80/40) sensitivity. **[OWNER]**

## 4. Limitations, stated in advance

- Kraken spread, cross-venue divergence and data-freshness gates can't be
  reconstructed; the replay assumes the observer would have passed them.
- Execution is assumed on Kraken TAOUSD; whether Kraken listed TAO for the whole
  window is not checked here and is reported with the results.
- Outcome prices are Binance candles; Kraken prices differ by about 10 bps.
- Retrospective, as in §1.

## 5. Commitments

- No parameter changes based on these results. A changed rule is a new
  experiment, tested prospectively or on data it was not designed on.
- Every result is published, including unfavourable or empty ones.
- **Amendments before the run** are allowed on the record: each is its own commit
  stating what changed, why, the date, and that no outcome had been examined.
