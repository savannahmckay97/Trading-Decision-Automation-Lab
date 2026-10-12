"""Synthetic tests for the prospective v1 analysis (research/prospective_v1/).

Synthetic fixtures only: no recorded holdout data is read. Prices and decisions are
invented to exercise each rule of PREREGISTRATION.md §3–§5.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research" / "prospective_v1"))
import analysis as A  # noqa: E402
import inputs as I  # noqa: E402

from market_observer.dataset import build_dataset_artifact, validate_dataset_manifest, write_dataset  # noqa: E402
from market_observer.util import canonical_json, stable_id  # noqa: E402

IV = A.INTERVAL_MS
T0 = A.Settings().block_origin_ms
START = A.Settings().holdout_start_ms
FIRST_CLOSE = START + (-(START + 1)) % IV  # first candle close after the holdout start
D = Decimal


def bar(close_time, o, h, low, c):
    return {"open_time_ms": close_time - IV + 1, "close_time_ms": close_time,
            "open": D(str(o)), "high": D(str(h)), "low": D(str(low)), "close": D(str(c))}


def tape_of(signal, bars):
    """bars[k-1] is candle k after the signal candle; None leaves a gap."""
    return {signal + k * IV: bar(signal + k * IV, *b) for k, b in enumerate(bars, start=1) if b is not None}


def trade(tape, signal, *, decision=None, inv="98", target="103", dist="2", horizon=16, rule="CLOSE",
          cutoff=None):
    return A.simulate_trade(
        signal_close_ms=signal, decision_time_ms=decision if decision is not None else signal + 1 + 120_000,
        invalidation=D(inv), target=None if target is None else D(target), risk_distance=D(dist),
        tape=tape, horizon=horizon, stop_rule=rule, cutoff_ms=cutoff if cutoff is not None else signal + 10**9)


FLAT = (100, 100.5, 99.5, 100)
SIG = FIRST_CLOSE + 4 * IV


class EntryTimingTests(unittest.TestCase):
    def test_entry_is_first_candle_opening_at_or_after_decision(self):
        self.assertEqual(A.entry_offset(SIG, SIG + 1 + 120_000), 2)   # normal 2-minute delay
        self.assertEqual(A.entry_offset(SIG, SIG + 1), 1)             # exactly at the next open
        self.assertEqual(A.entry_offset(SIG, SIG - 5), 1)
        self.assertEqual(A.entry_offset(SIG, SIG + 1 + IV), 2)        # exactly at candle 2's open
        self.assertEqual(A.entry_offset(SIG, SIG + 2 + IV), 3)

    def test_pre_entry_candle_price_never_decides_an_exit(self):
        # Candle 1 (pre-entry) would hit the target only via its high... rule 2 makes it not entered,
        # and a pre-entry low below invalidation without a close below changes nothing.
        tape = tape_of(SIG, [(100, 100.5, 90, 100)] + [FLAT] * 16)
        result = trade(tape, SIG)
        self.assertEqual((result["status"], result["reason"]), ("ENTERED", "HORIZON"))
        self.assertEqual(result["entry_candle_offset"], 2)
        self.assertEqual(result["entry_price"], D("100"))


class NotEnteredTests(unittest.TestCase):
    def check(self, bars, reason, **kw):
        result = trade(tape_of(SIG, bars), SIG, **kw)
        self.assertEqual((result["status"], result["reason"], result["r"]), ("NOT_ENTERED", reason, D(0)))

    def test_each_reason(self):
        self.check([(100, 100, 97, 97.5)] + [FLAT] * 16, "PRE_ENTRY_INVALIDATED")
        self.check([(100, 103, 99, 100)] + [FLAT] * 16, "PRE_ENTRY_TARGET_TOUCHED")
        self.check([FLAT, (103, 104, 102, 103)] + [FLAT] * 15, "OPEN_AT_OR_ABOVE_TARGET")
        self.check([FLAT, (98, 99, 97, 98.5)] + [FLAT] * 15, "OPEN_AT_OR_BELOW_INVALIDATION")

    def test_order_invalidation_before_target_in_one_pre_entry_candle(self):
        self.check([(100, 103, 97, 97.5)] + [FLAT] * 16, "PRE_ENTRY_INVALIDATED")

    def test_target_unavailable_uses_only_rules_1_and_4(self):
        result = trade(tape_of(SIG, [(100, 110, 99, 100), (120, 121, 119, 120)] + [FLAT] * 15), SIG, target=None)
        self.assertEqual(result["status"], "ENTERED")
        self.check([FLAT, (98, 99, 97, 98.5)] + [FLAT] * 15, "OPEN_AT_OR_BELOW_INVALIDATION", target=None)


class ExitTests(unittest.TestCase):
    def test_target_exit_at_level(self):
        result = trade(tape_of(SIG, [FLAT, FLAT, (100, 103.5, 99.5, 101)] + [FLAT] * 14), SIG)
        self.assertEqual((result["reason"], result["exit_price"], result["r"]), ("TARGET", D("103"), D("1.5")))
        self.assertEqual(result["bars_held"], 2)

    def test_close_stop_exit_at_close(self):
        result = trade(tape_of(SIG, [FLAT, FLAT, (100, 100, 97, 97)] + [FLAT] * 14), SIG)
        self.assertEqual((result["reason"], result["exit_price"], result["r"]), ("CLOSE_STOP", D("97"), D("-1.5")))

    def test_horizon_exit_at_16th_close(self):
        bars = [FLAT] * 16 + [(100, 101, 99.5, 101.5)]
        bars[16] = (100, 101.6, 99.5, 101.5)
        result = trade(tape_of(SIG, bars), SIG)
        self.assertEqual((result["reason"], result["exit_price"], result["bars_held"]), ("HORIZON", D("101.5"), 16))
        self.assertEqual(result["r"], D("0.75"))

    def test_same_candle_target_and_close_stop_target_first(self):
        result = trade(tape_of(SIG, [FLAT, FLAT, (100, 103, 96, 97)] + [FLAT] * 14), SIG)
        self.assertEqual(result["reason"], "TARGET")

    def test_same_candle_wick_stop_first(self):
        result = trade(tape_of(SIG, [FLAT, FLAT, (100, 103, 97.5, 101)] + [FLAT] * 14), SIG, rule="WICK")
        self.assertEqual((result["reason"], result["exit_price"], result["r"]), ("WICK_STOP", D("98"), D("-1")))

    def test_wick_gap_through_exits_at_worse_open(self):
        result = trade(tape_of(SIG, [FLAT, FLAT, (95, 96, 94, 95.5)] + [FLAT] * 14), SIG, rule="WICK")
        self.assertEqual((result["reason"], result["exit_price"], result["r"]), ("GAP_STOP_AT_OPEN", D("95"), D("-2.5")))

    def test_r_uses_original_risk_distance_when_entry_opens_just_above_invalidation(self):
        bars = [FLAT, (98.01, 98.5, 98.01, 98.4), (98.4, 98.4, 97, 97.5)] + [FLAT] * 14
        result = trade(tape_of(SIG, bars), SIG)
        self.assertEqual(result["reason"], "CLOSE_STOP")
        self.assertEqual(result["r"], (D("97.5") - D("98.01")) / D("2"))  # not / 0.01


class CensoringTests(unittest.TestCase):
    def test_missing_pre_entry_entry_and_path_candles(self):
        self.assertEqual(trade(tape_of(SIG, [None] + [FLAT] * 16), SIG)["reason"], "MISSING_PRE_ENTRY_CANDLE")
        self.assertEqual(trade(tape_of(SIG, [FLAT, None] + [FLAT] * 15), SIG)["reason"], "MISSING_ENTRY_CANDLE")
        result = trade(tape_of(SIG, [FLAT, FLAT, FLAT, None] + [FLAT] * 13), SIG)
        self.assertEqual((result["status"], result["reason"], result["missing_offset"]),
                         ("CENSORED", "MISSING_PATH_CANDLE", 2))

    def test_missing_candle_after_exit_does_not_censor(self):
        result = trade(tape_of(SIG, [FLAT, (100, 104, 99, 100), None] + [FLAT] * 14), SIG)
        self.assertEqual(result["reason"], "TARGET")

    def test_pre_entry_rule_on_present_candle_settles_case_despite_missing_candle(self):
        decision = SIG + 1 + IV + 60_000  # entry is candle 3; candles 1, 2 are pre-entry
        result = trade(tape_of(SIG, [None, (100, 100, 97, 97)] + [FLAT] * 16), SIG, decision=decision)
        self.assertEqual((result["status"], result["reason"]), ("NOT_ENTERED", "PRE_ENTRY_INVALIDATED"))
        self.assertTrue(result["pre_entry_path_incomplete"])

    def test_horizon_after_cutoff_censors_even_an_early_exit(self):
        tape = tape_of(SIG, [FLAT, (100, 104, 99, 100)] + [FLAT] * 15)
        cutoff = SIG + 10 * IV
        self.assertEqual(trade(tape, SIG, cutoff=cutoff)["reason"], "HORIZON_AFTER_CUTOFF")
        self.assertEqual(trade(tape, SIG, horizon=4, cutoff=cutoff)["reason"], "TARGET")

    def test_censoring_sensitivity_scores(self):
        cases = [case(T0 + 5 * IV, "ENTERED", "1"), case(T0 + 6 * IV, "CENSORED", None),
                 case(T0 + DAY + 5 * IV, "NOT_ENTERED", "0")]
        self.assertEqual(A._mean([A.censored_as(D(0))(c) for c in cases]), D(1) / 3)
        self.assertEqual(A._mean([A.censored_as(D(-1))(c) for c in cases]), D(0))
        self.assertEqual(A.arm_summary(cases)["mean_r"], D("0.5"))


DAY = A.DAY_MS


def case(close, status, r, reason="X"):
    return {"candle_close_time_ms": close, "status": status, "reason": reason,
            "r": D(r) if r is not None else None, "utc_day": A.utc_day(close)}


SETTINGS = A.Settings()


class BootstrapTests(unittest.TestCase):
    def test_block_construction(self):
        self.assertEqual(A.block_key(T0 + IV - 1, 1, T0), 0)
        self.assertEqual(A.block_key(T0 + DAY - 1, 1, T0), 0)
        self.assertEqual(A.block_key(T0 + DAY + IV - 1, 1, T0), 1)
        self.assertEqual(A.block_key(T0 + 2 * DAY - 1, 2, T0), 0)
        self.assertEqual(A.block_key(T0 + 2 * DAY + IV - 1, 2, T0), 1)
        self.assertEqual(A.block_key(T0 + 7 * DAY - 1, 7, T0), 0)
        self.assertEqual(A.block_key(T0 + 13 * DAY, 7, T0), 1)
        self.assertEqual(A.block_key(T0 + 14 * DAY, 7, T0), 2)

    def test_deterministic_and_percentiles(self):
        cases = [case(T0 + d * DAY + IV - 1, "ENTERED", str(d - 3)) for d in range(8)]
        first = A.ratio_interval(cases, A.scored_r, 1, SETTINGS)
        again = A.ratio_interval(cases, A.scored_r, 1, SETTINGS)
        self.assertEqual(first, again)
        A._DRAW_CACHE.clear()
        self.assertEqual(A.ratio_interval(cases, A.scored_r, 1, SETTINGS), first)
        self.assertEqual(A.percentile_indices(10_000), (249, 9749))
        # Manual recomputation with a fresh generator.
        import random
        rng = random.Random(20261012)
        values = sorted(sum(D(i - 3) for i in picks) / 8
                        for picks in ([rng.randrange(8) for _ in range(8)] for _ in range(10_000)))
        self.assertEqual((first["lower"], first["upper"]), (values[249], values[9749]))
        self.assertEqual(first["units"], 8)

    def test_units_exclude_blocks_without_scored_case_so_no_draw_is_empty(self):
        cases = [case(T0 + IV - 1, "ENTERED", "1"), case(T0 + DAY + IV - 1, "CENSORED", None),
                 case(T0 + 2 * DAY + IV - 1, "NOT_ENTERED", "0")]
        result = A.ratio_interval(cases, A.scored_r, 1, SETTINGS)
        self.assertEqual((result["status"], result["units"]), ("COMPUTED", 2))  # censored-only day dropped
        self.assertTrue(D(0) <= result["lower"] <= result["upper"] <= D(1))

    def test_fewer_than_two_units_gives_no_interval(self):
        one = A.ratio_interval([case(T0 + IV - 1, "ENTERED", "1"), case(T0 + 2 * IV - 1, "ENTERED", "2")],
                               A.scored_r, 1, SETTINGS)
        self.assertEqual((one["status"], one["lower"]), ("NOT_COMPUTED_FEWER_THAN_2_UNITS", None))
        self.assertEqual(A.ratio_interval([], A.scored_r, 1, SETTINGS)["units"], 0)


class ContrastTests(unittest.TestCase):
    def test_joint_resampling_keeps_both_arms_of_a_block(self):
        tdal = [case(T0 + IV - 1, "ENTERED", "2"), case(T0 + DAY + IV - 1, "ENTERED", "0"),
                case(T0 + 2 * DAY + IV - 1, "ENTERED", "5")]            # day 2 has no comparator
        comp = [case(T0 + 3 * IV - 1, "ENTERED", "1"), case(T0 + DAY + 3 * IV - 1, "NOT_ENTERED", "0"),
                case(T0 + DAY + 4 * IV - 1, "ENTERED", "-1"), case(T0 + 3 * DAY + IV - 1, "ENTERED", "9")]
        result = A.contrast_interval(tdal, comp, 1, SETTINGS)
        self.assertEqual(result["retained_blocks"], [0, 1])
        self.assertEqual((result["blocks_tdal_only"], result["blocks_comparator_only"]), (1, 1))
        self.assertEqual(result["estimate"], D(1) - D(0))  # TDAL (2+0)/2 - comparator (1+0-1)/3
        import random
        rng = random.Random(20261012)
        units = [(D(2), 1, D(1), 1), (D(0), 1, D(-1), 2)]
        values = []
        for _ in range(10_000):
            picks = [rng.randrange(2) for _ in range(2)]
            values.append(sum(units[i][0] for i in picks) / sum(units[i][1] for i in picks)
                          - sum(units[i][2] for i in picks) / sum(units[i][3] for i in picks))
        values.sort()
        self.assertEqual((result["lower"], result["upper"]), (values[249], values[9749]))

    def test_contrast_block_with_one_arm_only_has_no_units(self):
        result = A.contrast_interval([case(T0 + IV - 1, "ENTERED", "1")], [case(T0 + DAY + IV - 1, "ENTERED", "1")],
                                     1, SETTINGS)
        self.assertEqual((result["units"], result["estimate"], result["status"]),
                         (0, None, "NOT_COMPUTED_FEWER_THAN_2_UNITS"))


class ReadingTests(unittest.TestCase):
    def read(self, low, high, sparse=False):
        return A.read_interval({"status": "COMPUTED", "lower": D(low), "upper": D(high)}, is_sparse=sparse,
                               positive="POS", negative="NEG")

    def test_boundaries(self):
        self.assertEqual(self.read("0.0001", "1"), "POS")
        self.assertEqual(self.read("0", "1"), "INCONCLUSIVE")
        self.assertEqual(self.read("-1", "0"), "INCONCLUSIVE")
        self.assertEqual(self.read("-1", "-0.0001"), "NEG")
        self.assertEqual(self.read("0.5", "1", sparse=True), "INCONCLUSIVE_SPARSE")
        self.assertEqual(A.read_interval({"status": "NOT_COMPUTED_FEWER_THAN_2_UNITS"}, is_sparse=False,
                                         positive="P", negative="N"), "INCONCLUSIVE_NO_INTERVAL")

    def test_sparse_thresholds_on_entered_trades_and_days(self):
        def summary(entered, days):
            cases = [case(T0 + (i % days) * DAY + IV - 1 + IV * (i // days), "ENTERED", "1") for i in range(entered)]
            cases += [case(T0 + 40 * DAY + IV * i, "NOT_ENTERED", "0") for i in range(50)]
            return A.arm_summary(cases)
        self.assertTrue(A.sparse(summary(29, 20), SETTINGS))   # not-entered cases don't count
        self.assertTrue(A.sparse(summary(30, 14), SETTINGS))
        self.assertFalse(A.sparse(summary(30, 15), SETTINGS))
        self.assertTrue(A.sparse(A.arm_summary([]), SETTINGS))


# --- synthetic export files (layout of prospective_data.py, PR #26) ------------

def price(k):
    """Deterministic synthetic close for candle index k."""
    return D(100) + D((k * 37) % 23 - 11) / 4


def candle_k(k, base_close):
    close_time = base_close + k * IV
    c, prev = price(k), price(k - 1)
    return {"open_time_ms": close_time - IV + 1, "close_time_ms": close_time, "open": prev,
            "high": max(prev, c) + D("0.5"), "low": min(prev, c) - D("0.5"), "close": c, "volume": D(10)}


def risk_contract(close, *, reward=True, inv_offset=D(2)):
    inv = close - inv_offset
    rr = ({"status": "AVAILABLE", "level": str(close + D(3)), "gross_opportunity_to_risk": str(D(3) / inv_offset)}
          if reward else {"status": "TARGET_UNAVAILABLE", "reason": "NO_CONFIRMED_LEVEL_ABOVE_REFERENCE_IN_WINDOW"})
    return {"status": "AVAILABLE", "reference_price": {"value": str(close)},
            "invalidation": {"level": str(inv), "derivation": {"breakout_atr": "1"}},
            "per_unit_risk": {"distance": str(inv_offset), "distance_atr": str(inv_offset)},
            "reward_reference": rr}


def replay_row(index, observed, close_time, signal, close):
    stale = signal == "DATA_STALE"
    eligible = signal.startswith("LONG_ELIGIBLE")
    risk = (risk_contract(close, reward=signal != "LONG_ELIGIBLE_NO_TARGET") if eligible
            else {"status": "WITHHELD_BLOCKED"} if stale else {"status": "NOT_APPLICABLE"})
    payload = {
        "schema_version": "replay_decision/0.2", "replay_version": I.PINNED_REPLAY_VERSION,
        "snapshot_index": index, "observed_at_ms": observed,
        "completed_candle_close_time_ms": None if stale else close_time,
        "signal": "LONG_ELIGIBLE" if eligible else signal, "setup": {"state": "IDLE"},
        "features": None if stale else {"completed_close": str(close), "atr_15m": "1",
                                        "return_15m_pct": "0.1" if index % 2 else "-0.1", "spread_bps": "3",
                                        "resistance": "1"},
        "gates": [], "risk": risk,
    }
    return {"decision_id": stable_id("replay_decision", payload), **payload}


def write_canonical(path, value):
    path.write_text(canonical_json(value) + "\n", encoding="utf-8")


def sha(path):
    return I.sha256_file(path)


class Segment:
    """One segment directory: snapshots, manifests, mapping, pinned replay, parity report."""

    def __init__(self, out, name, *, first_k, last_k, base_close, signals, delay_ms=90_000,
                 no_live=(), extra_polls=(), history=40, live_failures=()):
        self.name, self.dir = name, Path(out) / name
        self.dir.mkdir(parents=True)
        snapshots, decisions, mapping = [], [], []
        for k in range(first_k, last_k + 1):
            close_time = base_close + k * IV
            polls = [(delay_ms, signals.get(k, "NO_SETUP"))] + [(d, s) for kk, d, s in extra_polls if kk == k]
            for delay, signal in polls:
                observed = close_time + 1 + delay
                snapshot = {"schema_version": "normalized_snapshot/0.1", "observed_at_ms": observed,
                            "exchange_time_ms": observed - 500, "research_symbol": "TAOUSDT",
                            "executable_pair": "TAOUSD",
                            "candles_15m": [candle_k(j, base_close) for j in range(k - history, k + 2)]}
                index = len(snapshots)
                snapshots.append(snapshot)
                decisions.append(replay_row(index, observed, close_time, signal, price(k)))
                sid = stable_id("snapshot", snapshot)
                live = index not in no_live
                mapping.append({"segment": name, "snapshot_index": index, "snapshot_id": sid,
                                "recomputed_snapshot_id": sid, "observed_at_ms": observed,
                                "status": "MATCHED" if live else "NO_LIVE_DECISION",
                                "live_decision_id": f"decision_{name}_{index}" if live else None,
                                "decision_time_ms": observed + 30_000 if live else observed,
                                "time_source": "LIVE_DECISION" if live else "OBSERVED_AT_FALLBACK"})
        rows, manifest = build_dataset_artifact(snapshots, source=f"prospective_v1:{name}",
                                                source_metadata={"segment": name})
        write_dataset(rows, manifest, jsonl_path=self.dir / "snapshots.jsonl",
                      manifest_path=self.dir / "dataset_manifest.json")
        self.manifest = manifest
        (self.dir / "mapping.jsonl").write_text("".join(canonical_json(m) + "\n" for m in mapping))
        (self.dir / "live_decisions.jsonl").write_text("")
        baseline = {"replay_version": I.PINNED_REPLAY_VERSION, "configuration": I.expected_replay_configuration(),
                    "configuration_id": stable_id("config", I.expected_replay_configuration()),
                    "input_snapshot_count": len(decisions)}
        write_canonical(self.dir / "replay.json", {
            "schema_version": "historical_replay/0.1", "baseline": baseline, "decisions": decisions,
            "gaps": [], "limitations": ["synthetic"], "summary": {"decision_count": len(decisions)},
            "replay_id": stable_id("replay", {"baseline": baseline,
                                              "decision_ids": [d["decision_id"] for d in decisions]})})
        write_canonical(self.dir / "parity_report.json", {
            "status": "PASS", "live_decisions_without_snapshot": [
                {"decision_id": f"fail_{i}", "decision_time_ms": t, "signal": "DATA_STALE", "error": "x"}
                for i, t in enumerate(live_failures)]})
        self.decisions, self.mapping = decisions, mapping

    def record(self, rows_pooled, rows_not_pooled):
        files = {n: sha(self.dir / n) for n in I.SEGMENT_FILES}
        return {"dataset_id": self.manifest["dataset_id"], "files_sha256": files,
                "rows_pooled": rows_pooled, "rows_not_pooled": rows_not_pooled}


def pool(out, segments, boundary=None):
    """The pooled directory, as prospective_data.pool_segments writes it."""
    pooled_dir = Path(out) / "pooled"
    pooled_dir.mkdir()
    write_canonical(pooled_dir / "seam_report.json", {"status": "OK", "seam": {"moved": False}, "overlap": {}})
    first_of, lines, counts = {}, [], {}
    for position, segment in enumerate(segments):
        pooled = not_pooled = 0
        for m, d in zip(segment.mapping, segment.decisions):
            candle = d["completed_candle_close_time_ms"]
            if boundary is not None:
                in_first = candle < boundary if candle is not None else m["observed_at_ms"] <= boundary
                if in_first != (position == 0):
                    not_pooled += 1
                    continue
            blocked = d["signal"] in ("DATA_STALE", "RISK_BLOCKED")
            reward = (d["risk"].get("reward_reference") or {}).get("status")
            item = {"pooled_index": len(lines), "segment": segment.name, "snapshot_index": m["snapshot_index"],
                    "snapshot_id": m["snapshot_id"], "observed_at_ms": m["observed_at_ms"],
                    "mapping_status": m["status"], "live_decision_id": m["live_decision_id"],
                    "decision_time_ms": m["decision_time_ms"], "time_source": m["time_source"],
                    "replay_decision_id": d["decision_id"], "completed_candle_close_time_ms": candle,
                    "signal": d["signal"], "setup_state": "IDLE", "risk_status": d["risk"]["status"],
                    "reward_reference_status": reward, "blocked": blocked, "first_gate_passing": False,
                    "duplicate": False, "signal_differs_from_first": False}
            if not blocked and candle is not None:
                if candle not in first_of:
                    first_of[candle] = item
                    item["first_gate_passing"] = True
                else:
                    item["duplicate"] = True
                    item["signal_differs_from_first"] = item["signal"] != first_of[candle]["signal"]
            item["targeted"] = (item["first_gate_passing"] and item["signal"] == "LONG_ELIGIBLE"
                                and reward == "AVAILABLE")
            lines.append(item)
            pooled += 1
        counts[segment.name] = (pooled, not_pooled)
    (pooled_dir / "population.jsonl").write_text("".join(canonical_json(i) + "\n" for i in lines))
    write_canonical(pooled_dir / "population_manifest.json", {
        "status": "OK", "pinned_code_commit": "76dbb5d-synthetic",
        "effective_cutover_candle_close_ms": boundary, "cutover_moved": False,
        "segments": {s.name: s.record(*counts[s.name]) for s in segments},
        "population_sha256": sha(pooled_dir / "population.jsonl"),
        "seam_report_sha256": sha(pooled_dir / "seam_report.json")})
    return Path(out)


def refingerprint(out, segments, boundary=None):
    """Rebuild the pooled directory after a deliberate edit (tests of later checks)."""
    import shutil
    shutil.rmtree(Path(out) / "pooled")
    return pool(out, segments, boundary)


BASE = FIRST_CLOSE  # candle index 0 is the first candle closing after the holdout start
TEST_SETTINGS = replace(A.Settings(), partition="SYNTHETIC_TEST", trigger_targeted_cases=3)


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "export"
        signals = {10: "LONG_ELIGIBLE", 40: "LONG_ELIGIBLE", 100: "LONG_ELIGIBLE", 130: "LONG_ELIGIBLE",
                   60: "LONG_ELIGIBLE_NO_TARGET", 20: "DATA_STALE"}
        self.segment = Segment(self.out, "segment1", first_k=0, last_k=200, base_close=BASE, signals=signals,
                               no_live=(3,), extra_polls=((40, 300_000, "NO_SETUP"), (50, 300_000, "NO_SETUP")),
                               live_failures=(START + 10 * IV,))
        pool(self.out, [self.segment])

    def tearDown(self):
        self.tmp.cleanup()

    def run_it(self, **kw):
        return A.run_analysis(self.out, settings=kw.pop("settings", TEST_SETTINGS), **kw)

    def argv(self, output):
        return ["--export-dir", str(self.out), "--output", str(output)]

    def test_full_run_population_cutoff_and_sections(self):
        result = self.run_it()
        window = result["window"]
        third = next(d for d in self.segment.decisions if d["completed_candle_close_time_ms"] == BASE + 100 * IV)
        self.assertEqual(window["cutoff_source"], "DERIVED_SECTION_5")
        self.assertEqual(window["cutoff_ms"], third["observed_at_ms"])
        self.assertEqual(result["population"]["first_observation_classes"],
                         {"NOT_ELIGIBLE": 96, "TARGETED": 3, "TARGET_UNAVAILABLE": 1})
        counts = result["population"]["selection_counts"]
        self.assertEqual((counts["blocked_rows"], counts["duplicate_rows"]), (1, 2))
        coverage = result["secondary"]["7_coverage"]
        self.assertEqual([c["candle_close_time_ms"] for c in coverage["changed_signal_duplicates"]], [BASE + 40 * IV])
        self.assertEqual(coverage["live_failures_without_snapshot"], 1)
        cases = result["primary"]["cases"]
        self.assertEqual(len(cases), 3)
        self.assertEqual(cases[-1]["reason"], "HORIZON_AFTER_CUTOFF")  # the trigger case itself
        self.assertEqual(result["primary"]["reading"], "INCONCLUSIVE_SPARSE")
        self.assertEqual(cases[0]["time_source"], "LIVE_DECISION")
        self.assertEqual(cases[0]["decision_time_ms"], cases[0]["observed_at_ms"] + 30_000)
        comparator = result["secondary"]["1_comparator"]
        self.assertEqual(comparator["candidates"], 96)
        self.assertEqual(sum(comparator["exclusions"].values()) + comparator["summary_all_window"]["cases"], 96)
        ledger = result["secondary"]["6_governed_paper_ledger"]
        self.assertEqual(ledger["decisions_offered"], 4)
        self.assertEqual({run["status"] for run in ledger["runs"].values()}, {"RUN"})
        self.assertEqual(coverage["no_live_decision_rows"][0]["snapshot_index"], 3)
        self.assertEqual(result["inputs"]["fingerprints_sha256"]["segment1/snapshots.jsonl"],
                         sha(self.out / "segment1" / "snapshots.jsonl"))
        self.assertTrue(result["deviations"])  # synthetic settings are labelled as not the holdout

    def test_no_live_decision_falls_back_to_observed_at(self):
        result = self.run_it()
        comparator = result["secondary"]["1_comparator"]
        row = [c for c in comparator["cases"] + comparator["excluded_cases"] if c["snapshot_index"] == 3][0]
        self.assertEqual((row["time_source"], row["decision_time_ms"]), ("OBSERVED_AT_FALLBACK", row["observed_at_ms"]))
        self.assertEqual(row["entry_candle_offset"], 2)

    def test_deterministic_and_exclusive_outputs(self):
        first, second = self.run_it(), self.run_it()
        self.assertEqual(first, second)
        out = Path(self.tmp.name) / "out" / "result.json"
        summary = A.write_outputs(first, out)
        self.assertIn("Reading: INCONCLUSIVE_SPARSE", summary.read_text())
        with self.assertRaises(FileExistsError):
            A.write_outputs(first, out)

    def test_holdout_partition_refuses_to_run_before_trigger(self):
        with self.assertRaises(A.AnalysisHalt):
            self.run_it(settings=A.Settings(), cutoff_ms=BASE + 150 * IV)

    def test_empty_window_is_inconclusive_sparse_not_a_crash(self):
        result = self.run_it(cutoff_ms=START + 1)
        self.assertEqual(result["primary"]["reading"], "INCONCLUSIVE_SPARSE")
        self.assertEqual(result["primary"]["summary"]["cases"], 0)
        self.assertEqual(result["primary"]["intervals"]["utc_day"]["status"], "NOT_COMPUTED_FEWER_THAN_2_UNITS")
        self.assertEqual(result["secondary"]["1_comparator"]["reading"], "INCONCLUSIVE_SPARSE")
        self.assertIn("6_governed_paper_ledger", result["secondary"])

    def test_cli_halts_on_tampered_replay_and_writes_nothing(self):
        path = self.out / "segment1" / "replay.json"
        data = json.loads(path.read_text())
        data["decisions"][5]["signal"] = "LONG_ELIGIBLE"
        write_canonical(path, data)
        refingerprint(self.out, [self.segment])
        out = Path(self.tmp.name) / "r.json"
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(A.main(self.argv(out)), 2)
        self.assertIn("decision_id", err.getvalue())
        self.assertFalse(out.exists())

    def test_cli_refuses_unrecorded_file_change(self):
        path = self.out / "segment1" / "mapping.jsonl"
        path.write_text(path.read_text() + "\n")
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(A.main(self.argv(Path(self.tmp.name) / "r.json")), 2)
        self.assertIn("SHA-256", err.getvalue())

    def test_cli_holdout_run_before_trigger_halts(self):
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(A.main(self.argv(Path(self.tmp.name) / "r.json")), 2)
        self.assertIn("trigger not met", err.getvalue())

    def test_cli_population_manifest_hash_and_status(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(A.main(self.argv(Path(self.tmp.name) / "r.json")
                                    + ["--population-manifest-sha256", "0" * 64]), 2)
        manifest_path = self.out / "pooled" / "population_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        write_canonical(manifest_path, {**manifest, "status": "HALT"})
        with self.assertRaises(I.InputError):
            self.run_it()

    def test_smoke_cli_writes_labelled_result(self):
        out = Path(self.tmp.name) / "smoke.json"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(A.main(self.argv(out) + ["--non-holdout-smoke", "--cutoff-ms", str(BASE + 200 * IV)]), 0)
        result = json.loads(out.read_text())
        self.assertEqual(result["partition"], "NON_HOLDOUT_SMOKE")
        self.assertTrue(out.with_suffix(".summary.md").exists())

    def test_population_flag_disagreement_halts(self):
        path = self.out / "pooled" / "population.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows[12]["first_gate_passing"] = False
        path.write_text("".join(canonical_json(r) + "\n" for r in rows))
        manifest_path = self.out / "pooled" / "population_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        write_canonical(manifest_path, {**manifest, "population_sha256": sha(path)})
        with self.assertRaises(A.AnalysisHalt):
            self.run_it()

    def test_snapshot_id_mismatch_halts(self):
        path = self.out / "pooled" / "population.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows[7]["snapshot_id"] = "snapshot_000"
        path.write_text("".join(canonical_json(r) + "\n" for r in rows))
        manifest_path = self.out / "pooled" / "population_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        write_canonical(manifest_path, {**manifest, "population_sha256": sha(path)})
        with self.assertRaises(A.AnalysisHalt):
            self.run_it()


class SeamTests(unittest.TestCase):
    def test_segments_supply_candles_on_their_side_of_the_cutover_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "export"
            boundary = BASE + 51 * IV  # the effective cutover candle: candle 51 on comes from segment 2
            one = Segment(out, "segment1", first_k=0, last_k=60, base_close=BASE,
                          signals={10: "LONG_ELIGIBLE", 55: "LONG_ELIGIBLE"})
            two = Segment(out, "segment2", first_k=45, last_k=120, base_close=BASE, delay_ms=150_000,
                          signals={48: "LONG_ELIGIBLE", 70: "LONG_ELIGIBLE", 80: "LONG_ELIGIBLE"})
            pool(out, [one, two], boundary)
            result = A.run_analysis(out, settings=TEST_SETTINGS, cutoff_ms=BASE + 120 * IV)
            cases = result["primary"]["cases"]
            self.assertEqual([(c["segment"], (c["candle_close_time_ms"] - BASE) // IV) for c in cases],
                             [("segment1", 10), ("segment2", 70), ("segment2", 80)])
            self.assertEqual(result["secondary"]["7_coverage"]["rows_not_pooled_by_segment"],
                             {"segment1": 10, "segment2": 6})
            export = I.load_export(out)
            self.assertEqual((export.supplier(boundary - 1), export.supplier(boundary)), ("segment1", "segment2"))


class InputAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "export"
        self.segment = Segment(self.out, "segment1", first_k=0, last_k=12, base_close=BASE,
                               signals={5: "LONG_ELIGIBLE"})
        pool(self.out, [self.segment])

    def tearDown(self):
        self.tmp.cleanup()

    def test_streaming_manifest_check_matches_dataset_module(self):
        path = self.out / "segment1" / "snapshots.jsonl"
        manifest = json.loads((self.out / "segment1" / "dataset_manifest.json").read_text())
        rows = [row for _, row in I.stream_verified_snapshots(path, manifest)]
        _, rebuilt = validate_dataset_manifest(rows, manifest)
        self.assertEqual(rebuilt["dataset_id"], manifest["dataset_id"])
        with self.assertRaises(I.InputError):
            list(I.stream_verified_snapshots(path, dict(manifest, snapshot_count=manifest["snapshot_count"] + 1)))

    def test_replay_stream_matches_json_load(self):
        path = self.out / "segment1" / "replay.json"
        whole = json.loads(path.read_text())
        self.assertEqual(list(I.iter_replay_decisions(path, chunk=97)), whole["decisions"])
        self.assertEqual(I.replay_id_of(path), whole["replay_id"])
        self.assertEqual(I.read_replay_baseline(path), whole["baseline"])

    def test_replay_version_and_configuration_are_pinned(self):
        path = self.out / "segment1" / "replay.json"
        data = json.loads(path.read_text())
        for edit in (lambda d: d["baseline"].update(replay_version="trading-decision-lab-replay-0.4.0"),
                     lambda d: d["baseline"]["configuration"].update(reward_search_bars=100),
                     lambda d: d.update(replay_id="replay_x")):
            changed = json.loads(json.dumps(data))
            edit(changed)
            write_canonical(path, changed)
            refingerprint(self.out, [self.segment])
            with self.assertRaises(I.InputError):
                I.load_export(self.out)

    def test_population_rows_must_carry_one_live_decision_or_the_fallback(self):
        path = self.out / "pooled" / "population.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]

        def bad(edit):
            changed = [dict(r) for r in rows]
            edit(changed)
            path.write_text("".join(canonical_json(r) + "\n" for r in changed))
            manifest_path = self.out / "pooled" / "population_manifest.json"
            manifest = json.loads(manifest_path.read_text())
            write_canonical(manifest_path, {**manifest, "population_sha256": sha(path)})
            with self.assertRaises(I.InputError):
                I.load_export(self.out)
        bad(lambda r: r[2].update(mapping_status="MULTIPLE_LIVE_DECISIONS"))
        bad(lambda r: r[2].update(decision_time_ms=r[2]["observed_at_ms"] - 1))
        bad(lambda r: r[2].update(mapping_status="NO_LIVE_DECISION"))   # time source contradicts
        bad(lambda r: r.pop(4))                                          # pooled_index gap
        bad(lambda r: r[3].update(signal="LONG_ELIGIBLE"))               # disagrees with the replay

    def test_parity_must_pass(self):
        write_canonical(self.out / "segment1" / "parity_report.json", {"status": "FAIL"})
        refingerprint(self.out, [self.segment])
        with self.assertRaises(I.InputError):
            I.load_export(self.out)


def pooled_row(index, close_time, signal, *, risk_status=None, reward=None, row=None):
    return I.PooledRow(index, "segment1", index, "snapshot_x", close_time + 60_000, "MATCHED", "d",
                       close_time + 90_000, "LIVE_DECISION", "r", close_time, signal, risk_status, reward,
                       False, True, False, False, False, row)


class LedgerTests(unittest.TestCase):
    def test_governor_enters_at_the_section_3_decision_time(self):
        signal = SIG
        row = I.slim_row(replay_row(0, signal + 61_000, signal, "LONG_ELIGIBLE", D(100)))
        late = signal + 1 + IV + 60_000  # decision after candle 2 opened: entry is candle 3
        item = pooled_row(0, signal, "LONG_ELIGIBLE", risk_status="AVAILABLE", reward="AVAILABLE", row=row)
        item.decision_time_ms = late
        tape = tape_of(signal, [FLAT] * 20)
        ledger = A.paper_ledger([item], tape, signal + 30 * IV, SETTINGS, "dataset", "replay")
        run = ledger["runs"]["MARKET_BASELINE/TIER_4_OWNER"]
        trade = run["artifact"]["trades"][0]
        self.assertEqual(trade["entry"]["bar_close_time_ms"], signal + 3 * IV)
        self.assertEqual((run["taker_fee_bps"], run["maker_fee_bps"]), (D(35), D(20)))
        self.assertEqual(ledger["runs"]["MARKET_BASELINE/TIER_1_SENSITIVITY"]["taker_fee_bps"], D(80))
        self.assertEqual(ledger["runs"]["MAKER_STAGGERED/TIER_1_SENSITIVITY"]["maker_fee_bps"], D(40))


class ComparatorTests(unittest.TestCase):
    def observation(self, close_time, snapshot_index=0):
        return pooled_row(snapshot_index, close_time, "NO_SETUP",
                          row={"features": {"completed_close": "100", "atr_15m": "2"}, "signal": "NO_SETUP"})

    def snapshot(self, close_time, highs):
        candles = []
        for i, high in enumerate(highs):
            t = close_time - (len(highs) - 1 - i) * IV
            candles.append({"open_time_ms": t - IV + 1, "close_time_ms": t, "open": D(99), "high": D(high),
                            "low": D(98), "close": D(99) if i < len(highs) - 1 else D(100), "volume": D(1)})
        return {"exchange_time_ms": close_time + 1000, "candles_15m": candles}

    def test_levels_from_own_snapshot_with_fixed_d(self):
        close = BASE + 20 * IV
        snap = self.snapshot(close, [100, 101, 106, 102, 101, 100.5, 100.5])
        levels = A.comparator_levels(self.observation(close), snap, D("0.6797"))
        self.assertEqual(levels["invalidation"], D(100) - D("0.6797") * 2)
        self.assertEqual(levels["risk_distance"], D("1.3594"))
        self.assertEqual(levels["target"], D(106))

    def test_target_unavailable_is_excluded(self):
        close = BASE + 20 * IV
        snap = self.snapshot(close, [100, 100, 100, 100, 100, 100, 100])
        levels = A.comparator_levels(self.observation(close), snap, D("0.6797"))
        self.assertEqual(levels["excluded"], "TARGET_UNAVAILABLE")

    def test_population_excludes_every_long_eligible_candle(self):
        def obs(k, signal, risk_status=None, reward=None):
            return pooled_row(k, BASE + k * IV, signal, risk_status=risk_status, reward=reward)
        items = [obs(0, "NO_SETUP"), obs(1, "LONG_ELIGIBLE", "AVAILABLE", "AVAILABLE"),
                 obs(2, "LONG_ELIGIBLE", "AVAILABLE", "TARGET_UNAVAILABLE"),
                 obs(3, "LONG_ELIGIBLE", "UNAVAILABLE"), obs(4, "RETEST_PENDING", "NOT_APPLICABLE")]
        self.assertEqual([A.classify(i) for i in items], ["NOT_ELIGIBLE", "TARGETED", "TARGET_UNAVAILABLE",
                                                          "ELIGIBLE_RISK_UNAVAILABLE", "NOT_ELIGIBLE"])


if __name__ == "__main__":
    unittest.main()
