"""Input adapter for the prospective v1 analysis (PREREGISTRATION.md §3, §6).

Thin and isolated on purpose: this is the only module that knows the file layout
and field names of the export/parity tooling (``prospective_data.py``, PR #26,
contract in its README "Output contract"), so a change there touches only this file.

Every check fails closed with ``InputError``: the analysis halts before any
outcome is computed and writes nothing.

Layout read (``prospective_data.py all --out OUT``)::

    OUT/pooled/population_manifest.json   status must be "OK"; fingerprints everything
    OUT/pooled/population.jsonl           one row per pooled replay row, pooling order
    OUT/pooled/seam_report.json
    OUT/<segment>/snapshots.jsonl         line i = snapshot_index i (prices, comparator levels)
    OUT/<segment>/dataset_manifest.json
    OUT/<segment>/replay.json             pinned 76dbb5d replay; decisions[i] = snapshot_index i
    OUT/<segment>/parity_report.json      status must be PASS; lists live failures without a snapshot
    OUT/<segment>/mapping.jsonl, live_decisions.jsonl   fingerprint-checked only

Everything large is streamed: ~500k snapshots of ~35 KB and ~20 KB replay rows a year.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from market_observer.config import ObserverConfig
from market_observer.dataset import DATASET_VERSION
from market_observer.snapshots import SnapshotError, decode_snapshot, load_snapshot_json
from market_observer.util import canonical_json, stable_id

# 76dbb5d's market_observer/replay.py REPLAY_VERSION (main has since moved to 0.4.0).
PINNED_REPLAY_VERSION = "trading-decision-lab-replay-0.3.0"
SEGMENT_ORDER = ("segment1", "segment2")
SEGMENT_FILES = ("snapshots.jsonl", "dataset_manifest.json", "mapping.jsonl", "live_decisions.jsonl",
                 "replay.json", "parity_report.json")
TIME_SOURCES = {"MATCHED": "LIVE_DECISION", "NO_LIVE_DECISION": "OBSERVED_AT_FALLBACK"}
# Replay-row fields kept for analysis (the rest is dropped to bound memory).
SLIM_FEATURES = ("completed_close", "atr_15m", "return_15m_pct", "spread_bps")


class InputError(ValueError):
    """An input cannot be trusted; the analysis halts until it is explained."""


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path, what: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"{what} {path}: {exc}") from exc


def iter_jsonl(path: Path) -> Iterator[Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise InputError(f"{path} line {line_number}: {exc}") from exc


# --- population (the export's pooled files) ----------------------------------

@dataclass
class PooledRow:
    """One row of population.jsonl, with the slim replay row attached later."""
    pooled_index: int
    segment: str
    snapshot_index: int
    snapshot_id: str
    observed_at_ms: int
    mapping_status: str
    live_decision_id: str | None
    decision_time_ms: int
    time_source: str
    replay_decision_id: str
    candle: int | None
    signal: str
    risk_status: str | None
    reward_reference_status: str | None
    blocked: bool
    first_gate_passing: bool
    duplicate: bool
    signal_differs_from_first: bool
    targeted: bool
    row: dict[str, Any] | None = None  # slim replay row, attached for first gate-passing rows


@dataclass
class ExportInputs:
    directory: Path
    segments: list[str]
    population_manifest: dict[str, Any]
    seam_report: dict[str, Any]
    rows: list[PooledRow]
    replays: dict[str, dict[str, Any]] = field(default_factory=dict)
    parity: dict[str, dict[str, Any]] = field(default_factory=dict)
    dataset_manifests: dict[str, dict[str, Any]] = field(default_factory=dict)
    fingerprints: dict[str, str] = field(default_factory=dict)

    def segment_file(self, segment: str, name: str) -> Path:
        return self.directory / segment / name

    def supplier(self, close_time_ms: int) -> str | None:
        """The segment whose snapshots supply this candle's prices (§2 cutover, as pooled)."""
        boundary = self.population_manifest.get("effective_cutover_candle_close_ms")
        if len(self.segments) == 1 or boundary is None:
            return self.segments[0]
        return self.segments[0] if close_time_ms < boundary else self.segments[1]


