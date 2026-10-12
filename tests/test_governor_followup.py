"""Regressions for the follow-up reviews of PR #11 (issue #22 comments and the
independent review): pending-order reservation, censored prefixes, entry timing,
not-entered rules, chronology, structural guards, lineage and replay integration."""
from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from market_observer import governor
from market_observer.config import ObserverConfig
from market_observer.dataset import build_dataset_artifact, write_dataset
from market_observer.governor import GovernorConfig, GovernorError, assess_trade, build_governed_paper_artifact
from market_observer.normalization import build_snapshot
from market_observer.paper import PaperExecutionConfig
from market_observer.replay import ReplayRunner, write_replay
from market_observer.util import canonical_json
from test_governor import INTERVAL, account, bar, decision, flat, maker, run
from test_observer import events_for


def up(i):
    return bar(i, 101, 103, 100.5, 102)  # never trades through a 100 limit


class PendingOrderTests(unittest.TestCase):
    def test_a_later_proposal_doesnt_depend_on_how_an_earlier_order_resolves(self):
        config = maker(entry_ttl_bars=3)
        verdicts = {}
        for name, third in (("later_fill", bar(3, 101, 101, 99, 100)), ("later_expiry", up(3))):
            bars = [flat(0), up(1), up(2), third, flat(4), flat(5), flat(6)]
            result = run([decision("a", bar=0), decision("b", bar=2)], bars, hold=4, config=config)
            verdicts[name] = (result["journal"][1]["status"], result["journal"][1]["reasons"])
        self.assertEqual(verdicts["later_fill"], verdicts["later_expiry"])
        self.assertEqual(verdicts["later_fill"], ("BLOCKED", ["POSITION_LIMIT_REACHED"]))

    def test_the_slot_is_released_once_the_order_has_resolved(self):
        bars = [flat(0), up(1), up(2), up(3), flat(4), flat(5), flat(6)]
        result = run([decision("a", bar=0), decision("b", bar=3)], bars, hold=2, config=maker())
        self.assertEqual(result["journal"][0]["paper_outcome"], "NOT_FILLED_TTL_EXPIRED")
        self.assertEqual(result["journal"][1]["status"], "APPROVED_FOR_PAPER")


class CensoredPrefixTests(unittest.TestCase):
    def test_known_legs_survive_a_later_missing_bar(self):
        bars = [flat(0), bar(1, 100, 100.5, 99.5, 100), bar(2, 100, 104, 100, 103.6)]  # bar 3 missing
        result = run([decision()], bars, hold=5, config=maker())
        trade = result["trades"][0]
        self.assertEqual(trade["status"], "CENSORED")
        self.assertEqual([e["kind"] for e in trade["events"]], ["ENTRY_FEE", "TAKE_PROFIT_1R"])
        self.assertGreater(trade["open_quantity"], 0)
        booked = sum(e["pnl_usd"] for e in trade["events"])
        self.assertGreater(booked, 0)
        self.assertEqual(result["summary"]["ending_equity_usd"], Decimal("1000") + booked)
        self.assertEqual(result["summary"]["ending_equity_basis"], "REALIZED_ONLY_OPEN_REMAINDER_UNVALUED")
        self.assertEqual(result["summary"]["censored_open_trade_count"], 1)


class EntryTimingTests(unittest.TestCase):
    def observed(self, item, minutes_after_close):
        item["observed_at_ms"] = item["completed_candle_close_time_ms"] + 1 + minutes_after_close * 60_000
        return item

    def test_entry_waits_for_the_first_candle_opening_after_the_decision(self):
        bars = [flat(0), bar(1, 100, 101, 99, 100), bar(2, 105, 106, 104, 105), flat(3), flat(4)]
        late = run([self.observed(decision(), 4)], bars, hold=2)["trades"][0]
        self.assertEqual(late["entry"]["raw_price"], Decimal("105"))
        immediate = run([decision()], bars, hold=2)["trades"][0]  # no observation time: next candle
        self.assertEqual(immediate["entry"]["raw_price"], Decimal("100"))

    def test_a_failure_before_entry_means_not_entered(self):
        bars = [flat(0), bar(1, 100, 100, 97, 97.2), flat(2), flat(3)]
        result = run([self.observed(decision(), 4)], bars, hold=2, config=maker())
        self.assertEqual(result["journal"][0]["paper_outcome"], "NOT_ENTERED_SETUP_INVALIDATED")


