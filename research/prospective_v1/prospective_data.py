"""Prospective v1 data tooling: export, pinned live/replay parity, segment seam and pooling.

Implements the frozen-input side of ``research/prospective_v1/PREREGISTRATION.md``
(§2 frozen inputs and seam rule, §3 decision-time mapping, §6 export and parity
checker). It reads decision fields only and never computes an outcome. The commands
and the exact output contract are in ``README.md`` beside this file.

Stages (each writes new files only and never overwrites):

* ``export``  observer SQLite (read-only) -> canonical snapshot JSONL, dataset
  manifest (``market_observer.dataset``), the §3 mapping and the live decisions.
* ``replay``  replays the exported JSONL with the *pinned* observer code in a
  subprocess (``python -m market_observer.replay`` run from a checkout whose HEAD
  must equal ``--pinned-commit``) and records the commit actually used.
* ``parity``  each replay row vs its mapped live decision on the §6 canonical contract.
* ``pool``    two processed segments + cutover -> seam check, effective cutover and
  one pooled population file.
* ``all``     export, replay and parity for both segments, then pool.

Everything streams: a year of 60 s polling is ~500k snapshots (~35 KB each), so no
stage holds every snapshot or every decision in memory.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from bisect import bisect_left
from itertools import zip_longest
from pathlib import Path
from typing import Any, Callable, Iterator

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from market_observer.asof import AsOfError, validate_snapshot_asof  # noqa: E402
from market_observer.dataset import DatasetBuilder, DatasetError  # noqa: E402
from market_observer.snapshots import SnapshotError, load_json  # noqa: E402
from market_observer.util import canonical_json, stable_id  # noqa: E402


TOOL_VERSION = "tdal-prospective-v1-data-0.1.0"
INTERVAL_MS = 900_000
BLOCKED_SIGNALS = frozenset({"DATA_STALE", "RISK_BLOCKED"})

# §6 canonical parity contract. The completed candle is the replay row's top-level
# field and, for a live decision, ``features.completed_candle_close_time_ms`` (live
# decisions have no top-level field; a failure decision has no features, so None).
CANONICAL_FIELDS = ("completed_candle_close_time_ms", "setup", "signal", "risk", "gates",
                    "features", "breakout_quality")
# Gates whose evidence echoes the run mode; only that ``mode`` key is excluded.
MODE_ECHO_GATES = frozenset({"T0_OBSERVATION_ONLY"})
# §2 seam: the setup fields that carry a setup across candles. ``transition`` (how a
# state was reached), ``last_evaluated_close_time_ms`` (bookkeeping) and
# ``state_updated`` (a per-row flag) are excluded; see README, "Design decisions".
SEAM_STATE_FIELDS = ("strategy_id", "state", "setup_id", "breakout_resistance", "breakout_atr",
                     "breakout_close_time_ms", "bars_since_breakout")
# What a replay carries into its first snapshot (engine.default_setup_state, same at 76dbb5d).
DEFAULT_SETUP_STATE = {
    "strategy_id": "BREAKOUT_RETEST_V1", "state": "IDLE", "setup_id": None,
    "breakout_resistance": None, "breakout_atr": None, "breakout_close_time_ms": None,
    "bars_since_breakout": 0, "last_evaluated_close_time_ms": None, "transition": "INITIALIZED",
}

# Files in a segment directory.
SNAPSHOTS = "snapshots.jsonl"
DATASET_MANIFEST = "dataset_manifest.json"
MAPPING = "mapping.jsonl"
LIVE_DECISIONS = "live_decisions.jsonl"
EXPORT_MANIFEST = "export_manifest.json"
REPLAY = "replay.json"
REPLAY_PROVENANCE = "replay_provenance.json"
REPLAY_SUMMARY = "replay_summary.jsonl"
PARITY_REPORT = "parity_report.json"
# Files in a pooled directory.
SEAM_REPORT = "seam_report.json"
POPULATION = "population.jsonl"
POPULATION_MANIFEST = "population_manifest.json"

MAX_LISTED_PROBLEMS = 200
_CHUNK = 1 << 20  # streaming read size for replay.json


class ProspectiveError(RuntimeError):
    """A halt condition from the plan, or an input that cannot be trusted."""


# --------------------------------------------------------------------------- helpers

def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        handle.write(canonical_json(value) + "\n")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def iter_jsonl(path: str | Path) -> Iterator[Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _new_dir(path: str | Path) -> Path:
    target = Path(path)
    if target.exists():
        raise FileExistsError(f"output directory must be new: {target}")
    target.mkdir(parents=True)
    return target


def _halt(stage: str, problems: list[str]) -> ProspectiveError:
    listed = problems[:MAX_LISTED_PROBLEMS]
    more = f"\n  ... and {len(problems) - len(listed)} more" if len(problems) > len(listed) else ""
    return ProspectiveError(f"{stage} halted ({len(problems)} problem(s)):\n  " + "\n  ".join(listed) + more)


def code_revision(root: Path = REPO_ROOT) -> dict[str, Any]:
    """The commit of this tooling code, and whether tracked files were modified."""
    try:
        commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                                text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return {"commit": None, "modified": None}
    return {"commit": commit, "modified": bool(dirty)}


def parse_utc_ms(text: str) -> int:
    parsed = dt.datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must state its timezone: {text}")
    return int(parsed.timestamp() * 1000)


def utc_iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    moment = dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def first_candle_close_at_or_after(cutover_ms: int) -> int:
    """15m candles close at open + 899_999 ms; the first close at or after ``cutover_ms``."""
    k = -(-(cutover_ms - (INTERVAL_MS - 1)) // INTERVAL_MS)
    return k * INTERVAL_MS + INTERVAL_MS - 1


def _live_candle(decision: dict[str, Any]) -> int | None:
    features = decision.get("features")
    return features.get("completed_candle_close_time_ms") if isinstance(features, dict) else None


def _reward_status(row: dict[str, Any]) -> str | None:
    reward = (row.get("risk") or {}).get("reward_reference")
    return reward.get("status") if isinstance(reward, dict) else None


def iter_replay_decisions(path: str | Path) -> Iterator[dict[str, Any]]:
    """Stream the ``decisions`` array of a replay result written by ``write_replay``
    (canonical JSON, one line, keys sorted so ``baseline`` precedes ``decisions``)."""
    decoder = json.JSONDecoder()
    marker = '"decisions":['
    with Path(path).open("r", encoding="utf-8") as handle:
        buffer = ""
        while marker not in buffer:
            chunk = handle.read(_CHUNK)
            if not chunk:
                raise ProspectiveError(f"{path}: no decisions array")
            buffer += chunk
        position = buffer.index(marker) + len(marker)
        while True:
            while True:
                while position < len(buffer) and buffer[position] in " \t\r\n,":
                    position += 1
                if position < len(buffer):
                    break
                chunk = handle.read(_CHUNK)
                if not chunk:
                    raise ProspectiveError(f"{path}: truncated decisions array")
                buffer, position = buffer[position:] + chunk, 0
            if buffer[position] == "]":
                return
            while True:
                try:
                    value, end = decoder.raw_decode(buffer, position)
                    break
                except json.JSONDecodeError:
                    chunk = handle.read(_CHUNK)
                    if not chunk:
                        raise ProspectiveError(f"{path}: truncated decision") from None
                    buffer, position = buffer[position:] + chunk, 0
            yield value
            position = end
            if position > _CHUNK:
                buffer, position = buffer[position:], 0


def read_replay_baseline(path: str | Path) -> dict[str, Any]:
    prefix = '{"baseline":'
    with Path(path).open("r", encoding="utf-8") as handle:
        head = handle.read(1 << 16)
    if not head.startswith(prefix):
        raise ProspectiveError(f"{path}: not a replay result")
    value, _end = json.JSONDecoder().raw_decode(head, len(prefix))
    return value


# --------------------------------------------------------------------------- export

def _open_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise ProspectiveError(f"database not found: {path}")
    try:
        return sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise ProspectiveError(f"cannot open database read-only: {exc}") from exc


def _check_receipts(db: sqlite3.Connection, index: int, payload: dict[str, Any], problems: list[str]) -> None:
    """§6: observed_at_ms against the snapshot's receipts and the matching raw_events rows."""
    observed = payload.get("observed_at_ms")
    for endpoint, event in sorted((payload.get("provenance") or {}).items()):
        where = f"snapshot {index} provenance.{endpoint}"
        if not isinstance(event, dict):
            problems.append(f"{where}: not an object")
            continue
        if type(event.get("received_time_ms")) is not int or event["received_time_ms"] > observed:
            problems.append(f"{where}: received_time_ms {event.get('received_time_ms')} after observed_at_ms {observed}")
        row = db.execute(
            "SELECT received_time_ms,event_time_ms,source,endpoint,symbol,critical FROM raw_events WHERE raw_id=?",
            (event.get("raw_id"),),
        ).fetchone()
        if row is None:
            problems.append(f"{where}: raw_events row {event.get('raw_id')} is missing")
            continue
        stored = {"received_time_ms": row[0], "event_time_ms": row[1], "source": row[2], "endpoint": row[3],
                  "symbol": row[4], "critical": bool(row[5])}
        for key, value in stored.items():
            if event.get(key) != value:
                problems.append(f"{where}.{key}: snapshot {event.get(key)!r} != raw_events {value!r}")
    for error in (payload.get("quality") or {}).get("collection_errors", []):
        if isinstance(error, dict) and type(error.get("received_time_ms")) is int and error["received_time_ms"] > observed:
            problems.append(f"snapshot {index}: collection error received after observed_at_ms")


