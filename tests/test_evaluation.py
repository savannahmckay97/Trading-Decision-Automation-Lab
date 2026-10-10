from __future__ import annotations

import copy
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from market_observer.config import ObserverConfig
from market_observer.dataset import DatasetError, build_dataset_artifact, write_dataset
from market_observer.evaluation import (
    EvaluationConfig, EvaluationError, build_evaluation_artifact, main,
    write_evaluation_artifact,
)
from market_observer.normalization import build_snapshot
from market_observer.replay import ReplayRunner
from test_observer import INTERVAL, events_for


def snapshots():
    result = [
        build_snapshot(*events_for("breakout"), "TAOUSDT", "TAOUSD"),
        build_snapshot(*events_for("retest"), "TAOUSDT", "TAOUSD"),
    ]
    for close in (Decimal("103"), Decimal("100"), Decimal("105")):
        row = copy.deepcopy(result[-1])
        row["observed_at_ms"] += INTERVAL
        row["exchange_time_ms"] += INTERVAL
        bar = row["candles_15m"][-1]
        bar.update(high=max(bar["open"], close) + 1, low=min(bar["open"], close) - 1, close=close)
        row["candles_15m"].append({
            "open_time_ms": bar["open_time_ms"] + INTERVAL,
            "close_time_ms": bar["close_time_ms"] + INTERVAL,
            "open": close, "high": close + 1, "low": close - 1,
            "close": close, "volume": Decimal(10),
        })
        row["market"].update(binance_last_price=close, kraken_last_price=close,
                             kraken_bid_price=close - Decimal("0.05"),
                             kraken_ask_price=close + Decimal("0.05"))
        for event in row["provenance"].values():
            event["received_time_ms"] += INTERVAL
            if event["event_time_ms"] is not None:
                event["event_time_ms"] += INTERVAL
        for name in ("open_interest_5m", "taker_flow_5m", "funding_history"):
            for item in row[name]:
                item["timestamp_ms"] += INTERVAL
        for series in row["positioning"].values():
            for item in series:
                item["timestamp_ms"] += INTERVAL
        result.append(row)
    return result


class DecisionEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.rows = snapshots()
        self.config = EvaluationConfig("synthetic-plumbing-only", "exploratory",
                                       self.rows[0]["observed_at_ms"],
                                       self.rows[-1]["observed_at_ms"], 1)

    def evaluate(self, rows=None, config=None):
        data = self.rows if rows is None else rows
        _, manifest = build_dataset_artifact(data, source="synthetic-test-fixture")
        return build_evaluation_artifact(data, manifest, observer_config=ObserverConfig(),
                                         config=self.config if config is None else config)

    def test_compares_same_universe_with_selection_and_abstention_baselines(self):
        result = self.evaluate()
        self.assertEqual(result["coverage"]["unique_valid_bars"], 5)
        self.assertEqual(result["coverage"]["outcome_status_counts"], {"AVAILABLE": 4, "CENSORED": 1})
        metrics = result["policies"]
        self.assertEqual(metrics["TDAL"]["selected_count"], 1)
        self.assertEqual(metrics["TDAL"]["mean_selected_forward_return_pct"],
                         Decimal(100) * (Decimal(103) / Decimal("101.5") - 1))
        self.assertEqual(metrics["SELECT_ALL"]["selected_count"], 5)
        self.assertEqual(metrics["ABSTAIN_ALL"]["selected_count"], 0)
        self.assertIsNone(metrics["ABSTAIN_ALL"]["mean_selected_forward_return_pct"])
        self.assertEqual(metrics["ABSTAIN_ALL"]["avoided_negative_market_move_count"], 2)
        self.assertEqual(metrics["ABSTAIN_ALL"]["missed_positive_market_move_count"], 2)
        self.assertEqual(metrics["POSITIVE_PRIOR_RETURN"]["labeled_selected_count"], 2)
        self.assertFalse(result["claims"]["decision_quality_improvement_demonstrated"])
        self.assertFalse(result["claims"]["performance_claim_available"])

    def test_duplicate_poll_cannot_multiply_observation_opportunities(self):
        duplicate = copy.deepcopy(self.rows[1])
        duplicate["observed_at_ms"] += 1
        rows = self.rows[:2] + [duplicate] + self.rows[2:]
        result = self.evaluate(rows)
        self.assertEqual(result["coverage"]["duplicate_valid_observations"], 1)
        self.assertEqual(result["policies"], self.evaluate()["policies"])

    def test_right_censoring_does_not_assign_zero_or_use_post_window_outcomes(self):
        config = replace(self.config, end_observed_at_ms=self.rows[1]["observed_at_ms"])
        result = self.evaluate(config=config)
        selected = result["policies"]["TDAL"]
        self.assertEqual(selected["selected_count"], 1)
        self.assertEqual(selected["labeled_selected_count"], 0)
        self.assertEqual(selected["unavailable_or_censored_selected_count"], 1)
        self.assertIsNone(selected["mean_selected_forward_return_pct"])
        self.assertIsNone(result["tdal_selection_lift_vs_all_pct_points"])

    def test_target_closed_but_not_received_by_window_end_is_censored(self):
        target = self.rows[2]["candles_15m"][-2]["close_time_ms"]
        result = self.evaluate(config=replace(self.config, end_observed_at_ms=target + 1))
        selected = result["policies"]["TDAL"]
        self.assertEqual(selected["labeled_selected_count"], 0)
        self.assertIsNone(selected["mean_selected_forward_return_pct"])
        eligible = next(row for row in result["decisions"] if row["selected"]["TDAL"])
        self.assertEqual(eligible["outcome"]["reason"], "path_not_observed_by_window_end")
        self.assertIsNone(eligible["invalidation"]["wick_touch"])

    def test_duplicate_gap_and_short_derivative_histories_are_rejected(self):
        for name in ("open_interest_5m", "taker_flow_5m"):
            for change in ("duplicate", "gap", "short"):
                with self.subTest(name=name, change=change):
                    rows = copy.deepcopy(self.rows)
                    series = rows[0][name]
                    if change == "duplicate":
                        series[-1]["timestamp_ms"] = series[-2]["timestamp_ms"]
                    elif change == "gap":
                        del series[-2]
                    else:
                        rows[0][name] = series[-2:]
                    with self.assertRaisesRegex(EvaluationError, "discontinuous derivative"):
                        self.evaluate(rows)

    def test_missing_future_bar_remains_unavailable(self):
        rows = copy.deepcopy(self.rows)
        missing_close = rows[2]["candles_15m"][-2]["close_time_ms"]
        for row in rows:
            row["candles_15m"] = [bar for bar in row["candles_15m"]
                                  if bar["close_time_ms"] != missing_close]
        result = self.evaluate(rows)
        self.assertEqual(result["policies"]["TDAL"]["labeled_selected_count"], 0)
        self.assertIsNone(result["policies"]["TDAL"]["mean_selected_forward_return_pct"])
        self.assertEqual(result["coverage"]["outcome_status_counts"]["UNAVAILABLE"], 1)

    def test_rejects_future_derivative_rows_and_future_receipts(self):
        for name in ("open_interest_5m", "taker_flow_5m", "funding_history"):
            with self.subTest(name=name):
                rows = copy.deepcopy(self.rows)
                rows[0][name][-1]["timestamp_ms"] = rows[0]["observed_at_ms"] + 1
                with self.assertRaisesRegex((EvaluationError, DatasetError), "future input"):
                    self.evaluate(rows)
        rows = copy.deepcopy(self.rows)
        rows[0]["provenance"]["kraken_ticker"]["received_time_ms"] += 1
        with self.assertRaisesRegex((EvaluationError, DatasetError), "future receipt"):
            self.evaluate(rows)

    def test_manifest_binding_rejects_changed_data_and_metadata(self):
        rows, manifest = build_dataset_artifact(self.rows, source="synthetic-test-fixture")
        for changed in ({**manifest, "snapshot_count": 999}, {**manifest, "jsonl_sha256": "wrong"}):
            with self.assertRaisesRegex(EvaluationError, "manifest does not match"):
                build_evaluation_artifact(rows, changed, observer_config=ObserverConfig(), config=self.config)

    def test_shared_asof_contract_rejects_future_events_and_missing_annotation_provenance(self):
        for change in ("event", "positioning_provenance"):
            with self.subTest(change=change):
                rows = copy.deepcopy(self.rows)
                if change == "event":
                    rows[0]["provenance"]["binance_taker"]["event_time_ms"] = rows[0]["observed_at_ms"] + 1
                    reason = "future event"
                else:
                    del rows[0]["provenance"]["binance_global_ratio"]
                    reason = "missing provenance"
                with self.assertRaisesRegex((EvaluationError, DatasetError), reason):
                    self.evaluate(rows)

    def test_blocked_observations_do_not_enter_any_selection_baseline(self):
        rows = copy.deepcopy(self.rows)
        for row in rows:
            row["quality"]["critical_missing"] = ["binance_taker"]
        result = self.evaluate(rows)
        self.assertEqual(result["coverage"]["blocked_observations"], 5)
        self.assertEqual(result["coverage"]["unique_valid_bars"], 0)
        self.assertIsNone(result["policies"]["SELECT_ALL"]["selection_fraction"])

    def test_invalidation_distinguishes_close_breach_from_wick_touch(self):
        rows = copy.deepcopy(self.rows)
        target = rows[2]["candles_15m"][-2]["close_time_ms"]
        for row in rows:
            for bar in row["candles_15m"]:
                if bar["close_time_ms"] == target:
                    bar["low"] = Decimal(99)
        result = self.evaluate(rows)
        self.assertEqual(result["tdal_invalidation"]["wick_touch_count"], 1)
        self.assertEqual(result["tdal_invalidation"]["close_breach_count"], 0)

    def test_future_outcomes_do_not_change_earlier_decisions_and_inputs_are_unchanged(self):
        original = copy.deepcopy(self.rows)
        prefix = ReplayRunner(ObserverConfig()).run(self.rows[:2])["decisions"]
        full = ReplayRunner(ObserverConfig()).run(self.rows)["decisions"]
        self.assertEqual(prefix, full[:2])
        self.assertEqual(self.evaluate(), self.evaluate())
        self.assertEqual(self.rows, original)

    def test_window_warmup_and_declared_holdout_do_not_claim_verified_holdout(self):
        result = self.evaluate(config=replace(self.config, partition="holdout",
                                             start_observed_at_ms=self.rows[1]["observed_at_ms"]))
        self.assertEqual(result["policies"]["TDAL"]["selected_count"], 1)
        self.assertFalse(result["claims"]["untouched_holdout_verified"])

    def test_cli_and_immutable_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows, manifest = build_dataset_artifact(self.rows, source="synthetic-test-fixture")
            write_dataset(rows, manifest, jsonl_path=root / "data.jsonl", manifest_path=root / "manifest.json")
            args = ["--jsonl", str(root / "data.jsonl"), "--dataset-manifest", str(root / "manifest.json"),
                    "--output", str(root / "evaluation.json"), "--experiment-id", "synthetic-plumbing-only",
                    "--partition", "exploratory", "--start-observed-at-ms", str(self.config.start_observed_at_ms),
                    "--end-observed-at-ms", str(self.config.end_observed_at_ms), "--horizon-bars", "1"]
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(args), 0)
            with self.assertRaises(FileExistsError):
                write_evaluation_artifact(self.evaluate(), root / "evaluation.json")

    def test_invalid_evaluation_design_is_rejected(self):
        for changes in ({"horizon_bars": 0}, {"horizon_bars": 1.5},
                        {"partition": "training-guessed"}, {"experiment_id": " "},
                        {"end_observed_at_ms": self.config.start_observed_at_ms - 1}):
            with self.subTest(changes=changes), self.assertRaises(EvaluationError):
                replace(self.config, **changes)