def load_export(directory: str | Path, *, expected_replay_version: str | None = PINNED_REPLAY_VERSION) -> ExportInputs:
    """Verify fingerprints and statuses, read the pooled population, attach replay rows."""
    directory = Path(directory)
    pooled = directory / "pooled"
    manifest = _read_json(pooled / "population_manifest.json", "population manifest")
    if not isinstance(manifest, dict) or manifest.get("status") != "OK":
        raise InputError("population_manifest.json is missing or its status is not OK: no analysis")
    fingerprints = {"pooled/population_manifest.json": sha256_file(pooled / "population_manifest.json")}
    for name, key in (("population.jsonl", "population_sha256"), ("seam_report.json", "seam_report_sha256")):
        actual = sha256_file(pooled / name)
        if actual != manifest.get(key):
            raise InputError(f"pooled/{name} does not match its recorded SHA-256")
        fingerprints[f"pooled/{name}"] = actual
    seam_report = _read_json(pooled / "seam_report.json", "seam report")
    if seam_report.get("status") != "OK":
        raise InputError("seam report status is not OK")
    recorded = manifest.get("segments")
    if not isinstance(recorded, dict) or not recorded:
        raise InputError("population manifest lists no segments")
    segments = [name for name in SEGMENT_ORDER if name in recorded]
    if len(segments) != len(recorded):
        raise InputError(f"unexpected segment names {sorted(recorded)}")
    result = ExportInputs(directory, segments, manifest, seam_report, [])
    for name in segments:
        expected = recorded[name].get("files_sha256") or {}
        for file_name in SEGMENT_FILES:
            if file_name not in expected:
                raise InputError(f"{name}: no recorded SHA-256 for {file_name}")
            actual = sha256_file(directory / name / file_name)
            if actual != expected[file_name]:
                raise InputError(f"{name}/{file_name} does not match its recorded SHA-256")
            fingerprints[f"{name}/{file_name}"] = actual
        parity = _read_json(directory / name / "parity_report.json", "parity report")
        if parity.get("status") != "PASS":
            raise InputError(f"{name}: parity status {parity.get('status')!r}, not PASS")
        result.parity[name] = parity
        dataset_manifest = _read_json(directory / name / "dataset_manifest.json", "dataset manifest")
        if dataset_manifest.get("dataset_id") != recorded[name].get("dataset_id"):
            raise InputError(f"{name}: dataset_id differs from the population manifest")
        if dataset_manifest.get("jsonl_sha256") != fingerprints[f"{name}/snapshots.jsonl"]:
            raise InputError(f"{name}: snapshots.jsonl does not match its dataset manifest")
        result.dataset_manifests[name] = dataset_manifest
    result.fingerprints = fingerprints
    result.rows = _load_population(pooled / "population.jsonl", segments)
    for name in segments:
        result.replays[name] = _attach_replay(directory / name / "replay.json", name,
                                              [row for row in result.rows if row.segment == name],
                                              expected_replay_version)
    return result


