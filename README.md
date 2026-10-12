# Trading Decision Automation Lab — project zero, v0.2

This is a **new and independent project**. It is not a continuation, refactor, release, or renamed copy of the Market Research Lab, SOL observer, TAO observer, prediction-model project, or any other prior trading thread.

The objective is broader: convert a researched trading doctrine into explicit data contracts, deterministic rules, explainable state, and eventually a continuously running decision-support application. The first implementation compartment observes public market data and emits structured eligibility states. It cannot trade.

## Non-negotiable project boundary

- Separate project name, package, database, documentation, tests, release history, and future repository.
- No prior rule, parameter, feature, result, or architecture is inherited automatically.
- A past idea may be adopted only when it improves correctness, safety, measurement, or performance.
- Every such adoption must be recorded in `ADOPTION_LOG.md`, including why it was selected and what was deliberately not inherited.
- Similar-looking projects remain separate unless the user explicitly authorizes a merger.

See `PROJECT_BOUNDARY.md` for the full rule.

## Included compartments

1. `spec/Trading_System_Doctrine_v0.1.md` — researched doctrine and rule hierarchy.
2. `spec/rule_catalog.yaml` — 21 machine-readable foundation rules.
3. `spec/data_dictionary.yaml` — 49 raw/config fields and 18 derived fields.
4. `market_observer/` — public collectors, normalization, features, gates, state and storage.
5. `market_observer/evaluator.py` — one side-effect-free decision path shared by live observation and replay.
6. `market_observer/replay.py` — chronological replay over saved normalized snapshots.
7. `market_observer/dataset.py` — immutable normalized historical-dataset artifacts.
8. `market_observer/outcomes.py` — post-hoc forward market labels, kept separate from evaluator inputs.
9. `market_observer/paper.py` — deterministic cost-aware research-only paper fills.
10. `market_observer/ingestion.py` — bounded offline historical candle imports, explicitly incomplete and unverified as-of reconstructions.
11. `market_observer/snapshots.py` — shared typed snapshot boundary and exact persisted-decimal decoding.
12. `market_observer/evaluation.py` — dataset-bound descriptive comparisons against simple decision-selection baselines.
13. `tests/` — deterministic live/replay, dataset, ingestion, outcome, paper and evaluation tests.

## First runnable behavior

The observer uses Binance USD-M futures as the research-context venue and Kraken spot as the executable-price confirmation venue. TAO is the initial instrument, not the permanent limit of the project.

It currently calculates:

- completed 15-minute candles only;
- causal support and resistance;
- Wilder ATR(14);
- volume percentile;
- 15-minute and 60-minute open-interest change;
- taker buy/sell ratio and buy share;
- funding and positioning annotations;
- Kraken executable spread;
- USD/USDT cross-venue divergence;
- price×OI classification;
- an explicitly uncalibrated breakout-quality score;
- a persistent breakout→retest state machine.

Every output contains `action: NO_ORDER` and `execution_permission: DENIED_OBSERVATION_ONLY`.

## Run locally

Python 3.10+; the observer has no third-party runtime dependency. Specification validation additionally uses PyYAML:

```sh
python -m pip install '.[dev]'
python -m unittest discover -s tests -v
python spec/validate_specifications.py
python observer.py --base TAO --pretty
python observer.py --base TAO --loop --poll-seconds 60
```

The default database is `data/trading_decision_lab.sqlite`. Use Ctrl-C to stop loop mode.

## Import historical candles

The offline importer turns a saved Binance USD-M 15-minute kline export, wrapped
with explicit instrument/range/acquisition metadata, into immutable normalized
snapshots. Start with this **synthetic three-bar example**, which demonstrates the
file workflow only:

```sh
python -m market_observer.ingestion \
  --source examples/binance_15m_synthetic.json --base TAO \
  --output data/example_candles.jsonl --manifest data/example_candles.manifest.json
python -m market_observer.replay \
  --jsonl data/example_candles.jsonl --base TAO --output results/example_replay.json
python -m json.tool data/example_candles.manifest.json
python -m json.tool results/example_replay.json
```

