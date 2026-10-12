# Prospective v1: export, pinned replay, parity and seam

`prospective_data.py` builds the frozen inputs that `PREREGISTRATION.md` §2, §3 and
§6 require before the one analysis runs. It reads **decision fields only**. It
never looks at a price after a decision and computes no outcome. The analysis
script (§4) reads the files it writes.

It lives here rather than in `market_observer/` because it is specific to this
study: it pins an evaluator commit, knows about recorder segments and the seam
cutover, and runs the pinned replay as a separate program. The only change to the
runtime package is `market_observer.dataset.DatasetBuilder`, an incremental form
of `build_dataset_artifact` (which now delegates to it). It has the same
validation, row order, fingerprint and manifest, without holding every snapshot
in memory. A year of 60 s polling is about 500k snapshots of about 35 KB each.

Every command writes new files only, never overwrites, and records SHA-256
fingerprints and the code revisions used.

## Commands (owner, at analysis time)

Run from a clone of `main` that contains this tool, with `PYTHONPATH` set to the
clone root.

```sh
# 0. A checkout of the pinned evaluator (once). The commit is a required argument
#    to every replay; the tool checks the checkout's HEAD and refuses modified files.
git worktree add --detach ../tdal-76dbb5d 76dbb5d

# 1. Freeze each segment's database: a read-only SQLite backup into a new,
#    self-contained file (export refuses a WAL-mode database that is still live).
#    Opening a WAL database, even read-only, creates -wal/-shm files beside it,
#    so freeze from a copy in a scratch directory rather than from a backup folder.
python research/prospective_v1/prospective_data.py freeze --db /scratch/seg1/observer.sqlite --out /analysis/segment1.sqlite
python research/prospective_v1/prospective_data.py freeze --db /scratch/seg2/observer.sqlite --out /analysis/segment2.sqlite

# 2. Everything: export, pinned replay and parity for each segment, then the seam and pooling.
python research/prospective_v1/prospective_data.py all \
  --segment1-db /analysis/segment1.sqlite --segment2-db /analysis/segment2.sqlite \
  --cutover 2026-10-13T00:00Z \
  --pinned-checkout ../tdal-76dbb5d --pinned-commit 76dbb5d \
  --out /analysis/prospective_v1
```

The same stages can be run one at a time:

```sh
python research/prospective_v1/prospective_data.py export --db /analysis/segment1.sqlite --segment segment1 --out /analysis/p/segment1
python research/prospective_v1/prospective_data.py replay --export-dir /analysis/p/segment1 --pinned-checkout ../tdal-76dbb5d --pinned-commit 76dbb5d
python research/prospective_v1/prospective_data.py parity --export-dir /analysis/p/segment1
# ... the same for segment2, then:
python research/prospective_v1/prospective_data.py pool --segment1-dir /analysis/p/segment1 --segment2-dir /analysis/p/segment2 --cutover 2026-10-13T00:00Z --out /analysis/p/pooled
```

Exit codes: `0` means OK or PASS. `1` means a parity FAIL or a seam/pool HALT, with
the reports written. `2` means the run halted before writing output (the export
halts and lists every problem, or an output already exists).

`--python` picks the interpreter for the pinned replay. By default it is the one
running the tool. The replay runs as
`python -m market_observer.replay --jsonl … --output …` with `cwd` and
`PYTHONPATH` set to the pinned checkout, and with other `PYTHON*` variables
cleared. A probe in the same environment confirms that the imported module is the
checkout's own.

## Output contract

`all --out OUT` writes `OUT/segment1/`, `OUT/segment2/` (one *segment directory*
each) and `OUT/pooled/`. All JSON is canonical: sorted keys, decimals as strings.
Times are integer UTC milliseconds.

### Segment directory

