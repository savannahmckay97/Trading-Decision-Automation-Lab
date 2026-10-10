# TDAL: purpose, product evidence and development direction

Investigation date: 9 October 2026. Authoritative inspected baseline:
`043c50d8bc1316eb3f0e06efd704de64dcc5602c` (22 commits).
This document distinguishes verified repository facts, inference and proposed
validation. It supersedes the README's previous RiskGovernor-first sequencing;
it does not replace the historical doctrine or merge another project.

Follow-up, 10 October 2026 (Melbourne): the shared as-of guard now closes the detectable future-row/receipt runtime failure described below; see `ASOF_CONTRACT.md`. The investigation below records the original baseline and evaluation-first intervention. Provider semantics, synchronized windows, live gap handling and product utility remain unverified.

## 1. Executive finding

**TDAL is a deterministic observation/research prototype for one long breakout
and retest setup, with replay, market labels and hypothetical fills. It is not
yet a validated trading-decision product.** It is worth continuing as a bounded
experiment. The current broad product ambition has not earned implementation.

It is converging technically: shared evaluation, causal candle calculations,
explicit missingness and post-hoc outcomes are sensible foundations. It is not
yet converging empirically: the repository contains synthetic behavior tests,
not demonstrated decision improvement, a real validated data sample or user
adoption evidence. A reproducible wrong rule is still a wrong rule.

The feared evidence-to-decision gap is **partial, not absolute**. `engine.py`
already turns features into a state and long eligibility. The weaker transition
is from that eligibility to a defensible, useful user decision and then to
measured learning. More indicators will not establish that transition.

The immediate intervention adds a small descriptive evaluation path and repairs
saved-input replay. Its outputs begin testing the existing rule; they do not
establish trade profitability, user benefit or market demand.

## 2. Purpose and reconstructed objectives

First user: Savannah, making discretionary crypto decisions with fragmented
exchange/chart information and an AI conversation. This comes from the task's
stated intent and project context; no repository user study verifies it.

Primary outcome: make a bounded trade-or-wait decision quicker, consistently and
with an inspectable case, objections, invalidation and missing information.
The expensive problem is acting on an inconsistent or incomplete interpretation,
then being unable to reconstruct what justified the decision. Preventing this
can have value without claiming improved market prediction.

| Objective | Evidence and implication |
|---|---|
| Primary user outcome | Doctrine sections 1/9 promise inspectable decisions. No usability or comparative benefit result exists. |
| Supporting technical | Normalization, Decimal arithmetic, gates, persistence and shared evaluator support reproducibility. Keep them small and improve actual contracts. |
| Research | Dataset, replay, outcomes and paper modules support falsifiable experiments. Results still need real causal input and baselines. |
| Automation | CLI polling and persistent state automate observation. Live execution is deliberately impossible. Continuous polling is a means, not success. |
| Possible product/commercial | A reusable evidence/decision record and decision-quality review workflow fit the existing capabilities. Demand and willingness to pay are untested. |
| Accidental objective | Implementing the doctrine's entire hierarchy, growing the rule registry, or completing a trading stack before demonstrating decision utility. Stop treating these as obligations. |

Competing definitions exist: the doctrine describes broad state classification,
long/short ranking, risk and eventual execution; the runnable system implements
one long setup; the README previously put account-risk machinery before
benchmark evidence. Resolve this by treating the doctrine as a research map,
the setup as an experiment, and the evidence-backed decision record as the
near-term product hypothesis. Reproducibility is necessary and largely tested;
it is an insufficient definition of useful or commercially successful.

TDAL should not become a universal feed aggregator, exchange terminal, indicator
collection, generic AI analyst, or execution bot before its specific decision
workflow has demonstrated value. Preserve observation acquisition as a
dependency boundary. The local `market_observer` package is TDAL's current
implementation compartment; its name alone is not evidence of merger with an
external Market Observer project. Renaming it now would create churn.

## 3. Verified current state