Use new output names for each run. Commands finish automatically; Ctrl-C stops a
running command. For real data, supply a saved source envelope in the documented
format. The importer does not download or schedule collection.

**Candle-only imports produce `DATA_STALE`, not trade signals.** OI, taker flow and
Kraken confirmation stay absent. Historical cutoffs are explicitly reconstructed;
actual acquisition timestamps are retained and historical availability is marked
unverified. The default feature warmup requires 97 completed bars, but enough
candles alone still cannot satisfy the existing strategy. These replay decisions
lack the feature anchor needed by the current outcome-label path.

See [the ingestion contract](docs/HISTORICAL_INGESTION_V0_1.md) for source fields,
strict validation, time semantics, limitations and verification.

## Historical decision replay

Replay deliberately uses the same `calculate_features`, gate, score and state-machine functions as the live observer. It does not contain a second strategy implementation.

From an observer database:

```sh
python -m market_observer.replay \
  --db data/trading_decision_lab.sqlite \
  --base TAO \
  --output results/tao_replay.json
```

Or from a JSON Lines file containing one complete normalized snapshot per line:

```sh
python -m market_observer.replay \
  --jsonl snapshots.jsonl \
  --base TAO \
  --output results/tao_replay.json
```

Add `--fee-bps-per-side` and `--slippage-bps-per-side` to record cost assumptions in each decision's `risk` contract, as for the observer. Omitted costs are reported as not supplied, never zero, and the assumptions are part of the replay `configuration_id`.

Replay input must already be chronological and must preserve the data actually available at each historical instant. The runner rejects reversed chronology, records duplicate and missing completed bars, resets an active setup across a detected gap, and refuses to overwrite a prior result file.

JSONL and SQLite loaders restore declared decimal fields exactly. Dataset and
replay boundaries reject unknown fields, including nested research outcomes.
Explicit candle-only reconstructions are accepted solely as incomplete research
inputs and fail closed; they are not evidence of historical signal availability.

This is **decision replay, not a profitability backtest**. It does not simulate fills, fees, slippage, funding, borrow, liquidation or expectancy.

## Post-hoc outcome labels

A frozen dataset and replay result can now be joined to future completed candles without contaminating the evaluator with future information. The label artifact records forward close return plus maximum favorable/adverse excursion at explicit completed-bar horizons. Missing future paths remain unavailable rather than being interpolated.

```sh
python -m market_observer.outcomes \
  --jsonl snapshots.jsonl \
  --dataset-manifest snapshots.manifest.json \
  --replay results/tao_replay.json \
  --output results/tao_outcomes.json
```

The default horizons are 1, 4, 16 and 96 completed 15-minute bars (15m, 1h, 4h and 24h). These are **market outcome labels, not trade returns**: no fill is assumed and no cost, funding, sizing or execution model is applied. Labels are post-hoc research artifacts and are forbidden as evaluator inputs.

## Cost-aware research paper fills

`market_observer.paper` turns only `LONG_ELIGIBLE` replay decisions into one-unit hypothetical fills. Entry is the next completed research-venue bar open. The simulator applies the signal-time Kraken spread as a disclosed round-trip proxy, explicit fixed slippage and explicit fixed fees, then exits on the strategy invalidation level or a caller-supplied maximum holding period.

No cost assumptions are silently defaulted. They must be supplied for each run:

```sh
python -m market_observer.paper \
  --jsonl snapshots.jsonl \
  --dataset-manifest snapshots.manifest.json \
  --replay results/tao_replay.json \
  --fee-bps-per-side "$FEE_BPS" \
  --slippage-bps-per-side "$SLIPPAGE_BPS" \
  --max-hold-bars "$MAX_HOLD_BARS" \
  --output results/tao_paper_fills.json
```

The execution contract is deliberately conservative about what is known. Historical candles currently come from Binance USD-M research data, not a historical Kraken execution tape, so every fill records `executable_venue_validated: false`. Funding, order-book depth, latency, partial fills, queue position and liquidity impact remain unmodeled. The artifact reports per-unit hypothetical execution results only; it does not size positions, compound equity, authorize trades or make a strategy-performance claim.