def _load_population(path: Path, segments: list[str]) -> list[PooledRow]:
    rows: list[PooledRow] = []
    previous: tuple[int, int] | None = None
    for position, item in enumerate(iter_jsonl(path)):
        where = f"population row {position}"
        try:
            row = PooledRow(
                pooled_index=item["pooled_index"], segment=item["segment"],
                snapshot_index=item["snapshot_index"], snapshot_id=item["snapshot_id"],
                observed_at_ms=item["observed_at_ms"], mapping_status=item["mapping_status"],
                live_decision_id=item["live_decision_id"], decision_time_ms=item["decision_time_ms"],
                time_source=item["time_source"], replay_decision_id=item["replay_decision_id"],
                candle=item["completed_candle_close_time_ms"], signal=item["signal"],
                risk_status=item["risk_status"], reward_reference_status=item["reward_reference_status"],
                blocked=item["blocked"], first_gate_passing=item["first_gate_passing"],
                duplicate=item["duplicate"], signal_differs_from_first=item["signal_differs_from_first"],
                targeted=item["targeted"])
        except (KeyError, TypeError) as exc:
            raise InputError(f"{where}: missing field {exc}") from exc
        if row.pooled_index != position or row.segment not in segments:
            raise InputError(f"{where}: pooled_index or segment out of order")
        order = (segments.index(row.segment), row.snapshot_index)
        if previous is not None and order <= previous:
            raise InputError(f"{where}: not in segment, snapshot_index order")
        previous = order
        if any(type(v) is not int for v in (row.snapshot_index, row.observed_at_ms, row.decision_time_ms)):
            raise InputError(f"{where}: integer times and index required")
        if TIME_SOURCES.get(row.mapping_status) != row.time_source:
            raise InputError(f"{where}: mapping status {row.mapping_status!r} with time source "
                             f"{row.time_source!r} (§3: anything but one live decision or none halts)")
        if row.mapping_status == "NO_LIVE_DECISION" and row.decision_time_ms != row.observed_at_ms:
            raise InputError(f"{where}: NO_LIVE_DECISION must fall back to observed_at_ms")
        if row.decision_time_ms < row.observed_at_ms:
            raise InputError(f"{where}: decision_time_ms before observed_at_ms")
        rows.append(row)
    return rows


# --- replay -----------------------------------------------------------------

def expected_replay_configuration() -> dict[str, Any]:
    """Default ObserverConfig in replay mode: TAO, 15m, k = 2, 144-bar search (§2).

    ObserverConfig's fields are unchanged between 76dbb5d and main (only methods were
    added), so main's public_dict is the pinned configuration.
    """
    return json.loads(canonical_json(ObserverConfig(mode="historical_replay").public_dict()))


def read_replay_baseline(path: Path) -> dict[str, Any]:
    """The baseline object; canonical JSON puts it first (same as prospective_data.read_replay_baseline)."""
    prefix = '{"baseline":'
    with Path(path).open("r", encoding="utf-8") as handle:
        head = handle.read(1 << 16)
    if not head.startswith(prefix):
        raise InputError(f"{path}: not a canonical replay result")
    value, _ = json.JSONDecoder().raw_decode(head, len(prefix))
    return value


def iter_replay_decisions(path: Path, chunk: int = 1 << 20) -> Iterator[dict[str, Any]]:
    """Stream ``decisions`` from a canonical replay result.

    Same approach as ``prospective_data.iter_replay_decisions`` (PR #26); kept here so
    this adapter does not depend on that module's internals.
    """
    decoder, marker = json.JSONDecoder(), '"decisions":['
    with Path(path).open("r", encoding="utf-8") as handle:
        buffer = ""
        while marker not in buffer:
            data = handle.read(chunk)
            if not data:
                raise InputError(f"{path}: no decisions array")
            buffer += data
        position = buffer.index(marker) + len(marker)
        while True:
            while True:
                while position < len(buffer) and buffer[position] in " \t\r\n,":
                    position += 1
                if position < len(buffer):
                    break
                data = handle.read(chunk)
                if not data:
                    raise InputError(f"{path}: truncated decisions array")
                buffer, position = buffer[position:] + data, 0
            if buffer[position] == "]":
                return
            while True:
                try:
                    value, end = decoder.raw_decode(buffer, position)
                    break
                except json.JSONDecodeError:
                    data = handle.read(chunk)
                    if not data:
                        raise InputError(f"{path}: truncated decision") from None
                    buffer, position = buffer[position:] + data, 0
            yield value
            position = end
            if position > chunk:
                buffer, position = buffer[position:], 0


