"""Immutable dataset artifacts for causal TDAL historical research.

This compartment freezes already-normalized snapshots. It does not backfill,
label outcomes, simulate fills, or import state from any other project.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Iterable

from .util import canonical_json, stable_id


DATASET_VERSION = "trading-decision-lab-dataset-0.1.0"


class DatasetError(ValueError):
    """Raised when snapshots cannot form a trustworthy frozen dataset."""


def build_dataset_artifact(
    snapshots: Iterable[dict[str, Any]], *, source: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate and fingerprint snapshots without sorting away chronology evidence."""
    rows: list[dict[str, Any]] = []
    previous_observed: int | None = None
    identities: set[tuple[str, str]] = set()
    digest = hashlib.sha256()

    for index, snapshot in enumerate(snapshots):
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
        if previous_observed is not None and observed_at < previous_observed:
            raise DatasetError(f"snapshot {index}: observed_at_ms moved backwards")
        if exchange_time > observed_at:
            raise DatasetError(
                f"snapshot {index}: exchange_time_ms is after observed_at_ms"
            )
        previous_observed = observed_at
        identities.add(
            (str(snapshot["research_symbol"]), str(snapshot["executable_pair"]))
        )
        row = dict(snapshot)
        digest.update((canonical_json(row) + "\n").encode("utf-8"))
        rows.append(row)

    if not rows:
        raise DatasetError("dataset must contain at least one snapshot")
    if len(identities) != 1:
        raise DatasetError(f"dataset mixes instrument identities: {sorted(identities)}")

    research_symbol, executable_pair = next(iter(identities))
    baseline = {
        "dataset_version": DATASET_VERSION,
        "source": source,
        "snapshot_count": len(rows),
        "research_symbol": research_symbol,
        "executable_pair": executable_pair,
        "first_observed_at_ms": int(rows[0]["observed_at_ms"]),
        "last_observed_at_ms": int(rows[-1]["observed_at_ms"]),
        "jsonl_sha256": digest.hexdigest(),
    }
    manifest = {
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
    return rows, manifest


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
    if destination.exists() or manifest_destination.exists():
        raise FileExistsError("dataset outputs must be new")
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest_destination.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(canonical_json(row) + "\n" for row in rows)
    actual_sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if actual_sha != manifest.get("jsonl_sha256"):
        raise DatasetError("manifest hash does not match dataset rows")
    try:
        destination.write_text(payload, encoding="utf-8")
        manifest_destination.write_text(
            canonical_json(manifest) + "\n", encoding="utf-8"
        )
    except Exception:
        destination.unlink(missing_ok=True)
        manifest_destination.unlink(missing_ok=True)
        raise