| Status | Present reality | Evidence |
|---|---|---|
| IMPLEMENTED | Twelve public REST requests, provenance records, normalization, features, spread/divergence gates, breakout/retest state, SQLite journal, CLI polling, backup/restore | `collectors.py`, `normalization.py`, `features.py`, `engine.py`, `storage.py`, `service.py`, `cli.py` |
| IMPLEMENTED | Shared evaluator; chronological replay; dataset fingerprints; forward labels; independent one-unit paper scenarios | `evaluator.py`, `replay.py`, `dataset.py`, `outcomes.py`, `paper.py` |
| PARTIAL | Evidence explanation is structured feature/gate/score JSON. Invalidation is implicit in setup fields, not a complete user decision contract. No opposing hypothesis/ranking. | `service._decision`, `ReplayRunner._decision`, `advance_breakout_retest` |
| PARTIAL | Causal candle calculations and chronology exist; derivative as-of validity, synchronized intervals and provider identity/publication semantics are not fully enforced. | `_completed_candles`, `_change_over`, `_taker_window`, `assess_gates` |
| SPECIFIED BUT NOT IMPLEMENTED | Account risk/heat/daily breaker, event blackout, opposing setups, regime probabilities, news/macro/correlations, depth sequencing, risk governor and order routing | Doctrine; rule catalogue versus runtime modules |
| EXPERIMENTAL | Breakout score/thresholds, setup predictive premise, annotations, forward labels and paper fill assumptions | Score says `EXPERIMENTAL_UNCALIBRATED`; artifacts deny validation/performance claims |
| STALE/OBSOLETE | Old implementation map said labels/fills were absent; TEST_RESULTS said 18 tests; doctrine section 12 still calls the existing YAML files the next artifact. Release history did not record later compartments. | Historical docs inspected before this change |

The inspected baseline's 39 tests passed locally. GitHub Actions run
`36647903497` passed install/tests/spec validation on Python 3.10 and 3.12.
CI tests deterministic code; it does not validate live providers, actual user
benefit, executable fills, untouched holdouts or research significance. YAML
validation checks structural references, not parity with Python rule behavior.

Issue/PR history: the all-state issues collection returned one merged PR
(`#1`, immutable datasets) and no standalone open/closed issues. No tracked
product experiment or user evaluation was found there. The attached archive is
an older source snapshot; matching YAML and validator were verified against the
repo. The attachment doctrine differs in Markdown escaping/presentation, so
the repository text remained authoritative.

## 4. Strategic diagnosis and concrete correctness limits

Increasing the chance of success: one asset/setup, no order path, shared
live/replay rules, explicit provenance/missingness, immutable research artifacts,
and willingness in the doctrine to retire failed rules. These choices preserve
the ability to investigate rather than forcing a complete platform.

Derailment risks: accepting reproducibility as utility; speculative breadth in
the doctrine; account infrastructure ahead of empirical comparison; disconnected
artifact interfaces; documentation drift; and tests mistaken for financial
evidence. Most runtime modules have current consumers: there is little evidence
of service/framework bloat. The larger baggage is conceptual and unvalidated
assumptions, not a need for a repository-wide rewrite.

Important reproduced/inspected limits:

1. **Future derivative input can pass the live evaluator.** A breakout fixture
   was modified so its latest OI row was 30 seconds after `observed_at_ms`, while
   provenance stayed unchanged. The evaluator used that row, raised measured OI
   change to about 101%, and returned `WATCH_LONG` with all gates passing.
   The new harness rejects this detectable case; the live boundary still needs
   repair. This is a trust prerequisite, not an argument for more features.
2. **Persisted replay was broken.** `canonical_json` stores decimals as strings;
   the old JSONL/SQLite loaders did not restore them. The new saved-input CLI
   test reproduced a string-versus-Decimal TypeError. This intervention fixes
   both loaders and verifies parity with in-memory replay.
3. **Execution-time truth is missing.** Paper entry uses the next candle open
   relative to the anchor close, without checking `observed_at_ms`. That open
   can already be past when the decision is observed. The model also uses
   Binance USDT research candles with a Kraken spot spread proxy, labels monetary
   fields USD, applies independent overlapping fills and does not model funding
   or obtainable Kraken execution. Treat it as a scenario calculator.
4. **Two invalidation meanings coexist.** Setup failure is a completed-close
   breach, while paper exit is a low/wick touch. Neither can silently stand in
   for the other. Evaluation now exposes both.
5. **A fingerprint does not prove causal truth.** Dataset validation checks
   identity, observation order and exchange time. It does not prove every field
   was published then. The older outcome/paper APIs accept caller-supplied
   dataset/replay identities without recomputing their relationship. The new
   harness avoids this join by recomputing replay from verified rows.
