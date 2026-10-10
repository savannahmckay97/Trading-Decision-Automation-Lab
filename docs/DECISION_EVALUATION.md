# Decision evaluation contract — 0.2

This is a small research harness for the existing rule. It introduces no broker,
service, database, optimizer or alternative strategy framework.

## Question answered

On a specified frozen dataset and observation window, how often does TDAL select
a state, what market movement follows those states, and what movements follow
its abstentions compared with simple selection policies?

It cannot answer whether a trade was obtainable, whether net trading expectancy
is positive, whether a user made better decisions, or whether a customer wants
the product. Those need separate evidence.

## Inputs and binding

- Canonical normalized snapshot JSONL and its complete matching manifest.
- The frozen `ObserverConfig` (default TAO, 15m; recorded in the artifact).
- An experiment ID, declared exploratory/holdout partition, inclusive observation
  start/end timestamps and one explicit completed-bar horizon.

Rebuild and compare the manifest, use the shared runtime as-of availability validator,
recompute shared replay, then derive outcomes afterwards. A foreign replay file
cannot accidentally be joined to this dataset because it is not an input.
An input hash demonstrates identity, not that a provider made the values
available at the claimed time. Holdout status is self-declared and remains
unverified by the software. Preregister experiments before examining outcomes.

## Evaluation universe

Replay uses the whole chronological dataset to preserve causal pre-window state.
Only observations inside the stated window contribute evaluation opportunities.
The first gate-passing observation for each completed candle counts once.
Blocked cycles count towards coverage but do not enter the selection baselines.
The universe therefore measures behavior conditional on valid observed inputs;
it does not measure the market opportunities missed during outages.

Policies share this universe:

| Policy | Selection rule |
|---|---|
| TDAL | Existing evaluator emits `LONG_ELIGIBLE` |
| POSITIVE_PRIOR_RETURN | Existing completed 15m return is positive |
| SELECT_ALL | Select every valid completed bar |
| ABSTAIN_ALL | Select none; no invented zero-return samples |

These are decision-selection baselines, not executable trading strategies or
portfolio benchmarks. The momentum baseline already uses an existing feature;
there is no second breakout/retest implementation.

## Outputs

- Dataset hash/ID, replay ID, full observer configuration, evaluation design and
  a content-derived evaluation ID.
- Observation coverage, blocks, duplicate polls and available/unavailable/censored
  labels. Replay gap counts cover the full input, including warmup.
- For each policy: selections and labeled selections, selection fraction, mean
  and median forward close movement, worst forward adverse excursion, and
  positive/negative/flat outcome counts.
- Labeled abstentions: negative market moves avoided and positive moves missed.
  These are directional opportunity proxies, not avoided losing trades.
- TDAL selection mean minus the all-valid-bar mean, expressed in percentage
  points. Different selected exposure and dependent samples prevent treating
  this number as alpha or a causal estimate of decision benefit.
- For eligible decisions: separate future completed-close breaches and wick
  touches of the frozen breakout resistance minus failure-buffer ATR.

The state-machine predicate uses a close breach; the paper model uses a wick
stop. The harness reports both and does not silently equate them.

### Reward reference first touch (0.2)

For each TDAL selection, `reward_first_touch` records whether the future path
reaches the decision's reward reference (`risk.reward_reference.level`) or its
invalidation first. The result is one of `TARGET_FIRST`, `STOP_FIRST`,
`AMBIGUOUS_SAME_BAR` or `NEITHER_WITHIN_HORIZON`, plus the bar count. It is
labelled separately under two stop meanings:

- **Wick rule** (the low touches invalidation): if a bar reaches both, the
  intrabar order is unknowable from 15m bars and the result stays
  `AMBIGUOUS_SAME_BAR`. It is never resolved optimistically or pessimistically.
- **Close rule** (the close is below invalidation): the breach only happens at
  the close, so an intrabar target touch in the same bar came first.

Selections without a reward reference (`TARGET_UNAVAILABLE`), without a risk
contract, or with censored/unavailable paths are counted but never scored.
`tdal_reward_first_touch` reports, on the same decided cases, the target-first
fraction, the breakeven rate at the mean ratio `1 / (1 + mean r)` (the rate at
which a set of 1R-risk cases with payoffs rᵢ breaks even under one common hit
rate), and the realised gross R (`+rᵢ` per target-first, `−1` per stop-first).
Averaging `1/(1+rᵢ)` would overstate the threshold, because the function is
convex. Realised R does not assume the hit rate is independent of the ratio.
Gross R assumes exits exactly at the levels. This is gross price movement from
the completed close: a touch is not a fill, there are no costs, and it is
conditional on a target having existed. It is not expectancy.

Every future bar in the requested path must exist. Labels with a target beyond
the evaluation window are censored even if later snapshots contain them. Missing
outcomes never become zero; statistics with no labeled samples are null. Saved
artifacts are immutable, and all claims of validated performance, calibrated
probabilities and demonstrated decision improvement remain false.

## Known limits and advancement

Current returns start at the completed candle close, which may precede the
observation time. They are descriptive market movement. Costs and executable
returns are deliberately excluded until entry timing and venue evidence are
correct. Overlapping horizons are dependent; this release supplies no IID
confidence intervals, significance tests, annualized Sharpe or optimizer.

The shared as-of validator rejects future derivative rows, events and receipt timestamps and requires matching provenance. See `ASOF_CONTRACT.md` for required inputs and the scheduled open-candle exception.
It cannot prove 5m endpoint interval semantics, synchronization with the 15m
bar, publication delay, revisions or payload venue identity. Provider contract
validation is the next prerequisite for real evidence.

Use synthetic fixtures to verify plumbing. Use prospective as-of recordings to
investigate actual utility. Freeze a primary horizon and comparison before
evaluation, preserve all trials, use disjoint/purged time windows and add
dependence-aware uncertainty only when there is a meaningful sample. Keep the
output exploratory until execution truth and a user comparison support stronger
claims. See `PRODUCT_DIRECTION.md` for the separate process-benefit experiment.

