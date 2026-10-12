"""Regressions for the independent review's main-branch findings (issue #22, M1-M5,
atomic state/decision writes and configuration-bound state)."""
from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

from market_observer.collectors import RawEvent, payload_identity_error
from market_observer.config import ObserverConfig
from market_observer.engine import STRATEGY_ID, assess_gates
from market_observer.features import calculate_features
from market_observer.normalization import build_snapshot
from market_observer.paper import PaperExecutionConfig, build_paper_fill_artifact
from market_observer.replay import ReplayError, ReplayRunner, bind_replay_to_snapshots, load_sqlite_snapshots
from market_observer.service import ObserverService
from market_observer.storage import EventStore
from market_observer.util import canonical_json
from test_observer import SequenceCollectors, events_for
from test_paper import INTERVAL, candle, eligible_decision, replay, snapshot


def with_payload(events, name, payload):
    event = events[name]
    events[name] = RawEvent(event.source, event.endpoint, event.symbol, event.event_time_ms,
                            event.received_time_ms, payload, event.critical)
    return events


def venue_gate(snap):
    features = calculate_features(snap, ObserverConfig())
    return next(g for g in assess_gates(snap, features, ObserverConfig()) if g["rule_id"] == "T0_VENUE_CONFIRMATION")


class ExecutableQuoteGateTests(unittest.TestCase):
    """M2: the venue gate checks the Kraken ask, not only last trades."""

    def test_matching_last_trades_with_a_distant_ask_block(self):
        events, errors = events_for("flat")
        price = events["binance_price"].payload["price"]
        with_payload(events, "kraken_ticker", {"error": [], "result": {"TAOUSD": {
            "a": ["110.05", "1", "1"], "b": ["109.95", "1", "1"], "c": [price, "1"]}}})
        gate = venue_gate(build_snapshot(events, errors, "TAOUSDT", "TAOUSD"))
        self.assertEqual(gate["status"], "FAIL")
        self.assertEqual(gate["evidence"]["last_trade_divergence_bps"], Decimal(0))
        self.assertGreater(gate["evidence"]["executable_ask_divergence_bps"], Decimal(600))

    def test_a_close_ask_passes(self):
        events, errors = events_for("flat")
        self.assertEqual(venue_gate(build_snapshot(events, errors, "TAOUSDT", "TAOUSD"))["status"], "PASS")


class ProviderIdentityTests(unittest.TestCase):
    """M4: payloads that name another instrument are never used."""

    def test_identity_rules(self):
        self.assertIsNone(payload_identity_error("binance_price", {"symbol": "TAOUSDT"}, "TAOUSDT"))
        self.assertIsNotNone(payload_identity_error("binance_price", {"symbol": "BTCUSDT"}, "TAOUSDT"))
        self.assertIsNotNone(payload_identity_error("binance_oi_history",
                                                    [{"symbol": "TAOUSDT"}, {"symbol": "BTCUSDT"}], "TAOUSDT"))
        self.assertIsNone(payload_identity_error("binance_klines", [[0, "1"]], "TAOUSDT"))  # request-bound
        self.assertIsNone(payload_identity_error("kraken_ticker", {"result": {"TAOUSD": {}}}, "TAOUSD"))
        self.assertIsNone(payload_identity_error("kraken_ticker", {"result": {"XXBTZUSD": {}}}, "XBTUSD"))
        self.assertIsNotNone(payload_identity_error("kraken_ticker", {"result": {"XXBTZUSD": {}}}, "TAOUSD"))
        self.assertIsNotNone(payload_identity_error("kraken_ticker", {"result": {"TAOUSD": {}, "X": {}}}, "TAOUSD"))

    def test_wrong_symbol_price_fails_closed_and_is_recorded(self):
        events, errors = events_for("breakout")
        payload = dict(events["binance_price"].payload, symbol="BTCUSDT")
        snap = build_snapshot(with_payload(events, "binance_price", payload), errors, "TAOUSDT", "TAOUSD")
        self.assertIsNone(snap["market"]["binance_last_price"])
        self.assertIn("binance_price", snap["quality"]["critical_missing"])
        self.assertEqual([item["endpoint"] for item in snap["quality"]["identity_mismatches"]], ["binance_price"])
        with tempfile.TemporaryDirectory() as directory, EventStore(Path(directory) / "o.sqlite") as store:
            decision = ObserverService(ObserverConfig(), store, SequenceCollectors([
                (with_payload(*[events_for("breakout")[0], "binance_price", payload]), errors)])).cycle()
        self.assertEqual(decision["signal"], "DATA_STALE")


