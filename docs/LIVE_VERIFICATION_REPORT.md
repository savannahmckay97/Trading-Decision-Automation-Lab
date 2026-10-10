# TDAL live provider verification — 2026-10-10 (UTC 07:55–08:17)

Clone: `~/tdal-livecheck`, branch `main` @ `fa84d57`. No source changes, no commits, no credentials used.
Python 3.13.5 venv; `python -m unittest discover -s tests` → 39 tests OK.
Network: Binance `fapi.binance.com` reachable (CloudFront POP `MEL51-P2`, i.e. Melbourne); **no HTTP 451 / geo-block observed** on any endpoint. Kraken via Cloudflare `MEL`.

## Bottom line

* Transport, symbol identity, payload shapes and units all match what `normalization.py` expects. Every endpoint returned HTTP 200 for TAO.
* **The live observer cannot produce anything but `DATA_STALE` with the current code.** The taker-flow freshness gate (limit 360 s) is unsatisfiable against real Binance data: the taker endpoint timestamps buckets by **open time** and publishes them 78–194 s after the bucket closes, so the age of the newest bucket was 378–777 s in 76/76 samples and in 7/7 + 1 one-shot observer cycles.
* Open-interest history also breaches its 360 s limit intermittently (11/76 samples, 14%).
* Fail-closed behaviour on real failures (invalid symbol, missing Kraken pair) is correct: `DATA_STALE`, no plausible false signal.
* `binance_book` and `binance_oi` are fetched every cycle but never consumed.

## 1. Endpoints and consumed fields

| name | URL (+params) | critical | fields consumed | consumer |
|---|---|---|---|---|
| binance_time | `GET fapi.binance.com/fapi/v1/time` | yes | `serverTime` | `exchange_time_ms` (completed-candle cutoff, candle-age gate), 5 s freshness |
| binance_price | `/fapi/v1/ticker/price?symbol=TAOUSDT` | yes | `price`, `time` | divergence vs Kraken; 15 s freshness |
| binance_book | `/fapi/v1/ticker/bookTicker?symbol=TAOUSDT` | no | `time` only (for event_time) | **none** |
| binance_klines | `/fapi/v1/klines?symbol=TAOUSDT&interval=15m&limit=150` | yes | idx 0 open time, 1–5 OHLCV (base-asset volume), 6 close time | all candle features |
| binance_oi | `/fapi/v1/openInterest?symbol=TAOUSDT` | no | `time` only | **none** |
| binance_oi_history | `/futures/data/openInterestHist?symbol=TAOUSDT&period=5m&limit=30` | yes | `timestamp`, `sumOpenInterest`, `sumOpenInterestValue` | OI 15m/60m change, quality score; 360 s freshness |
| binance_taker | `/futures/data/takerlongshortRatio?symbol=TAOUSDT&period=5m&limit=30` | yes | `timestamp`, `buyVol`, `sellVol` | taker ratio / buy share (score + retest gate `engine.py:187`); 360 s freshness |
| binance_funding | `/fapi/v1/fundingRate?symbol=TAOUSDT&limit=100` | no | `fundingTime`, `fundingRate` | annotation only |
| binance_global_ratio | `/futures/data/globalLongShortAccountRatio?…&period=5m&limit=2` | no | `timestamp`, `longAccount`, `shortAccount`, `longShortRatio` | annotation only |
| binance_top_account_ratio | `/futures/data/topLongShortAccountRatio?…&period=5m&limit=2` | no | same | annotation only |
| binance_top_position_ratio | `/futures/data/topLongShortPositionRatio?…&period=5m&limit=2` | no | same | annotation only |
| kraken_ticker | `GET api.kraken.com/0/public/Ticker?pair=TAOUSD` | yes | `result.<first>.a[0]`, `b[0]`, `c[0]` | spread gate, divergence gate; freshness on local receive time |

## 2. Live endpoint checks (curl, 07:55:11Z)

