from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from market_observer.config import ObserverConfig
from market_observer.engine import STRATEGY_ID
from market_observer.evaluator import evaluate_snapshot
from market_observer.features import FeatureError
from market_observer.normalization import build_snapshot
from market_observer.replay import ReplayRunner
from market_observer.service import ObserverService
from market_observer.storage import EventStore
from test_observer import INTERVAL, SequenceCollectors, events_for


def gap_events(*, intervals=1, spread=False, offset=0):
    """A real later candle window, including a failure in an unevaluated bar."""
    events, errors = deepcopy(events_for("retest", spread=spread, received_offset=offset))
    shift = intervals * INTERVAL
    for name, event in list(events.items()):
        payload = deepcopy(event.payload)
        if name == "binance_klines":
            for bar in payload:
                bar[0] += shift
                bar[6] += shift
            # The skipped candle invalidates the original breakout hypothesis.
            payload[-3][3] = "98"
            payload[-3][4] = "99"
        elif isinstance(payload, list):
            for row in payload:
                key = "fundingTime" if name == "binance_funding" else "timestamp"
                row[key] += shift
        elif name == "binance_time":
            payload["serverTime"] += shift
        elif "time" in payload:
            payload["time"] += shift
        events[name] = replace(event, payload=payload, received_time_ms=event.received_time_ms + shift,
                               event_time_ms=event.event_time_ms + shift)
    return events, errors


def snapshot(batch):
    return build_snapshot(*batch, "TAOUSDT", "TAOUSD")


def continuity(gates):
    return next(g["evidence"] for g in gates if g["rule_id"] == "T0_STATE_CONTINUITY")


class StateContinuityTests(unittest.TestCase):
    def setUp(self):
        self.config = ObserverConfig()
        self.first = snapshot(events_for("breakout"))
        self.prior = evaluate_snapshot(self.first, self.config, None).state

    def test_skipped_failure_resets_live_and_replay_without_retest_eligibility(self):
        for intervals in (1, 3):
            with self.subTest(intervals=intervals):
                later = snapshot(gap_events(intervals=intervals))
                original = deepcopy(self.prior)
                live = evaluate_snapshot(later, self.config, self.prior)
                replay = ReplayRunner(self.config).run([self.first, later])["decisions"][-1]
                self.assertEqual(live.signal, "NO_TRADE")
                self.assertEqual(live.state["state"], "IDLE")
                self.assertIsNone(live.state["setup_id"])
                self.assertEqual(live.state["bars_since_breakout"], 0)
                self.assertEqual(live.state["transition"], "GAP_RESET")
                self.assertTrue(live.state_updated)
                self.assertEqual(replay["setup"], {**live.state, "state_updated": True})
                self.assertEqual(replay["risk"], live.risk)
                self.assertEqual(continuity(replay["gates"]), continuity(live.gates))
                self.assertEqual(continuity(live.gates)["missing_bars"], intervals)
                self.assertTrue(continuity(live.gates)["setup_reset"])
                self.assertEqual(self.prior, original)

    def test_restart_persists_reset_and_duplicate_poll_does_not_reset_again(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observer.sqlite"
            with EventStore(path) as store:
                ObserverService(self.config, store, SequenceCollectors([events_for("breakout")])).cycle()
            with EventStore(path) as store:
                reset = ObserverService(self.config, store, SequenceCollectors([gap_events()])).cycle()
                self.assertEqual(reset["signal"], "NO_TRADE")
                self.assertEqual(continuity(reset["gates"])["status"], "GAP")
            with EventStore(path) as store:
                duplicate = ObserverService(self.config, store, SequenceCollectors([gap_events(offset=1)])).cycle()
                self.assertFalse(duplicate["setup"]["state_updated"])
                self.assertEqual(continuity(duplicate["gates"])["status"], "DUPLICATE")
                self.assertFalse(continuity(duplicate["gates"])["setup_reset"])
                self.assertIsNone(store.load_setup_state(STRATEGY_ID, "TAOUSDT")["setup_id"])

    def test_blocked_gap_freezes_state_then_reset_matches_replay_after_recovery(self):
        blocked = snapshot(gap_events(spread=True))
        good = snapshot(gap_events(offset=1))
        live_blocked = evaluate_snapshot(blocked, self.config, self.prior)
        self.assertEqual(live_blocked.signal, "RISK_BLOCKED")
        self.assertFalse(live_blocked.state_updated)
        self.assertEqual(live_blocked.state, self.prior)
        self.assertFalse(continuity(live_blocked.gates)["setup_reset"])
        recovered = evaluate_snapshot(good, self.config, live_blocked.state)
        replay = ReplayRunner(self.config).run([self.first, blocked, good])["decisions"]
        self.assertEqual(replay[1]["setup"], {**self.prior, "state_updated": False})
        self.assertEqual(replay[2]["setup"], {**recovered.state, "state_updated": True})
        self.assertTrue(continuity(replay[2]["gates"])["setup_reset"])

    def test_new_breakout_after_gap_has_new_setup_not_old_hypothesis(self):
        later = snapshot(gap_events())
        bar = later["candles_15m"][-2]
        bar.update(high=bar["high"] + 10, close=bar["close"] + 10)
        live = evaluate_snapshot(later, self.config, self.prior)
        self.assertEqual(live.signal, "WATCH_LONG")
        self.assertNotEqual(live.state["setup_id"], self.prior["setup_id"])
        self.assertEqual(live.state["bars_since_breakout"], 0)
        self.assertTrue(continuity(live.gates)["setup_reset"])

    def test_contiguous_and_duplicate_evaluations_preserve_existing_behavior(self):
        retest = evaluate_snapshot(snapshot(events_for("retest")), self.config, self.prior)
        self.assertEqual(retest.signal, "LONG_ELIGIBLE")
        self.assertEqual(continuity(retest.gates)["status"], "CONTIGUOUS")
        duplicate = evaluate_snapshot(snapshot(events_for("retest")), self.config, retest.state)
        self.assertEqual(duplicate.state, retest.state)
        self.assertFalse(duplicate.state_updated)

    def test_backward_irregular_or_unconfirmed_persisted_clock_fails_closed(self):
        for previous in (self.prior["last_evaluated_close_time_ms"] + INTERVAL,
                         self.prior["last_evaluated_close_time_ms"] - 1, None, True, "bad"):
            with self.subTest(previous=previous):
                state = {**self.prior, "last_evaluated_close_time_ms": previous}
                original = deepcopy(state)
                with self.assertRaises(FeatureError):
                    evaluate_snapshot(self.first, self.config, state)
                self.assertEqual(state, original)


if __name__ == "__main__":
    unittest.main()