def freeze_database(source: str | Path, destination: str | Path) -> dict[str, Any]:
    """Copy an observer database through SQLite's backup API, read-only on the source,
    into a new self-contained file (rollback journal, no -wal/-shm) to export from."""
    source, destination = Path(source), Path(destination)
    if destination.exists():
        raise FileExistsError(f"destination must be new: {destination}")
    src = _open_readonly(source)
    try:
        target = sqlite3.connect(destination)
        try:
            src.backup(target)
            target.execute("PRAGMA journal_mode=DELETE")
            result = target.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok":
                raise ProspectiveError(f"copy failed its integrity check: {result}")
        finally:
            target.close()
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    finally:
        src.close()
    return {"source": str(source.resolve()), "copy": str(destination.resolve()),
            "copy_sha256": sha256_file(destination)}


def export_segment(database: str | Path, out_dir: str | Path, *, segment: str,
                   research_symbol: str = "TAOUSDT") -> dict[str, Any]:
    """Export one recorder segment into a new directory.

    On any halt condition nothing is left behind and ProspectiveError lists every
    problem found. Run it on a frozen copy of the database: the file is hashed
    before and after, and a change halts the export.
    """
    database, out = Path(database), Path(out_dir)
    if out.exists():
        raise FileExistsError(f"output directory must be new: {out}")
    work = out.with_name(out.name + ".partial")
    work.mkdir(parents=True)  # raises if a previous partial run was left behind
    try:
        manifest = _export_into(database, work, segment, research_symbol)
        if out.exists():
            raise FileExistsError(f"output directory must be new: {out}")
        work.rename(out)
        return manifest
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise


def _is_wal(path: Path) -> bool:
    with path.open("rb") as handle:
        header = handle.read(100)
    return len(header) == 100 and header[:16] == b"SQLite format 3\x00" and header[18] == 2


