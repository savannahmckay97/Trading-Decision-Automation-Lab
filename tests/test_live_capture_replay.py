from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from market_observer.asof import AsOfError, validate_snapshot_asof
from market_observer.collectors import PublicCollectors
from market_observer.config import ObserverConfig
from market_observer.evaluator import evaluate_snapshot
from market_observer.normalization import build_snapshot
from market_observer.replay import (ReplayError, ReplayRunner, load_jsonl_snapshots,
                                    load_sqlite_snapshots)
from market_observer.service import ObserverService
from market_observer.snapshots import SnapshotError, decode_snapshot
from market_observer.storage import EventStore
from market_observer.util import canonical_json
from test_observer import SequenceCollectors, payloads


def capture(stage="breakout", offset=0):
    """Real collector timestamp semantics with deterministic public responses.

    Derivatives are published 194s after close; independent responses arrive
    after Binance time, and the kline envelope includes a future scheduled close.
    """
    data, exchange = payloads(stage)
    data["binance_price"]["time"] = exchange + 400
    data["binance_oi"]["time"] = exchange + 200

    class PublicResponses:
        def get(self, base, path, params):
            spec = next(spec for spec in collectors._specifications()
                        if spec[3:6] == (base, path, params))
            return deepcopy(data[spec[0]])

    collectors = PublicCollectors(ObserverConfig(), PublicResponses())
    events = {}
    for spec in collectors._specifications():
        delay = 1200 if spec[0] == "kraken_ticker" else 800
        if spec[0] == "binance_time":
            delay = 100
        with patch("market_observer.collectors.now_ms", return_value=exchange + delay + offset):
            events[spec[0]] = collectors._fetch_one(*spec)
    return events, []


def snapshot(stage="breakout"):
    return build_snapshot(*capture(stage), "TAOUSDT", "TAOUSD")


class LiveCaptureReplayTests(unittest.TestCase):
    def test_envelope_events_between_exchange_and_observation_match_live_guard(self):
        row = snapshot()
        before = deepcopy(row)
        for endpoint in ("kraken_ticker", "binance_price", "binance_oi"):
            self.assertGreater(row["provenance"][endpoint]["event_time_ms"], row["exchange_time_ms"])
        self.assertGreater(row["provenance"]["binance_klines"]["event_time_ms"], row["observed_at_ms"])
        validate_snapshot_asof(row)
        decoded = decode_snapshot(row, require_complete=True)
        self.assertEqual(decoded, row)
        self.assertEqual(row, before)
        live = evaluate_snapshot(row, ObserverConfig(), None)
        replay = ReplayRunner(ObserverConfig()).run([row])["decisions"][0]
        self.assertEqual(live.signal, "WATCH_LONG")
        self.assertEqual(replay["features"], live.features)
        self.assertEqual(replay["risk"], live.risk)

    def test_collector_service_jsonl_sqlite_replay_parity_preserves_evidence(self):
        batches = [capture("breakout"), capture("retest")]
        rows = [build_snapshot(*batch, "TAOUSDT", "TAOUSD") for batch in batches]
        original = deepcopy(rows)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "captured.sqlite"
            with EventStore(database) as store:
                service = ObserverService(ObserverConfig(), store, SequenceCollectors(batches))
                live = [service.cycle(), service.cycle()]
            jsonl = root / "capture.jsonl"
            jsonl.write_text("".join(canonical_json(row) + "\n" for row in rows))
            expected = ReplayRunner(ObserverConfig()).run(rows)
            from_jsonl = ReplayRunner(ObserverConfig()).run(load_jsonl_snapshots(jsonl))
            from_sqlite = ReplayRunner(ObserverConfig()).run(load_sqlite_snapshots(database))
            self.assertEqual(expected, from_jsonl)
            self.assertEqual(expected, from_sqlite)
            loaded = list(load_jsonl_snapshots(jsonl))
            self.assertEqual(loaded, original)
            self.assertEqual(list(load_sqlite_snapshots(database)), original)
            self.assertEqual([row["signal"] for row in live], ["WATCH_LONG", "LONG_ELIGIBLE"])
            for recorded, replayed in zip(live, from_sqlite["decisions"]):
                for field in ("signal", "setup", "features", "risk", "breakout_quality"):
                    self.assertEqual(canonical_json(recorded[field]), canonical_json(replayed[field]))
                for a, b in zip(recorded["gates"], replayed["gates"]):
                    self.assertEqual(a["status"], b["status"])
                    if a["rule_id"] != "T0_OBSERVATION_ONLY":
                        self.assertEqual(a, b)
                self.assertEqual(recorded["action"], "NO_ORDER")
                self.assertEqual(replayed["action"], "NO_ORDER")
        self.assertEqual(rows, original)

    def test_future_envelope_events_and_receipts_remain_rejected(self):
        for endpoint in ("kraken_ticker", "binance_price", "binance_taker", "binance_oi"):
            for field in ("event_time_ms", "received_time_ms"):
                with self.subTest(endpoint=endpoint, field=field):
                    row = snapshot()
                    row["provenance"][endpoint][field] = row["observed_at_ms"] + 1
                    with self.assertRaises(SnapshotError):
                        decode_snapshot(row, require_complete=True)
                    with self.assertRaises(AsOfError):
                        validate_snapshot_asof(row)

    def test_series_after_exchange_cutoff_are_rejected_even_before_receipt(self):
        for series in ("open_interest_5m", "taker_flow_5m", "funding_history"):
            with self.subTest(series=series):
                row = snapshot()
                row[series][-1]["timestamp_ms"] = row["exchange_time_ms"] + 1
                self.assertLess(row[series][-1]["timestamp_ms"], row["observed_at_ms"])
                with self.assertRaisesRegex(SnapshotError, "future input"):
                    decode_snapshot(row, require_complete=True)
                with self.assertRaises(AsOfError):
                    validate_snapshot_asof(row)
        row = snapshot()
        row["positioning"]["global"][-1]["timestamp_ms"] = row["exchange_time_ms"] + 1
        with self.assertRaises(SnapshotError):
            decode_snapshot(row, require_complete=True)

    def test_persisted_future_events_fail_both_loaders(self):
        row = snapshot()
        row["provenance"]["kraken_ticker"]["event_time_ms"] = row["observed_at_ms"] + 1
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jsonl = root / "invalid.jsonl"
            jsonl.write_text(canonical_json(row) + "\n")
            with self.assertRaises(ReplayError):
                list(load_jsonl_snapshots(jsonl))
            database = root / "invalid.sqlite"
            with EventStore(database) as store:
                store.append_snapshot(row)
            with self.assertRaises(ReplayError):
                list(load_sqlite_snapshots(database))


if __name__ == "__main__":
    unittest.main()
