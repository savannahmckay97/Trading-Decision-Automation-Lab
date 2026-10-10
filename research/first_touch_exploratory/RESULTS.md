# Results: exploratory first-touch analysis (2026-10-11)

Run exactly as preregistered in `PREREGISTRATION.md` (commit `8feefde`), with
the two post-registration corrections in §3. **Exploratory, n = 7–9.** Nothing
here is evidence of predictive value, and no parameter was or will be changed
because of it.

## 1. Population

17 `LONG_ELIGIBLE` decisions over 28.95 days. **8 had no reward reference**
(`TARGET_UNAVAILABLE`: no confirmed swing high above the entry within 36 h), so
only 9 are labelled. No decision was censored at any horizon.

## 2. Preregistered results (as specified at `8feefde`)

| Horizon | Stop rule | Target first | Stop first | Ambiguous | Neither | Target-first fraction of decided | Prereg threshold: mean of 1/(1+r) |
|---|---|---|---|---|---|---|---|
| **16 bars (primary)** | **close** | **3** | **4** | 0 | 2 | **3/7 = 0.43** | 0.51 |
| 16 bars | wick | 1 | 5 | 1 | 2 | 1/6 = 0.17 | 0.49 |
| 4 bars | close | 2 | 3 | 0 | 4 | 2/5 = 0.40 | 0.57 |
| 4 bars | wick | 1 | 5 | 1 | 2 | 1/6 = 0.17 | 0.49 |
| 96 bars | close | 3 | 6 | 0 | 0 | 3/9 = 0.33 | 0.46 |
| 96 bars | wick | 1 | 7 | 1 | 0 | 1/8 = 0.13 | 0.43 |

## 3. Post-registration corrections (added after the results; flagged as such)

**(a) The preregistered threshold was biased.** A peer review of PR #8 pointed
out that, for decisions risking 1R each with different payoffs rᵢ, a common
hit rate *p* breaks even at `p = 1 / (1 + mean r)`. The averaged
`mean(1/(1+rᵢ))` is always at least that (Jensen; the function is convex), so
the preregistered threshold was too strict and leaned towards "the ratio
overstates the opportunity". The harness now reports the corrected threshold
and the realised gross R per case (`+rᵢ` if the target came first, `−1` if
the stop did):

| Horizon | Stop rule | Target-first fraction | Breakeven at mean ratio | Realised gross R (sum / mean) |
|---|---|---|---|---|
| **16 (primary)** | **close** | **0.43** | **0.40** | **−2.35R / −0.34R** |
| 16 | wick | 0.17 | 0.37 | −4.68R / −0.78R |
| 4 | close | 0.40 | 0.49 | −2.16R / −0.43R |
| 96 | close | 0.33 | 0.36 | −4.35R / −0.48R |

On the primary measure, the target-first rate sits slightly *above* the
corrected breakeven, yet realised gross R is negative. Both statements are
true because the three targets reached had small ratios (0.81, 0.32, 0.52),
while two of the four stops had the largest (4.63, 2.91). The common-*p*
threshold assumes the hit rate does not depend on the ratio. Realised R does
not assume that, so it is the more direct per-case number. With 7 cases the
apparent "large ratios fail" pattern is indistinguishable from chance and must
not be acted on.

**(b) The reconstructed decision time was too early.** `history.py` placed
decisions 180 s after the close, but the slowest taker publication observed
live was 194 s. It now uses 240 s. This changes no computed value (same
candles, same buckets), and every per-decision label was re-verified as
identical.

**(c) Wording slip.** The preregistration's rationale for the 16-bar horizon
contains a garbled phrase ("short relative to the setup's 6-bar expiry
multiplied out"). The choice itself is unchanged: 4 h was picked a priori as
a middle horizon between 1 h and 24 h.

## 4. Per-decision record (16-bar horizon; bars to event in brackets)

| Close (UTC 2026) | Gross ratio | Close rule | Wick rule |
|---|---|---|---|
| 09-12 10:30 | 0.81 | TARGET_FIRST (6) | STOP_FIRST (2) |
| 09-13 06:30 | — | TARGET_UNAVAILABLE | — |
| 09-18 00:15 | 1.00 | STOP_FIRST (1) | STOP_FIRST (1) |
| 09-18 08:30 | — | TARGET_UNAVAILABLE | — |
| 09-19 03:15 | — | TARGET_UNAVAILABLE | — |
| 09-25 11:30 | — | TARGET_UNAVAILABLE | — |
| 09-25 23:45 | — | TARGET_UNAVAILABLE | — |
| 09-26 11:45 | — | TARGET_UNAVAILABLE | — |
| 09-27 06:30 | 2.99 | NEITHER | NEITHER |
| 09-27 07:30 | 4.63 | STOP_FIRST (7) | STOP_FIRST (1) |
| 10-02 06:00 | 0.32 | TARGET_FIRST (1) | TARGET_FIRST (1) |
| 10-03 23:15 | 2.48 | NEITHER | NEITHER |
| 10-05 21:00 | 0.52 | TARGET_FIRST (1) | AMBIGUOUS (1) |
| 10-06 15:45 | 0.45 | STOP_FIRST (1) | STOP_FIRST (1) |
| 10-09 05:45 | 2.91 | STOP_FIRST (1) | STOP_FIRST (1) |
| 10-10 05:30 | — | TARGET_UNAVAILABLE | — |
| 10-10 07:15 | — | TARGET_UNAVAILABLE | — |

The 09-27 pair (one hour apart) and the 10-10 pair overlap, so they are not
independent.

## 5. Uncertainty

3 of 7 has a 95% Wilson interval of roughly 16%–75%. Every comparison above
lies well inside the noise. These counts cannot distinguish a useful setup from
a useless one, which was expected before running (`EVALUATION_READINESS.md` §3).

## 6. Limitations specific to these numbers

- **Bar 1 starts before the decision.** Many events happen on the first bar,
  whose range includes the ~4 minutes before a real decision could exist. A
  bar-1 touch may therefore be unreachable. This is a known harness limitation
  ("returns start at the completed close"). It matters more here because 11 of
  the 14 touches (two rules × 9 decisions, minus 4 `NEITHER`) occurred on
  bar 1.
- **Gross R assumes exits exactly at the levels.** A close-rule stop exits at a
  close below the level, which is worse than −1R. Gaps are ignored. There are
  no costs.
- The data is reconstructed history with the assumptions in `history.py`.

## 7. What it does suggest, structurally

These points come from geometry measured independently of outcomes, not from
the outcome counts.

- **The setup invalidation is not a usable protective stop under the wick
  rule.** Stop distances were a median 0.94 ATR from entry (range 0.30–2.62;
  five under 0.6 ATR). A 15-minute bar typically spans about 1 ATR, so an
  intrabar wick through the invalidation is expected noise. That is consistent
  with 4 of 5 wick stop-outs at 16 bars happening on the very next bar.
  `DECISION_RISK.md` already separates setup failure (close) from a protective
  stop. For the RiskGovernor this means a protective stop needs its own
  definition, which should be designed on first principles and validated on new
  data, **not fitted to these 9 cases.**
- **Target coverage is the larger practical gap.** 8 of 17 setups had no
  reference at all.
