from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from market_observer.asof import AsOfError, validate_snapshot_asof
from market_observer.config import ObserverConfig
from market_observer.evaluator import evaluate_snapshot
from market_observer.features import calculate_features
from market_observer.normalization import build_snapshot
from market_observer.replay import ReplayRunner
from market_observer.service import ObserverService
from market_observer.storage import EventStore
from test_observer import SequenceCollectors, events_for


def snapshot(stage="breakout"):
    return build_snapshot(*events_for(stage), "TAOUSDT", "TAOUSD")


class AsOfContractTests(unittest.TestCase):
    def test_valid_snapshot_is_unchanged_and_open_candle_remains_excluded(self):
        row = snapshot()
        original = copy.deepcopy(row)
        # The actual collector uses max candle close for this envelope field.
        row["provenance"]["binance_klines"]["event_time_ms"] = row["candles_15m"][-1]["close_time_ms"]
        self.assertGreater(row["provenance"]["binance_klines"]["event_time_ms"], row["observed_at_ms"])
        before = copy.deepcopy(row)
        validate_snapshot_asof(row)
        evaluation = evaluate_snapshot(row, ObserverConfig(), None)
        self.assertEqual(evaluation.signal, "WATCH_LONG")
        self.assertEqual(evaluation.features, calculate_features(original, ObserverConfig()))
        self.assertEqual(row, before)

    def test_future_rows_are_rejected_even_when_provenance_is_fresh(self):
        for name in ("open_interest_5m", "taker_flow_5m", "funding_history"):
            with self.subTest(name=name):
                row = snapshot()
                row[name][-1]["timestamp_ms"] = row["observed_at_ms"] + 30_000
                with self.assertRaisesRegex(AsOfError, name + r".*future input"):
                    validate_snapshot_asof(row)
        for name in ("global", "top_accounts", "top_positions"):
            with self.subTest(name=name):
                row = snapshot()
                row["positioning"][name][-1]["timestamp_ms"] = row["observed_at_ms"] + 1
                with self.assertRaisesRegex(AsOfError, "positioning.*future input"):
                    validate_snapshot_asof(row)

    def test_future_receipts_and_events_are_rejected(self):
        for field, reason in (("received_time_ms", "future receipt"), ("event_time_ms", "future event")):
            with self.subTest(field=field):
                row = snapshot()
                row["provenance"]["binance_taker"][field] = row["observed_at_ms"] + 1
                with self.assertRaisesRegex(AsOfError, "binance_taker.*" + reason):
                    validate_snapshot_asof(row)

    def test_future_exchange_clock_is_rejected(self):
        row = snapshot()
        row["exchange_time_ms"] = row["observed_at_ms"] + 1
        with self.assertRaisesRegex(AsOfError, "future exchange time"):
            validate_snapshot_asof(row)

    def test_timestamp_equality_is_available(self):
        row = snapshot()
        for event in row["provenance"].values():
            event["event_time_ms"] = row["observed_at_ms"]
            event["received_time_ms"] = row["observed_at_ms"]
        for name in ("open_interest_5m", "taker_flow_5m", "funding_history"):
            row[name][-1]["timestamp_ms"] = row["observed_at_ms"]
        for series in row["positioning"].values():
            series[-1]["timestamp_ms"] = row["observed_at_ms"]
        validate_snapshot_asof(row)

    def test_missing_or_malformed_required_provenance_is_rejected(self):
        for value in (None, [], {}, {"binance_taker": None}):
            with self.subTest(value=value):
                row = snapshot()
                row["provenance"] = value
                with self.assertRaises(AsOfError):
                    validate_snapshot_asof(row)
        row = snapshot()
        row["provenance"]["binance_taker"] = None
        with self.assertRaisesRegex(AsOfError, "endpoint record must be an object"):
            validate_snapshot_asof(row)

    def test_timestamps_must_be_nonnegative_integers_without_coercion(self):
        for value in (None, True, -1, 1.5, "2000000000000"):
            for path in ("observed", "receipt", "row"):
                with self.subTest(value=value, path=path):
                    row = snapshot()
                    if path == "observed":
                        row["observed_at_ms"] = value
                    elif path == "receipt":
                        row["provenance"]["binance_taker"]["received_time_ms"] = value
                    else:
                        row["open_interest_5m"][-1]["timestamp_ms"] = value
                    with self.assertRaisesRegex(AsOfError, "non-negative integer"):
                        validate_snapshot_asof(row)

    def test_missing_or_malformed_required_series_is_rejected(self):
        for name in ("open_interest_5m", "taker_flow_5m"):
            for value in (None, [], {}, [None], [{}]):
                with self.subTest(name=name, value=value):
                    row = snapshot()
                    row[name] = value
                    with self.assertRaises(AsOfError):
                        validate_snapshot_asof(row)

    def test_optional_absence_remains_unavailable_and_present_data_requires_provenance(self):
        row = snapshot()
        row["funding_history"] = []
        row["positioning"] = {name: [] for name in row["positioning"]}
        for name in ("binance_funding", "binance_global_ratio", "binance_top_account_ratio", "binance_top_position_ratio"):
            del row["provenance"][name]
        result = evaluate_snapshot(row, ObserverConfig(), None)
        self.assertIsNone(result.features["funding_rate"])
        self.assertIsNone(result.features["global_long_account_share"])
        # A successfully fetched empty optional endpoint may have no event time.
        empty = snapshot()
        empty["funding_history"] = []
        empty["provenance"]["binance_funding"]["event_time_ms"] = None
        self.assertIsNone(evaluate_snapshot(empty, ObserverConfig(), None).features["funding_rate"])
        for endpoint in ("binance_funding", "binance_global_ratio", "binance_top_account_ratio", "binance_top_position_ratio"):
            with self.subTest(endpoint=endpoint):
                row = snapshot()
                del row["provenance"][endpoint]
                with self.assertRaisesRegex(AsOfError, "missing provenance"):
                    validate_snapshot_asof(row)
                row = snapshot()
                row["provenance"][endpoint]["event_time_ms"] = None
                with self.assertRaisesRegex(AsOfError, "non-negative integer"):
                    validate_snapshot_asof(row)

    def test_kraken_without_provider_event_time_uses_receipt_freshness(self):
        row = snapshot()
        row["provenance"]["kraken_ticker"]["event_time_ms"] = None
        self.assertEqual(evaluate_snapshot(row, ObserverConfig(), None).signal, "WATCH_LONG")
        row["provenance"]["kraken_ticker"]["received_time_ms"] = row["observed_at_ms"] + 1
        with self.assertRaisesRegex(AsOfError, "future receipt"):
            validate_snapshot_asof(row)

    def test_precomputed_features_cannot_bypass_guard_or_mutate_prior_state(self):
        prior = evaluate_snapshot(snapshot(), ObserverConfig(), None).state
        original = copy.deepcopy(prior)
        row = snapshot("retest")
        cached = calculate_features(row, ObserverConfig())
        row["open_interest_5m"][-1]["timestamp_ms"] = row["observed_at_ms"] + 1
        for features in (None, cached):
            with self.subTest(cached=features is not None):
                with patch("market_observer.evaluator.calculate_features") as calculate:
                    with self.assertRaisesRegex(AsOfError, "future input"):
                        evaluate_snapshot(row, ObserverConfig(), prior, features=features)
                    calculate.assert_not_called()
        self.assertEqual(prior, original)


