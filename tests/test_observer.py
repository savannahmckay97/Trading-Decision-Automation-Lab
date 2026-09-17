from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from market_observer.collectors import CollectionError, PublicCollectors, RawEvent
from market_observer.config import ObserverConfig
from market_observer.engine import advance_breakout_retest, assess_gates, breakout_quality
from market_observer.features import FeatureError, calculate_features
from market_observer.normalization import build_snapshot
from market_observer.risk import maximum_quantity
from market_observer.replay import ReplayError, ReplayRunner, sequence_status, write_replay
from market_observer.service import ObserverService
from market_observer.storage import EventStore, default_database_path, restore_database


BASE_TIME = 2_000_000_000_000
INTERVAL = 900_000


def kline(open_time, open_, high, low, close, volume):
    return [open_time, str(open_), str(high), str(low), str(close), str(volume), open_time + INTERVAL - 1, "0", 1, "0", "0", "0"]


def payloads(stage="flat", spread=False, missing=None):
    complete_count = 101 if stage == "retest" else 100
    rows = []
    for i in range(complete_count):
        values = (100, 101, 99, 100, 10)
        if i == 99 and stage in {"breakout", "retest"}:
            values = (100, 104, 99, 103, 100)
        if i == 100 and stage == "retest":
            values = (103, 103.5, 100.8, 101.5, 20)
        rows.append(kline(BASE_TIME + i * INTERVAL, *values))
    last_close = Decimal(str(rows[-1][4]))
    exchange_time = BASE_TIME + complete_count * INTERVAL + 1_000
    rows.append(kline(BASE_TIME + complete_count * INTERVAL, last_close, last_close + 1, last_close - 1, last_close, 5))
    five_minute_times = [exchange_time - (29 - i) * 300_000 - 60_000 for i in range(30)]
    oi = [
        {"symbol": "TAOUSDT", "sumOpenInterest": str(1000 + i * 2), "sumOpenInterestValue": str(100_000 + i), "timestamp": ts}
        for i, ts in enumerate(five_minute_times)
    ]
    taker = [
        {"buySellRatio": "2.333", "buyVol": "70", "sellVol": "30", "timestamp": ts}
        for ts in five_minute_times
    ]
    funding = [
        {"symbol": "TAOUSDT", "fundingTime": exchange_time - (99 - i) * 28_800_000, "fundingRate": str(Decimal(i - 50) / Decimal("100000"))}
        for i in range(100)
    ]
    ratio = [
        {"symbol": "TAOUSDT", "longAccount": "0.60", "shortAccount": "0.40", "longShortRatio": "1.5", "timestamp": five_minute_times[-2]},
        {"symbol": "TAOUSDT", "longAccount": "0.62", "shortAccount": "0.38", "longShortRatio": "1.6316", "timestamp": five_minute_times[-1]},
    ]
    bid = last_close - (Decimal("1") if spread else Decimal("0.05"))
    ask = last_close + (Decimal("1") if spread else Decimal("0.05"))
    result = {
        "binance_time": {"serverTime": exchange_time},
        "binance_price": {"symbol": "TAOUSDT", "price": str(last_close), "time": exchange_time},
        "binance_book": {"symbol": "TAOUSDT", "bidPrice": str(last_close - Decimal("0.01")), "askPrice": str(last_close + Decimal("0.01")), "time": exchange_time},
        "binance_klines": rows,
        "binance_oi": {"symbol": "TAOUSDT", "openInterest": oi[-1]["sumOpenInterest"], "time": exchange_time},
        "binance_oi_history": oi,
        "binance_taker": taker,
        "binance_funding": funding,
        "binance_global_ratio": ratio,
        "binance_top_account_ratio": ratio,
        "binance_top_position_ratio": ratio,
        "kraken_ticker": {"error": [], "result": {"TAOUSD": {"a": [str(ask), "1", "1"], "b": [str(bid), "1", "1"], "c": [str(last_close), "1"]}}},
    }
    if missing:
        result.pop(missing, None)
    return result, exchange_time


def events_for(stage="flat", spread=False, missing=None, received_offset=0):
    items, exchange_time = payloads(stage, spread, missing)
    critical = {"binance_time", "binance_price", "binance_klines", "binance_oi_history", "binance_taker", "kraken_ticker"}
    events = {}
    for name, payload in items.items():
        if name in {"binance_oi_history", "binance_taker", "binance_global_ratio", "binance_top_account_ratio", "binance_top_position_ratio"}:
            event_time = int(payload[-1]["timestamp"])
        elif name == "binance_funding":
            event_time = int(payload[-1]["fundingTime"])
        else:
            event_time = exchange_time
        source = "kraken_spot" if name == "kraken_ticker" else "binance_usds_m_futures"
        symbol = "TAOUSD" if name == "kraken_ticker" else "TAOUSDT"
        events[name] = RawEvent(source, name, symbol, event_time, exchange_time + received_offset, payload, name in critical)
    errors = []
    if missing:
        errors.append(CollectionError("fixture", missing, "TAOUSDT", missing in critical, exchange_time + received_offset, "fixture missing"))
    return events, errors


