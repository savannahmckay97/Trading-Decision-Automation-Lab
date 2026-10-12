# Redesign brief: from one rule to a layered, hypothesis-driven observer

For Codex (and any agent) picking up the decision-logic redesign. Written 2026-10-13
after the historical replay (PR #21). Read `AGENTS.md` and `CLAUDE.md` first; every rule
there still applies.

## 1. Why a redesign

PR #21 replayed the current rule, BREAKOUT_RETEST_V1, over 2.5 years of Binance's public
archive for TAO (543 setups, preregistered). Under realistic exits it has **no edge**:

- Exiting at the close that breaks invalidation: −0.02R per setup (stops average −1.97R).
- Wick stop: −0.01R. Signals pick no better 4-hour moves than random candles.
- Stops sit a median 0.65% from entry, and a Kraken Tier 4 round trip costs about 0.68%,
  so **costs alone are about 1R per trade**. Half the reached targets were under 0.5R away.

Diagnosis of the current design:

- **One pattern decides.** A 15m breakout above recent resistance, then a retest.
- **Most collected data is ignored.** `features.py` computes funding rate, funding
  percentile and three long/short positioning shares; `engine.py` never reads them.
  Volume, OI change and taker flow only feed a coarse −5..+5 gate.
- **No context.** No higher timeframe, no other asset (not even BTC), no market regime,
  no calendar or cycle, no short side.
- **Trade geometry ignores costs.** Stops and targets are too close for fees to be small.

## 2. What the owner wants (the target behaviour)

The observer should take a proper observation of everything available, then reason in
layers, the way a careful discretionary trader would, but recorded and testable:

- Notice patterns, repeated movements, synchronicity between assets and timeframes, and
  behavioural cues (positioning extremes, funding stress, flow imbalances, volume shifts).
- Use technical-analysis principles (structure, trend, levels, momentum, volatility) as
  inputs, not as the whole decision.
- **Several independent layers either validate or invalidate a decision.** A setup in one
  layer is a candidate; other layers confirm, veto, or size it down. WAIT is a normal answer.
- Form **hypotheses**, make educated guesses, and keep score. A hypothesis that turns out
  wrong is not deleted on the spot: it is revised into a new version and kept being tested
  on candles that come *after* the revision.
- Cover more than TAO. Add **BTC** first (strongly cyclical, and it drives the rest of the
  market), then others. Market-wide context matters: BTC's cycle and halving phase,
  alt-season vs BTC-led phases, seasonal tendencies.

The owner may add an earlier conversation that sets out the layers in more detail.
**Section 7 is reserved for it**; where it conflicts with this brief, it wins.

## 3. Non-negotiable requirements

1. **Causality.** Every input is as-of decision time. Higher-timeframe candles count only
   once closed. Cross-asset inputs are aligned on the same as-of clock.
2. **Hypothesis ledger with versioning, no look-ahead.** Each hypothesis is an explicit,
   machine-evaluable rule with an ID, version, creation time, and a prediction it makes
   (direction, horizon, invalidation, target). Predictions are written **before** their
   outcome exists. A revision creates a new version, effective only for candles closing
   after the revision time. Old versions keep their record and are never rescored with
   hindsight. Every hypothesis ever tried is listed, including discarded ones.
3. **Guard against false discoveries.** A system that watches many patterns will find
   some that "worked" by chance. Track how many hypotheses were tried; judge each on data
   after its creation (walk-forward), require a minimum sample before promoting one, and
   correct for the number tried. A promoted hypothesis is then preregistered and confirmed
   prospectively, as in `research/prospective_v1/`.
4. **Costs first.** Expected move and stop distance must make round-trip costs small
   (proposal: costs ≤ 0.25R at the owner's tier), or the answer is WAIT. Evaluate with
   realistic exits (close-exit R, wick stops), never a −1R floor.
5. **Long and short**, plus WAIT. Observation only: no order route, no credentials.
6. **Fail closed.** A missing or stale layer blocks or downgrades; it is never imputed.

## 4. Candidate layers (each emits evidence: supports, opposes, or unknown)

| Layer | Inputs (already collected unless marked) |
|---|---|
| Structure and trend | 15m/1h/4h/1d candles: swing structure, levels, trend, momentum (1h+ is new) |
| Volatility regime | ATR percentile, range expansion or compression |
| Derivatives positioning | funding rate and percentile, OI change, global/top long-short shares |
| Flow | taker buy share, volume percentile |
| Cross-asset | BTC (and later ETH) trend and lead-lag, synchrony across assets (new) |
| Market cycle and calendar | halving phase, BTC dominance or alt/BTC strength (new source needed), month and weekday |
| Execution quality | Kraken spread, cross-venue divergence, data freshness (existing gates) |

## 5. Honest cautions

- **Cycles are few.** BTC has had about four halving cycles. That's enough to use as
  context, far too few to prove a cycle "works like clockwork". Weight it accordingly.
- **Recurrence can be hindsight.** TAO was chosen for behaving in a recurring way; the
  current rule tried to exploit that and the archive didn't support it.
- **What has been seen, and what hasn't.** TAO 2024-04..2026-09 was scored for v1 only.
  That tells us one rule fails; it says nothing about which other patterns work, so the
  history stays usable for new rules provided nobody designs by studying TAO's past price
  action and tuning to fit it. Don't delete the v1 results: they are the audit trail.
- **Lock a test period.** Design and tune on TAO up to 2025-09-30. Keep 2025-10-01 to
  2026-09-30 locked: no new idea is scored on it during design. A finished, written-down
  rule gets one run on it, then prospective confirmation on the recorder.

## 6. Suggested sequence

1. Review the repo and add explanatory comments (owner request); no behaviour change.
2. Multi-asset configuration; fetch the BTCUSDT archive with
   `research/historical_archive/fetch_archive.py` (PR #21) and check fidelity the same way.
3. A layer framework: each layer is a pure function of an as-of snapshot returning
   evidence with provenance; the decision combines layers explicitly and records why.
4. The hypothesis ledger and a walk-forward evaluator over the archive, with the test
   period in §5 (2025-10-01..2026-09-30) locked before any hypothesis is scored on it.
5. First hypotheses, written down before scoring. Then preregister the survivors and run
   them prospectively on the recorder.

## 7. Owner's layer design (to be added)

_Reserved for the owner's earlier discussion of decision layers._