class NotEnteredParityTests(unittest.TestCase):
    def test_maker_and_market_refuse_the_same_open_below_invalidation(self):
        bars = [flat(0), bar(1, 97, 97.4, 96.8, 97.2), flat(2), flat(3)]  # opens below 97.5, above 96.5
        for config in (maker(), GovernorConfig(execution_model="MARKET_BASELINE")):
            result = run([decision()], bars, hold=2, config=config)
            self.assertEqual(result["journal"][0]["paper_outcome"], "NOT_ENTERED_OPEN_AT_OR_BELOW_INVALIDATION")


class InputContractTests(unittest.TestCase):
    def test_contradictory_chronology_is_rejected_not_sorted(self):
        with self.assertRaises(GovernorError):
            run([decision("later", bar=2), decision("earlier", bar=0)], [flat(i) for i in range(5)])

    def test_malformed_shapes_block_instead_of_crashing(self):
        listed_costs = decision()
        listed_costs["risk"]["costs"] = ["30"]
        self.assertIn("COSTS_INCOMPLETE", assess_trade(listed_costs, account(), GovernorConfig())["reasons"])
        listed_features = decision()
        listed_features["features"] = [1]
        self.assertIn("FEATURES_MALFORMED", assess_trade(listed_features, account(), GovernorConfig())["reasons"])
        streak = assess_trade(decision(), account(consecutive_losses="3"), GovernorConfig())
        self.assertEqual(streak["reasons"], ["ACCOUNT_STATE_INVALID"])
        result = run([listed_features], [flat(0), flat(1)])  # in a governed run too
        self.assertEqual(result["journal"][0]["status"], "BLOCKED")

    def test_result_id_is_bound_to_decision_content(self):
        bars = [flat(0), flat(1), flat(2)]
        original = run([decision()], bars)
        edited = decision()
        edited["risk"]["reference_price"]["value"] = Decimal("100.5")
        self.assertNotEqual(run([edited], bars)["governed_paper_id"], original["governed_paper_id"])


class ReplayIntegrationTests(unittest.TestCase):
    """Recorder-shaped snapshots -> ReplayRunner -> governor, with a stale cycle and a duplicate poll."""

    def snapshots(self):
        batches = [events_for("breakout"), events_for("breakout", missing="binance_taker", received_offset=1),
                   events_for("retest", received_offset=2), events_for("retest", received_offset=3)]
        return [build_snapshot(events, errors, "TAOUSDT", "TAOUSD") for events, errors in batches]

    def test_replay_output_runs_through_the_governor(self):
        rows = self.snapshots()
        replay = json.loads(canonical_json(ReplayRunner(ObserverConfig()).run(rows)))
        signals = [d["signal"] for d in replay["decisions"]]
        self.assertIn("DATA_STALE", signals)
        self.assertEqual(signals.count("LONG_ELIGIBLE"), 2)
        result = build_governed_paper_artifact(
            rows, replay, dataset_id="fixture",
            paper_config=PaperExecutionConfig(fee_bps_per_side=Decimal("35"), slippage_bps_per_side=Decimal("5"),
                                              max_hold_bars=4),
            config=GovernorConfig(maker_fee_bps="20"))
        self.assertEqual(result["summary"]["proposal_count"], 1)
        self.assertEqual(result["summary"]["duplicate_polls_count"], 1)
        self.assertEqual(result["summary"]["non_proposals_count"], 2)

    def test_cli_rejects_a_replay_that_isnt_of_the_dataset(self):
        rows = self.snapshots()
        dataset_rows, manifest = build_dataset_artifact(rows, source="fixture")
        replay = ReplayRunner(ObserverConfig()).run(dataset_rows)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_dataset(dataset_rows, manifest, jsonl_path=root / "d.jsonl", manifest_path=root / "d.json")
            forged = json.loads(canonical_json(replay))
            forged["decisions"][-1]["research_symbol"] = "BTCUSDT"
            write_replay(forged, root / "forged.json")
            args = ["--jsonl", str(root / "d.jsonl"), "--dataset-manifest", str(root / "d.json"),
                    "--fee-bps-per-side", "35", "--slippage-bps-per-side", "5", "--max-hold-bars", "4",
                    "--maker-fee-bps", "20"]
            with self.assertRaises(GovernorError):
                governor.main(args + ["--replay", str(root / "forged.json"), "--output", str(root / "g1.json")])
            write_replay(replay, root / "genuine.json")
            self.assertEqual(governor.main(args + ["--replay", str(root / "genuine.json"),
                                                   "--output", str(root / "g2.json")]), 0)


if __name__ == "__main__":
    unittest.main()