def _export_into(database: Path, out: Path, segment: str, research_symbol: str) -> dict[str, Any]:
    if not database.is_file():
        raise ProspectiveError(f"database not found: {database}")
    if _is_wal(database):
        # Even a read-only open of a WAL database creates -wal/-shm files beside it.
        raise ProspectiveError(f"{database} is in WAL mode, i.e. not a frozen copy; make one with `freeze`")
    database_sha = sha256_file(database)
    db = _open_readonly(database)
    problems: list[str] = []
    try:
        db.execute("BEGIN")  # one read snapshot for every query below

        # Pass 1: live decisions, written in rowid order; a small index by snapshot.
        by_snapshot: dict[str, list[dict[str, Any]]] = {}
        without_snapshot: list[dict[str, Any]] = []
        live_count = other_symbol_decisions = 0
        with (out / LIVE_DECISIONS).open("x", encoding="utf-8") as live_out:
            for decision_id, decision_time, symbol, payload_json in db.execute(
                "SELECT decision_id, decision_time_ms, research_symbol, payload_json FROM decisions ORDER BY rowid"
            ):
                if symbol != research_symbol:
                    other_symbol_decisions += 1
                    continue
                payload = json.loads(payload_json)
                if stable_id("decision", payload) != decision_id:
                    problems.append(f"live decision {decision_id}: stored ID does not match its payload")
                if payload.get("decision_time_ms") != decision_time:
                    problems.append(f"live decision {decision_id}: decision_time_ms column differs from payload")
                live_out.write(canonical_json({"decision_id": decision_id, **payload}) + "\n")
                live_count += 1
                summary = {"decision_id": decision_id, "decision_time_ms": decision_time,
                           "signal": payload.get("signal"), "candle": _live_candle(payload)}
                if payload.get("snapshot_id") is None:
                    gates = payload.get("gates") or [{}]
                    without_snapshot.append({"decision_id": decision_id, "decision_time_ms": decision_time,
                                             "signal": payload.get("signal"),
                                             "error": (gates[0].get("evidence") or {}).get("error")})
                else:
                    by_snapshot.setdefault(payload["snapshot_id"], []).append(summary)

        # Pass 2: snapshots in replay order, streamed into the JSONL and the mapping.
        other_symbol_snapshots = db.execute(
            "SELECT COUNT(*) FROM normalized_snapshots WHERE research_symbol<>?", (research_symbol,)
        ).fetchone()[0]
        builder = DatasetBuilder(source=f"prospective_v1:{segment}",
                                 source_metadata={"segment": segment, "database_sha256": database_sha,
                                                  "research_symbol": research_symbol})
        seen: set[str] = set()
        no_live: list[dict[str, Any]] = []
        counts = {"matched": 0, "asof_audit_failures": 0}
        first_observed = last_observed = None
        cursor = db.execute(
            "SELECT snapshot_id, observed_at_ms, payload_json FROM normalized_snapshots "
            "WHERE research_symbol=? ORDER BY observed_at_ms, rowid", (research_symbol,))
        with (out / SNAPSHOTS).open("x", encoding="utf-8") as jsonl, \
                (out / MAPPING).open("x", encoding="utf-8") as mapping_out:
            for index, (snapshot_id, observed_at, payload_json) in enumerate(cursor):
                seen.add(snapshot_id)
                payload = json.loads(payload_json)
                recomputed = stable_id("snapshot", payload)
                if recomputed != snapshot_id:
                    problems.append(f"snapshot {index}: stored snapshot_id {snapshot_id} != recomputed {recomputed}")
                if payload.get("observed_at_ms") != observed_at:
                    problems.append(f"snapshot {index}: observed_at_ms column {observed_at} != payload "
                                    f"{payload.get('observed_at_ms')}")
                _check_receipts(db, index, payload, problems)
                try:
                    validate_snapshot_asof(payload)
                    asof = {"asof_audit": "PASS", "asof_error": None}
                except AsOfError as exc:
                    asof = {"asof_audit": "FAIL", "asof_error": str(exc)}
                    counts["asof_audit_failures"] += 1
                try:
                    row = builder.add(load_json(payload_json))
                except (SnapshotError, DatasetError) as exc:
                    problems.append(f"snapshot {index} ({snapshot_id}): not exportable: {exc}")
                    break  # the dataset's chronology can't be trusted past this point
                line = DatasetBuilder.line(row)
                if stable_id("snapshot", row) != snapshot_id:
                    problems.append(f"snapshot {index}: exported row does not reproduce the stored snapshot_id")
                jsonl.write(line)

                record = {"segment": segment, "snapshot_index": index, "snapshot_id": snapshot_id,
                          "recomputed_snapshot_id": recomputed, "observed_at_ms": observed_at, **asof}
                matches = by_snapshot.get(snapshot_id, [])
                if len(matches) > 1:
                    problems.append(f"snapshot {index} ({snapshot_id}): {len(matches)} live decisions "
                                    f"{sorted(item['decision_id'] for item in matches)}")
                if matches:
                    decision = matches[0]
                    counts["matched"] += 1
                    record.update(status="MATCHED", live_decision_id=decision["decision_id"],
                                  live_decision_time_ms=decision["decision_time_ms"],
                                  decision_time_ms=decision["decision_time_ms"], time_source="LIVE_DECISION",
                                  live_signal=decision["signal"],
                                  live_completed_candle_close_time_ms=decision["candle"])
                    if observed_at > decision["decision_time_ms"]:
                        problems.append(f"snapshot {index}: observed_at_ms {observed_at} after decision_time_ms "
                                        f"{decision['decision_time_ms']} of {decision['decision_id']}")
                else:
                    record.update(status="NO_LIVE_DECISION", live_decision_id=None, live_decision_time_ms=None,
                                  decision_time_ms=observed_at, time_source="OBSERVED_AT_FALLBACK",
                                  live_signal=None, live_completed_candle_close_time_ms=None)
                    no_live.append({"snapshot_index": index, "snapshot_id": snapshot_id, "observed_at_ms": observed_at})
                mapping_out.write(canonical_json(record) + "\n")
                first_observed = observed_at if first_observed is None else first_observed
                last_observed = observed_at
    finally:
        db.close()

    for snapshot_id, items in by_snapshot.items():
        if snapshot_id not in seen:
            problems.append(f"live decision(s) {[item['decision_id'] for item in items]} reference snapshot "
                            f"{snapshot_id}, which is not in the export")
    if sha256_file(database) != database_sha:
        problems.append("the database changed during the export; export a frozen copy")
    if builder.count == 0 and not problems:
        problems.append(f"no {research_symbol} snapshots in {database}")
    if problems:
        raise _halt(f"export of {segment}", problems)

    dataset_manifest = builder.manifest()
    if sha256_file(out / SNAPSHOTS) != dataset_manifest["jsonl_sha256"]:
        raise ProspectiveError("snapshots.jsonl does not match the dataset fingerprint")
    _write_json(out / DATASET_MANIFEST, dataset_manifest)
    files = {name: sha256_file(out / name) for name in (SNAPSHOTS, DATASET_MANIFEST, MAPPING, LIVE_DECISIONS)}
    export_manifest = {
        "schema_version": "prospective_v1_export/0.1",
        "tool_version": TOOL_VERSION,
        "segment": segment,
        "tooling_code_revision": code_revision(),
        "source": {"database": str(database.resolve()), "database_sha256": database_sha,
                   "research_symbol": research_symbol},
        "dataset_id": dataset_manifest["dataset_id"],
        "counts": {
            "snapshots": builder.count,
            "matched": counts["matched"],
            "no_live_decision": len(no_live),
            "live_decisions": live_count,
            "live_decisions_without_snapshot": len(without_snapshot),
            "asof_audit_failures": counts["asof_audit_failures"],
            "other_symbol_snapshots": other_symbol_snapshots,
            "other_symbol_decisions": other_symbol_decisions,
        },
        "first_observed_at_ms": first_observed,
        "last_observed_at_ms": last_observed,
        "no_live_decision": no_live,
        "live_decisions_without_snapshot": without_snapshot,
        "files_sha256": files,
    }
    _write_json(out / EXPORT_MANIFEST, export_manifest)
    return export_manifest