class SequenceCollectors:
    def __init__(self, batches):
        self.batches = list(batches)

    def collect(self):
        return self.batches.pop(0)


class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.config = ObserverConfig()

    def snapshot(self, **kwargs):
        events, errors = events_for(**kwargs)
        return build_snapshot(events, errors, "TAOUSDT", "TAOUSD")

    def test_incomplete_candle_is_excluded_and_features_are_causal(self):
        features = calculate_features(self.snapshot(stage="breakout"), self.config)
        self.assertEqual(features["completed_close"], Decimal("103"))
        self.assertEqual(features["resistance"], Decimal("101"))
        self.assertEqual(features["volume_percentile"], Decimal("1"))
        self.assertEqual(features["price_oi_quadrant"], "PRICE_UP_OI_UP")

    def test_gap_in_required_window_fails_closed(self):
        snapshot = self.snapshot()
        del snapshot["candles_15m"][-20]
        with self.assertRaises(FeatureError):
            calculate_features(snapshot, self.config)

    def test_spread_and_divergence_are_explicit(self):
        snapshot = self.snapshot(spread=True)
        features = calculate_features(snapshot, self.config)
        gates = assess_gates(snapshot, features, self.config)
        spread_gate = next(item for item in gates if item["rule_id"] == "T0_SPREAD_BUDGET")
        self.assertEqual(spread_gate["status"], "FAIL")
        venue_gate = next(item for item in gates if item["rule_id"] == "T0_VENUE_CONFIRMATION")
        self.assertFalse(venue_gate["evidence"]["parity_assumed"])


class StateMachineTests(unittest.TestCase):
    def setUp(self):
        self.config = ObserverConfig()

    def feature_set(self, stage):
        events, errors = events_for(stage=stage)
        snapshot = build_snapshot(events, errors, "TAOUSDT", "TAOUSD")
        features = calculate_features(snapshot, self.config)
        gates = assess_gates(snapshot, features, self.config)
        quality = breakout_quality(features, self.config, all(item["status"] == "PASS" for item in gates if item["category"] == "DATA_STALE"))
        return features, quality

    def test_breakout_then_retest_becomes_eligible(self):
        breakout, quality = self.feature_set("breakout")
        state, signal = advance_breakout_retest(None, breakout, quality, self.config)
        self.assertEqual((state["state"], signal), ("BREAKOUT_OBSERVED", "WATCH_LONG"))
        retest, retest_quality = self.feature_set("retest")
        state, signal = advance_breakout_retest(state, retest, retest_quality, self.config)
        self.assertEqual((state["state"], signal), ("LONG_ELIGIBLE", "LONG_ELIGIBLE"))

    def test_same_completed_candle_is_idempotent(self):
        features, quality = self.feature_set("breakout")
        first, _ = advance_breakout_retest(None, features, quality, self.config)
        second, _ = advance_breakout_retest(first, features, quality, self.config)
        self.assertEqual(first, second)