if __name__ == "__main__":
    unittest.main()



from market_observer.evaluation import _first_touch, _first_touch_summary


def bar(high, low, close):
    return {"high": Decimal(str(high)), "low": Decimal(str(low)), "close": Decimal(str(close))}


def risk(target="110", stop="95", ratio="2"):
    return {
        "status": "AVAILABLE",
        "invalidation": {"level": Decimal(stop)},
        "reward_reference": {"status": "AVAILABLE", "level": Decimal(target),
                             "gross_opportunity_to_risk": Decimal(ratio)},
    }


AVAILABLE = {"status": "AVAILABLE"}


class RewardFirstTouchTests(unittest.TestCase):
    def test_target_first(self):
        result = _first_touch(risk(), AVAILABLE, [bar(105, 100, 104), bar(111, 103, 109)])
        self.assertEqual(result["wick_stop"], {"result": "TARGET_FIRST", "bars": 2})
        self.assertEqual(result["close_stop"], {"result": "TARGET_FIRST", "bars": 2})
        self.assertEqual(result["breakeven_target_rate"], Decimal(1) / 3)

    def test_stop_first(self):
        result = _first_touch(risk(), AVAILABLE, [bar(101, 94, 94.5), bar(112, 94, 111)])
        self.assertEqual(result["wick_stop"], {"result": "STOP_FIRST", "bars": 1})
        self.assertEqual(result["close_stop"], {"result": "STOP_FIRST", "bars": 1})

    def test_same_bar_is_ambiguous_for_wick_but_ordered_for_close(self):
        result = _first_touch(risk(), AVAILABLE, [bar(111, 94, 94)])
        self.assertEqual(result["wick_stop"]["result"], "AMBIGUOUS_SAME_BAR")
        # A close-based stop can only happen at the close, after the intrabar touch.
        self.assertEqual(result["close_stop"], {"result": "TARGET_FIRST", "bars": 1})

    def test_wick_and_close_rules_can_disagree(self):
        result = _first_touch(risk(), AVAILABLE, [bar(100, 94, 99), bar(110, 98, 109)])
        self.assertEqual(result["wick_stop"], {"result": "STOP_FIRST", "bars": 1})
        self.assertEqual(result["close_stop"], {"result": "TARGET_FIRST", "bars": 2})

    def test_neither_within_horizon(self):
        result = _first_touch(risk(), AVAILABLE, [bar(105, 96, 100)])
        self.assertEqual(result["wick_stop"], {"result": "NEITHER_WITHIN_HORIZON", "bars": None})

    def test_missing_target_risk_or_path_is_never_scored(self):
        no_target = risk()
        no_target["reward_reference"] = {"status": "TARGET_UNAVAILABLE", "reason": "X"}
        self.assertEqual(_first_touch(no_target, AVAILABLE, [bar(200, 1, 100)])["status"], "TARGET_UNAVAILABLE")
        self.assertEqual(_first_touch({"status": "WITHHELD_BLOCKED"}, AVAILABLE, None)["status"], "RISK_UNAVAILABLE")
        censored = {"status": "CENSORED", "reason": "horizon_exceeds_evaluation_window"}
        self.assertEqual(_first_touch(risk(), censored, None)["status"], "CENSORED")

    def test_summary_compares_target_rate_with_breakeven_on_decided_cases(self):
        rows = [{"reward_first_touch": item} for item in (
            _first_touch(risk(ratio="1"), AVAILABLE, [bar(111, 100, 105)]),
            _first_touch(risk(ratio="3"), AVAILABLE, [bar(100, 94, 94)]),
            _first_touch(risk(), AVAILABLE, [bar(111, 94, 94)]),
            _first_touch(risk(), AVAILABLE, [bar(105, 96, 100)]),
            {"status": "TARGET_UNAVAILABLE", "reason": "X"},
        )] + [{"reward_first_touch": None}]
        summary = _first_touch_summary(rows)
        self.assertEqual(summary["selected_count"], 5)
        self.assertEqual(summary["status_counts"], {"LABELED": 4, "TARGET_UNAVAILABLE": 1})
        wick = summary["wick_stop"]
        self.assertEqual(wick["decided_count"], 2)
        self.assertEqual(wick["target_first_fraction_of_decided"], Decimal("0.5"))
        self.assertEqual(wick["mean_breakeven_target_rate_of_decided"], (Decimal("0.5") + Decimal("0.25")) / 2)
        self.assertEqual(wick["result_counts"]["AMBIGUOUS_SAME_BAR"], 1)
        self.assertEqual(summary["close_stop"]["decided_count"], 3)
        self.assertEqual(summary["basis"], "GROSS_DESCRIPTIVE_NO_COSTS_NOT_EXPECTANCY")