## Decision risk contract

Each decision includes a `risk` object stating the active setup's completed-close invalidation level, the per-unit distance to it at the research venue, and each cost component separately: observed Kraken spread, optional `--fee-bps-per-side` / `--slippage-bps-per-side` assumptions (reported as not supplied, never zero, when omitted) and unmodelled funding. It also reports a descriptive reward reference, the nearest confirmed overhead swing high, with a gross opportunity-to-risk ratio, or `TARGET_UNAVAILABLE` (see `docs/REWARD_REFERENCE.md`); it is not a take-profit and never affects eligibility. It computes no quantity and grants no permission; blocked or setup-less cycles withhold figures. See `docs/DECISION_RISK.md`.

## Meaning of signals

| Signal | Meaning |
|---|---|
| `NO_TRADE` | No qualifying setup |
| `WATCH_LONG` | Breakout observed; retest still required |
| `LONG_ELIGIBLE` | Retest conditions passed; research eligibility only |
| `DATA_STALE` | Required data is missing, malformed, discontinuous or old |
| `RISK_BLOCKED` | Spread, venue-confirmation or mode veto failed |

Blocked or stale cycles freeze the state machine. Repeated polling of the same completed candle cannot advance it. The shared as-of guard rejects detectable future derivative evidence, events and receipts before live/replay feature calculation, including when precomputed features are supplied. Offline evaluation uses the same guard. See [availability contract and working-loop path](docs/ASOF_CONTRACT.md) for timestamp semantics, limitations and local capture instructions.

## Decision usefulness evaluation

The next question is whether the existing rule selects useful decision states, before building account controls or adding inputs. The evaluation command verifies the frozen dataset, rejects detectable future derivative rows/receipts, recomputes the shared replay and compares `LONG_ELIGIBLE` selections with positive-prior-return, select-all and abstain-all baselines on the same valid completed bars.

```sh
python -m market_observer.evaluation \
  --jsonl snapshots.jsonl \
  --dataset-manifest snapshots.manifest.json \
  --experiment-id "$EXPERIMENT_ID" \
  --partition exploratory \
  --start-observed-at-ms "$WINDOW_START_MS" \
  --end-observed-at-ms "$WINDOW_END_MS" \
  --horizon-bars 4 \
  --output results/tao_decision_evaluation.json
```

Each valid completed candle is counted once. Missing/censored labels remain unavailable. Results include selection coverage, descriptive forward movement, abstention opportunity costs and separate close-breach/wick-touch counts. These are **market comparisons, not executable returns or proof of better decisions**. A declared holdout is not automatically verified as untouched. Derivative windows now use exact completed-candle boundaries; broader provider timing semantics and real user benefit still require validation.

Evaluation 0.2 also labels each TDAL selection by whether its reward reference or its invalidation is reached first. See [evaluation readiness](docs/EVALUATION_READINESS.md) for why no real dataset can yet produce TDAL selections, and the measured setup frequency.

See [evaluation contract](docs/DECISION_EVALUATION.md) and [product investigation and direction](docs/PRODUCT_DIRECTION.md).

## Current verification boundary

The integrated deterministic suite includes 199 tests covering live/replay guards,
the per-decision risk contract, persisted JSONL/SQLite parity, historical ingestion,
immutable datasets, legacy manifest compatibility, outcome/paper workflows and
descriptive evaluation. GitHub Actions runs the suite plus specification validation
on Python 3.10 and 3.12. The ingestion implementation's Binance request returned
HTTP 451; issue #7 now has a successful read-only live provider check and exact
derivative candle alignment (see [contract and live evidence](docs/DERIVATIVE_CANDLE_ALIGNMENT.md)). No credential is used
and no order route exists.

Next expand provider timing/identity validation and capture actual as-of observations; then test the narrow decision record and frozen rule against baselines. A RiskGovernor becomes necessary when account-aware proposed trades are a validated use case. Parameter optimization and live execution remain out of scope. The doctrine is a research map, not a commitment to implement every listed capability.