class ServiceTests(unittest.TestCase):
    def test_service_persists_restart_state_and_never_orders(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observer.sqlite"
            first_collectors = SequenceCollectors([events_for("breakout")])
            with EventStore(path) as store:
                first = ObserverService(ObserverConfig(), store, first_collectors).cycle()
                self.assertEqual(first["signal"], "WATCH_LONG")
                self.assertEqual(first["action"], "NO_ORDER")
            second_collectors = SequenceCollectors([events_for("retest", received_offset=1)])
            with EventStore(path) as store:
                second = ObserverService(ObserverConfig(), store, second_collectors).cycle()
                self.assertEqual(second["signal"], "LONG_ELIGIBLE")
                self.assertEqual(second["execution_permission"], "DENIED_OBSERVATION_ONLY")
                counts = store.counts()
                self.assertEqual(counts["normalized_snapshots"], 2)
                self.assertEqual(counts["decisions"], 2)

    def test_backup_and_restore_preserve_state_and_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "observer.sqlite"
            backup = root / "observer.backup.sqlite"
            restored = root / "observer.restored.sqlite"
            with EventStore(original) as store:
                decision = ObserverService(
                    ObserverConfig(), store, SequenceCollectors([events_for("breakout")])
                ).cycle()
                expected_setup = decision["setup"]["setup_id"]
                store.backup_to(backup)
            restore_database(backup, restored)
            with EventStore(restored) as store:
                self.assertEqual(store.counts()["decisions"], 1)
                state = store.load_setup_state("BREAKOUT_RETEST_V1", "TAOUSDT")
                self.assertEqual(state["setup_id"], expected_setup)

    def test_missing_critical_input_emits_data_stale_and_freezes_state(self):
        with tempfile.TemporaryDirectory() as directory:
            with EventStore(Path(directory) / "observer.sqlite") as store:
                collectors = SequenceCollectors([events_for("breakout"), events_for("retest", missing="binance_taker", received_offset=1)])
                service = ObserverService(ObserverConfig(), store, collectors)
                first = service.cycle()
                second = service.cycle()
                self.assertEqual(first["setup"]["state"], "BREAKOUT_OBSERVED")
                self.assertEqual(second["signal"], "DATA_STALE")
                self.assertFalse(second["setup"]["state_updated"])
                self.assertEqual(second["setup"]["state"], "BREAKOUT_OBSERVED")

    def test_wide_executable_spread_blocks_state_update(self):
        with EventStore(":memory:") as store:
            service = ObserverService(ObserverConfig(), store, SequenceCollectors([events_for("breakout", spread=True)]))
            decision = service.cycle()
            self.assertEqual(decision["signal"], "RISK_BLOCKED")
            self.assertEqual(decision["setup"]["state"], "IDLE")


class SafetyTests(unittest.TestCase):
    def test_project_identity_and_state_are_separate(self):
        root = Path(__file__).resolve().parents[1]
        self.assertIn('name = "trading-decision-automation-lab"', (root / "pyproject.toml").read_text())
        self.assertEqual(default_database_path().name, "trading_decision_lab.sqlite")
        self.assertTrue((root / "PROJECT_BOUNDARY.md").exists())
        self.assertTrue((root / "ADOPTION_LOG.md").exists())
        self.assertFalse((root / "rules.json").exists())

    def test_configuration_forbids_live_mode(self):
        with self.assertRaises(ValueError):
            ObserverConfig(mode="guarded_live").validate()

    def test_collector_contains_only_public_market_endpoints(self):
        paths = [spec[4].lower() for spec in PublicCollectors(ObserverConfig())._specifications()]
        forbidden = {"/fapi/v1/order", "/fapi/v2/account", "/fapi/v2/positionrisk", "/0/private"}
        self.assertFalse(any(any(path.startswith(item) for item in forbidden) for path in paths))

    def test_costs_reduce_maximum_quantity(self):
        without_cost = maximum_quantity(equity_usd=1000, risk_fraction="0.0025", entry_price=100, invalidation_price=99, round_trip_cost_bps=0)
        with_cost = maximum_quantity(equity_usd=1000, risk_fraction="0.0025", entry_price=100, invalidation_price=99, round_trip_cost_bps=20)
        self.assertLess(with_cost["maximum_quantity"], without_cost["maximum_quantity"])

    def test_risk_fraction_above_one_percent_is_rejected(self):
        with self.assertRaises(ValueError):
            maximum_quantity(equity_usd=1000, risk_fraction="0.02", entry_price=100, invalidation_price=99, round_trip_cost_bps=0)


class ReplayTests(unittest.TestCase):
    def snapshot(self, stage, received_offset=0):
        events, errors = events_for(stage=stage, received_offset=received_offset)
        return build_snapshot(events, errors, "TAOUSDT", "TAOUSD")

    def test_replay_uses_production_state_machine_and_never_orders(self):
        result = ReplayRunner(ObserverConfig()).run(
            [self.snapshot("breakout"), self.snapshot("retest", received_offset=1)]
        )
        self.assertEqual(
            [item["signal"] for item in result["decisions"]],
            ["WATCH_LONG", "LONG_ELIGIBLE"],
        )
        self.assertEqual(result["summary"]["sequence_counts"]["CONTIGUOUS"], 1)
        self.assertTrue(all(item["action"] == "NO_ORDER" for item in result["decisions"]))
        self.assertTrue(
            all(item["execution_permission"] == "DENIED_HISTORICAL_REPLAY" for item in result["decisions"])
        )
        self.assertFalse(result["summary"]["performance_claim_available"])

    def test_sequence_gap_accounting_is_explicit(self):
        status = sequence_status(1_000_000, 1_000_000 + 3 * INTERVAL, INTERVAL)
        self.assertEqual(status["status"], "GAP")
        self.assertEqual(status["missing_bars"], 2)

    def test_replay_rejects_reverse_chronology(self):
        first = self.snapshot("breakout")
        second = self.snapshot("retest", received_offset=1)
        first["observed_at_ms"] = second["observed_at_ms"] + 1
        with self.assertRaises(ReplayError):
            ReplayRunner(ObserverConfig()).run([first, second])

    def test_replay_output_is_immutable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "replay.json"
            write_replay({"result": "first"}, path)
            with self.assertRaises(FileExistsError):
                write_replay({"result": "replacement"}, path)
            self.assertIn("first", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