| file | content |
|---|---|
| `snapshots.jsonl` | Canonical snapshot JSONL in replay order (`normalized_snapshots` by `observed_at_ms`, then rowid). Line *i* is `snapshot_index` *i*. |
| `dataset_manifest.json` | `market_observer.dataset` manifest (`dataset_id`, `jsonl_sha256`, `source_metadata.database_sha256`). |
| `live_decisions.jsonl` | Every live decision of the symbol, in rowid order: `{"decision_id", ...the stored payload}`. Includes failure decisions. |
| `mapping.jsonl` | One row per snapshot, in export order (below). |
| `export_manifest.json` | Counts, `no_live_decision` list, `live_decisions_without_snapshot` list (`decision_id`, `decision_time_ms`, `signal`, `error`), database SHA-256, tooling code revision, `files_sha256`. |
| `replay.json` | The pinned replay's own output (`historical_replay/0.1`), unmodified. `decisions[i]` is the replay row for `snapshot_index` *i* and carries the full `setup`, `risk`, `features`, `gates`, `breakout_quality`. |
| `replay_provenance.json` | `code_commit` (the full hash of the code that ran), `pinned_commit_requested`, `replay_version`, `replay_module`, `python`, `python_version`, `command`, `replay_sha256`, `snapshots_sha256`, `replay_configuration`. |
| `replay_summary.jsonl` | One compact row per replay row: `snapshot_index`, `replay_decision_id`, `observed_at_ms`, `completed_candle_close_time_ms`, `signal`, `setup` (full object), `risk_status`, `reward_reference_status`, `canonical_sha256`. |
| `parity_report.json` | `status` PASS/FAIL, `counts`, every `mismatches[]` entry (`snapshot_index`, `snapshot_id`, `live_decision_id`, `live_signal`, `replay_signal`, `differences[]` of `{path, live, replay}`), `structural_problems`, `separate_validations` (including `asof_audit_failures[]`), and the `no_live_decision` and `live_decisions_without_snapshot` lists. |

`mapping.jsonl` row fields: `segment`, `snapshot_index`, `snapshot_id` (stored),
`recomputed_snapshot_id`, `observed_at_ms`, `status` (`MATCHED` or
`NO_LIVE_DECISION`), `live_decision_id`, `live_decision_time_ms`,
`decision_time_ms` (the live time, or `observed_at_ms` as a fallback),
`time_source` (`LIVE_DECISION` or `OBSERVED_AT_FALLBACK`), `live_signal`,
`live_completed_candle_close_time_ms`, `asof_audit` (`PASS`/`FAIL`) and
`asof_error`.

### Pooled directory

| file | content |
|---|---|
| `seam_report.json` | Always written. `status` OK/HALT, `halts[]`, `seam` (first candidate candle, `effective_cutover_candle_close_ms`, `moved`, the carried setups on both sides, every `disagreements[]` entry), `overlap` (segment 1 vs segment 2 on overlapping candles: `with_differences[]` on the canonical fields, `blocked_differences[]`). |
| `population.jsonl` | Written only when OK. One row per pooled replay row, segment 1 first, each in `snapshot_index` order (below). |
| `population_manifest.json` | Effective cutover, per-segment provenance and fingerprints, the candles each segment supplies (first/last/count), and counts: rows, blocked, first gate-passing candles, duplicates, changed-signal duplicates, `NO_LIVE_DECISION`, `targeted`, `eligible_target_unavailable`. |

`population.jsonl` row fields: `pooled_index`, `segment`, `snapshot_index`,
`snapshot_id`, `observed_at_ms`, `mapping_status`, `live_decision_id`,
`decision_time_ms`, `time_source`, `replay_decision_id`,
`completed_candle_close_time_ms`, `signal`, `setup_state`, `risk_status`,
`reward_reference_status`, `blocked` (signal `DATA_STALE` or `RISK_BLOCKED`),
`first_gate_passing`, `duplicate`, `signal_differs_from_first` and `targeted`
(first gate-passing, `LONG_ELIGIBLE`, reward reference `AVAILABLE`).

The analysis script takes the full decision (risk levels, `per_unit_risk`,
`features` for the comparator) from `<segment>/replay.json` `decisions[snapshot_index]`
(`iter_replay_decisions` streams it) and the candle prices from
`<segment>/snapshots.jsonl` line `snapshot_index`. It must refuse to run unless
`population_manifest.json` exists with `status: OK`, and it should check the
recorded SHA-256 values. `first_gate_passing` is computed over the whole pooled
series. Applying the §3 window (holdout start to cut-off, on `observed_at_ms`)
afterwards gives the same result, because rows are in time order.

## What is checked

