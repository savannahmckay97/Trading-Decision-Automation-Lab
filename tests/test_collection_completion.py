from __future__ import annotations

from unittest.mock import patch
import unittest

from market_observer.collectors import CollectionError, PublicCollectors
from market_observer.config import ObserverConfig
from market_observer.evaluator import evaluate_snapshot
from market_observer.normalization import build_snapshot
from market_observer.service import ObserverService
from market_observer.storage import EventStore
from test_observer import SequenceCollectors, events_for


def late_failure(stage="breakout"):
    events, errors = events_for(stage)
    latest = max(event.received_time_ms for event in events.values())
    events.pop("binance_funding")
    errors.append(CollectionError("binance_usds_m_futures", "binance_funding", "TAOUSDT",
                                  False, latest + 30_000, "request exhausted retries"))
    return events, errors


class CompletionTests(unittest.TestCase):
    def test_direct_normalization_includes_late_optional_error_receipt(self):
        events, errors = late_failure()
        row = build_snapshot(events, errors, "TAOUSDT", "TAOUSD")
        self.assertEqual(row["observed_at_ms"], errors[-1].received_time_ms)
        result = evaluate_snapshot(row, ObserverConfig(), None)
        self.assertEqual(result.signal, "DATA_STALE")
        stale = next(g["evidence"]["stale_or_invalid"] for g in result.gates if g["rule_id"] == "T0_DATA_FRESHNESS")
        for endpoint in ("binance_taker", "binance_oi_history"):
            self.assertEqual(stale[endpoint]["age_ms"], 30_000)

    def test_live_late_optional_failure_freezes_existing_setup(self):
        with EventStore(":memory:") as store:
            service = ObserverService(ObserverConfig(), store,
                                      SequenceCollectors([events_for("breakout"), late_failure("retest")]))
            first = service.cycle()
            previous = store.load_setup_state("BREAKOUT_RETEST_V1", "TAOUSDT")
            blocked = service.cycle()
            self.assertEqual(first["signal"], "WATCH_LONG")
            self.assertEqual(blocked["signal"], "DATA_STALE")
            self.assertFalse(blocked["setup"]["state_updated"])
            self.assertEqual(store.load_setup_state("BREAKOUT_RETEST_V1", "TAOUSDT"), previous)
            self.assertEqual(blocked["action"], "NO_ORDER")

    def test_collector_completion_after_successful_receipts_is_used_by_service(self):
        events, _ = events_for("breakout")
        latest = max(event.received_time_ms for event in events.values())
        class Batch(PublicCollectors):
            def _fetch_one(self, name, *args):
                return events[name]
        collectors = Batch(ObserverConfig())
        with patch("market_observer.collectors.now_ms", return_value=latest + 30_000), EventStore(":memory:") as store:
            blocked = ObserverService(ObserverConfig(), store, collectors).cycle()
            self.assertEqual(collectors.completed_at_ms, latest + 30_000)
            self.assertEqual(blocked["signal"], "DATA_STALE")
            self.assertFalse(blocked["setup"]["state_updated"])

    def test_no_completion_metadata_uses_service_clock_after_collect(self):
        events, errors = events_for("breakout")
        latest = max(event.received_time_ms for event in events.values())
        class Custom:
            def collect(self):
                return events, errors
        with patch("market_observer.service.now_ms", return_value=latest + 30_000), EventStore(":memory:") as store:
            self.assertEqual(ObserverService(ObserverConfig(), store, Custom()).cycle()["signal"], "DATA_STALE")

    def test_completion_cannot_precede_any_receipt_or_have_malformed_clock(self):
        events, errors = late_failure()
        latest = errors[-1].received_time_ms
        for invalid in (latest - 1, True, "clock", -1, float(latest)):
            with self.subTest(completion=invalid), self.assertRaises(ValueError):
                build_snapshot(events, errors, "TAOUSDT", "TAOUSD", completed_at_ms=invalid)
        row = build_snapshot(events, errors, "TAOUSDT", "TAOUSD", completed_at_ms=latest + 1000)
        self.assertEqual(row["observed_at_ms"], latest + 1000)

    def test_optional_failure_can_finish_after_error_receipt(self):
        events, _ = events_for("breakout")
        latest = max(event.received_time_ms for event in events.values())
        class Batch(PublicCollectors):
            def _fetch_one(self, name, *args):
                if name == "binance_funding":
                    raise ValueError("timeout")
                return events[name]
        collectors = Batch(ObserverConfig())
        with patch("market_observer.collectors.now_ms", side_effect=[latest + 30_000, latest + 31_000]):
            good, errors = collectors.collect()
        row = build_snapshot(good, errors, "TAOUSDT", "TAOUSD", completed_at_ms=collectors.completed_at_ms)
        self.assertEqual(row["observed_at_ms"], latest + 31_000)
        self.assertEqual(row["quality"]["collection_errors"][0]["received_time_ms"], latest + 30_000)
        self.assertEqual(evaluate_snapshot(row, ObserverConfig(), None).signal, "DATA_STALE")


if __name__ == "__main__":
    unittest.main()