| endpoint | HTTP | result | notes (verified live) |
|---|---|---|---|
| binance_time | 200 | PASS | `{"serverTime":…}`; local−server skew −1650…+67 ms (incl. RTT) |
| binance_price | 200 | PASS | `{"price","symbol":"TAOUSDT","time"}`; `time` is last-trade time, age ≤7.4 s in 76 samples (limit 15 s) |
| binance_book | 200 | PASS (shape) / unused | has bid/ask/qty/time |
| binance_klines | 200 | PASS | 150 rows × 12 fields, strings for prices, ms ints; open-time step 900000; close_time = open+899999; **last row is the in-progress candle** (close_time > serverTime) in every snapshot — correctly excluded by `close_time_ms < exchange_time` (`features.py:72`) |
| binance_oi | 200 | PASS (shape) / unused | |
| binance_oi_history | 200 | **FAIL (freshness)** | shape OK, ascending, 5 m step; `timestamp` is a point stamp; published 44–161 s after stamp → newest-row age up to 444 s; >360 s in 11/76 samples |
| binance_taker | 200 | **FAIL (freshness)** | shape OK; `timestamp` = **bucket open time** (verified: buyVol+sellVol for ts T equals 5 m kline vol/taker-buy for kline open T, 6/6 buckets); published 78–194 s after bucket end → newest-row age 378–777 s, ≤360 s in 0/76 |
| binance_funding | 200 | PASS | 100 rows ascending; interval between settlements observed 3 h and 4 h (not 8 h) |
| global/top ratios | 200 | PASS | 2 rows each, fields as expected |
| kraken_ticker | 200 | PASS | result key `TAOUSD` (AssetPairs: altname `TAOUSD`, wsname `TAO/USD`, quote `ZUSD`, status online); `error: []`. Served from Cloudflare cache (`cf-cache-status: HIT`, `max-age=2`) so data may be ≤~2 s older than receive time |

Rate limits: Binance `x-mbx-used-weight-1m` reached only 5 for the `/fapi/v1/*` calls; `/futures/data/*` returns no weight header. No 429/418 seen. Kraken returns no rate-limit headers.

## 3. End-to-end observer

One-shot (`--base TAO --pretty`, 07:56:03Z): exit 0, `signal: DATA_STALE`, `action: NO_ORDER`, `execution_permission: DENIED_OBSERVATION_ONLY`. Only failing gate: `T0_DATA_FRESHNESS` → `binance_taker age_ms 664364 > 360000`. All other gates passed (spread 3.35 bps, divergence 4.47 bps). Features computed sensibly (close 281.42, ATR 1.32, support 274.31 / resistance 282.89).

Loop (`--loop --poll-seconds 60`, 07:57:16→08:03:38, SIGINT): 7 cycles, all `DATA_STALE` on taker age (437–685 s). Completed candle advanced correctly from close 07:44:59.999 to 07:59:59.999 at the 08:00:23 cycle. Setup state stayed IDLE, `state_updated: false` every cycle; `setup_states` table empty; 84 raw events, 7 snapshots/decisions/heartbeats, 0 collection errors. Stopped cleanly ("observer stopped").

Because every cycle was gated, the live loop proves only that blocked cycles freeze state. To exercise the non-blocked path on real data I ran a **scratch-only diagnostic** (bypasses gates; not production behaviour) feeding the 7 stored live snapshots through `calculate_features` → `advance_breakout_retest`: state changed only on the first snapshot of each completed candle (07:45 close, 08:00 close) and was unchanged on the 5 repeated polls → repeated polling of the same completed candle does not advance state.

## 4. Fail-closed checks (live)

* `--base NOTACOIN`: Binance `/fapi/v1/*` → HTTP 400 `-1121 Invalid symbol`; `/futures/data/*` → **HTTP 200 `[]`** (no error); Kraken → `EQuery:Unknown asset pair`. Observer → `DATA_STALE` via `T0_FAIL_CLOSED` (`insufficient completed candles: need 97, got 0`), features null. PASS. Took 6.2 s because 4xx errors are retried 3× (`http.py:32-40`).
* `--base 1000PEPE` (Binance listed, Kraken not): `DATA_STALE`, `critical_missing: [kraken_ticker]`, spread/divergence null → also RISK gates fail. PASS.
* `--base BTC`: Kraken returns key `XXBTZUSD` for request `BTCUSD`; `_kraken_ticker` takes the first result value so it works (divergence 3.96 bps); still `DATA_STALE` on taker/OI freshness.

## 5. Unconsumed requests

`binance_book` (`collectors.py:101`) and `binance_oi` (`collectors.py:103`) are fetched every cycle and stored in `raw_events`, but `normalization.build_snapshot` never reads them; only `_event_time` touches them (`collectors.py:132`). Confirmed by grep. Funding and the three positioning ratios are normalized and reported but feed no gate or score.

## 6. Correctness findings