class SharedSnapshotContractTests(unittest.TestCase):
    """M3: live applies the same structural contract as replay."""

    def test_misdated_older_candle_fails_closed_live_without_creating_state(self):
        events, errors = events_for("breakout")
        rows = deepcopy(events["binance_klines"].payload)
        rows[-20][6] -= 12_345
        with_payload(events, "binance_klines", rows)
        with tempfile.TemporaryDirectory() as directory, EventStore(Path(directory) / "o.sqlite") as store:
            decision = ObserverService(ObserverConfig(), store, SequenceCollectors([(events, errors)])).cycle()
            self.assertEqual(decision["signal"], "DATA_STALE")
            self.assertIsNone(store.load_setup_state(STRATEGY_ID, "TAOUSDT"))


class ReplayBindingTests(unittest.TestCase):
    """M1: a replay is accepted only if it is the replay of the verified snapshots."""

    def setUp(self):
        events, errors = events_for("breakout")
        self.rows = [build_snapshot(events, errors, "TAOUSDT", "TAOUSD")]
        result = ReplayRunner(ObserverConfig(mode="historical_replay")).run(self.rows)
        self.replay = json.loads(canonical_json(result))  # as read back from its JSON file

    def test_the_genuine_replay_binds(self):
        bind_replay_to_snapshots(self.rows, self.replay)

    def test_foreign_edited_or_reconfigured_replays_are_rejected(self):
        edited = deepcopy(self.replay)
        edited["decisions"][0]["research_symbol"] = "BTCUSDT"
        reconfigured = deepcopy(self.replay)
        reconfigured["baseline"]["configuration"]["failure_buffer_atr"] = 0.5
        flat_events, flat_errors = events_for("flat")
        foreign = json.loads(canonical_json(ReplayRunner(ObserverConfig(mode="historical_replay")).run(
            [build_snapshot(flat_events, flat_errors, "TAOUSDT", "TAOUSD")])))
        for name, candidate in (("edited", edited), ("reconfigured", reconfigured), ("foreign", foreign)):
            with self.subTest(name), self.assertRaises(ReplayError):
                bind_replay_to_snapshots(self.rows, candidate)


class PaperPollDeduplicationTests(unittest.TestCase):
    """M5: repeated polls of one eligible candle are one paper opportunity."""

    def test_two_eligible_polls_of_one_candle_make_one_fill(self):
        candles = [candle(0, 99, 101, 98, 100), candle(INTERVAL, 101, 103, 100, 102),
                   candle(2 * INTERVAL, 102, 104, 101, 103)]
        result = build_paper_fill_artifact(
            [snapshot(3 * INTERVAL, 3 * INTERVAL, candles)],
            replay([eligible_decision("first"), eligible_decision("repeat")]),
            dataset_id="dataset_fixture",
            config=PaperExecutionConfig(fee_bps_per_side=Decimal("5"), slippage_bps_per_side=Decimal("5"),
                                        max_hold_bars=2),
        )
        self.assertEqual([fill["decision_id"] for fill in result["fills"]], ["first"])
        self.assertEqual(result["summary"]["duplicate_eligible_polls_count"], 1)


