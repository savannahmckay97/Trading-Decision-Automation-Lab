# Preregistration: exploratory first-touch analysis on 29 days of TAOUSDT

Written and pushed **before** any forward outcome was computed. The git commit
containing this file timestamps the plan. Results go in a separate later
commit (`RESULTS.md`).

## Status

**Exploratory, not confirmatory.** The data is a reconstructed history, not
as-observed recordings (see `docs/EVALUATION_READINESS.md`). Confirmatory
evidence must come from prospective as-of recordings after issue #7 is fixed.

## Frozen inputs

- **Data:** the 29-day download made by
  `research/setup_frequency/fetch_binance_history.py` on 2026-10-11, held
  outside the repo. SHA-256:
  - `klines.json`  d328c851b118526a486bc629bb6ae60dff513ece725557075040f78113998f70
  - `oi_hist.json` 09ccfcb10def9195aa3262b4a764eb5c49dc06ae368cd515cba180e89e9fd7fa
  - `taker.json`   3ee7fbc9766a3313782bcf0c62542d2a1ad95141e650476937931641608defb1
  - `meta.json`    1a521456022adfdaa721d7c3595c5c2fdc1c8aecc7cc2965d922f02fdce253d5
- **Rule and configuration:** default `ObserverConfig` at this commit. Breakout
  and retest parameters are unchanged. The reward reference uses k = 2 and a
  144-bar search. No fee or slippage assumption is supplied, so only gross
  ratios are used.
- **Replay assumptions:** exactly those in `research/setup_frequency/history.py`.
- **Population:** every `LONG_ELIGIBLE` decision the replay produces (17 were
  counted before this plan; their outcomes are unseen).
- **Code:** `research/first_touch_exploratory/run.py` at this commit, using the
  harness's `_first_touch` and `_first_touch_summary` unchanged.

## Analysis

- **Primary horizon: 16 bars (4 h).** It is long enough for levels in a 36 h
  search to be reachable, and short relative to the setup's 6-bar expiry
  multiplied out. Secondary horizons: 4 bars (1 h) and 96 bars (24 h), reported
  alongside and not used to choose anything.
- **Primary stop rule: close** (`COMPLETED_CLOSE_BELOW`, the state machine's
  meaning). The wick rule is secondary.
- **Primary comparison:** on decided cases (`TARGET_FIRST` or `STOP_FIRST`),
  the target-first fraction against the mean breakeven rate `1/(1 + gross ratio)`.
- **Also reported:** every status count (`TARGET_UNAVAILABLE`, `NEITHER`,
  `AMBIGUOUS`, censored) and every per-decision row.

## Commitments

- **No parameter changes based on these results.** This covers breakout,
  retest, quality, reward k or window, and the horizon. Any later change must be
  justified independently and tested on new prospective data.
- **No claims of predictive value, profitability or calibration.** With about 9
  decided cases at most, any difference from breakeven is expected to be within
  noise. Results are reported as counts, without p-values.
- **Everything is published.** All horizons, both stop rules and every
  decision are reported, including unfavourable or empty results.