def replay_id_of(path: Path) -> str | None:
    """The top-level replay_id, which canonical JSON writes after the decisions array."""
    with Path(path).open("rb") as handle:
        end = handle.seek(0, 2)
        handle.seek(max(0, end - (1 << 16)))
        tail = handle.read().decode("utf-8", errors="replace")
    marker = '"replay_id":"'
    if marker not in tail:
        return None
    start = tail.rindex(marker) + len(marker)
    return tail[start:tail.index('"', start)]


def slim_row(row: dict[str, Any]) -> dict[str, Any]:
    """Keep what the analysis reads; the full risk object only for LONG_ELIGIBLE rows."""
    features = row.get("features")
    return {
        "decision_id": row.get("decision_id"), "snapshot_index": row.get("snapshot_index"),
        "observed_at_ms": row.get("observed_at_ms"), "signal": row.get("signal"),
        "completed_candle_close_time_ms": row.get("completed_candle_close_time_ms"),
        "features": ({key: features.get(key) for key in SLIM_FEATURES} if isinstance(features, dict) else None),
        "risk": row.get("risk") if row.get("signal") == "LONG_ELIGIBLE" else None,
    }


def _attach_replay(path: Path, segment: str, pooled: list[PooledRow], expected_version: str | None) -> dict[str, Any]:
    """Verify the pinned replay row by row and attach slim rows to first gate-passing rows.

    Checks: version and configuration are the pinned ones; every decision_id recomputes
    from its row; snapshot_index is the row's position; replay_id recomputes; each pooled
    row agrees with its replay row (decision ID, observed_at_ms, candle, signal, risk and
    reward-reference status).
    """
    baseline = read_replay_baseline(path)
    if expected_version is not None and baseline.get("replay_version") != expected_version:
        raise InputError(f"{segment}: replay version {baseline.get('replay_version')!r}, "
                         f"expected the pinned {expected_version!r}")
    configuration = baseline.get("configuration")
    if configuration != expected_replay_configuration():
        raise InputError(f"{segment}: replay configuration is not the default ObserverConfig (§2)")
    if stable_id("config", configuration) != baseline.get("configuration_id"):
        raise InputError(f"{segment}: configuration_id does not match its configuration")
    by_index = {row.snapshot_index: row for row in pooled}
    decision_ids: list[str] = []
    for position, row in enumerate(iter_replay_decisions(path)):
        payload = {key: value for key, value in row.items() if key != "decision_id"}
        if stable_id("replay_decision", payload) != row.get("decision_id"):
            raise InputError(f"{segment}: replay decision {position} does not match its decision_id")
        if row.get("snapshot_index") != position or row.get("schema_version") != "replay_decision/0.2":
            raise InputError(f"{segment}: replay decision {position} is out of place or not replay_decision/0.2")
        decision_ids.append(row["decision_id"])
        mine = by_index.get(position)
        if mine is None:
            continue
        reward = (row.get("risk") or {}).get("reward_reference")
        checks = {
            "replay_decision_id": (mine.replay_decision_id, row["decision_id"]),
            "observed_at_ms": (mine.observed_at_ms, row.get("observed_at_ms")),
            "completed_candle_close_time_ms": (mine.candle, row.get("completed_candle_close_time_ms")),
            "signal": (mine.signal, row.get("signal")),
            "risk_status": (mine.risk_status, (row.get("risk") or {}).get("status")),
            "reward_reference_status": (mine.reward_reference_status,
                                        reward.get("status") if isinstance(reward, dict) else None),
        }
        for name, (pooled_value, replay_value) in checks.items():
            if pooled_value != replay_value:
                raise InputError(f"{segment} snapshot {position}: population {name} {pooled_value!r} "
                                 f"!= replay {replay_value!r}")
        if mine.first_gate_passing:
            mine.row = slim_row(row)
    if baseline.get("input_snapshot_count") != len(decision_ids):
        raise InputError(f"{segment}: input_snapshot_count does not match the decisions")
    if pooled and pooled[-1].snapshot_index >= len(decision_ids):
        raise InputError(f"{segment}: population refers to rows beyond the replay")
    replay_id = stable_id("replay", {"baseline": baseline, "decision_ids": decision_ids})
    if replay_id != replay_id_of(path):
        raise InputError(f"{segment}: replay_id does not match its content")
    return {"replay_id": replay_id, "replay_version": baseline.get("replay_version"),
            "configuration_id": baseline.get("configuration_id"), "decisions": len(decision_ids)}