- **Export (halts, writing nothing):**
  - every stored `snapshot_id` must equal `stable_id("snapshot", payload)`, and
    the exported JSONL row must reproduce it;
  - the `observed_at_ms` column must match the payload;
  - every provenance receipt must be at or before `observed_at_ms`, and must
    equal its `raw_events` row (`received_time_ms`, `event_time_ms`, `source`,
    `endpoint`, `symbol`, `critical`); collection errors must not be received
    after `observed_at_ms`;
  - each live `decision_id` must equal its payload's ID, and `decision_time_ms`
    must match;
  - more than one live decision for a snapshot halts;
  - a live decision referencing a snapshot that is not in the export halts;
  - `observed_at_ms > decision_time_ms` halts;
  - the dataset contract (`DatasetBuilder`) must hold;
  - the database file must not change during the export;
  - the as-of audit is recorded per snapshot and does not halt. A failure is a
    blocked cycle, and parity checks that live and replay agree on it.
- **Parity:** for each `MATCHED` snapshot, the §6 canonical fields:
  - the completed candle (the replay's top-level field vs the live
    `features.completed_candle_close_time_ms`);
  - the full `setup`, `signal`, full `risk`, full `gates` (rule ID, status,
    category, evidence), full `features` and full `breakout_quality`.

  Live failure decisions that have a snapshot are compared like any other.
  Those without one have no replay row and are listed as blocked cycles.
  `NO_LIVE_DECISION` rows are listed, not compared.
- **Seam and pooling:** see below. Pooling also requires both parities to PASS
  and both replays to come from the same commit.

## Design decisions and ambiguities

1. **Pinned code.** The live recordings come from `76dbb5d` (replay
   `trading-decision-lab-replay-0.3.0`). Current `main` has a different venue
   gate and extra features, so a main-code replay does not reproduce them. A
   test shows this, and another shows that a `76dbb5d` recording replays with
   zero canonical differences under `76dbb5d`. The export (JSONL, manifests,
   mapping) uses current `main` code. It only copies stored payloads, and the
   JSONL rows are checked against the stored IDs.
2. **Mode echo.** Gate `T0_OBSERVATION_ONLY` carries `evidence.mode`
   (`observation_only` live, `historical_replay` in replay). That one key is
   excluded as "mode metadata" (§6). Every other gate field is compared,
   including `category`.
3. **Seam "canonical setup state".** The seam compares `strategy_id`, `state`,
   `setup_id`, `breakout_resistance`, `breakout_atr`, `breakout_close_time_ms`
   and `bars_since_breakout`. It leaves out:
   - `transition`, which records how a state was reached. Otherwise an IDLE
     reached by `TERMINAL_RESET` would never match one that is still
     `INITIALIZED`, and the seam could halt for no substantive reason;
   - `last_evaluated_close_time_ms`, which is bookkeeping;
   - `state_updated`, a per-row flag.

   **The owner should confirm this choice or amend the plan.**
4. **"State carried into candle C".** For each segment, this is the setup of
   the replay row just before that segment's first row evaluating a candle at or
   after C (the default IDLE state if there is none). Candidates for C step
   through the 15m grid from the first candle closing at or after the cutover. If
   a segment has no row at or after a candidate, it has stopped recording, and
   the seam halts.
5. **Rows without a candle** (failure `DATA_STALE` rows) go with the candle
   they would have evaluated: segment 1 if `observed_at_ms` is at or before the
   effective cutover candle's close, otherwise segment 2. They are blocked cycles
   and count only towards coverage.
6. **Overlap comparison** covers every candle that both segments evaluated,
   including candles before the effective cutover. Differences there are
   expected while the two replays' states differ (for example in
   `T0_STATE_CONTINUITY` evidence). They are reported, as §2 asks, and do not
   halt.
7. **Structurally invalid stored snapshots.** `76dbb5d`'s replay has no
   fail-closed mode. A stored snapshot that it, or the dataset contract, rejects
   halts the run. One example is `exchange_time_ms > observed_at_ms` from clock
   skew, which live recorded as a blocked cycle. §2 covers a collector change but
   not this case. If it happens, the plan needs an amendment.
8. **Memory.** This tool streams. The pinned replay does not: `76dbb5d` keeps
   every replay decision in memory and writes one JSON line, at roughly 20 KB per
   decision, or about 10 GB at 500k snapshots. Check the machine before the run.
   Changing that would mean not running the pinned code unchanged.
9. **Cost assumptions.** The replay uses the default `ObserverConfig`, with no
   fee or slippage. If the recorder ran with cost flags, parity shows it as
   `risk.costs` differences.
