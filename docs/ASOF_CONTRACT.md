# Shared observation availability contract

Implemented follow-up to the evaluation-first PR, 10 October 2026 (Melbourne).
This change closes detectable normalized timestamp contradictions in live,
replay and offline evaluation. It does not certify live provider integration.

## Runtime behavior

`validate_snapshot_asof` is pure and runs before feature calculation or supplied
features in `evaluate_snapshot`. Replay also calls it before its preliminary
feature/sequence calculation. Offline evaluation uses the same validator and
wraps failures as `EvaluationError` instead of producing a partial report.

- Observation/exchange, receipt and consumed derivative-row timestamps must be
  non-negative integers. Boolean, float and string coercions are forbidden.
- Exchange time, receipts and derivative event/row times cannot exceed
  `observed_at_ms`; equality is allowed. There is no silent clock-skew allowance.
- Required public endpoint provenance and non-empty OI/taker series are required.
- Funding and positioning remain optional, but supplied rows need matching
  endpoint provenance and valid as-of timestamps.
- Kraken REST can have no provider event timestamp: its existing freshness gate
  uses receipt time. That absence never becomes a fabricated exchange timestamp.
- Binance kline envelope `event_time_ms` currently means the maximum scheduled
  candle close, including the open candle. It is checked for numeric validity,
  not rejected solely for being future. Receipts are still checked, and existing
  feature logic excludes candles whose close is not before exchange time.

Live service and replay failures emit `DATA_STALE`, preserve setup state and
retain `NO_ORDER`. Replay reports these cycles as `ASOF_INVALID`; their invalid
observation timestamp is preserved as evidence, not repaired or used to advance
chronology. Field-specific errors appear in the existing failure gate evidence.

This validates timestamp consistency, not payload authenticity, venue identity,
provider publication delay/revisions, 5m interval semantics, synchronized feature
windows or continuity of derivative series. Those remain provider-contract work.
Previously saved contradictory snapshots now fail rather than reproduce their
former eligibility. No dataset is rewritten and no rule threshold is tuned.

## Verification

`tests/test_asof.py` checks future rows, receipts/events, equality, strict clock
types, provenance, optional annotations, normal open candles, precomputed-feature
bypass prevention, live/replay state preservation and malformed replay clocks.
`tests/test_evaluation.py` checks that offline evaluation shares the contract.
The full 68-test suite and specification validation pass locally on Python 3.12.
Passing fixtures proves behavior, not market or user benefit.

## Path to the working decision-support loop

1. Review the evaluation-first PR, then this dependent guard PR. Neither adds
   account access or order submission.
2. Validate public provider timing, symbol, units and required-series contracts
   against actual raw captures. Resolve live post-gap state handling. A failed
   public probe is a block, not a reason to substitute synthetic market evidence.
3. Run observation on the user's local machine with a dedicated TDAL database.
   Demonstrate persisted raw events, normalized observations, decisions, restart
   recovery and saved-input replay. No model should edit a running checkout.
4. Render one readable decision record from existing evidence: hypothesis,
   support, objections, invalidation, expiry, missing information and unknown
   confidence. Keep eligibility separate from account approval.
5. Attach outcome review after three completed 15m candles (45m); report pending,
   unavailable and available paths explicitly. Reuse post-hoc labels; future
   outcomes never feed the original decision. This is observation assessment,
   not a validated prediction or an executable trade return.
6. Freeze a prospective experiment and compare with simple baselines and the
   user's existing workflow. Adopt, simplify, reject or retain as inconclusive.
   Only then consider calibrated predictions, account-aware intents or new inputs.

PR #2 and this PR overlap in the evaluator/replay/evaluation path and need ordered
integration. Local environment/read-only connectivity checks can run while code
is reviewed. Recording starts from one tested immutable checkout; future edits
use separate branches and a planned restart. Live trading stays outside this path.

## Local capture handoff

After the guard branch's test matrix passes, use the existing CLI from a local
checkout of that exact reviewed commit. The public requests use no credentials.
Start with one cycle; only then start polling. A successful cycle records raw
events, normalized inputs and a decision in the existing SQLite journal. A
blocked cycle is useful diagnostics but is not a validated market observation.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
python -m unittest discover -s tests -q
python spec/validate_specifications.py
python observer.py --base TAO --pretty
```

Inspect the one-cycle output and provider errors before using:

```sh
python observer.py --base TAO --loop --poll-seconds 60
```

Ctrl-C stops polling. The default database is `data/trading_decision_lab.sqlite`.
The rolling three-candle review and readable decision record are subsequent
work, not features delivered by this guard. No process has been started on the
user's machine. The cloud workspace's bounded Binance/Kraken probes timed out;
this is an environment-specific unresolved connectivity result, not an outage
claim or evidence that the same requests fail from the local machine.