# --- snapshots and dataset manifest -----------------------------------------

def stream_verified_snapshots(path: Path, manifest: dict[str, Any]) -> Iterator[tuple[int, dict[str, Any]]]:
    """Yield (index, decoded snapshot), then verify the dataset manifest at exhaustion.

    Mirrors ``market_observer.dataset.build_dataset_artifact`` + ``validate_dataset_manifest``
    check for check (equivalence is tested) without holding every row. The caller must
    consume the whole iterator; the manifest comparison raises at the end.
    """
    source = manifest.get("source") if isinstance(manifest, dict) else None
    if not isinstance(source, str) or not source.strip():
        raise InputError("manifest source is required")
    version = manifest.get("dataset_version")
    if version not in {DATASET_VERSION, "trading-decision-lab-dataset-0.1.0"}:
        raise InputError("unsupported dataset version")
    digest = hashlib.sha256()
    previous_observed = previous_exchange = first_observed = last_observed = None
    identities: set[tuple[str, str]] = set()
    count = 0
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            index = count
            try:
                row = decode_snapshot(load_snapshot_json(line))
            except (SnapshotError, json.JSONDecodeError) as exc:
                raise InputError(f"{path} line {line_number}: {exc}") from exc
            missing = sorted({"observed_at_ms", "exchange_time_ms", "research_symbol",
                              "executable_pair"} - row.keys())
            if missing:
                raise InputError(f"snapshot {index}: missing identity fields {missing}")
            observed, exchange = int(row["observed_at_ms"]), int(row["exchange_time_ms"])
            if previous_observed is not None and observed < previous_observed:
                raise InputError(f"snapshot {index}: observed_at_ms moved backwards")
            if exchange > observed:
                raise InputError(f"snapshot {index}: exchange_time_ms is after observed_at_ms")
            if previous_exchange is not None and exchange < previous_exchange:
                raise InputError(f"snapshot {index}: exchange_time_ms moved backwards")
            previous_observed, previous_exchange = observed, exchange
            identities.add((str(row["research_symbol"]), str(row["executable_pair"])))
            digest.update((canonical_json(row) + "\n").encode("utf-8"))
            first_observed = observed if first_observed is None else first_observed
            last_observed = observed
            count += 1
            yield index, row
    if not count:
        raise InputError(f"{path}: dataset must contain at least one snapshot")
    if len(identities) != 1:
        raise InputError(f"dataset mixes instrument identities: {sorted(identities)}")
    research_symbol, executable_pair = next(iter(identities))
    baseline = {
        "dataset_version": version, "source": source, "snapshot_count": count,
        "research_symbol": research_symbol, "executable_pair": executable_pair,
        "first_observed_at_ms": first_observed, "last_observed_at_ms": last_observed,
        "jsonl_sha256": digest.hexdigest(),
    }
    if manifest.get("source_metadata") is not None:
        baseline["source_metadata"] = manifest["source_metadata"]
    rebuilt = {
        "schema_version": "historical_dataset_manifest/0.1",
        "dataset_id": stable_id("historical_dataset", baseline), **baseline,
        "claims": {"chronological_input": True, "identity_consistent": True,
                   "outcome_labels_present": False, "fill_simulation_present": False,
                   "performance_claim_available": False},
    }
    if json.loads(canonical_json(rebuilt)) != json.loads(canonical_json(manifest)):
        raise InputError(f"dataset manifest does not match snapshot JSONL {path}")


def snapshot_id(snapshot: dict[str, Any]) -> str:
    """The live store's ID (market_observer/storage.py: stable_id("snapshot", payload))."""
    return stable_id("snapshot", snapshot)