6. **Specifications duplicate runtime concepts.** Python configuration is not
   loaded from YAML; dictionary quote freshness (5s) differs from runtime quote
   receipt gating (15s). The YAML level slice also differs from Python's exact
   window convention. Do not call catalogue validation executable conformance.
7. **Descriptions exceed behavior.** Funding/positioning are annotations, not
   decision factors; current OI and Binance book requests have no feature
   consumer. Live output still lists paper simulation among unknown items,
   although a separate historical simulator exists. Narrow that wording in the
   next decision-record task.

## 5. Decision-intelligence pipeline

| Transition | Assessment | Missing value |
|---|---|---|
| Data → observation | Implemented, partially trustworthy | Verify provider semantics/identity and synchronized as-of windows |
| Observation → interpretation | Narrowly implemented | Price/OI labels and participation score; no tested probabilistic interpretation |
| Interpretation → market state | Partial | Setup state exists; broader trend/range/liquidity/event regime does not |
| State → hypothesis | Implicit experiment | One breakout/retest continuation hypothesis; no explicit opposing case |
| Hypothesis → decision candidate | Implemented narrowly | `WATCH_LONG`, `LONG_ELIGIBLE`, wait/block; no alternative ranking |
| Decision → risk/invalidation | Partial | Implicit level, separate size utility, no account-aware intent |
| Risk → action | Manual and unproven | Always `NO_ORDER`; no concise user-facing review contract |
| Decision → outcome | Research implementation | Labels and hypothetical fills; no reconciled executable/user outcomes |
| Outcome → learning | Previously absent as evaluation; now descriptive | No demonstrated incremental usefulness, adoption/retirement results or calibrated confidence |

The weakest product link is **candidate → useful human decision → measured
learning**. Provider causality is an immediate prerequisite. A minimum proof is
one complete decision record, delivered using true as-of evidence, compared with
a simple alternative and judged after its declared horizon. A whole regime or
prediction engine is unnecessary for that experiment.

## 6. Product viability and external reality check

Verified product evidence: software can deterministically reproduce a narrow
rule on fixtures. Nothing in the inspected repo establishes predictive edge,
avoided harmful trades, time saved, willingness to keep using it, or demand.
Commercial viability is unknown. It should not be estimated from technical
feasibility or the financial cost of a bad trade.

Current primary product sources checked on 9 October 2026:

