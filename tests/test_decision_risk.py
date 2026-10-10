from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from decimal import Decimal

from market_observer.config import ObserverConfig
from market_observer.decision_risk import assess_decision_risk
from market_observer.engine import default_setup_state
from market_observer.evaluator import evaluate_snapshot
from market_observer.normalization import build_snapshot
from market_observer.replay import ReplayRunner
from market_observer.replay import main as replay_main
from market_observer.service import ObserverService
from market_observer.storage import EventStore
from market_observer.util import canonical_json
from test_observer import INTERVAL, SequenceCollectors, events_for


def snapshot(stage, **kwargs):
    return build_snapshot(*events_for(stage, **kwargs), "TAOUSDT", "TAOUSD")


def flatten_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from flatten_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from flatten_keys(item)


class DecisionRiskTests(unittest.TestCase):
    def setUp(self):
        self.config = ObserverConfig()

    def test_watch_long_reports_close_invalidation_and_setup_window(self):
        evaluation = evaluate_snapshot(snapshot("breakout"), self.config, None)
        risk = evaluation.risk
        state = evaluation.state
        self.assertEqual(evaluation.signal, "WATCH_LONG")
        self.assertEqual(risk["status"], "AVAILABLE")
        self.assertEqual(risk["setup_id"], state["setup_id"])
        expected_level = state["breakout_resistance"] - Decimal("0.15") * state["breakout_atr"]
        self.assertEqual(risk["invalidation"]["level"], expected_level)
        self.assertEqual(risk["invalidation"]["rule"], "COMPLETED_CLOSE_BELOW")
        self.assertFalse(risk["invalidation"]["intrabar_touch_invalidates"])
        self.assertEqual(risk["reference_price"]["value"], Decimal("103"))
        self.assertEqual(risk["per_unit_risk"]["distance"], Decimal("103") - expected_level)
        self.assertEqual(risk["per_unit_risk"]["distance_quote"], "USDT")
        self.assertFalse(risk["venue"]["cross_venue_parity_assumed"])
        self.assertEqual(
            risk["setup_window"]["last_evaluable_close_time_ms"],
            state["breakout_close_time_ms"] + self.config.expiry_bars * INTERVAL,
        )

    def test_long_eligible_uses_breakout_levels_frozen_in_setup(self):
        first = evaluate_snapshot(snapshot("breakout"), self.config, None)
        second = evaluate_snapshot(snapshot("retest", received_offset=1), self.config, first.state)
        risk = second.risk
        self.assertEqual(second.signal, "LONG_ELIGIBLE")
        self.assertEqual(risk["status"], "AVAILABLE")
        self.assertEqual(risk["invalidation"]["level"], first.risk["invalidation"]["level"])
        self.assertEqual(risk["reference_price"]["value"], Decimal("101.5"))
        self.assertEqual(
            risk["setup_window"]["last_evaluable_close_time_ms"],
            second.completed_candle_close_time_ms,
        )

    def test_unknown_fee_and_slippage_are_not_zero(self):
        risk = evaluate_snapshot(snapshot("breakout"), self.config, None).risk
        costs = risk["costs"]
        self.assertGreater(costs["spread"]["round_trip_bps"], 0)
        self.assertEqual(costs["spread"]["status"], "OBSERVED_SIGNAL_TIME_PROXY")
        for name in ("fees", "slippage"):
            self.assertEqual(costs[name]["status"], "NOT_SUPPLIED")
            self.assertIsNone(costs[name]["per_side_bps"])
        self.assertEqual(costs["funding"]["status"], "NOT_MODELLED")
        self.assertIsNone(costs["modelled_round_trip_bps"])
        self.assertEqual(costs["modelled_basis"], "INCOMPLETE_MISSING_FEES_SLIPPAGE")
        self.assertIsNone(risk["per_unit_risk"]["distance_plus_modelled_costs_bps"])
        self.assertFalse(risk["per_unit_risk"]["gap_loss_bounded"])

    def test_one_supplied_cost_still_leaves_total_incomplete(self):
        config = replace(self.config, fee_bps_per_side=5)
        costs = evaluate_snapshot(snapshot("breakout"), config, None).risk["costs"]
        self.assertEqual(costs["fees"]["status"], "CONFIGURED_ASSUMPTION")
        self.assertIsNone(costs["modelled_round_trip_bps"])
        self.assertEqual(costs["modelled_basis"], "INCOMPLETE_MISSING_SLIPPAGE")

    def test_explicit_zero_cost_is_distinct_from_unknown(self):
        config = replace(self.config, fee_bps_per_side=0, slippage_bps_per_side=0)
        costs = evaluate_snapshot(snapshot("breakout"), config, None).risk["costs"]
        self.assertEqual(costs["fees"]["status"], "CONFIGURED_ASSUMPTION")
        self.assertEqual(costs["modelled_round_trip_bps"], costs["spread"]["round_trip_bps"])

    def test_supplied_costs_follow_paper_conventions_and_exclude_funding(self):
        config = replace(self.config, fee_bps_per_side=5, slippage_bps_per_side=2.5)
        risk = evaluate_snapshot(snapshot("breakout"), config, None).risk
        costs = risk["costs"]
        self.assertEqual(costs["fees"]["round_trip_bps"], Decimal("10"))
        self.assertEqual(costs["slippage"]["round_trip_bps"], Decimal("5.0"))
        self.assertEqual(
            costs["modelled_round_trip_bps"],
            costs["spread"]["round_trip_bps"] + Decimal("15.0"),
        )
        self.assertIn("EXCLUDES_FUNDING", costs["modelled_basis"])
        self.assertEqual(
            risk["per_unit_risk"]["distance_plus_modelled_costs_bps"],
            risk["per_unit_risk"]["distance_bps_of_reference"] + costs["modelled_round_trip_bps"],
        )

    def test_negative_or_nonfinite_cost_configuration_is_rejected(self):
        for field, value in (("fee_bps_per_side", -1), ("slippage_bps_per_side", float("nan"))):
            with self.assertRaises(ValueError):
                replace(self.config, **{field: value}).validate()

    def test_cost_assumptions_enter_configuration_identity(self):
        config = replace(self.config, fee_bps_per_side=5)
        self.assertNotEqual(config.public_dict(), self.config.public_dict())

    def test_no_active_setup_is_not_applicable(self):
        evaluation = evaluate_snapshot(snapshot("flat"), self.config, None)
        self.assertEqual(evaluation.signal, "NO_TRADE")
        self.assertEqual(evaluation.risk["status"], "NOT_APPLICABLE")
        self.assertNotIn("invalidation", evaluation.risk)

    def test_blocked_cycle_withholds_figures_even_with_active_setup(self):
        first = evaluate_snapshot(snapshot("breakout"), self.config, None)
        blocked = evaluate_snapshot(
            snapshot("retest", spread=True, received_offset=1), self.config, first.state
        )
        self.assertEqual(blocked.signal, "RISK_BLOCKED")
        self.assertEqual(blocked.state["state"], "BREAKOUT_OBSERVED")
        self.assertEqual(blocked.risk["status"], "WITHHELD_BLOCKED")
        self.assertNotIn("invalidation", blocked.risk)

    def test_reference_at_or_below_invalidation_fails_closed(self):
        setup = {
            **default_setup_state(),
            "state": "RETEST_PENDING",
            "setup_id": "BREAKOUT_RETEST_V1:1",
            "breakout_resistance": Decimal("100"),
            "breakout_atr": Decimal("2"),
            "breakout_close_time_ms": 1,
        }
        features = {"completed_close": Decimal("99.7"), "completed_candle_close_time_ms": 2, "spread_bps": None}
        risk = assess_decision_risk("WATCH_LONG", setup, features, self.config)
        self.assertEqual(risk["status"], "UNAVAILABLE")
        self.assertEqual(risk["reason"], "reference_at_or_below_invalidation")

    def test_malformed_setup_fails_closed(self):
        setup = {**default_setup_state(), "state": "RETEST_PENDING", "breakout_resistance": "100", "breakout_atr": None}
        features = {"completed_close": "101", "completed_candle_close_time_ms": 2}
        risk = assess_decision_risk("WATCH_LONG", setup, features, self.config)
        self.assertEqual(risk["status"], "UNAVAILABLE")

    def test_persisted_string_values_give_identical_contract(self):
        evaluation = evaluate_snapshot(snapshot("breakout"), self.config, None)
        restored_state = json.loads(canonical_json(evaluation.state))
        restored = assess_decision_risk(
            evaluation.signal, restored_state, evaluation.features, self.config, snapshot("breakout")
        )
        self.assertEqual(canonical_json(restored), canonical_json(evaluation.risk))

    def test_contract_never_sizes_or_permits_execution(self):
        evaluation = evaluate_snapshot(snapshot("breakout"), self.config, None)
        keys = set(flatten_keys(evaluation.risk))
        self.assertFalse({key for key in keys if "quantity" in key or "notional" in key})
        self.assertEqual(evaluation.risk["sizing"], "NOT_COMPUTED_ACCOUNT_STATE_UNKNOWN")
        self.assertEqual(evaluation.risk["execution_permission"], "NONE")
        self.assertEqual(evaluation.risk["reward_reference"]["role"], "DESCRIPTIVE_OPPORTUNITY_NOT_TAKE_PROFIT")

    def test_live_and_replay_emit_identical_risk(self):
        snapshots = [snapshot("breakout"), snapshot("retest", received_offset=1)]
        replay = ReplayRunner(self.config).run(snapshots)["decisions"]
        with EventStore(":memory:") as store:
            service = ObserverService(
                self.config,
                store,
                SequenceCollectors([events_for("breakout"), events_for("retest", received_offset=1)]),
            )
            live = [service.cycle(), service.cycle()]
        self.assertEqual([item["risk"]["status"] for item in live], ["AVAILABLE", "AVAILABLE"])
        for live_decision, replay_decision in zip(live, replay):
            self.assertEqual(canonical_json(live_decision["risk"]), canonical_json(replay_decision["risk"]))

    def test_failure_decisions_carry_withheld_risk(self):
        broken = snapshot("breakout")
        broken["candles_15m"] = broken["candles_15m"][:5]
        decision = ReplayRunner(self.config).run([broken])["decisions"][0]
        self.assertEqual(decision["signal"], "DATA_STALE")
        self.assertEqual(decision["risk"]["status"], "WITHHELD_BLOCKED")

    def test_asof_rejection_carries_withheld_risk(self):
        future = snapshot("breakout")
        future["exchange_time_ms"] = future["observed_at_ms"] + 1
        result = ReplayRunner(self.config).run([future])
        decision = result["decisions"][0]
        self.assertEqual(decision["sequence"]["status"], "ASOF_INVALID")
        self.assertEqual(decision["risk"]["status"], "WITHHELD_BLOCKED")
        self.assertNotIn("invalidation", decision["risk"])

    def test_replay_cli_records_cost_assumptions(self):
        with tempfile.TemporaryDirectory() as root:
            source = f"{root}/snapshots.jsonl"
            with open(source, "w", encoding="utf-8") as handle:
                handle.write(canonical_json(snapshot("breakout")) + "\n")
            with redirect_stdout(io.StringIO()):
                replay_main(["--jsonl", source, "--output", f"{root}/costed.json",
                             "--fee-bps-per-side", "5", "--slippage-bps-per-side", "2.5"])
                replay_main(["--jsonl", source, "--output", f"{root}/plain.json"])
            with open(f"{root}/costed.json", encoding="utf-8") as handle:
                costed = json.load(handle)
            with open(f"{root}/plain.json", encoding="utf-8") as handle:
                plain = json.load(handle)
        costs = costed["decisions"][0]["risk"]["costs"]
        self.assertEqual(costs["fees"]["status"], "CONFIGURED_ASSUMPTION")
        self.assertEqual(Decimal(str(costs["slippage"]["round_trip_bps"])), Decimal("5"))
        self.assertEqual(costed["baseline"]["configuration"]["fee_bps_per_side"], 5)
        self.assertEqual(plain["decisions"][0]["risk"]["costs"]["fees"]["status"], "NOT_SUPPLIED")
        self.assertNotEqual(costed["baseline"]["configuration_id"], plain["baseline"]["configuration_id"])

    def test_replay_cli_rejects_negative_cost(self):
        with tempfile.TemporaryDirectory() as root, self.assertRaises(SystemExit) as raised:
            replay_main(["--jsonl", f"{root}/unused.jsonl", "--output", f"{root}/out.json",
                         "--fee-bps-per-side", "-1"])
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
