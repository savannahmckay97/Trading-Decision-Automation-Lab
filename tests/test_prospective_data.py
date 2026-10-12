"""Prospective v1 export, pinned-replay parity and seam tooling, on synthetic databases only."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "research" / "prospective_v1"))
sys.path.insert(0, str(ROOT / "tests"))
import prospective_data as pd  # noqa: E402
import prospective_fixtures as fx  # noqa: E402

from market_observer.config import ObserverConfig  # noqa: E402
from market_observer.dataset import build_dataset_artifact  # noqa: E402
from market_observer.replay import (REPLAY_VERSION, ReplayRunner, load_jsonl_snapshots,  # noqa: E402
                                    load_sqlite_snapshots, write_replay)
from market_observer.util import canonical_json, stable_id  # noqa: E402

PINNED = "76dbb5d"
# Segment 1: breakout, a duplicate poll of it, a failure without a snapshot, a DATA_STALE
# cycle (missing taker flow -> as-of failure), the retest, two quiet candles.
SEGMENT1 = [["ok", 100, 0], ["ok", 100, 1], ["nosnapshot", 101, 0], ["missing", 101, 2, "binance_taker"],
            ["ok", 101, 3], ["ok", 102, 0], ["ok", 103, 0]]


def in_process_runner(mutate=None):
    """Replay with the current code in-process (stands in for the pinned subprocess)."""
    def run(jsonl, output):
        result = ReplayRunner(ObserverConfig()).run(load_jsonl_snapshots(jsonl))
        if mutate:
            mutate(result["decisions"])
        write_replay(result, output)
        return {"runner": "in-process-test", "code_commit": "current-test-code", "replay_version": REPLAY_VERSION}
    return run


class Workspace:
    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def db(self, name, steps, **kwargs):
        live = self.root / f"{name}.live.sqlite"
        fx.build_db(live, steps, **kwargs)
        frozen = self.root / f"{name}.sqlite"
        pd.freeze_database(live, frozen)
        return frozen

    def cleanup(self):
        self._tmp.cleanup()


class ProspectiveTestCase(unittest.TestCase):
    def setUp(self):
        self.ws = Workspace()
        self.addCleanup(self.ws.cleanup)

    def processed(self, name, steps, runner=None, **kwargs):
        database = self.ws.db(name, steps, **kwargs)
        out = self.ws.root / name
        pd.process_segment(database, out, segment=name, runner=runner or in_process_runner())
        return out


class ExportTests(ProspectiveTestCase):
    def test_mapping_matches_live_decisions_and_dataset_functions(self):
        database = self.ws.db("segment1", SEGMENT1)
        out = self.ws.root / "export"
        manifest = pd.export_segment(database, out, segment="segment1")
        mapping = list(pd.iter_jsonl(out / pd.MAPPING))
        live = list(pd.iter_jsonl(out / pd.LIVE_DECISIONS))
        self.assertEqual(len(live), 7)
        self.assertEqual(len(mapping), 6)  # the failure without a snapshot has no row
        self.assertEqual([row["snapshot_index"] for row in mapping], list(range(6)))
        by_id = {item["decision_id"]: item for item in live}
        for row in mapping:
            self.assertEqual(row["status"], "MATCHED")
            self.assertEqual(row["time_source"], "LIVE_DECISION")
            self.assertEqual(row["snapshot_id"], row["recomputed_snapshot_id"])
            decision = by_id[row["live_decision_id"]]
            self.assertEqual(decision["snapshot_id"], row["snapshot_id"])
            self.assertEqual(row["decision_time_ms"], decision["decision_time_ms"])
            self.assertEqual(row["decision_time_ms"] - row["observed_at_ms"], fx.DECISION_DELAY_MS)
            self.assertEqual(row["live_signal"], decision["signal"])
        self.assertEqual([row["live_signal"] for row in mapping],
                         ["WATCH_LONG", "WATCH_LONG", "DATA_STALE", "LONG_ELIGIBLE", "NO_TRADE", "NO_TRADE"])
        self.assertEqual(mapping[0]["live_completed_candle_close_time_ms"], fx.candle_close(100))
        self.assertEqual(mapping[1]["live_completed_candle_close_time_ms"], fx.candle_close(100))
        self.assertIsNone(mapping[2]["live_completed_candle_close_time_ms"])
        self.assertEqual(mapping[2]["asof_audit"], "FAIL")
        self.assertIn("binance_taker", mapping[2]["asof_error"])
        self.assertEqual(manifest["counts"]["live_decisions_without_snapshot"], 1)
        self.assertIn("collection completion", manifest["live_decisions_without_snapshot"][0]["error"])

        # The JSONL is the dataset build of the stored snapshots, in replay order.
        snapshots = list(load_sqlite_snapshots(database, "TAOUSDT"))
        _rows, expected = build_dataset_artifact(snapshots, source="prospective_v1:segment1",
                                                 source_metadata=_json(out / pd.DATASET_MANIFEST)["source_metadata"])
        self.assertEqual(_json(out / pd.DATASET_MANIFEST), expected)
        exported = list(pd.iter_jsonl(out / pd.SNAPSHOTS))
        self.assertEqual([stable_id("snapshot", row) for row in exported], [row["snapshot_id"] for row in mapping])
        self.assertEqual(ReplayRunner(ObserverConfig()).run(load_jsonl_snapshots(out / pd.SNAPSHOTS)),
                         ReplayRunner(ObserverConfig()).run(snapshots))
        for name, digest in manifest["files_sha256"].items():
            self.assertEqual(pd.sha256_file(out / name), digest)
        self.assertEqual(manifest["source"]["database_sha256"], pd.sha256_file(database))
        with self.assertRaises(FileExistsError):
            pd.export_segment(database, out, segment="segment1")

    def test_missing_live_decision_falls_back_to_observed_at(self):
        database = self.ws.db("segment1", SEGMENT1)
        with contextlib.closing(sqlite3.connect(database)) as db, db:
            snapshot_id = db.execute("SELECT snapshot_id FROM normalized_snapshots ORDER BY observed_at_ms, rowid "
                                     "LIMIT 1 OFFSET 3").fetchone()[0]
            db.execute("DELETE FROM decisions WHERE json_extract(payload_json,'$.snapshot_id')=?", (snapshot_id,))
        manifest = pd.export_segment(database, self.ws.root / "export", segment="segment1")
        row = list(pd.iter_jsonl(self.ws.root / "export" / pd.MAPPING))[3]
        self.assertEqual(row["status"], "NO_LIVE_DECISION")
        self.assertEqual(row["time_source"], "OBSERVED_AT_FALLBACK")
        self.assertEqual(row["decision_time_ms"], row["observed_at_ms"])
        self.assertIsNone(row["live_decision_id"])
        self.assertEqual(manifest["no_live_decision"],
                         [{"snapshot_index": 3, "snapshot_id": snapshot_id, "observed_at_ms": row["observed_at_ms"]}])

    def assertHalts(self, database, text):
        out = self.ws.root / "halted"
        with self.assertRaises(pd.ProspectiveError) as caught:
            pd.export_segment(database, out, segment="segment1")
        self.assertIn(text, str(caught.exception))
        self.assertFalse(out.exists())
        self.assertFalse(out.with_name(out.name + ".partial").exists())

    def test_snapshot_id_mismatch_halts(self):
        database = self.ws.db("segment1", SEGMENT1)
        with contextlib.closing(sqlite3.connect(database)) as db, db:
            snapshot_id, payload = db.execute("SELECT snapshot_id, payload_json FROM normalized_snapshots "
                                              "ORDER BY rowid LIMIT 1").fetchone()
            edited = json.loads(payload)
            edited["market"]["kraken_last_price"] = "999"
            db.execute("UPDATE normalized_snapshots SET payload_json=? WHERE snapshot_id=?",
                       (canonical_json(edited), snapshot_id))
        self.assertHalts(database, f"stored snapshot_id {snapshot_id} != recomputed")

    def test_duplicate_live_decisions_halt(self):
        database = self.ws.db("segment1", SEGMENT1)
        with contextlib.closing(sqlite3.connect(database)) as db, db:
            row = db.execute("SELECT decision_time_ms, research_symbol, setup_state, signal, payload_json "
                             "FROM decisions WHERE json_extract(payload_json,'$.snapshot_id') IS NOT NULL "
                             "ORDER BY rowid LIMIT 1").fetchone()
            copy = json.loads(row[4])
            copy["decision_time_ms"] += 1
            db.execute("INSERT INTO decisions VALUES(?,?,?,?,?,?)",
                       (stable_id("decision", copy), copy["decision_time_ms"], row[1], row[2], row[3], canonical_json(copy)))
        self.assertHalts(database, "2 live decisions")

    def test_decision_before_observation_halts(self):
        self.assertHalts(self.ws.db("segment1", SEGMENT1[:2], decision_delay_ms=-1), "after decision_time_ms")

    def test_receipt_without_raw_event_halts(self):
        database = self.ws.db("segment1", SEGMENT1[:2])
        with contextlib.closing(sqlite3.connect(database)) as db, db:
            payload = json.loads(db.execute("SELECT payload_json FROM normalized_snapshots LIMIT 1").fetchone()[0])
            db.execute("DELETE FROM raw_events WHERE raw_id=?", (payload["provenance"]["kraken_ticker"]["raw_id"],))
        self.assertHalts(database, "raw_events row")

    def test_live_wal_database_is_refused(self):
        live = self.ws.root / "live.sqlite"
        fx.build_db(live, SEGMENT1[:1])
        with self.assertRaises(pd.ProspectiveError) as caught:
            pd.export_segment(live, self.ws.root / "export", segment="segment1")
        self.assertIn("freeze", str(caught.exception))


class ParityTests(ProspectiveTestCase):
    def test_parity_passes_and_lists_blocked_cycles(self):
        out = self.processed("segment1", SEGMENT1)
        report = _json(out / pd.PARITY_REPORT)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["counts"]["compared"], 6)
        self.assertEqual(report["counts"]["identical"], 6)
        self.assertEqual(report["counts"]["live_decisions_without_snapshot"], 1)
        failures = report["separate_validations"]["asof_audit_failures"]
        self.assertEqual([(f["snapshot_index"], f["live_signal"], f["replay_signal"]) for f in failures],
                         [(2, "DATA_STALE", "DATA_STALE")])
        provenance = _json(out / pd.REPLAY_PROVENANCE)
        self.assertEqual(provenance["code_commit"], "current-test-code")
        self.assertEqual(provenance["replay_sha256"], pd.sha256_file(out / pd.REPLAY))
        summary = list(pd.iter_jsonl(out / pd.REPLAY_SUMMARY))
        self.assertEqual(len(summary), 6)
        self.assertEqual(summary[3]["signal"], "LONG_ELIGIBLE")

    def test_parity_reports_a_changed_field(self):
        def mutate(decisions):
            decisions[3]["breakout_quality"]["components"]["volume"] += 1
            decisions[4]["risk"]["status"] = "EDITED"
        out = self.processed("segment1", SEGMENT1, runner=in_process_runner(mutate))
        report = _json(out / pd.PARITY_REPORT)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["counts"]["mismatched"], 2)
        first, second = report["mismatches"]
        self.assertEqual(first["snapshot_index"], 3)
        self.assertEqual(first["differences"], [{"path": ".breakout_quality.components.volume",
                                                 "live": first["differences"][0]["live"],
                                                 "replay": first["differences"][0]["live"] + 1}])
        self.assertEqual([d["path"] for d in second["differences"]], [".risk.status"])

    def test_mode_echo_is_the_only_gate_exclusion(self):
        live = {"signal": "NO_TRADE", "features": {"completed_candle_close_time_ms": 5},
                "gates": [{"rule_id": "T0_OBSERVATION_ONLY", "status": "PASS",
                           "evidence": {"mode": "observation_only", "orders_available": False}}]}
        replay = {"signal": "NO_TRADE", "completed_candle_close_time_ms": 5, "features": live["features"],
                  "gates": [{"rule_id": "T0_OBSERVATION_ONLY", "status": "PASS",
                             "evidence": {"mode": "historical_replay", "orders_available": False}}]}
        view = lambda d, is_live: pd.canonical_view(d, live=is_live)  # noqa: E731
        self.assertEqual(pd.diff_values(view(live, True), view(replay, False)), [])
        replay["gates"][0]["evidence"]["orders_available"] = True
        self.assertEqual([d["path"] for d in pd.diff_values(view(live, True), view(replay, False))],
                         [".gates[0].evidence.orders_available"])

    def test_cli_export_and_parity(self):
        database = self.ws.db("segment1", SEGMENT1)
        out = self.ws.root / "cli"
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(pd.main(["export", "--db", str(database), "--segment", "segment1", "--out", str(out)]), 0)
        self.assertEqual(json.loads(printed.getvalue())["snapshots"], 6)
        pd.replay_export(out, in_process_runner())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(pd.main(["parity", "--export-dir", str(out)]), 0)
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(pd.main(["parity", "--export-dir", str(out)]), 2)  # never overwrites
        self.assertIn("already exists", errors.getvalue())

    def test_streaming_reader_handles_chunk_boundaries(self):
        out = self.processed("segment1", SEGMENT1)
        expected = json.loads((out / pd.REPLAY).read_text())["decisions"]
        with mock.patch.object(pd, "_CHUNK", 37):
            self.assertEqual(list(pd.iter_replay_decisions(out / pd.REPLAY)), expected)
        self.assertEqual(pd.read_replay_baseline(out / pd.REPLAY)["input_snapshot_count"], 6)


class SeamTests(ProspectiveTestCase):
    def cutover(self, n_complete):
        """A cutover making candle ``n_complete`` the first candidate."""
        return fx.candle_close(n_complete - 1) + 1

    def test_cutover_candle_arithmetic(self):
        cutover = pd.parse_utc_ms("2026-10-13T00:00Z")
        self.assertEqual(pd.utc_iso(pd.first_candle_close_at_or_after(cutover)), "2026-10-13T00:14:59.999Z")
        self.assertEqual(pd.first_candle_close_at_or_after(cutover - 1), cutover - 1)

    def test_seam_agrees_at_cutover_and_never_mixes_segments(self):
        one = self.processed("segment1", SEGMENT1)
        two = self.processed("segment2", [["ok", 100, 7], ["ok", 101, 7], ["ok", 102, 7], ["ok", 103, 7],
                                          ["ok", 104, 7]])
        report = pd.pool_segments(one, two, self.cutover(102), self.ws.root / "pooled")
        self.assertEqual(report["status"], "OK")
        self.assertEqual(report["seam"]["effective_cutover_candle_close_ms"], fx.candle_close(102))
        self.assertFalse(report["seam"]["moved"])
        population = list(pd.iter_jsonl(self.ws.root / "pooled" / pd.POPULATION))
        sources = {}
        for row in population:
            candle = row["completed_candle_close_time_ms"]
            if candle is not None:
                sources.setdefault(candle, set()).add(row["segment"])
                expected = "segment1" if candle < fx.candle_close(102) else "segment2"
                self.assertEqual(row["segment"], expected)
        self.assertTrue(all(len(segments) == 1 for segments in sources.values()))
        self.assertEqual(sorted(sources), [fx.candle_close(n) for n in range(100, 105)])
        first = [row for row in population if row["first_gate_passing"]]
        self.assertEqual(len(first), 5)
        duplicates = [row for row in population if row["duplicate"]]
        self.assertEqual([(row["segment"], row["snapshot_index"]) for row in duplicates], [("segment1", 1)])
        blocked = [row for row in population if row["blocked"]]
        self.assertEqual([(row["segment"], row["snapshot_index"]) for row in blocked], [("segment1", 2)])
        manifest = _json(self.ws.root / "pooled" / pd.POPULATION_MANIFEST)
        self.assertEqual(manifest["counts"]["eligible_target_unavailable"], 1)
        self.assertEqual(manifest["segments"]["segment1"]["last_candle_close_ms"], fx.candle_close(101))
        self.assertEqual(manifest["segments"]["segment2"]["first_candle_close_ms"], fx.candle_close(102))
        self.assertEqual(manifest["population_sha256"], pd.sha256_file(self.ws.root / "pooled" / pd.POPULATION))
        # Both segments saw candles 100-103; their first gate-passing rows are compared.
        self.assertEqual(report["overlap"]["overlapping_candles"], 4)

    def test_seam_disagreement_moves_the_cutover(self):
        one = self.processed("segment1", SEGMENT1)
        two = self.processed("segment2", [["ok", 101, 5], ["ok", 102, 5], ["ok", 103, 5], ["ok", 104, 5]])
        report = pd.pool_segments(one, two, self.cutover(102), self.ws.root / "pooled")
        self.assertEqual(report["status"], "OK")
        seam = report["seam"]
        self.assertTrue(seam["moved"])
        self.assertEqual(seam["effective_cutover_candle_close_ms"], fx.candle_close(103))
        self.assertEqual([d["candle_close_ms"] for d in seam["disagreements"]], [fx.candle_close(102)])
        self.assertEqual(seam["disagreements"][0]["segment1"]["setup"]["state"], "LONG_ELIGIBLE")
        self.assertEqual(seam["disagreements"][0]["segment2"]["setup"]["state"], "IDLE")
        # Agreement ignores how IDLE was reached (TERMINAL_RESET vs INITIALIZED).
        self.assertEqual(seam["segment1_carried"]["setup"]["transition"], "TERMINAL_RESET")
        self.assertEqual(seam["segment2_carried"]["setup"]["transition"], "INITIALIZED")
        population = list(pd.iter_jsonl(self.ws.root / "pooled" / pd.POPULATION))
        self.assertEqual({row["completed_candle_close_time_ms"] for row in population if row["segment"] == "segment2"},
                         {fx.candle_close(103), fx.candle_close(104)})

    def test_seam_without_agreement_halts(self):
        one = self.processed("segment1", SEGMENT1[:6])  # stops after candle 102
        two = self.processed("segment2", [["ok", 101, 5], ["ok", 102, 5], ["ok", 103, 5]])
        out = self.ws.root / "pooled"
        with contextlib.redirect_stdout(io.StringIO()):
            code = pd.main(["pool", "--segment1-dir", str(one), "--segment2-dir", str(two),
                            "--cutover", pd.utc_iso(self.cutover(102)), "--out", str(out)])
        self.assertEqual(code, 1)
        report = _json(out / pd.SEAM_REPORT)
        self.assertEqual(report["status"], "HALT")
        self.assertIn("segment 1 was still recording", report["halts"][0])
        self.assertFalse((out / pd.POPULATION).exists())

    def test_parity_failure_blocks_pooling(self):
        def mutate(decisions):
            decisions[0]["signal"] = "NO_TRADE"
        one = self.processed("segment1", SEGMENT1, runner=in_process_runner(mutate))
        two = self.processed("segment2", [["ok", 100, 7], ["ok", 101, 7], ["ok", 102, 7], ["ok", 103, 7]])
        report = pd.pool_segments(one, two, self.cutover(102), self.ws.root / "pooled")
        self.assertEqual(report["status"], "HALT")
        self.assertIn("segment1: live/replay parity FAIL", report["halts"][0])
        self.assertFalse((self.ws.root / "pooled" / pd.POPULATION).exists())


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


FAKE_REPLAY = '''
import argparse, json
REPLAY_VERSION = "fake-replay-1"
def main():
    p = argparse.ArgumentParser(); p.add_argument("--jsonl"); p.add_argument("--output")
    a = p.parse_args()
    rows = [json.loads(line) for line in open(a.jsonl) if line.strip()]
    decisions = [{"snapshot_index": i, "observed_at_ms": r["observed_at_ms"]} for i, r in enumerate(rows)]
    out = {"baseline": {"replay_version": REPLAY_VERSION, "input_snapshot_count": len(rows)}, "decisions": decisions}
    open(a.output, "x").write(json.dumps(out, sort_keys=True, separators=(",", ":")) + "\\n")
if __name__ == "__main__":
    main()
'''


class PinnedReplayTests(ProspectiveTestCase):
    def fake_checkout(self):
        checkout = self.ws.root / "fake-checkout"
        (checkout / "market_observer").mkdir(parents=True)
        (checkout / "market_observer" / "__init__.py").write_text("")
        (checkout / "market_observer" / "replay.py").write_text(FAKE_REPLAY)
        _git("init", "-q", cwd=checkout)
        _git("add", ".", cwd=checkout)
        _git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "fake", cwd=checkout)
        return checkout, _git("rev-parse", "HEAD", cwd=checkout)

    def test_subprocess_wrapper_records_the_code_that_ran(self):
        checkout, head = self.fake_checkout()
        database = self.ws.db("segment1", SEGMENT1[:2])
        out = self.ws.root / "export"
        pd.export_segment(database, out, segment="segment1")
        record = pd.replay_export(out, pd.PinnedReplay(checkout, head[:7]))
        self.assertEqual(record["code_commit"], head)
        self.assertEqual(record["pinned_commit_requested"], head[:7])
        self.assertEqual(record["replay_version"], "fake-replay-1")
        self.assertEqual(Path(record["replay_module"]), (checkout / "market_observer" / "replay.py").resolve())
        self.assertEqual(record["command"][1:3], ["-m", "market_observer.replay"])
        self.assertEqual(_json(out / pd.REPLAY_PROVENANCE)["code_commit"], head)

    def test_wrong_commit_or_modified_checkout_is_refused(self):
        checkout, head = self.fake_checkout()
        (checkout / "other.txt").write_text("x")
        _git("add", ".", cwd=checkout)
        _git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "next", cwd=checkout)
        jsonl, output = self.ws.root / "in.jsonl", self.ws.root / "out.json"
        jsonl.write_text("")
        with self.assertRaisesRegex(pd.ProspectiveError, "not the pinned"):
            pd.PinnedReplay(checkout, head)(jsonl, output)
        newest = _git("rev-parse", "HEAD", cwd=checkout)
        (checkout / "market_observer" / "replay.py").write_text(FAKE_REPLAY + "\n# edited\n")
        with self.assertRaisesRegex(pd.ProspectiveError, "modified tracked files"):
            pd.PinnedReplay(checkout, newest)(jsonl, output)
        with self.assertRaisesRegex(pd.ProspectiveError, "pinned commit is required"):
            pd.PinnedReplay(checkout, "")


@unittest.skipUnless(shutil.which("git"), "git is not available")
class PinnedCommitTests(ProspectiveTestCase):
    """End to end against a real worktree of the pinned commit, when the clone has it
    (CI's shallow checkout doesn't, and the test skips)."""

    def setUp(self):
        super().setUp()
        probe = subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", f"{PINNED}^{{commit}}"],
                               capture_output=True)
        if probe.returncode != 0:
            self.skipTest(f"commit {PINNED} is not in this clone")
        self.worktree = self.ws.root / "pinned"
        _git("worktree", "add", "--detach", "-q", str(self.worktree), PINNED, cwd=ROOT)
        self.addCleanup(subprocess.run, ["git", "-C", str(ROOT), "worktree", "remove", "--force", str(self.worktree)],
                        capture_output=True)

    def pinned_db(self, name, steps):
        """A recorder database written by the pinned observer itself."""
        live = self.ws.root / f"{name}.live.sqlite"
        env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
        env.update(PYTHONPATH=str(self.worktree), PYTHONDONTWRITEBYTECODE="1")
        done = subprocess.run([sys.executable, str(ROOT / "tests" / "prospective_fixtures.py"), str(live),
                               json.dumps(steps)], cwd=self.worktree, env=env, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(str(self.worktree.resolve()), str(Path(done.stdout.strip()).resolve()))
        frozen = self.ws.root / f"{name}.sqlite"
        pd.freeze_database(live, frozen)
        return frozen

    def test_pinned_recording_reproduces_under_pinned_replay(self):
        database = self.pinned_db("segment1", SEGMENT1)
        out = self.ws.root / "segment1"
        report = pd.process_segment(database, out, segment="segment1", runner=pd.PinnedReplay(self.worktree, PINNED))
        provenance = _json(out / pd.REPLAY_PROVENANCE)
        self.assertTrue(provenance["code_commit"].startswith(PINNED))
        self.assertEqual(provenance["replay_version"], "trading-decision-lab-replay-0.3.0")
        self.assertEqual(report["status"], "PASS", json.dumps(report["mismatches"])[:2000])
        self.assertEqual(report["counts"]["compared"], 6)

    def test_current_main_recording_does_not_reproduce_under_pinned_replay(self):
        # Why the commit is pinned: main's venue gate and features differ from 76dbb5d.
        out = self.processed("segment1", SEGMENT1[:2], runner=pd.PinnedReplay(self.worktree, PINNED))
        report = _json(out / pd.PARITY_REPORT)
        self.assertEqual(report["status"], "FAIL")
        paths = {d["path"] for m in report["mismatches"] for d in m["differences"]}
        self.assertIn(".features.executable_ask_divergence_bps", paths)


def _json(path):
    return json.loads(Path(path).read_text())


if __name__ == "__main__":
    unittest.main()
