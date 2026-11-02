"""Immutable dataset artifacts for causal TDAL historical research.

This compartment freezes already-normalized snapshots. It does not backfill,
label outcomes, simulate fills, or import state from any other project.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable

from .util import canonical_json, stable_id
from .snapshots import SnapshotError, decode_snapshot


DATASET_VERSION = "trading-decision-lab-dataset-0.2.0"


class DatasetError(ValueError):
    """Raised when snapshots cannot form a trustworthy frozen dataset."""


class DatasetBuilder:
    """Incremental form of ``build_dataset_artifact``: the same validation, row
    order, fingerprint and manifest, without holding every row in memory. Each
    ``add`` returns the decoded row; ``line`` gives the exact JSONL line hashed."""

    def __init__(self, *, source: str, source_metadata: dict[str, Any] | None = None,
                 dataset_version: str = DATASET_VERSION) -> None:
        if dataset_version not in {DATASET_VERSION, "trading-decision-lab-dataset-0.1.0"}:
            raise DatasetError("unsupported dataset version")
        self.source = source
        self.source_metadata = source_metadata
        self.dataset_version = dataset_version
        self.count = 0
        self.first_observed: int | None = None
        self.previous_observed: int | None = None
        self.previous_exchange: int | None = None
        self.identities: set[tuple[str, str]] = set()
        self.digest = hashlib.sha256()

    @staticmethod
    def line(row: dict[str, Any]) -> str:
        return canonical_json(row) + "\n"

    def add(self, snapshot: Any) -> dict[str, Any]:
        index = self.count
        try:
            snapshot = decode_snapshot(snapshot)
        except SnapshotError as exc:
            raise DatasetError(f"snapshot {index}: {exc}") from exc
        if not isinstance(snapshot, dict):
            raise DatasetError(f"snapshot {index}: must be an object")
        required = {
            "observed_at_ms",
            "exchange_time_ms",
            "research_symbol",
            "executable_pair",
        }
        missing = sorted(required - snapshot.keys())
        if missing:
            raise DatasetError(f"snapshot {index}: missing identity fields {missing}")
        try:
            observed_at = int(snapshot["observed_at_ms"])
            exchange_time = int(snapshot["exchange_time_ms"])
        except (TypeError, ValueError) as exc:
            raise DatasetError(f"snapshot {index}: invalid timestamp") from exc
        if self.previous_observed is not None and observed_at < self.previous_observed:
            raise DatasetError(f"snapshot {index}: observed_at_ms moved backwards")
        if exchange_time > observed_at:
            raise DatasetError(
                f"snapshot {index}: exchange_time_ms is after observed_at_ms"
            )
        self.previous_observed = observed_at
        if self.previous_exchange is not None and exchange_time < self.previous_exchange:
            raise DatasetError(f"snapshot {index}: exchange_time_ms moved backwards")
        self.previous_exchange = exchange_time
        self.identities.add(
            (str(snapshot["research_symbol"]), str(snapshot["executable_pair"]))
        )
        row = dict(snapshot)
        self.digest.update(self.line(row).encode("utf-8"))
        if self.first_observed is None:
            self.first_observed = observed_at
        self.count += 1
        return row

    def manifest(self) -> dict[str, Any]:
        if not self.count:
            raise DatasetError("dataset must contain at least one snapshot")
        if len(self.identities) != 1:
            raise DatasetError(f"dataset mixes instrument identities: {sorted(self.identities)}")

        research_symbol, executable_pair = next(iter(self.identities))
        baseline = {
            "dataset_version": self.dataset_version,
            "source": self.source,
            "snapshot_count": self.count,
            "research_symbol": research_symbol,
            "executable_pair": executable_pair,
            "first_observed_at_ms": int(self.first_observed),
            "last_observed_at_ms": int(self.previous_observed),
            "jsonl_sha256": self.digest.hexdigest(),
        }
        if self.source_metadata is not None:
            baseline["source_metadata"] = self.source_metadata
        return {
            "schema_version": "historical_dataset_manifest/0.1",
            "dataset_id": stable_id("historical_dataset", baseline),
            **baseline,
            "claims": {
                "chronological_input": True,
                "identity_consistent": True,
                "outcome_labels_present": False,
                "fill_simulation_present": False,
                "performance_claim_available": False,
            },
        }


def build_dataset_artifact(
    snapshots: Iterable[dict[str, Any]], *, source: str,
    source_metadata: dict[str, Any] | None = None,
    dataset_version: str = DATASET_VERSION,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate and fingerprint snapshots without sorting away chronology evidence."""
    builder = DatasetBuilder(source=source, source_metadata=source_metadata,
                             dataset_version=dataset_version)
    rows = [builder.add(snapshot) for snapshot in snapshots]
    return rows, builder.manifest()


def validate_dataset_manifest(
    snapshots: Iterable[dict[str, Any]], manifest: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Rebuild the declared supported baseline, retaining acquisition metadata."""
    source = manifest.get("source")
    if not isinstance(source, str) or not source.strip():
        raise DatasetError("manifest source is required")
    rows, rebuilt = build_dataset_artifact(
        snapshots, source=source, source_metadata=manifest.get("source_metadata"),
        dataset_version=manifest.get("dataset_version"),
    )
    if rebuilt != manifest:
        raise DatasetError("dataset manifest does not match snapshot JSONL")
    return rows, rebuilt


def write_dataset(
    rows: Iterable[dict[str, Any]],
    manifest: dict[str, Any],
    *,
    jsonl_path: str | Path,
    manifest_path: str | Path,
) -> None:
    """Write new JSONL + manifest outputs; neither may already exist."""
    destination = Path(jsonl_path)
    manifest_destination = Path(manifest_path)
    if destination.resolve() == manifest_destination.resolve():
        raise DatasetError("JSONL and manifest paths must differ")
    if destination.exists() or manifest_destination.exists():
        raise FileExistsError("dataset outputs must be new")
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest_destination.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(canonical_json(row) + "\n" for row in rows)
    actual_sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if actual_sha != manifest.get("jsonl_sha256"):
        raise DatasetError("manifest hash does not match dataset rows")
    created: list[Path] = []
    try:
        for path, content in ((destination, payload),
                              (manifest_destination, canonical_json(manifest) + "\n")):
            with path.open("x", encoding="utf-8") as handle:
                created.append(path)
                handle.write(content)
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