class RewardFirstTouchIntegrationTests(unittest.TestCase):
    setUp = DecisionEvaluationTests.setUp
    evaluate = DecisionEvaluationTests.evaluate

    def test_artifact_labels_tdal_selection_against_reward_and_invalidation(self):
        rows = snapshots()
        for row in rows:
            # Bar 2 sits outside every feature window; it only creates a confirmed overhead pivot.
            row["candles_15m"][2]["high"] = Decimal("102.5")
        result = self.evaluate(rows)
        selected = [item for item in result["decisions"] if item["selected"]["TDAL"]]
        self.assertEqual(len(selected), 1)
        touch = selected[0]["reward_first_touch"]
        self.assertEqual(touch["status"], "LABELED")
        self.assertEqual(touch["target_level"], Decimal("102.5"))
        self.assertEqual(touch["close_stop"], {"result": "TARGET_FIRST", "bars": 1})
        future = rows[2]["candles_15m"][-2]
        expected_wick = "AMBIGUOUS_SAME_BAR" if future["low"] <= touch["invalidation_level"] else "TARGET_FIRST"
        self.assertEqual(touch["wick_stop"]["result"], expected_wick)
        self.assertEqual(result["tdal_reward_first_touch"]["status_counts"], {"LABELED": 1})
        # Eligibility is identical with or without the reward-only pivot.
        plain = self.evaluate()
        self.assertEqual([d["signal"] for d in plain["decisions"]], [d["signal"] for d in result["decisions"]])

    def test_fixture_without_overhead_reports_target_unavailable(self):
        result = self.evaluate()
        self.assertEqual(result["tdal_reward_first_touch"]["status_counts"], {"TARGET_UNAVAILABLE": 1})
        self.assertEqual(result["tdal_reward_first_touch"]["wick_stop"]["decided_count"], 0)
