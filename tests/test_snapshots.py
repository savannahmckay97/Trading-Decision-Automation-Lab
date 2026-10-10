from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from market_observer.config import ObserverConfig
from market_observer.dataset import DatasetError, build_dataset_artifact, write_dataset
from market_observer.normalization import build_snapshot
from market_observer.replay import ReplayError, ReplayRunner, load_jsonl_snapshots, load_sqlite_snapshots
from market_observer.snapshots import SnapshotError, decode_snapshot, load_snapshot_json
from market_observer.storage import EventStore
from market_observer.util import canonical_json
from test_observer import events_for


def full_snapshot(stage="breakout"):
    events, errors = events_for(stage)
    return build_snapshot(events, errors, "TAOUSDT", "TAOUSD")


class SnapshotBoundaryTests(unittest.TestCase):
    def test_jsonl_round_trip_preserves_complete_replay_behavior(self):
        snapshots = [full_snapshot(), full_snapshot("retest")]
        expected = ReplayRunner(ObserverConfig()).run(snapshots)
        self.assertEqual([d["signal"] for d in expected["decisions"]], ["WATCH_LONG", "LONG_ELIGIBLE"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.jsonl"
            rows, manifest = build_dataset_artifact(snapshots, source="synthetic")
            write_dataset(rows, manifest, jsonl_path=path, manifest_path=Path(directory)/"manifest.json")
            loaded = list(load_jsonl_snapshots(path))
            self.assertEqual(loaded, snapshots)  # Includes all funding/positioning/market decimals.
            self.assertEqual(ReplayRunner(ObserverConfig()).run(loaded), expected)

    def test_sqlite_round_trip_preserves_complete_replay_behavior(self):
        snapshots = [full_snapshot(), full_snapshot("retest")]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observer.sqlite"
            with EventStore(path) as store:
                for item in snapshots:
                    store.append_snapshot(item)
            loaded = list(load_sqlite_snapshots(path))
            self.assertEqual(loaded, snapshots)
            self.assertEqual(ReplayRunner(ObserverConfig()).run(loaded),
                             ReplayRunner(ObserverConfig()).run(snapshots))

    def test_rejects_nested_research_results_in_dataset_and_direct_replay(self):
        for path in [(), ("candles_15m", 0), ("market",), ("quality",),
                     ("provenance", "binance_klines"), ("open_interest_5m", 0),
                     ("positioning", "global", 0)]:
            for field in ("labels", "decisions", "paper_fills"):
                with self.subTest(path=path, field=field):
                    item = full_snapshot()
                    target = item
                    for key in path:
                        target = target[key]
                    target[field] = [{"future_return": "123"}]
                    with self.assertRaises(DatasetError):
                        build_dataset_artifact([item], source="synthetic")
                    with self.assertRaises(ReplayError):
                        ReplayRunner(ObserverConfig()).run([item])

    def test_rejects_invalid_decimal_and_timestamp_types(self):
        for value in ("NaN", "Infinity", "-Infinity", "oops", True, {"labels": []}):
            with self.subTest(value=value):
                item = full_snapshot()
                item["candles_15m"][0]["close"] = value
                with self.assertRaises(SnapshotError):
                    decode_snapshot(item)
        for value in (True, -1, 1.5, "1000"):
            item = full_snapshot()
            item["observed_at_ms"] = value
            with self.assertRaises(SnapshotError):
                decode_snapshot(item)

    def test_json_decimal_tokens_are_exact_and_numeric_ids_stay_strings(self):
        item = full_snapshot()
        payload = canonical_json(item).replace('"103"', '103.000000000000000000001')
        decoded = load_snapshot_json(payload)
        self.assertEqual(decoded["market"]["binance_last_price"], Decimal("103.000000000000000000001"))
        self.assertIsInstance(decoded["provenance"]["binance_klines"]["raw_id"], str)

    def test_duplicate_json_keys_and_nonfinite_tokens_are_rejected(self):
        for payload in ('{"labels": [], "labels": []}', '{"value": NaN}'):
            with self.assertRaises(SnapshotError):
                load_snapshot_json(payload)

    def test_invalid_persisted_inputs_report_replay_error(self):
        item = full_snapshot()
        item["candles_15m"][0]["close"] = "NaN"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"invalid.jsonl"
            path.write_text(canonical_json(item))
            with self.assertRaisesRegex(ReplayError, "line 1"):
                list(load_jsonl_snapshots(path))
            db = Path(directory)/"invalid.sqlite"
            with EventStore(db) as store:
                store.append_snapshot(item)
            with self.assertRaisesRegex(ReplayError, "stored snapshot"):
                list(load_sqlite_snapshots(db))

    def test_auxiliary_rows_after_exchange_cutoff_are_rejected(self):
        for name in ("open_interest_5m", "taker_flow_5m", "funding_history", "global"):
            with self.subTest(name=name):
                item = full_snapshot()
                item["observed_at_ms"] += 1000
                rows = item["positioning"][name] if name == "global" else item[name]
                rows[-1]["timestamp_ms"] = item["exchange_time_ms"] + 1
                with self.assertRaisesRegex(SnapshotError, "future input"):
                    decode_snapshot(item)
                result = ReplayRunner(ObserverConfig()).run([item])
                self.assertEqual(result["decisions"][0]["signal"], "DATA_STALE")

    def test_market_quantity_ranges_fail_closed(self):
        cases = [("open_interest_5m", "open_interest", "-1"),
                 ("open_interest_5m", "open_interest_value", "-1"),
                 ("taker_flow_5m", "buy_volume", "-1"),
                 ("taker_flow_5m", "sell_volume", "-1"),
                 ("global", "long_share", "1.1"),
                 ("global", "short_share", "-0.1"),
                 ("global", "long_short_ratio", "0"),
                 ("candles_15m", "volume", "-1"),
                 ("candles_15m", "low", "0")]
        for name, field, value in cases:
            with self.subTest(name=name, field=field):
                item = full_snapshot()
                rows = item["positioning"][name] if name == "global" else item[name]
                rows[-1][field] = value
                with self.assertRaises(SnapshotError):
                    decode_snapshot(item)
                with self.assertRaises(ReplayError):
                    ReplayRunner(ObserverConfig()).run([item])

    def test_early_close_and_invalid_ohlc_are_rejected(self):
        for field, value in (("close_time_ms", 1), ("high", Decimal("1"))):
            item = full_snapshot()
            item["candles_15m"][-1][field] = value
            with self.assertRaises(SnapshotError):
                decode_snapshot(item)

    def test_provenance_identity_and_availability_are_checked(self):
        for endpoint in ("binance_klines", "kraken_ticker"):
            for field, value in (("source", "wrong_venue"), ("endpoint", "wrong_endpoint"),
                                 ("symbol", "BTCUSDT"), ("received_time_ms", "future")):
                with self.subTest(endpoint=endpoint, field=field):
                    item = full_snapshot()
                    item["provenance"][endpoint][field] = (
                        item["observed_at_ms"] + 1 if value == "future" else value)
                    with self.assertRaises(SnapshotError):
                        decode_snapshot(item)

    def test_decode_does_not_mutate_input(self):
        item = full_snapshot()
        original = deepcopy(item)
        result = decode_snapshot(item)
        result["candles_15m"][0]["close"] = Decimal("1")
        self.assertEqual(item, original)


if __name__ == "__main__":
    unittest.main()