| Existing category | Already available | Consequence for TDAL |
|---|---|---|
| [TradingView strategies](https://www.tradingview.com/pine-script-docs/concepts/strategies/) and [replay](https://www.tradingview.com/support/solutions/43000712747-bar-replay-how-and-why-to-test-a-strategy-in-the-past/) | Chart measurements, strategy simulation, replay and alerts | More indicators or a breakout signal alone do not justify a separate product |
| [CoinGlass API](https://docs.coinglass.com/reference/endpoint-overview) | Derivatives, spot, options, ETF, on-chain and index data families | Aggregating more feeds is a crowded, maintenance-heavy proposition |
| [TradeZella](https://www.tradezella.com/features) | Journaling, analytics and trade replay | A journal alone is not a demonstrated gap; test a sharper decision workflow |
| [Freqtrade](https://www.freqtrade.io/en/stable/lookahead-analysis/) | Existing strategy software includes lookahead checking; [recursive analysis](https://www.freqtrade.io/en/stable/recursive-analysis/) checks indicator initialization effects | Custom bots and backtests are commodity capabilities; trust needs stronger evidence than code ownership |

These sources establish feature overlap, not the absence of every possible
competitor. Exchange interfaces, screeners and spreadsheets already support
much of observation and manual comparison. A generic AI assistant can explain
context, but TDAL could offer enforced timestamp/provenance contracts, frozen
rules and complete prospective records. That possible distinction must be
shown to help a user; deterministic jargon is not a differentiator by itself.

The plausible thesis is **an evidence-backed trade-or-wait record whose
reasoning, objections and invalidation can be audited and evaluated**. Durable
value could arise from verified venue semantics, a high-quality prospective
decision dataset, trusted review/evaluation and fit to a user's actual workflow.
The weighted score, public indicators and feed wrappers offer little inherent
defensibility. No moat or demand has been demonstrated.

Most natural progression: private decision tool → reusable evidence/decision
journal with quality review. A workstation is technically possible but needs
repeated external use across workflows; an API needs proven consumers and
stable contracts; a signal service needs incremental outcome/calibration
evidence; execution needs separate authorization and operational proof.
Do not build any of these commercial surfaces before demonstrating repeated
use, useful decisions and willingness to pay. Licensing and operating costs
must be assessed for the eventual distribution model.

The most informative experiments are different: a prospective data-contract
audit; a frozen-rule comparison including simpler alternatives; and a
counterbalanced user pilot measuring process quality and decision time. A
profitable short sample cannot substitute for any of them.

## 7. Architecture over the next development horizon

Keep one local Python application. Acquisition supplies normalized, provenanced
observations; TDAL's decision logic consumes them. Preserve that interface if
an external observer supplies inputs later. Do not combine repositories,
databases, thresholds, state or prior performance claims.

The near-term runtime needs a collector/normalizer, causal features and gates,
one experimental setup, a plain decision record and the existing SQLite journal.
Offline research needs frozen datasets, the same evaluator, outcome labels and
the small comparison harness. Future labels cannot flow into runtime inputs.
Risk sizing remains separate from observation; hypothetical fills remain a
disclosed optional research calculation. No new service boundary is necessary.

| Major component | Classification | Consequence |
|---|---|---|
| `config`, `util`, Decimal serialization | CORE NOW | Freeze and record actual runtime settings; fix correctness rather than design a configuration framework |
| `http`, consumed collectors, normalization/provenance | CORE NOW | Verify live contracts and failure behavior; these are acquisition dependencies, not decision intelligence |
| Causal OHLCV features and freshness/identity gates | CORE NOW | Required for meaningful experiments; align derivatives and validate identities before trusting them |
| `evaluator` | CORE NOW | Retain the single shared deterministic path; no parallel strategy implementation |
| Breakout/retest state and score in `engine` | RESEARCH/EXPERIMENTAL | Retain as the first falsifiable hypothesis; no promotion based on tests alone |
| Funding/positioning annotations | RESEARCH/EXPERIMENTAL | Do not add weight or infer confirmation without an incremental-value experiment |
| `service`, `cli`, SQLite `storage` | CORE NOW | Keep local observation/journaling and tested recovery; fix state/provenance behavior before operational scale |
| `dataset`, `replay`, `outcomes`, new `evaluation` | CORE NOW for research | Use them to measure the existing hypothesis, not as reasons to build more tooling |
| `paper` | RESEARCH/EXPERIMENTAL | Correct arrival time, quote units and execution evidence before economic claims |
| `risk.maximum_quantity` | PREMATURE as application integration | Small tested utility can remain; postpone a governor until account-aware intents are a validated need |
| Structured user decision record and pilot | NEEDED SOON | Makes the mechanism useful enough to test; use the current output/journal, not a new web platform |
| YAML catalogue versus Python configuration | REDUNDANT as executable authorities | Make the distinction explicit; catalogue is a research index today, not loaded runtime policy. Resolve active-rule conformance in the boundary task |
| Binance book/current-OI requests with no consumer | SHOULD BE REMOVED | In the collector-contract task, remove requests that provide no current evidence benefit; less failure surface and maintenance |
| Docker/Compose deployment | PREMATURE as production operation | Leave optional local convenience; do not expand hosting/monitoring before proven behavior and use |
| Old next-build text/implementation map/test-count claims | STALE | Current affected docs corrected here; retain historical versions as history, not planning authority |
| 105-rule doctrine and future execution/macro architecture | RESEARCH/EXPERIMENTAL; PREMATURE as backlog | Mine testable hypotheses selectively; do not implement the map wholesale |

There is no evidence justifying microservices, event buses, plugins, strategy
registries, a feature store, distributed agents, cloud orchestration, a model
trainer or a large database. Most current indirection has a consumer and is
reasonable. Do not rewrite it for aesthetic simplicity.

Research promotion needs no new framework: each experiment gets a short
Markdown record with hypothesis, feature/parameter versions, dataset IDs,
primary endpoint, baseline, dates, trial budget and rejection criterion. Link
immutable results and write an explicit adopt/reject/inconclusive decision.
Rejected code leaves the runtime path; preserve its experiment record and Git
history. A successful exploratory result remains research until replication
and user evidence justify adoption. The production core should enforce contracts
and render records, not include every promising experiment.

## 8. Brutally constrained MVP

**A local TAO long-breakout/retest decision record and evaluation loop.** TAO is
selected to test the existing hypothesis cheaply, not because it is proven the
best asset. Do not add a second asset until the first loop works.

- Inputs: 97 completed 15m Binance TAO/USDT OHLCV bars (the current minimum
  feature window); at least 13 valid 5m OI samples spanning 60m; three complete
  5m taker-buy/sell samples for the aligned 15m interval; current Binance
  research quote and Kraken TAO/USD last/bid/ask; exchange/observation clocks;
  raw endpoint/symbol IDs, event times, receipt times and configuration/version.
  Actual timing/as-of joins must be verified and documented against providers.
- Transformations: existing causal 32-bar levels, Wilder ATR(14), 96-bar volume
  percentile, OI changes, taker share, quote spread/divergence gates and the
  frozen breakout/retest state machine. No new score factors.
- Output: a single timestamped record with `WATCH_LONG`, research long candidate,
  wait or blocked status; setup ID and expiry; supporting and contradictory
  evidence; research-quote completed-close invalidation; current venue quotes;
  missing information; and `confidence: UNKNOWN_UNCALIBRATED`. The integer score
  must not be presented as a probability. Execution stays `NO_ORDER`.
- Manual: scheduled-event checking, position/account state, choice of actual
  entry, quantity and exit; Kraken execution/stop translation; approval to act;
  reasons for overrides; review of the decision afterward. If these are unknown,
  the record cannot imply an account-approved trade.
- Automated: observation quality checks, feature calculation, rule state,
  evidence record, persistent logging, replay and fixed-horizon comparison.
- Provenance: versioned rules/configuration, snapshot/raw IDs, venue/product/quote
  distinction, receipt-age caveats and explicit unavailable values.
- Uncertainty: failed inputs cause abstention/block; unavailable context is
  named; interpretive confidence stays unknown. No unsupported probability.
- Evaluation: freeze a primary 4-bar (60m) descriptive horizon before examining
  results; record every state and abstention, compare baselines, then review
  invalidation and user decision quality. This horizon is a proposed experiment
  design, not a parameter selected for historical performance.
- Excluded: shorts, trailing-stop automation, multi-asset portfolio logic,
  automated account sizing, live orders, machine learning, regime probabilities,
  order-book depth, news/whales/sentiment/macro feeds, cloud/API products and
  parameter optimization. Funding/positioning do not influence this MVP decision.

The repository has passed an engineering foundation milestone. It has not passed
this useful MVP: provider trust, a complete readable decision record and real
comparative evaluation remain missing.

## 9. Validation framework

Keep two claims separate: **process benefit** (more defensible/faster decisions)
and **economic/selective benefit** (better conditional outcomes after obtainable
costs). The first can justify a personal tool even if the second is absent.
Conversely a favorable return sample does not prove usable decision support.

The smallest evidence loop:

1. Verify contracts with timestamped provider captures and negative tests.
   Acceptance: no detected future/mixed/wrong-identity required inputs can yield
   eligibility; persisted replay reproduces the observed decision semantics.
   Measure blocked fractions, provider lag and gaps, not just uptime.
2. Preregister one primary horizon and rule/configuration, a simple baseline,
   trial budget and disjoint chronological evaluation periods. Record actual
   as-of data prospectively; do not invent historical publication times.
   Purge boundary-spanning labels. Holdout declarations alone do not enforce
   that the data was unseen.
3. Use the new harness for coverage, selection frequency, forward excursions,
   close/wick invalidation and abstention opportunities. Treat it as screening,
   not statistically conclusive evidence. Count distinct setup episodes as
   well as bars; overlapping horizons/polls do not create independent evidence.
4. Compare full participation filtering with the same setup using simpler
   evidence in a preregistered ablation. Use the shared engine and common data
   universe; do not interpret removal of thresholds as removal of data needs.
   Preserve all failed variants. Assess stability across time/conditions.
5. Run a small counterbalanced paired user pilot: present as-of states through
   the usual chart/workflow or TDAL record before future outcomes are revealed.
   Log decision time, support/objections, invalidation, missing facts and chosen
   trade/wait action. Use a fixed rubric and review blind to future profit.
   Check whether the tool is actually used again without prompting.

For the initial pilot, twenty paired cases can reveal obvious workflow problems;
they are not a power calculation or proof of universal benefit. Agree a practical
time-saving target before the pilot, and require no deterioration in rubric
quality. The rubric asks whether evidence was available, venue/units were
correct, objections were disclosed, invalidation existed, missingness was honest
and unsupported confidence was avoided. Consistency means equivalent evidence
receives equivalent treatment, not that the tool always says the same thing.

Calibration is unavailable while probabilities are absent. If a probability is
later introduced, first freeze an observable target and test proper scores
(such as Brier), calibration by bins and sample uncertainty. Do not retroactively
turn the weighted score into confidence.

Risk-adjusted outcomes, net expectancy and counterfactual trades require causal
arrival prices, executable venue tapes, costs and a position model. No Sharpe,
portfolio drawdown or profitability claim should be derived from the current
independent one-unit fills. After obtaining a meaningful sample, use paired,
dependence-aware uncertainty (for example calendar-block resampling), cost stress
and untouched replication. Profitability is one endpoint, never the sole verdict.

## 10. Dependency-driven roadmap and kill criteria

| Stage | Small number of outcomes | Uncertainty resolved / value unlocked |
|---|---|---|
| NOW | Runnable dataset-bound comparison (this change); provider/timing/identity repair and actual captures | Can the existing rule be measured without inventing input truth? |
| NEXT | One readable decision record; prospective baseline/ablation study and paired personal pilot | Does it add information or improve the actual trade-or-wait workflow? |
| LATER | Simplify based on results; reconcile hypothetical arrival/venue/costs; add account-aware research intents only if the user workflow needs them | Which mechanism survives, and what is obtainable and safely reviewable? |
| ONLY IF VALIDATED | Second user/asset; probabilities or contextual inputs with incremental evidence; then reusable UI/API, and separately authorized guarded execution | Does utility generalize, do consumers want it, and does automation preserve it? |

Kill criteria are advancement decisions, not promises about sample significance:

- If providers cannot supply auditable as-of evidence reliably enough for the
  proposed decision horizon, suspend that decision mode. Do not manufacture
  timestamps or relax causality to keep emitting signals.
- If the participation score does not improve the preregistered comparison
  across independent evaluation windows, remove it from decisions and test the
  simpler rule. An inconclusive sample warrants no promotion or optimization.
- If the setup fails the specified outcome/risk comparison after realistic
  execution validation, reject it as a signal mechanism. A process/journal use
  case may remain viable and must be judged separately.
- If the personal pilot supplies no meaningful time/quality benefit or is not
  repeatedly used, stop building its UI and revise or abandon that workflow.
- If a proposed input cannot name a present hypothesis, consumer and incremental
  evaluation, do not collect it in the production path.
- If external users do not repeat use or express credible willingness to pay,
  stop commercial packaging; do not confuse positive interviews with demand.

Retesting a rejected experiment needs new independent evidence or a changed
causal premise, not another threshold search on the same sample.

## 11. Repository intervention and verification

Single intervention: make the existing decision mechanism evaluable before
expanding the stack. It outranks a RiskGovernor because no validated
account-aware intent exists; new sources because marginal information value is
unknown; a rewrite because existing layers have real consumers; and a richer UI
because underlying evidence still needs a reproducible comparison. A future
causality repair remains necessary and is explicitly first in the follow-on
tasks. The harness's as-of checks do not silently certify the live evaluator.

Files changed:

- `market_observer/evaluation.py`: small dataset-bound comparison and immutable CLI artifact.
- `market_observer/replay.py`: restore declared decimal fields at saved-input boundaries.
- `tests/test_evaluation.py`: twelve meaningful evaluation safeguards/CLI tests.
- `tests/test_observer.py`: two persistence-to-replay regression tests.
- `README.md`: runnable evaluation, precise limits and revised next step.
- `TEST_RESULTS.md`: current local results and historical CI distinction.
- `docs/IMPLEMENTATION_V0_1.md`: current implemented compartments and advancement gate.
- `docs/DECISION_EVALUATION.md`: evaluation universe, metrics and claim limits.
- `docs/PRODUCT_DIRECTION.md`: investigation, purpose, classifications and development gates.
- `CHANGELOG.md`: unreleased intervention summary.

Local verification: all 53 tests and specification validation (21 rules,
49 raw/config fields, 18 derived fields) passed on Python 3.12.14. Compilation
and diff checks are performed for delivery. Clean installation and the remote
Python matrix are checked separately; any pending/failed remote result must be
reported in the delivery message. No provider integration, trading performance
or real user benefit is claimed from synthetic tests.

No existing trading rule was tuned, no external project's code/state was adopted,
and no order route was introduced.

## 12. Unresolved decisions requiring evidence or user judgment

1. Is the first useful product a candidate screener, a risk/exit review or a
   decision journal? The current code most cheaply tests a candidate record;
   the personal workflow pilot should determine whether that is actually wanted.
2. Does TAO justify being first after provider access, liquidity and data truth
   are checked? Existing work reduces experimental cost but proves no superiority.
3. Is continued personal use enough, or is eventual commercialization an actual
   objective? External recruitment/pricing work is premature until personal
   benefit exists. Commercial intent changes later validation burden.
4. Which time/quality trade-off is meaningful to Savannah? Agree the pilot target
   before outcomes; software correctness cannot choose it.
5. Should completed-close thesis failure and an intrabar executable protective
   stop coexist? They are distinct concepts. Their mapping requires execution
   evidence and the intended decision horizon, not an accidental implementation.

A smaller local architecture, no live orders, no uncalibrated probabilities and
evaluation before expansion can already be resolved from evidence. They do not
require another round of preference questions.

## 13. Stop-doing list and lightweight entropy rules

Stop adding feeds without a hypothesis; implementing the whole doctrine;
building an account governor before validated intents; introducing shorts or
trailing exits to satisfy breadth; optimizing the weighted score; rebuilding
architecture for elegance; adding agents/services/databases; and calling passing
tests a successful product. Stop creating documentation that repeats promises
without an implemented consumer or advancement criterion.

Lightweight rules:

- Every change names the user value or uncertainty it resolves and how it will
  be checked. A feature with neither stays out of the roadmap.
- One active product slice and one primary experiment at a time. Freeze design
  before results; budget variants and record every rejection.
- Runtime configuration is recorded as executed. Research catalogues cannot
  silently become a second source of executable truth.
- No experiment graduates automatically. Require an adoption record and delete
  abandoned runtime paths, preserving findings in Git/experiment records.
- Update affected implementation claims in the same change. Historical docs
  remain dated; current commands must work from a clean checkout.
- Every new required input adds a consumer, quality contract and marginal-value
  test. Unused requests are removal candidates, not future assets.
- Completion means a working end-to-end behavior, honest limits and its
  evaluation—not merged modules or a larger test count.

## 14. Next three high-value Work tasks

**1. Repair and demonstrate the observation contract.** Inspect current official
provider semantics for timestamp, interval, symbol and units; use small captured
raw fixtures and a bounded public probe when available. Enforce as-of derivative
rows, required-series continuity/alignment, payload identity, receipt truth and
post-gap live state handling. Remove unconsumed book/current-OI requests. Resolve
the active YAML/runtime contract discrepancies without introducing an interpreter.
Acceptance: future/wrong-venue/gapped required data cannot yield eligibility;
normal captured input yields reconciled features and saved-replay parity; any
unavailable provider remains an explicit unresolved block. No account/order APIs.

**2. Complete the decision record and conduct a personal workflow pilot.** Use
the existing evaluator output to produce one readable JSON/text record with
hypothesis, support, objections, research invalidation, expiry, unavailable
context and unknown confidence. Keep candidate versus account approval explicit.
Correct stale live unknown-field wording. Persist human trade/wait choice and
override reason alongside the record using the existing journal. Test complete
and blocked records, then run preregistered paired as-of cases measuring decision
time, rubric quality and repeated use. Delivery includes actual pilot evidence
or a ready-to-run protocol; do not invent participation or outcomes.

**3. Run a preregistered mechanism evaluation and make an adoption decision.**
After task 1, freeze a real prospective dataset, primary 60m horizon, configuration,
simple comparator, score/participation ablation, disjoint windows and trial budget.
Run the harness, count independent episodes and coverage, compare abstentions
and invalidations, and disclose dependent samples. Repair arrival-time and
venue/currency/cost modeling before any economic claim; if no execution tape is
available, deliver descriptive findings only. Produce an adopt/simplify/reject/
inconclusive decision for the score and setup. No optimizer or extra feed.