# --------------------------------------------------------------------------- pinned replay

ReplayRunnerFn = Callable[[Path, Path], dict[str, Any]]

_PROBE = ("import json, sys, market_observer.replay as r; "
          "print(json.dumps({'file': r.__file__, 'replay_version': r.REPLAY_VERSION, "
          "'python': sys.version.split()[0]}))")


def _git(checkout: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(checkout), *args], capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise ProspectiveError(f"git {' '.join(args)} failed in {checkout}: {detail.strip()}") from exc


class PinnedReplay:
    """Runs ``python -m market_observer.replay`` from a checkout pinned to one commit.

    The checkout's HEAD must resolve to ``commit`` and its tracked files must be
    unmodified, before and after the run, and the replay module that runs must be
    the checkout's own (checked by a probe in the same environment).
    """

    def __init__(self, checkout: str | Path, commit: str, python: str | None = None) -> None:
        if not commit:
            raise ProspectiveError("a pinned commit is required")
        self.checkout = Path(checkout).resolve()
        self.commit = commit
        self.python = python or sys.executable

    def verify(self) -> str:
        expected = _git(self.checkout, "rev-parse", "--verify", f"{self.commit}^{{commit}}")
        head = _git(self.checkout, "rev-parse", "HEAD")
        if head != expected:
            raise ProspectiveError(f"checkout {self.checkout} is at {head}, not the pinned {self.commit} ({expected})")
        if _git(self.checkout, "status", "--porcelain", "--untracked-files=no"):
            raise ProspectiveError(f"checkout {self.checkout} has modified tracked files")
        return head

    def _env(self) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHON")}
        env.update(PYTHONPATH=str(self.checkout), PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
        return env

    def __call__(self, jsonl: Path, output: Path) -> dict[str, Any]:
        head = self.verify()
        env = self._env()
        probe = subprocess.run([self.python, "-c", _PROBE], cwd=self.checkout, env=env,
                               capture_output=True, text=True)
        if probe.returncode != 0:
            raise ProspectiveError(f"pinned replay probe failed: {probe.stderr.strip()}")
        info = json.loads(probe.stdout.strip().splitlines()[-1])
        module = Path(info["file"]).resolve()
        if self.checkout not in module.parents:
            raise ProspectiveError(f"replay module resolved to {module}, outside the pinned checkout")
        command = [self.python, "-m", "market_observer.replay", "--jsonl", str(Path(jsonl).resolve()),
                   "--output", str(Path(output).resolve())]
        run = subprocess.run(command, cwd=self.checkout, env=env, capture_output=True, text=True)
        if run.returncode != 0:
            raise ProspectiveError(f"pinned replay failed (exit {run.returncode}): {run.stderr.strip()}")
        if self.verify() != head:
            raise ProspectiveError("pinned checkout changed during the replay")
        return {
            "runner": "subprocess",
            "pinned_commit_requested": self.commit,
            "code_commit": head,
            "checkout": str(self.checkout),
            "replay_module": str(module),
            "replay_version": info["replay_version"],
            "python": self.python,
            "python_version": info["python"],
            "command": command,
            "stdout": run.stdout.strip(),
        }


def replay_export(export_dir: str | Path, runner: ReplayRunnerFn) -> dict[str, Any]:
    """Replay an export's JSONL with ``runner`` and record what ran."""
    export_dir = Path(export_dir)
    export_manifest = _read_json(export_dir / EXPORT_MANIFEST)
    if sha256_file(export_dir / SNAPSHOTS) != export_manifest["files_sha256"][SNAPSHOTS]:
        raise ProspectiveError("snapshots.jsonl does not match its export manifest")
    output = export_dir / REPLAY
    if output.exists() or (export_dir / REPLAY_PROVENANCE).exists():
        raise FileExistsError(f"replay outputs already exist in {export_dir}")
    provenance = runner(export_dir / SNAPSHOTS, output)
    baseline = read_replay_baseline(output)
    if baseline.get("replay_version") != provenance.get("replay_version"):
        raise ProspectiveError(f"replay output version {baseline.get('replay_version')} != the code that ran "
                               f"({provenance.get('replay_version')})")
    expected = export_manifest["counts"]["snapshots"]
    if baseline.get("input_snapshot_count") != expected:
        raise ProspectiveError(f"replay covered {baseline.get('input_snapshot_count')} snapshots, export has {expected}")
    record = {
        "schema_version": "prospective_v1_replay_provenance/0.1",
        "tool_version": TOOL_VERSION,
        "segment": export_manifest["segment"],
        "dataset_id": export_manifest["dataset_id"],
        "snapshots_sha256": export_manifest["files_sha256"][SNAPSHOTS],
        "replay_sha256": sha256_file(output),
        "replay_configuration": baseline.get("configuration"),
        "replay_configuration_id": baseline.get("configuration_id"),
        "tooling_code_revision": code_revision(),
        **provenance,
    }
    _write_json(export_dir / REPLAY_PROVENANCE, record)
    return record


# --------------------------------------------------------------------------- parity

_ABSENT = "<absent>"


def diff_values(left: Any, right: Any, path: str = "") -> list[dict[str, Any]]:
    """Every leaf difference between two JSON values, as {path, live, replay}."""
    if isinstance(left, dict) and isinstance(right, dict):
        result: list[dict[str, Any]] = []
        for key in sorted(set(left) | set(right), key=str):
            result.extend(diff_values(left.get(key, _ABSENT), right.get(key, _ABSENT), f"{path}.{key}"))
        return result
    if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        result = []
        for index, (a, b) in enumerate(zip(left, right)):
            result.extend(diff_values(a, b, f"{path}[{index}]"))
        return result
    if type(left) is not type(right) or left != right:
        return [{"path": path, "live": left, "replay": right}]
    return []


def _without_mode(gates: Any) -> Any:
    """Gate list without the run-mode echo in T0_OBSERVATION_ONLY's evidence
    (``observation_only`` live, ``historical_replay`` in replay): mode metadata,
    which §6 excludes. Its status and every other evidence field stay compared."""
    if not isinstance(gates, list):
        return gates
    result = []
    for gate in gates:
        if isinstance(gate, dict) and gate.get("rule_id") in MODE_ECHO_GATES and isinstance(gate.get("evidence"), dict):
            gate = {**gate, "evidence": {k: v for k, v in gate["evidence"].items() if k != "mode"}}
        result.append(gate)
    return result


def canonical_view(decision: dict[str, Any], *, live: bool) -> dict[str, Any]:
    view = {name: decision.get(name, _ABSENT) for name in CANONICAL_FIELDS}
    view["gates"] = _without_mode(view["gates"])
    if live:
        view["completed_candle_close_time_ms"] = _live_candle(decision)
    return view


def canonical_sha256(decision: dict[str, Any], *, live: bool) -> str:
    return _sha256_text(canonical_json(canonical_view(decision, live=live)))


def check_parity(export_dir: str | Path) -> dict[str, Any]:
    """§6 live vs replay parity for one segment.

    Writes ``replay_summary.jsonl`` (one compact row per replay row, used by ``pool``)
    and ``parity_report.json`` (status PASS or FAIL, every difference listed).
    """
    export_dir = Path(export_dir)
    export_manifest = _read_json(export_dir / EXPORT_MANIFEST)
    provenance = _read_json(export_dir / REPLAY_PROVENANCE)
    for name, expected in export_manifest["files_sha256"].items():
        if sha256_file(export_dir / name) != expected:
            raise ProspectiveError(f"{name} does not match its export manifest")
    if sha256_file(export_dir / REPLAY) != provenance["replay_sha256"]:
        raise ProspectiveError("replay.json does not match its provenance record")
    for name in (REPLAY_SUMMARY, PARITY_REPORT):
        if (export_dir / name).exists():
            raise FileExistsError(f"{export_dir / name} already exists")

    # Live decisions: canonical fingerprint and byte offset, so only mismatches are re-read.
    live_index: dict[str, tuple[str, int]] = {}
    with (export_dir / LIVE_DECISIONS).open("rb") as handle:
        offset = 0
        for line in handle:
            decision = json.loads(line)
            live_index[decision["decision_id"]] = (canonical_sha256(decision, live=True), offset)
            offset += len(line)

    def live_decision(decision_id: str) -> dict[str, Any]:
        with (export_dir / LIVE_DECISIONS).open("rb") as handle:
            handle.seek(live_index[decision_id][1])
            return json.loads(handle.readline())

    structural: list[str] = []
    mismatches: list[dict[str, Any]] = []
    asof_failures: list[dict[str, Any]] = []
    compared = rows = 0
    with (export_dir / REPLAY_SUMMARY).open("x", encoding="utf-8") as summary_out:
        for row, replayed in zip_longest(iter_jsonl(export_dir / MAPPING), iter_replay_decisions(export_dir / REPLAY)):
            if row is None or replayed is None:
                structural.append("replay and mapping have different lengths")
                break
            rows += 1
            if replayed.get("snapshot_index") != row["snapshot_index"] or replayed.get("observed_at_ms") != row["observed_at_ms"]:
                structural.append(f"replay row {replayed.get('snapshot_index')} does not line up with snapshot "
                                  f"{row['snapshot_index']}")
                continue
            replay_sha = canonical_sha256(replayed, live=False)
            summary_out.write(canonical_json({
                "snapshot_index": row["snapshot_index"], "replay_decision_id": replayed.get("decision_id"),
                "observed_at_ms": replayed.get("observed_at_ms"),
                "completed_candle_close_time_ms": replayed.get("completed_candle_close_time_ms"),
                "signal": replayed.get("signal"), "setup": replayed.get("setup"),
                "risk_status": (replayed.get("risk") or {}).get("status"),
                "reward_reference_status": _reward_status(replayed),
                "canonical_sha256": replay_sha,
            }) + "\n")
            if row["asof_audit"] == "FAIL":
                asof_failures.append({"snapshot_index": row["snapshot_index"], "error": row["asof_error"],
                                      "replay_signal": replayed.get("signal"), "live_signal": row["live_signal"]})
            if row["status"] != "MATCHED":
                continue
            compared += 1
            if live_index[row["live_decision_id"]][0] == replay_sha:
                continue
            differences = diff_values(canonical_view(live_decision(row["live_decision_id"]), live=True),
                                      canonical_view(replayed, live=False))
            mismatches.append({"snapshot_index": row["snapshot_index"], "snapshot_id": row["snapshot_id"],
                               "live_decision_id": row["live_decision_id"], "live_signal": row["live_signal"],
                               "replay_signal": replayed.get("signal"), "differences": differences})
    if rows != export_manifest["counts"]["snapshots"] and not structural:
        structural.append(f"replay covered {rows} rows, export has {export_manifest['counts']['snapshots']}")
    status = "PASS" if not mismatches and not structural else "FAIL"
    report = {
        "schema_version": "prospective_v1_parity/0.1",
        "tool_version": TOOL_VERSION,
        "segment": export_manifest["segment"],
        "status": status,
        "canonical_fields": list(CANONICAL_FIELDS),
        "pinned_code_commit": provenance.get("code_commit"),
        "replay_version": provenance.get("replay_version"),
        "dataset_id": export_manifest["dataset_id"],
        "replay_sha256": provenance["replay_sha256"],
        "replay_summary_sha256": sha256_file(export_dir / REPLAY_SUMMARY),
        "counts": {"snapshots": export_manifest["counts"]["snapshots"], "compared": compared,
                   "identical": compared - len(mismatches), "mismatched": len(mismatches),
                   "no_live_decision": export_manifest["counts"]["no_live_decision"],
                   "live_decisions_without_snapshot": export_manifest["counts"]["live_decisions_without_snapshot"],
                   "asof_audit_failures": len(asof_failures)},
        # Enforced by export (it halts otherwise), restated here for the record.
        "separate_validations": {
            "snapshot_id_recomputation": "PASS",
            "receipts_and_raw_events": "PASS",
            "observed_at_not_after_decision_time": "PASS",
            "asof_audit_failures": asof_failures,
        },
        "structural_problems": structural,
        "mismatches": mismatches,
        "no_live_decision": export_manifest["no_live_decision"],
        "live_decisions_without_snapshot": export_manifest["live_decisions_without_snapshot"],
    }
    _write_json(export_dir / PARITY_REPORT, report)
    return report


# --------------------------------------------------------------------------- seam and pooling

def seam_state(setup: dict[str, Any]) -> dict[str, Any]:
    return {name: setup.get(name) for name in SEAM_STATE_FIELDS}


class _SegmentRows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.evaluated = [(i, row["completed_candle_close_time_ms"]) for i, row in enumerate(rows)
                          if row.get("completed_candle_close_time_ms") is not None]
        self.candles = [candle for _index, candle in self.evaluated]

    def carried_into(self, candle: int) -> dict[str, Any] | None:
        """The setup this replay carries into ``candle``: that of the row just before its
        first row evaluating a candle at or after it (the default state if there is no
        earlier row). None if the replay never evaluates a candle at or after it."""
        position = bisect_left(self.candles, candle)
        if position == len(self.candles):
            return None
        index = self.evaluated[position][0]
        setup = self.rows[index - 1]["setup"] if index > 0 else DEFAULT_SETUP_STATE
        return {"row_before": index - 1 if index > 0 else None, "first_row_at_or_after": index,
                "first_candle_at_or_after_ms": self.candles[position], "setup": setup}


def find_effective_cutover(segment1: list[dict[str, Any]], segment2: list[dict[str, Any]],
                           cutover_ms: int) -> dict[str, Any]:
    """§2 seam rule on two independent replays (rows need ``completed_candle_close_time_ms``
    and ``setup``). Returns the effective cutover candle, or a HALT."""
    first = first_candle_close_at_or_after(cutover_ms)
    one, two = _SegmentRows(segment1), _SegmentRows(segment2)
    disagreements: list[dict[str, Any]] = []
    candle = first
    while True:
        carried1, carried2 = one.carried_into(candle), two.carried_into(candle)
        if carried1 is None or carried2 is None:
            which = "segment 1" if carried1 is None else "segment 2"
            return {"status": "HALT", "first_candidate_candle_close_ms": first,
                    "effective_cutover_candle_close_ms": None,
                    "reason": f"seam: no candle from {utc_iso(first)} on where both replays' setup states agree "
                              f"while {which} was still recording (checked up to {utc_iso(candle)})",
                    "disagreements": disagreements}
        state1, state2 = seam_state(carried1["setup"]), seam_state(carried2["setup"])
        if state1 == state2:
            return {"status": "OK", "first_candidate_candle_close_ms": first,
                    "effective_cutover_candle_close_ms": candle, "moved": candle != first,
                    "agreeing_state": state1, "segment1_carried": carried1, "segment2_carried": carried2,
                    "disagreements": disagreements}
        disagreements.append({"candle_close_ms": candle, "segment1": carried1, "segment2": carried2,
                              "differences": [{"path": d["path"], "segment1": d["live"], "segment2": d["replay"]}
                                              for d in diff_values(state1, state2)]})
        candle += INTERVAL_MS


def _first_gate_passing(rows: list[dict[str, Any]]) -> dict[int, int]:
    first: dict[int, int] = {}
    for index, row in enumerate(rows):
        candle = row.get("completed_candle_close_time_ms")
        if candle is not None and row.get("signal") not in BLOCKED_SIGNALS and candle not in first:
            first[candle] = index
    return first


def _fetch_rows(replay_path: Path, indices: set[int]) -> dict[int, dict[str, Any]]:
    found: dict[int, dict[str, Any]] = {}
    if not indices:
        return found
    for index, row in enumerate(iter_replay_decisions(replay_path)):
        if index in indices:
            found[index] = row
            if len(found) == len(indices):
                break
    return found


def compare_overlap(summary1: list[dict[str, Any]], summary2: list[dict[str, Any]],
                    replay1: Path | None = None, replay2: Path | None = None) -> dict[str, Any]:
    """§2: candles both segments evaluated. Their first gate-passing observations are
    compared on the canonical fields (reported, never a halt); candles gate-passing in
    one segment only are listed as blocked-cycle differences."""
    candles1 = {r["completed_candle_close_time_ms"] for r in summary1 if r.get("completed_candle_close_time_ms") is not None}
    candles2 = {r["completed_candle_close_time_ms"] for r in summary2 if r.get("completed_candle_close_time_ms") is not None}
    first1, first2 = _first_gate_passing(summary1), _first_gate_passing(summary2)
    overlap = sorted(candles1 & candles2)
    blocked, differing = [], []
    for candle in overlap:
        a, b = first1.get(candle), first2.get(candle)
        if a is None or b is None:
            blocked.append({"candle_close_ms": candle,
                            "segment1": "GATE_PASSING" if a is not None else "BLOCKED_ONLY",
                            "segment2": "GATE_PASSING" if b is not None else "BLOCKED_ONLY"})
        elif summary1[a]["canonical_sha256"] != summary2[b]["canonical_sha256"]:
            differing.append((candle, a, b))
    rows1 = _fetch_rows(replay1, {a for _c, a, _b in differing}) if replay1 else {}
    rows2 = _fetch_rows(replay2, {b for _c, _a, b in differing}) if replay2 else {}
    with_differences = []
    for candle, a, b in differing:
        found = diff_values(canonical_view(rows1[a], live=False), canonical_view(rows2[b], live=False)) \
            if a in rows1 and b in rows2 else [{"path": "", "live": "<canonical fingerprint differs>",
                                               "replay": "<canonical fingerprint differs>"}]
        with_differences.append({"candle_close_ms": candle, "segment1_snapshot_index": a,
                                 "segment2_snapshot_index": b,
                                 "differences": [{"path": d["path"], "segment1": d["live"], "segment2": d["replay"]}
                                                 for d in found]})
    compared = sum(1 for candle in overlap if candle in first1 and candle in first2)
    return {"overlapping_candles": len(overlap), "compared": compared, "identical": compared - len(differing),
            "with_differences": with_differences, "blocked_differences": blocked}


def pool_segments(segment1_dir: str | Path, segment2_dir: str | Path, cutover_ms: int,
                  out_dir: str | Path) -> dict[str, Any]:
    """Seam check and pooled population. Always writes seam_report.json; writes the
    population files only when status is OK (both parities PASS, same pinned code,
    seam agreement found)."""
    dirs = {"segment1": Path(segment1_dir), "segment2": Path(segment2_dir)}
    loaded: dict[str, dict[str, Any]] = {}
    halts: list[str] = []
    for name, directory in dirs.items():
        export_manifest = _read_json(directory / EXPORT_MANIFEST)
        provenance = _read_json(directory / REPLAY_PROVENANCE)
        parity = _read_json(directory / PARITY_REPORT)
        if sha256_file(directory / MAPPING) != export_manifest["files_sha256"][MAPPING]:
            raise ProspectiveError(f"{name}: mapping.jsonl does not match its export manifest")
        if sha256_file(directory / REPLAY) != provenance["replay_sha256"]:
            raise ProspectiveError(f"{name}: replay.json does not match its provenance")
        if sha256_file(directory / REPLAY_SUMMARY) != parity["replay_summary_sha256"]:
            raise ProspectiveError(f"{name}: replay_summary.jsonl does not match its parity report")
        if parity["status"] != "PASS":
            halts.append(f"{name}: live/replay parity {parity['status']} ({parity['counts']['mismatched']} "
                         f"mismatched snapshots, {len(parity['structural_problems'])} structural problems)")
        loaded[name] = {"dir": directory, "manifest": export_manifest, "provenance": provenance,
                        "parity_sha256": sha256_file(directory / PARITY_REPORT),
                        "summary": list(iter_jsonl(directory / REPLAY_SUMMARY))}
    commits = {item["provenance"].get("code_commit") for item in loaded.values()}
    if len(commits) != 1:
        halts.append(f"segments were replayed with different code: {sorted(map(str, commits))}")

    seam = find_effective_cutover(loaded["segment1"]["summary"], loaded["segment2"]["summary"], cutover_ms)
    if seam["status"] != "OK":
        halts.append(seam["reason"])
    overlap = compare_overlap(loaded["segment1"]["summary"], loaded["segment2"]["summary"],
                              dirs["segment1"] / REPLAY, dirs["segment2"] / REPLAY)
    out = _new_dir(out_dir)
    status = "OK" if not halts else "HALT"
    seam_report = {
        "schema_version": "prospective_v1_seam/0.1", "tool_version": TOOL_VERSION, "status": status,
        "halts": halts, "requested_cutover_ms": cutover_ms, "requested_cutover_utc": utc_iso(cutover_ms),
        "seam_state_fields": list(SEAM_STATE_FIELDS), "seam": seam, "overlap": overlap,
    }
    _write_json(out / SEAM_REPORT, seam_report)
    if halts:
        return seam_report

    boundary = seam["effective_cutover_candle_close_ms"]
    first_of: dict[int, dict[str, Any]] = {}
    counts = {"rows": 0, "blocked": 0, "duplicates": 0, "duplicates_with_changed_signal": 0,
              "no_live_decision": 0, "targeted": 0, "eligible_target_unavailable": 0}
    per_segment = {name: {"rows_pooled": 0, "rows_not_pooled": 0, "candles": set()} for name in dirs}
    with (out / POPULATION).open("x", encoding="utf-8") as population:
        for name in ("segment1", "segment2"):
            for row, summary in zip(iter_jsonl(dirs[name] / MAPPING), loaded[name]["summary"]):
                candle = summary["completed_candle_close_time_ms"]
                # A blocked row without a candle goes with the candle it would have evaluated.
                in_first = candle < boundary if candle is not None else row["observed_at_ms"] <= boundary
                if in_first != (name == "segment1"):
                    per_segment[name]["rows_not_pooled"] += 1
                    continue
                blocked = summary["signal"] in BLOCKED_SIGNALS
                item = {
                    "pooled_index": counts["rows"], "segment": name, "snapshot_index": row["snapshot_index"],
                    "snapshot_id": row["snapshot_id"], "observed_at_ms": row["observed_at_ms"],
                    "mapping_status": row["status"], "live_decision_id": row["live_decision_id"],
                    "decision_time_ms": row["decision_time_ms"], "time_source": row["time_source"],
                    "replay_decision_id": summary["replay_decision_id"],
                    "completed_candle_close_time_ms": candle, "signal": summary["signal"],
                    "setup_state": (summary.get("setup") or {}).get("state"),
                    "risk_status": summary["risk_status"],
                    "reward_reference_status": summary["reward_reference_status"],
                    "blocked": blocked, "first_gate_passing": False, "duplicate": False,
                    "signal_differs_from_first": False,
                }
                if not blocked and candle is not None:
                    if candle not in first_of:
                        first_of[candle] = item
                        item["first_gate_passing"] = True
                    else:
                        item["duplicate"] = True
                        item["signal_differs_from_first"] = item["signal"] != first_of[candle]["signal"]
                item["targeted"] = (item["first_gate_passing"] and item["signal"] == "LONG_ELIGIBLE"
                                    and item["reward_reference_status"] == "AVAILABLE")
                eligible_unavailable = (item["first_gate_passing"] and item["signal"] == "LONG_ELIGIBLE"
                                        and not item["targeted"])
                population.write(canonical_json(item) + "\n")
                counts["rows"] += 1
                counts["blocked"] += blocked
                counts["duplicates"] += item["duplicate"]
                counts["duplicates_with_changed_signal"] += item["signal_differs_from_first"]
                counts["no_live_decision"] += row["status"] == "NO_LIVE_DECISION"
                counts["targeted"] += item["targeted"]
                counts["eligible_target_unavailable"] += eligible_unavailable
                per_segment[name]["rows_pooled"] += 1
                if candle is not None:
                    per_segment[name]["candles"].add(candle)
    counts["candles_with_first_gate_passing"] = len(first_of)

    def segment_record(name: str) -> dict[str, Any]:
        candles = sorted(per_segment[name]["candles"])
        item = loaded[name]
        return {"directory": str(item["dir"].resolve()), "dataset_id": item["manifest"]["dataset_id"],
                "database_sha256": item["manifest"]["source"]["database_sha256"],
                "files_sha256": {**item["manifest"]["files_sha256"], REPLAY: item["provenance"]["replay_sha256"],
                                 PARITY_REPORT: item["parity_sha256"]},
                "rows": len(item["summary"]), "rows_pooled": per_segment[name]["rows_pooled"],
                "rows_not_pooled": per_segment[name]["rows_not_pooled"], "candles": len(candles),
                "first_candle_close_ms": candles[0] if candles else None,
                "last_candle_close_ms": candles[-1] if candles else None}

    manifest = {
        "schema_version": "prospective_v1_population/0.1", "tool_version": TOOL_VERSION, "status": "OK",
        "tooling_code_revision": code_revision(),
        "pinned_code_commit": next(iter(commits)),
        "requested_cutover_ms": cutover_ms,
        "first_candidate_candle_close_ms": seam["first_candidate_candle_close_ms"],
        "effective_cutover_candle_close_ms": boundary,
        "effective_cutover_candle_close_utc": utc_iso(boundary),
        "cutover_moved": seam["moved"],
        "rule": "segment1 supplies rows whose completed candle closes before the effective cutover candle, "
                "segment2 rows from that candle on; a row without a candle goes to segment1 if observed_at_ms "
                "<= the effective cutover candle's close, else to segment2",
        "segments": {name: segment_record(name) for name in ("segment1", "segment2")},
        "counts": counts,
        "seam_report_sha256": sha256_file(out / SEAM_REPORT),
        "population_sha256": sha256_file(out / POPULATION),
    }
    _write_json(out / POPULATION_MANIFEST, manifest)
    return {**seam_report, "population_manifest": manifest}


# --------------------------------------------------------------------------- orchestration

def process_segment(database: str | Path, out_dir: str | Path, *, segment: str, runner: ReplayRunnerFn,
                    research_symbol: str = "TAOUSDT") -> dict[str, Any]:
    export_segment(database, out_dir, segment=segment, research_symbol=research_symbol)
    replay_export(out_dir, runner)
    return check_parity(out_dir)


def run_all(segment1_db: str | Path, segment2_db: str | Path, cutover_ms: int, out_dir: str | Path,
            runner: ReplayRunnerFn, research_symbol: str = "TAOUSDT") -> dict[str, Any]:
    out = _new_dir(out_dir)
    for name, database in (("segment1", segment1_db), ("segment2", segment2_db)):
        process_segment(database, out / name, segment=name, runner=runner, research_symbol=research_symbol)
    return pool_segments(out / "segment1", out / "segment2", cutover_ms, out / "pooled")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Prospective v1 export, pinned replay, parity and seam")
    commands = result.add_subparsers(dest="command", required=True)

    def pinned(command: argparse.ArgumentParser) -> None:
        command.add_argument("--pinned-checkout", required=True,
                             help="git checkout (e.g. a worktree) of the pinned observer code")
        command.add_argument("--pinned-commit", required=True,
                             help="commit the checkout must be at (the plan pins 76dbb5d)")
        command.add_argument("--python", default=None, help="interpreter for the pinned replay (default: this one)")

    freeze = commands.add_parser("freeze", help="read-only backup of a live database into a new frozen copy")
    freeze.add_argument("--db", required=True)
    freeze.add_argument("--out", required=True, help="new file")
    export = commands.add_parser("export", help="observer SQLite -> JSONL, manifests, mapping")
    export.add_argument("--db", required=True)
    export.add_argument("--segment", required=True, help="label, e.g. segment1")
    export.add_argument("--out", required=True, help="new directory")
    export.add_argument("--research-symbol", default="TAOUSDT")
    replay = commands.add_parser("replay", help="replay an export with the pinned code")
    replay.add_argument("--export-dir", required=True)
    pinned(replay)
    parity = commands.add_parser("parity", help="live vs replay canonical parity for one export")
    parity.add_argument("--export-dir", required=True)
    pool = commands.add_parser("pool", help="seam check and pooled population for two processed segments")
    pool.add_argument("--segment1-dir", required=True)
    pool.add_argument("--segment2-dir", required=True)
    pool.add_argument("--cutover", required=True, help="ISO time with zone, e.g. 2026-10-13T00:00Z")
    pool.add_argument("--out", required=True, help="new directory")
    every = commands.add_parser("all", help="export, replay and parity for both segments, then pool")
    every.add_argument("--segment1-db", required=True)
    every.add_argument("--segment2-db", required=True)
    every.add_argument("--cutover", required=True, help="ISO time with zone, e.g. 2026-10-13T00:00Z")
    every.add_argument("--out", required=True, help="new directory")
    every.add_argument("--research-symbol", default="TAOUSDT")
    pinned(every)
    return result


def main(argv: list[str] | None = None) -> int:
    """Exit 0: OK/PASS. 1: parity FAIL or seam/pool HALT (reports written). 2: halted before output."""
    args = parser().parse_args(argv)
    try:
        if args.command == "freeze":
            ok, summary = True, freeze_database(args.db, args.out)
        elif args.command == "export":
            manifest = export_segment(args.db, args.out, segment=args.segment, research_symbol=args.research_symbol)
            ok, summary = True, {"dataset_id": manifest["dataset_id"], **manifest["counts"]}
        elif args.command == "replay":
            record = replay_export(args.export_dir, PinnedReplay(args.pinned_checkout, args.pinned_commit, args.python))
            ok, summary = True, {key: record[key] for key in ("code_commit", "replay_version", "replay_sha256")}
        elif args.command == "parity":
            report = check_parity(args.export_dir)
            ok, summary = report["status"] == "PASS", {"status": report["status"], **report["counts"]}
        else:
            cutover = parse_utc_ms(args.cutover)
            if args.command == "pool":
                report = pool_segments(args.segment1_dir, args.segment2_dir, cutover, args.out)
            else:
                report = run_all(args.segment1_db, args.segment2_db, cutover, args.out,
                                 PinnedReplay(args.pinned_checkout, args.pinned_commit, args.python),
                                 research_symbol=args.research_symbol)
            ok = report["status"] == "OK"
            summary = {"status": report["status"], "halts": report["halts"],
                       "effective_cutover_candle_close_ms": report["seam"].get("effective_cutover_candle_close_ms"),
                       "counts": report.get("population_manifest", {}).get("counts")}
    except (ProspectiveError, FileExistsError) as exc:
        print(f"HALT: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, sort_keys=True, default=str))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
