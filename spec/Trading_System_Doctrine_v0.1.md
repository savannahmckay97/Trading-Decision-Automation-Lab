# Evidence-Grounded Trading System Doctrine

**Version:** 0.1 — research map, rule skeleton, data contract, and build sequence  
**Date:** 17 September 2026  
**Purpose:** Convert discretionary market reasoning into explicit, testable, auditable rules that can eventually drive a continuously running crypto market-observation and decision-support application.

> This document is not a promise of profitable prediction. Its job is to make every decision falsifiable: what was known, which rule fired, what evidence supported it, what would invalidate it, how much could be lost, and whether the same rule worked out of sample after fees and slippage.

---

## 1. Foundational conclusion from the research

The system must not be designed as a machine that outputs “price will rise” or “price will fall.” Financial returns have weak signal, non-stationary relationships, fat tails, reflexivity, changing participants, and execution costs. Simple technical rules often look profitable in-sample and then fail out of sample; one true out-of-sample replication of classic technical rules found little predictive value, while a 2023 study of 6,406 rules across 41 markets found that apparent predictability decayed, was highly cost-sensitive, and did not persist out of sample ([Sullivan-style replication](https://www.pure.ed.ac.uk/ws/files/18967583/predictability_of_the_simple_technical_trading_rules.pdf); [Zakamulin and Giner, 2023](https://link.springer.com/article/10.1007/s11408-023-00433-2)).

The program should instead perform six narrower jobs:

1. **Observe:** collect synchronized market, derivatives, macro, news, and execution data.
2. **Describe:** convert raw data into reproducible features and market-state labels.
3. **Test:** evaluate conditional hypotheses such as “breakout with rising open interest behaves differently from breakout with falling open interest.”
4. **Decide:** rank long, short, and no-trade alternatives under explicit uncertainty.
5. **Control risk:** determine whether a valid thesis is tradable at the available entry, stop distance, liquidity, leverage, and account risk.
6. **Learn:** record predictions, rejected trades, fills, costs, invalidations, and outcomes so rules can be revised without rewriting history.

The principal output is therefore not a prediction. It is a **decision record**:

```text
market state -> eligible setups -> evidence score -> invalidation ->
risk/return after costs -> action or no-trade -> observed outcome
```

---

## 2. Evidence map: what survives scrutiny

### 2.1 Technical indicators are measurements, not independent evidence

RSI, MACD, moving averages, Bollinger Bands, ATR, stochastic oscillators, and most chart indicators are transformations of the same underlying price/volume history. Combining five price-derived indicators does not create five independent confirmations. It often counts the same fact five times.

Useful role:

- compress or normalize price history;
- identify volatility, distance from a mean, trend slope, or momentum;
- define repeatable triggers and invalidations;
- make hypotheses testable.

Invalid role:

- treating “overbought” as synonymous with “must fall”;
- treating indicator agreement as proof when all indicators share the same inputs;
- selecting parameters because they maximize historical profit;
- assuming a rule remains valid across assets, regimes, and execution conditions.

### 2.2 Some effects exist, but horizon and regime matter

- **Trend/time-series momentum:** empirical evidence exists across multiple asset classes and long samples, particularly at medium horizons, but that does not validate every intraday moving-average crossover. Moskowitz, Ooi, and Pedersen found return persistence over roughly one to twelve months across 58 futures instruments ([paper](https://doi.org/10.1016/j.jfineco.2011.11.003)). Long historical work reports trend-following evidence over more than a century, with material variation across environments ([AQR study](https://www.aqr.com/-/media/AQR/Documents/Insights/Journal-Article/AQR-JPM-Fall-2017.pdf)).
- **Short-horizon reversal/mean reversion:** often resembles compensation for providing liquidity and bearing adverse-selection risk rather than a free anomaly. Its strength and speed vary with volatility, turnover, spread, and liquidity ([Dai et al.](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4339591)).
- **Order flow:** at very short horizons, order-flow imbalance, spread, depth, cancellations, and aggressive trades are closer to the price-formation mechanism than RSI or MACD. Cont, Kukanov, and Stoikov found short-interval price changes related approximately linearly to order-flow imbalance, with impact inversely related to depth ([paper](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1712822)). Recent crypto research likewise finds modest out-of-sample predictive information in order flow; importantly, economic value remains small and cost-sensitive ([Order flow and cryptocurrency returns](https://www.sciencedirect.com/science/article/pii/S1386418126000029)).
- **Volatility scaling:** reducing exposure as forecast volatility rises can improve risk-adjusted results in several factor portfolios, because volatility is more forecastable than returns ([Moreira and Muir](https://doi.org/10.1111/jofi.12513)). This supports sizing by volatility, not interpreting volatility as direction.
- **Sentiment/news:** news can alter returns and volatility, but effects are event-, asset-, and period-dependent. “Positive news = price rises” is not a stable law. Bitcoin research has found different responses across macro announcement types and signs ([Corbet et al.](https://doi.org/10.1080/1351847x.2020.1737168)). Sentiment must be timestamped against what was expected, what occurred, and what was already priced.

### 2.3 Backtests are unusually easy to fool

Data reuse creates false discoveries. White’s Reality Check formalized the problem that the best model found during a large specification search may be the winner by chance ([White, 2000](https://users.ssc.wisc.edu/~behansen/718/White2000.pdf)). The Probability of Backtest Overfitting compares in-sample winners with their out-of-sample degradation ([Bailey et al.](https://www.carmamaths.org/resources/jon/backtest2.pdf)). The Deflated Sharpe Ratio adjusts reported performance for multiple testing and non-normal returns ([Bailey and López de Prado](https://doi.org/10.3905/jpm.2014.40.5.094)).

Consequences for this project:

- every tested variant counts, including discarded experiments;
- random train/test splits are forbidden for time series;
- feature calculation must use only data available at that historical instant;
- final evaluation must include untouched chronological data;
- costs, latency, funding, rejected orders, partial fills, and delistings belong inside the test;
- a strategy is not “validated” because one asset, month, or parameter set worked.

### 2.4 Execution is part of the strategy

Paper profit is not executable profit. Spread, taker fees, maker rebates, queue position, slippage, latency, price impact, adverse selection, unfilled limits, funding, borrow availability, and liquidation rules can reverse the ranking of strategies. Implementation shortfall should compare the decision-time paper portfolio with actual fills and missed fills, not merely total fees.

### 2.5 Operational safety outranks signal sophistication

Knight Capital lost more than $460 million after defective deployment and inadequate automated controls sent millions of unintended orders. The SEC identified missing capital thresholds, inadequate controls, and poor deployment procedures ([SEC order](https://www.sec.gov/files/litigation/admin/2013/34-70694.pdf)). The lesson is not historical trivia: a mediocre signal with hard limits survives; a brilliant signal without kill switches can destroy the account.

---

## 3. Trading-style taxonomy

| Style | Economic premise | Usually suitable regime | Primary failure mode | Data burden | Automation difficulty |
|---|---|---|---|---:|---:|
| Trend following | information diffuses gradually; behaviour persists | directional, sustained movement | whipsaw in ranges; late entries | OHLCV, volatility, costs | Low–medium |
| Breakout | acceptance beyond a well-observed boundary triggers repricing/stops | expansion after compression or catalyst | false break, liquidity sweep | OHLCV, volume, OI, flow, levels | Medium |
| Momentum | recent relative/absolute strength persists | broad participation and stable risk appetite | crowded unwind, reversal | cross-asset returns, volume, OI | Medium |
| Mean reversion | temporary dislocation returns toward equilibrium | liquid, bounded, non-trending market | fading a genuine repricing | spread, depth, volatility, reference value | Medium–high |
| Event-driven | surprise changes expected cash flows/rates/regulation | discrete information release | wrong expectation baseline; latency | calendar, consensus, actual, timestamped news | High |
| Carry/funding | earn compensation for holding an exposure | stable basis/funding regime | crowded trade and tail liquidation | spot/perp basis, funding, borrow, margin | Medium |
| Relative value/stat-arb | related instruments temporarily diverge | stable relationship, ample liquidity | structural break; leg risk | synchronized multi-asset data | High |
| Market making | earn spread while controlling inventory | two-sided liquidity and manageable toxicity | adverse selection; inventory runaway | full L2/L3 book, trades, latency | Very high |
| Arbitrage | equivalent claims temporarily mispriced | fragmented venues | transfer/latency/counterparty risk | multi-venue books, fees, balances | Very high |
| Discretionary synthesis | human integrates qualitative context | novel events and sparse precedents | inconsistency, recency bias, emotional switching | broad mixed data | Hard to validate |

No style is universally superior. A system must first identify which premises are currently plausible; it must not apply mean-reversion logic to a catalyst-driven trend or breakout logic inside low-volume noise.

---

## 4. Canonical rule schema

Every doctrine rule must be stored using the same fields:

| Field | Meaning |
|---|---|
| `rule_id` | Stable identifier; never reused after retirement |
| `version` | Rule definition version |
| `purpose` | What decision the rule informs |
| `horizon` | Seconds, minutes, hours, days, or portfolio horizon |
| `inputs` | Exact fields, sources, and lookback windows |
| `calculation` | Deterministic formula or model version |
| `condition` | Boolean or scored firing condition |
| `independence_group` | Price, flow, derivatives, macro, news, execution, risk |
| `action` | Permit, prohibit, score, size, exit, or alert |
| `invalidation` | Observable fact that defeats the thesis |
| `expiry` | Maximum lifespan of the signal |
| `confidence` | Calibrated historical probability, never intuition presented as probability |
| `cost_model` | Fees, spread, slippage, funding, impact assumptions |
| `failure_modes` | Known reasons the rule may lie |
| `test_status` | Draft, unit-tested, backtested, paper-traded, shadow-live, approved |
| `provenance` | Research and experiment IDs supporting the rule |

---

## 5. Doctrine hierarchy

### Tier 0 — non-negotiable system and account invariants

These rules require no market prediction.

1. **No order without an invalidation.** A trade without a price/time/event condition proving it wrong is prohibited.
2. **No order without predetermined account risk.** Quantity is derived from allowed loss and stop distance, never from desired profit.
3. **Leverage does not define risk.** Risk is quantity × adverse price distance plus costs and gap allowance.
4. **No averaging down by default.** Additional entries require a separately valid setup and must keep total risk below the original account-risk ceiling.
5. **No instantaneous directional flip.** Closing a failed long does not create evidence for a short. A new setup must independently qualify.
6. **No stale inputs.** Each feature has maximum age; if any required field exceeds it, output is `DATA_STALE`, not a trade.
7. **No mismatched venue trigger.** A Kraken trade may use Binance context, but entry, stop, liquidation, spread, and executable price must be verified on Kraken.
8. **No market order when observed spread/slippage exceeds the strategy budget.**
9. **No new exposure around a scheduled high-impact event unless the strategy is explicitly event-tested.**
10. **Maximum simultaneous account risk is capped.** Correlated positions are aggregated, not treated as independent.
11. **Daily loss limit and consecutive-loss circuit breaker are mandatory.**
12. **Kill switch must cancel open orders and disable new ones while preserving position visibility.**
13. **Exchange/API disagreement produces abstention.** Never silently substitute a stale or different market.
14. **Every order uses a unique client ID and idempotency guard.** Retrying a request must not duplicate exposure.
15. **Live execution is impossible until paper and shadow modes pass predefined acceptance tests.**

### Tier 1 — single-factor descriptive rules

Single-factor rules label facts. They do not authorize trades alone.

16. `PRICE_ABOVE_LEVEL`: last completed candle closes above a pre-registered level.
17. `PRICE_BELOW_LEVEL`: last completed candle closes below a pre-registered level.
18. `NEW_LOCAL_HIGH/LOW`: price exceeds the rolling high/low over a declared lookback.
19. `TREND_SLOPE`: log-price regression or moving-average slope is positive/negative beyond noise threshold.
20. `VOLATILITY_STATE`: ATR/realized volatility percentile is low, normal, high, or extreme.
21. `VOLUME_STATE`: volume percentile versus same time-of-day baseline.
22. `RSI_STATE`: normalized momentum descriptor; overbought is not a short trigger.
23. `BAND_DISTANCE`: standardized distance from moving mean/Bollinger band.
24. `SPREAD_STATE`: spread in basis points versus recent distribution.
25. `DEPTH_STATE`: executable depth within fixed basis-point bands.
26. `FUNDING_STATE`: annualized/current funding percentile and sign.
27. `OI_CHANGE`: open-interest change over fixed horizons.
28. `TAKER_IMBALANCE`: aggressive buy volume / aggressive sell volume.
29. `ACCOUNT_POSITIONING`: long/short account ratio, explicitly separated from position-size ratio.
30. `EVENT_PROXIMITY`: time until scheduled event.
31. `DATA_QUALITY`: missingness, latency, sequence gaps, venue divergence.

### Tier 2 — two-factor relational rules

These begin to express mechanisms.

32. **Price up + OI up:** new positions support the move; direction of new positions remains uncertain until funding/flow confirms.
33. **Price up + OI down:** short covering/deleveraging is plausible; continuation quality is lower unless spot demand confirms.
34. **Price down + OI up:** new short exposure or trapped longs; liquidation risk grows.
35. **Price down + OI down:** long liquidation/deleveraging; selling may exhaust but is not automatically complete.
36. **Breakout + volume expansion:** stronger acceptance evidence than breakout on declining volume.
37. **Breakout + spread expansion:** apparent strength may be illiquidity; penalize rather than reward.
38. **Positive price + negative delta/taker flow:** absorption or weakening momentum; require confirmation.
39. **Flat price + strong aggressive buying:** ask-side absorption; record as resistance evidence.
40. **Flat price + strong aggressive selling:** bid-side absorption; record as support evidence.
41. **High RSI + rising OI:** continuation and crowded-long risk coexist; prohibit automatic shorting.
42. **High funding + rising OI:** leveraged-long crowding; reduce long size and increase liquidation sensitivity.
43. **Negative funding + rising price:** short-squeeze conditions; short entries require stronger confirmation.
44. **Volatility up + depth down:** gap/slippage risk; reduce quantity or abstain.
45. **Signal edge ≤ estimated round-trip costs:** trade prohibited.

### Tier 3 — setup rules using independent evidence groups

#### 3A. Breakout-and-retest long

46. Level must be registered before the breakout or generated by a deterministic method.
47. A completed candle must close beyond the level by a volatility-normalized buffer.
48. Volume or trade intensity must exceed its baseline, unless historical testing supports low-volume breakouts for that asset/regime.
49. Spread and depth must remain tradable.
50. OI/spot flow decides whether the move is new risk, squeeze, or ambiguous.
51. Entry occurs on successful retest or controlled continuation—not automatically at the first high.
52. Invalidation lies below reclaimed structure, not at an arbitrary percentage.
53. Expected first target must exceed costs plus minimum reward/risk threshold.
54. Signal expires if retest does not occur within its tested window.

#### 3B. Failed-breakout short

55. Price trades beyond resistance but cannot maintain a completed-candle close above it.
56. Rejection must be accompanied by at least one independent sign: seller imbalance, OI/funding crowding, depth withdrawal, or broad-market rejection.
57. Entry waits for loss of the rejection candle’s confirmation level or a failed retest.
58. Stop belongs beyond the failed-break extreme plus volatility allowance.
59. A still-rising market/sector benchmark reduces or vetoes the short.
60. If price reclaims and accepts above resistance, exit; do not widen the stop to preserve the opinion.

#### 3C. Pullback continuation

61. Higher-timeframe trend is intact.
62. Pullback volume/urgency is lower than impulse volume, or selling is visibly absorbed.
63. The pullback holds a predefined structural or volatility-adjusted zone.
64. Entry requires renewed buying/selling pressure in trend direction.
65. Failure of the structural level converts the state to neutral, not automatically opposite.

#### 3D. Mean-reversion trade

66. Regime must be classified as bounded/non-trending.
67. Deviation must be extreme relative to current volatility, not a fixed percentage.
68. Liquidity must be sufficient to survive temporary continuation.
69. Catalyst-driven moves are excluded.
70. Target is the estimated equilibrium/reference price; stop marks evidence that repricing is structural.

### Tier 4 — regime, portfolio, and qualitative rules

71. Classify at least: trend/range, volatility, liquidity, leverage/crowding, and event state.
72. Strategy eligibility is conditional on regime; ineligible strategies receive zero score regardless of indicator strength.
73. Regime classifiers may express probabilities, but those probabilities require calibration testing.
74. BTC, ETH, total-market, dollar/yield, and asset-specific context are features, not immutable leaders.
75. Correlation is rolling and unstable; portfolio risk uses stressed correlations as well as recent estimates.
76. Scheduled events require consensus, actual, prior, revisions, timestamp, and surprise—not headline sentiment alone.
77. Unscheduled news requires source credibility, novelty, scope, directness, and market reaction timestamps.
78. Price reaction can contradict textual sentiment; observed reaction outranks a simplistic positive/negative label.
79. Regulatory events are represented as state machines: proposed, scheduled, delayed, passed, failed, implemented, litigated.
80. No qualitative input may silently override Tier 0 risk limits.

### Tier 5 — execution and live-operation rules

81. Model market, limit, stop-market, stop-limit, and trailing orders separately.
82. A trailing stop is primarily an exit mechanism; using it as entry requires independent testing.
83. Record decision price, arrival price, submitted price, fills, fill time, quantity, fees, and missed quantity.
84. Compute implementation shortfall for every trade and aggregate by setup, venue, time, volatility, and order type.
85. Order-book walls are provisional; persistence, replenishment, executions, and cancellations matter more than one snapshot.
86. Never treat displayed depth outside an executable price band as immediately available liquidity.
87. Detect crossed books, zero/negative spreads, sequence gaps, timestamp drift, frozen feeds, and implausible ticks.
88. Reconcile internal position state with exchange state continuously.
89. On restart, recover exchange truth before submitting orders.
90. Enforce rate limits and exponential backoff without replaying stale decisions.
91. Separate market-data credentials from trading credentials; withdrawal permission is never granted.
92. Secrets are stored in an encrypted secret manager/environment, never source code, logs, screenshots, Git, or chat documents.

### Tier 6 — research governance and model learning

93. Every hypothesis is written before examining its test result.
94. Every parameter search is logged; the number of trials informs significance adjustment.
95. Use chronological walk-forward evaluation with embargo/purging where labels overlap.
96. Maintain untouched final holdout periods and cross-asset validation.
97. Benchmark complex models against no-trade, buy-and-hold, and simple rule baselines.
98. Report return, drawdown, turnover, tail loss, calibration, hit rate, payoff ratio, exposure, and implementation shortfall—not accuracy alone.
99. Use bootstrapping or appropriate dependence-aware inference; do not assume IID returns.
100. Recalculate performance after realistic fee, funding, spread, latency, and slippage stress.
101. Apply multiple-testing controls such as Reality Check/SPA/PBO/Deflated Sharpe where appropriate.
102. A rule graduates only when performance persists across time, assets, reasonable parameters, and cost stress.
103. Detect drift in features, labels, calibration, fill quality, and outcome distribution.
104. Retirement is valid learning. Failed rules remain recorded to prevent rediscovery and selective memory.
105. Human overrides require a reason code and are evaluated like any other strategy.

---

## 6. Rule-to-data contract

| Evidence group | Required fields | Derived values | Used by rules | Preferred source |
|---|---|---|---|---|
| Trades/OHLCV | trade ID, price, size, side/aggressor, event time; candles | returns, VWAP, realized vol, ATR, RSI, MACD, bands, volume percentiles | 16–23, 36, 46–70 | Binance public streams for research; Kraken executable venue confirmation |
| Top of book | bid/ask price and size, timestamp | spread, microprice, top imbalance | 24, 37–40, 44, 83–87 | Binance/Kraken WebSocket |
| Depth updates | price-level additions/removals, sequence IDs | OFI, depth bands, cancellations, replenishment, book slope | 25, 37–40, 44, 56, 85–87 | Binance diff-depth + snapshot; Kraken book feed |
| Derivatives | mark/index price, OI, funding, basis, liquidation trades | OI deltas, funding percentiles, basis, crowding | 26–35, 41–43, 50, 56 | Binance Futures; Kraken Futures where applicable |
| Positioning | global and top-trader account/position ratios | crowding change and divergence | 29, 41–43, 56 | Binance public derivatives endpoints |
| Cross-market | BTC/ETH/market index, DXY, yields, equities, volatility proxies | beta, correlation, relative strength, risk regime | 59, 71–75 | exchange feeds plus licensed/public macro provider |
| Events | event calendar, consensus, prior, actual, revision, release time | standardized surprise, event window | 9, 30, 76 | official agencies first; calendar vendor second |
| News/regulation | source, publication/event time, entity, event type, status | credibility, novelty, relevance, sentiment, reaction | 77–80 | official regulator/company sources; reputable news feeds |
| Execution | order/fill IDs, timestamps, requested/filled size, prices, fees | slippage, fill rate, latency, shortfall, adverse selection | 8, 14, 45, 81–90 | Kraken private API and local decision log |
| Account/risk | equity, free margin, positions, liquidation price, realized/unrealized PnL | account risk, portfolio heat, drawdown, correlation exposure | 1–13 | Kraken private API plus local ledger |
| Data quality | receive/event clocks, heartbeat, gaps, retries, venue divergence | freshness and quality flags | 6, 7, 13, 31, 87–90 | collector telemetry |

### Required access and credentials

Public Binance and Kraken market-data endpoints generally require no login. Private account state and order execution require exchange API credentials.

Minimum safe credential policy:

- one read-only Kraken key for balances, positions, orders, and fills;
- later, a separate trading key with order permission but **no withdrawal permission**;
- IP allowlisting where available;
- secrets outside the repository;
- key rotation and revocation procedure;
- sandbox/demo credentials where supported;
- no need to give the program—or an AI model—the user’s exchange password or two-factor code.

Official starting references: [Binance Developers](https://developers.binance.com/) and [Kraken API documentation](https://docs.kraken.com/api/).

---

## 7. Translation difficulty

| Rule class | Coding difficulty | Main difficulty |
|---|---:|---|
| Deterministic indicators and completed-candle levels | Easy | correct windows and no look-ahead |
| OI/funding/taker ratios | Easy–medium | timestamp alignment and endpoint semantics |
| Breakout/retest state machine | Medium | temporal state, expiry, wick vs close |
| Risk sizing and hard limits | Medium | exchange precision, gaps, portfolio aggregation |
| Full order-flow imbalance | Medium–high | synchronized book reconstruction and sequence gaps |
| Support/resistance generation | Medium–high | avoiding arbitrary hindsight-selected levels |
| Regime classification | High | non-stationarity and probability calibration |
| News/event interpretation | High | expectations, timestamps, source quality, novelty |
| Fill optimization/market making | Very high | latency, queue position, adverse selection |
| Self-updating prediction models | Very high | leakage, drift, governance, safe deployment |

---

## 8. Prototype architecture

```text
Exchange/API adapters
        ↓
Raw append-only event log
        ↓
Normalized synchronized records
        ↓
Feature engine ── Data-quality monitor
        ↓
Market-state classifier
        ↓
Rule engine + conflict resolver
        ↓
Risk/eligibility gate
        ↓
Decision journal
        ↓
Alerts → paper broker → shadow live → guarded execution
```

### Component boundaries

1. **Collectors:** know API formats, reconnection, sequence numbers, and timestamps; they do not make trading decisions.
2. **Normalizer:** converts venues into a stable schema and preserves raw provenance.
3. **Feature engine:** calculates versioned features with declared lookbacks.
4. **Rule engine:** evaluates immutable rule versions and produces reasons, not orders.
5. **Risk engine:** can veto every rule; rules cannot veto risk limits.
6. **Execution adapter:** converts approved intents to venue orders and reconciles fills.
7. **Journal/evaluator:** records every decision—including no-trades—and later labels outcomes.
8. **Interface:** explains the state in plain language and shows data freshness, conflicts, and invalidation.

This separation prevents an API change, indicator rewrite, or language-model interpretation from silently changing account risk.

---

## 9. The deliberately small first executable system

The first coded release should observe **one market and one timeframe**, initially `TAO/USDT` on Binance for research context, with Kraken `TAO/USD` used for executable verification.

### Inputs

- completed 15-minute OHLCV;
- 1-minute or 5-minute taker buy/sell volume;
- current and historical open interest;
- funding rate;
- global and top-trader positioning;
- best bid/ask and shallow depth;
- Kraken last/bid/ask;
- data timestamps and health.

### Outputs

- trend, volatility, and liquidity state;
- breakout level and whether it is pre-existing;
- `price × OI` quadrant;
- taker-flow state;
- crowding/funding state;
- one of: `WATCH_LONG`, `WATCH_SHORT`, `LONG_ELIGIBLE`, `SHORT_ELIGIBLE`, `NO_TRADE`, `DATA_STALE`;
- exact evidence, conflicts, invalidation, expiry, and estimated costs;
- no automated live order.

### First setup implemented

`BREAKOUT_RETEST_V1` using Rules 46–54 plus Tier 0 gates. The program must first prove it can reproduce the kind of TAO explanation that motivated this project:

```text
fresh higher high + completed close + OI response + taker flow + funding/crowding
+ executable-venue confirmation + resistance/retest state -> eligible or no-trade
```

The initial success criterion is not profit. It is **reproducibility**: given the same timestamped inputs, the program emits the same state and explanation every time.

---

## 10. Build and validation phases

### Phase A — specification

- Freeze schemas, units, time zones, symbol mapping, and rule IDs.
- Define “completed candle,” “break,” “hold,” “retest,” “rejection,” “fresh OI,” and “stale.”
- Create fixtures for known screenshots/timestamps.

### Phase B — observation pipeline

- Collect and persist raw Binance and Kraken data.
- Reconnect safely and detect gaps.
- Calculate features without trading logic.
- Compare calculations against exchange UI and independent calculations.

### Phase C — deterministic rule engine

- Implement Tier 0, Tier 1, and `BREAKOUT_RETEST_V1`.
- Unit-test boundary cases and state transitions.
- Emit machine-readable JSON plus plain-English explanations.

### Phase D — historical replay

- Replay candle-by-candle/event-by-event without access to future data.
- Include fees, funding, spread, slippage scenarios, and signal expiry.
- Log all parameter trials and apply multiple-testing controls.

### Phase E — forward paper observation

- Run continuously without placing orders.
- Record every eligible and rejected setup.
- Measure calibration, cost assumptions, alert timeliness, and data failures.

### Phase F — shadow and guarded execution

- Shadow intended orders against live books.
- Compare simulated and obtainable fills.
- Only after acceptance thresholds: permit tiny fixed-risk orders behind daily loss limits, position caps, idempotency, and kill switches.

---

## 11. What this doctrine refuses to do

- Promise a directional forecast because several indicators agree.
- Produce fabricated precision such as “63% chance” without calibrated historical evidence.
- optimize dozens of thresholds and report only the winner;
- treat Binance analytics as the executable Kraken price;
- allow a language model to place unbounded orders;
- infer causality from correlation or a single recent episode;
- erase losing hypotheses from the research record;
- mistake greater complexity for greater edge.

The deeper logic is simple: **a trading system is a chain of claims. Every link—data, feature, inference, eligibility, sizing, execution, and evaluation—must be independently inspectable.** Most failed systems focus on improving the forecast while leaving one of the other links unmeasured.

---

## 12. Immediate next build artifact

The next compartment should be a machine-readable `rule_catalog.yaml` containing Tier 0, Tier 1, and `BREAKOUT_RETEST_V1`, paired with a `data_dictionary.yaml`. Those two files become the contract for the first collector and prevent the Python code from becoming the undocumented source of truth.

---

## Research method note

This version was informed by 192 search results across 24 targeted research workstreams, prioritizing primary papers, official API documentation, and regulatory postmortems. Promotional indicator pages, affiliate content, unsupported forecasts, and “profitable bot” claims without cost-adjusted out-of-sample evidence were excluded. This is a foundation, not a completed literature review: later versions should add a formal source ledger, study-quality grading, and evidence links at individual-rule level.