class StatePersistenceTests(unittest.TestCase):
    def test_state_and_decision_commit_together_or_not_at_all(self):
        with tempfile.TemporaryDirectory() as directory, EventStore(Path(directory) / "o.sqlite") as store:
            broken = {"research_symbol": "TAOUSDT", "setup": {"state": "IDLE"}, "signal": "NO_TRADE"}  # no time
            with self.assertRaises(KeyError):
                store.commit_cycle(broken, state={"state": "BREAKOUT_OBSERVED"}, strategy_id=STRATEGY_ID,
                                   configuration_id="config_a")
            self.assertIsNone(store.load_setup_state(STRATEGY_ID, "TAOUSDT"))
            self.assertEqual(store.counts()["decisions"], 0)

    def test_state_is_not_resumed_under_a_different_configuration(self):
        with tempfile.TemporaryDirectory() as directory, EventStore(Path(directory) / "o.sqlite") as store:
            store.save_setup_state(STRATEGY_ID, "TAOUSDT", {"state": "RETEST_PENDING"}, "config_a")
            self.assertEqual(store.load_setup_state(STRATEGY_ID, "TAOUSDT", "config_a"), {"state": "RETEST_PENDING"})
            self.assertIsNone(store.load_setup_state(STRATEGY_ID, "TAOUSDT", "config_b"))
            store.save_setup_state(STRATEGY_ID, "TAOUSDT", {"state": "IDLE"})  # legacy, unrecorded
            self.assertIsNone(store.load_setup_state(STRATEGY_ID, "TAOUSDT", "config_a"))

    def test_service_resets_a_setup_saved_under_other_parameters(self):
        with tempfile.TemporaryDirectory() as directory, EventStore(Path(directory) / "o.sqlite") as store:
            first = ObserverService(ObserverConfig(), store, SequenceCollectors([events_for("breakout")])).cycle()
            self.assertEqual(first["setup"]["state"], "BREAKOUT_OBSERVED")
            again = lambda config: ObserverService(config, store, SequenceCollectors(
                [events_for("breakout", received_offset=again.offset)])).cycle()
            again.offset = 1
            same = again(ObserverConfig())
            # Same configuration: the saved setup resumes, and the candle was already evaluated.
            self.assertFalse(same["setup"]["state_updated"])
            again.offset = 2
            changed = again(ObserverConfig(expiry_bars=4))
            # Changed parameters: the saved setup is not resumed; the candle is evaluated afresh.
            self.assertTrue(changed["setup"]["state_updated"])
            self.assertEqual(changed["setup"]["state"], "BREAKOUT_OBSERVED")
            self.assertNotEqual(changed["configuration_id"], first["configuration_id"])