1. **Taker freshness gate is unsatisfiable live — observer is permanently DATA_STALE** (verified live). `engine.py:49-55` limits `binance_taker` age to 360 s, measured from `collectors.py:137` = max bucket `timestamp`, which Binance defines as bucket open time and publishes 78–194 s after bucket close. Minimum possible age ≈ 300 s + publication lag; observed 378–777 s. Impact: no WATCH_LONG / LONG_ELIGIBLE can ever be produced live; the README's live-loop behaviour is unachievable. Fail-safe direction, but the live path is non-functional. The test fixture (`tests/test_observer.py:42`) stamps taker/OI rows 60 s before exchange time, which is why the suite does not catch this.
2. **OI-history freshness flaps** (verified live): same 360 s limit, publication lag 44–161 s → stale in ~14% of samples. Once (1) is fixed, decisions will still intermittently go DATA_STALE.
3. **Taker and OI windows are anchored to the latest published 5 m row, not to the completed 15 m candle** (inferred from code + live timestamps). `features.py:32-34` take the last 3 taker buckets / latest OI row regardless of `completed_candle_close_time_ms`. Live example at 08:00:23 evaluating candle 07:45–08:00: taker buckets were 07:40, 07:45, 07:50 (covering 07:40–07:55, missing the candle's last 5 min and including 5 min of the previous candle); OI "15m change" used 07:55 vs 07:40. Since state advances only on the first non-blocked poll of a candle (`evaluator.py:236-238`), the taker buy share used by the retest gate (`engine.py:187`) and the quality score depend on poll timing relative to Binance publication. Not look-ahead (data were available at decision time) but a candle/flow misalignment that can change a retest decision. Replay reproduces whatever was stored, so parity holds.
4. **4xx/429/451 are retried with sleeps** (`http.py:32-40`; 4xx retry verified live, 429/451 inferred). Deterministic errors cost ~3 s per cycle; retrying 429s without honouring `Retry-After` risks escalating to a 418 ban. Also, `HTTPError` bodies (e.g. Binance `-1121`) are not captured in `collection_errors`, only "HTTP Error 400".
5. **`/futures/data/*` endpoints return `200 []` for unknown symbols** (verified live): these are not recorded as collection errors or `critical_missing`; fail-closed still happens later via FeatureError, so no false result, but the diagnostic is indirect.
6. Minor (verified live): Kraken ticker comes through a 2 s CDN cache and has no provider timestamp; freshness uses local receive time (`engine.py:62`), so true quote age is up to ~2 s older than reported. Within the 15 s budget.

## 7. Verified vs inferred

Verified live: HTTP status, no geo-block, payload shapes, symbol identity (TAOUSDT / TAOUSD), kline timestamp semantics and incomplete last candle, taker timestamp = bucket open, publication lags, freshness failures, end-to-end run, state freezing, invalid-symbol fail-closed, unconsumed requests.
Inferred (code reading, not exercised live): 429/451 handling, decision impact of finding 3 on an actual retest (no non-blocked live cycle exists), behaviour of other geographies.

## 8. Commands

```sh
gh repo clone savannahmckay97/Trading-Decision-Automation-Lab ~/tdal-livecheck
cd ~/tdal-livecheck && ~/miniconda/bin/python -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m unittest discover -s tests
# direct endpoint checks: curl -s -D <hdr> -A TradingDecisionAutomationLab/0.1 <each URL in §1>
curl -s "https://api.kraken.com/0/public/AssetPairs?pair=TAOUSD"
python -I $SCRATCH/lag_sampler.py 1260          # 21 min, 15 s cadence: time/oiHist/taker/price ages
.venv/bin/python observer.py --base TAO --pretty --db data/livecheck_oneshot.sqlite
timeout -s INT 370 .venv/bin/python observer.py --base TAO --loop --poll-seconds 60 --db data/livecheck_loop.sqlite
.venv/bin/python observer.py --base NOTACOIN --db data/livecheck_invalid.sqlite
.venv/bin/python observer.py --base 1000PEPE --db data/livecheck_1000PEPE.sqlite
.venv/bin/python observer.py --base BTC --db data/livecheck_BTC.sqlite
.venv/bin/python $SCRATCH/idempotence_diag.py data/livecheck_loop.sqlite   # scratch-only, bypasses gates
```

Raw evidence (not committed; kept locally): `~/tdal-livecheck/evidence/` (`raw/*.json|*.hdr`, `lag.jsonl`, `oneshot.json`, `loop.jsonl`, `invalid.json`, `base_*.json`, plus `lag_sampler.py` and `idempotence_diag.py`); SQLite DBs in `~/tdal-livecheck/data/`.

Tracking: findings 1–3 are tracked in issue #7.