class AsOfIntegrationTests(unittest.TestCase):
    def test_future_raw_oi_blocks_live_and_replay_without_advancing_state(self):
        initial = events_for("breakout")
        invalid = events_for("retest")
        invalid[0]["binance_oi_history"].payload[-1]["timestamp"] = invalid[0]["binance_time"].received_time_ms + 30_000
        rows = [build_snapshot(*batch, "TAOUSDT", "TAOUSD") for batch in (initial, invalid)]
        with EventStore(":memory:") as store:
            service = ObserverService(ObserverConfig(), store, SequenceCollectors([initial, invalid]))
            first = service.cycle()
            previous = store.load_setup_state("BREAKOUT_RETEST_V1", "TAOUSDT")
            blocked = service.cycle()
            self.assertEqual(first["signal"], "WATCH_LONG")
            self.assertEqual(blocked["signal"], "DATA_STALE")
            self.assertFalse(blocked["setup"]["state_updated"])
            self.assertEqual(store.load_setup_state("BREAKOUT_RETEST_V1", "TAOUSDT"), previous)
            self.assertIn("open_interest_5m", blocked["gates"][0]["evidence"]["error"])
            self.assertEqual(blocked["action"], "NO_ORDER")
            self.assertEqual(store.counts()["decisions"], 2)
        result = ReplayRunner(ObserverConfig()).run(rows)
        first, blocked = result["decisions"]
        self.assertEqual(blocked["signal"], "DATA_STALE")
        self.assertEqual(blocked["setup"]["state"], first["setup"]["state"])
        self.assertFalse(blocked["setup"]["state_updated"])
        self.assertIsNone(blocked["features"])
        self.assertEqual(blocked["sequence"]["status"], "ASOF_INVALID")
        self.assertEqual(result["summary"]["sequence_counts"]["ASOF_INVALID"], 1)
        self.assertEqual(blocked["action"], "NO_ORDER")

    def test_replay_guard_runs_before_features_and_rejects_future_receipt(self):
        row = snapshot()
        row["provenance"]["binance_taker"]["received_time_ms"] += 1
        with patch("market_observer.replay.calculate_features") as calculate:
            decision = ReplayRunner(ObserverConfig()).run([row])["decisions"][0]
            calculate.assert_not_called()
        self.assertEqual(decision["signal"], "DATA_STALE")
        self.assertIn("future receipt", decision["gates"][0]["evidence"]["error"])

    def test_replay_malformed_observation_timestamp_is_blocked_without_coercion(self):
        for value in (None, True, 1.5, "2000000000000"):
            with self.subTest(value=value):
                row = snapshot()
                row["observed_at_ms"] = value
                decision = ReplayRunner(ObserverConfig()).run([row])["decisions"][0]
                self.assertEqual(decision["signal"], "DATA_STALE")
                self.assertEqual(decision["observed_at_ms"], value)


if __name__ == "__main__":
    unittest.main()