class FollowUpReviewTests(unittest.TestCase):
    """Follow-up review of PR #24 (Codex inline comments and the independent review)."""

    def test_operational_settings_dont_reset_a_saved_setup(self):
        with tempfile.TemporaryDirectory() as directory, EventStore(Path(directory) / "o.sqlite") as store:
            first = ObserverService(ObserverConfig(), store, SequenceCollectors([events_for("breakout")])).cycle()
            again = ObserverService(ObserverConfig(request_timeout_seconds=30, poll_seconds=120), store,
                                    SequenceCollectors([events_for("breakout", received_offset=1)])).cycle()
            self.assertEqual(again["configuration_id"], first["configuration_id"])
            self.assertFalse(again["setup"]["state_updated"])  # resumed, no invented transition
            self.assertEqual(again["setup"]["transition"], "IDLE_TO_BREAKOUT_OBSERVED")

    def test_a_rule_change_resets_visibly(self):
        with tempfile.TemporaryDirectory() as directory, EventStore(Path(directory) / "o.sqlite") as store:
            ObserverService(ObserverConfig(), store, SequenceCollectors([events_for("flat")])).cycle()
            changed = ObserverService(ObserverConfig(expiry_bars=4), store,
                                      SequenceCollectors([events_for("flat", received_offset=1)])).cycle()
            self.assertEqual(changed["setup"]["transition"], "CONFIGURATION_RESET")

    def test_kraken_codes_for_pairs_the_config_generates(self):
        cases = [("BTC", "XXBTZUSD"), ("BTC", "XBTUSD"), ("DOGE", "XDGUSD"), ("TAO", "TAOUSD"), ("ETH", "XETHZUSD")]
        for base, key in cases:
            pair = ObserverConfig(base_asset=base).executable_pair
            self.assertIsNone(payload_identity_error("kraken_ticker", {"result": {key: {}}}, pair), (pair, key))
        self.assertIsNotNone(payload_identity_error("kraken_ticker", {"result": {"XXBTZUSD": {}}}, "TAOUSD"))

    def test_endpoints_that_name_their_instrument_must_do_so(self):
        self.assertIsNotNone(payload_identity_error("binance_price", {"price": "1"}, "TAOUSDT"))
        self.assertIsNotNone(payload_identity_error("binance_oi_history", [{"sumOpenInterest": "1"}], "TAOUSDT"))
        self.assertIsNone(payload_identity_error("binance_funding", [], "TAOUSDT"))  # empty: nothing to misattribute
        self.assertIsNone(payload_identity_error("binance_taker", [{"buyVol": "1"}], "TAOUSDT"))  # request-bound

    def test_identity_mismatch_records_stay_replayable(self):
        events, errors = events_for("breakout")
        payload = dict(events["binance_book"].payload, symbol="BTCUSDT")  # non-critical: cycle still evaluates
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "o.sqlite"
            with EventStore(path) as store:
                ObserverService(ObserverConfig(), store, SequenceCollectors(
                    [(with_payload(events, "binance_book", payload), errors)])).cycle()
            rows = list(load_sqlite_snapshots(path, "TAOUSDT"))
        self.assertEqual(rows[0]["quality"]["identity_mismatches"][0]["endpoint"], "binance_book")

    def test_fail_closed_replay_matches_live_across_a_malformed_cycle(self):
        bad, bad_errors = events_for("breakout", received_offset=1)
        rows = deepcopy(bad["binance_klines"].payload)
        rows[-20][6] -= 12_345
        with_payload(bad, "binance_klines", rows)
        batches = [events_for("breakout"), (bad, bad_errors), events_for("retest", received_offset=2)]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "o.sqlite"
            with EventStore(path) as store:
                service = ObserverService(ObserverConfig(), store, SequenceCollectors(batches))
                live = [service.cycle()["signal"] for _ in batches]
            self.assertEqual(live[1], "DATA_STALE")
            with self.assertRaises(ReplayError):
                ReplayRunner(ObserverConfig()).run(load_sqlite_snapshots(path, "TAOUSDT"))
            replay = ReplayRunner(ObserverConfig(), invalid_snapshots="fail_closed").run(
                load_sqlite_snapshots(path, "TAOUSDT", strict=False))
        self.assertEqual([d["signal"] for d in replay["decisions"]], live)
        self.assertEqual(replay["decisions"][1]["sequence"]["status"], "SNAPSHOT_INVALID")

    def test_state_rows_stay_readable_by_older_code(self):
        with tempfile.TemporaryDirectory() as directory, EventStore(Path(directory) / "o.sqlite") as store:
            store.save_setup_state(STRATEGY_ID, "TAOUSDT", {"state": "RETEST_PENDING"}, "config_a")
            (payload,) = store.db.execute("SELECT payload_json FROM setup_states").fetchone()
            self.assertEqual(json.loads(payload), {"state": "RETEST_PENDING"})  # the format 76dbb5d reads

    def test_binding_names_a_replay_version_mismatch(self):
        events, errors = events_for("breakout")
        rows = [build_snapshot(events, errors, "TAOUSDT", "TAOUSD")]
        old = json.loads(canonical_json(ReplayRunner(ObserverConfig()).run(rows)))
        old["baseline"]["replay_version"] = "trading-decision-lab-replay-0.3.0"
        with self.assertRaisesRegex(ReplayError, "produced by"):
            bind_replay_to_snapshots(rows, old)

if __name__ == "__main__":
    unittest.main()
