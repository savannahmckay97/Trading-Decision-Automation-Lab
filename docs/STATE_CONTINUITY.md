# Completed-candle state continuity

Issue #12, item 1: a breakout hypothesis must not bridge unevaluated candles.
The live observer and replay now use the same reset rule in `evaluate_snapshot`.
The shared `continuity.sequence_status` classifies completed-candle spacing.

Before advancing an eligible observation, compare its completed close with the
persisted setup's `last_evaluated_close_time_ms`:

- First observation: evaluate from the initial state.
- Same close: preserve idempotence; do not advance or claim an update.
- One interval later: continue the existing setup.
- Multiple whole intervals later: discard the old setup and evaluate the current
  candle from IDLE. Never infer transitions through unseen candles. A new
  breakout may start independently; the old setup cannot qualify a retest.
- Backward/irregular spacing or an active setup with no trustworthy previous
  clock: fail closed, without writing state.

For example, a breakout evaluated at 10:00 followed by the next valid observation
at 10:30 has one missing decision candle. Even if 10:30 looks like a retest,
the previous hypothesis is discarded: the unseen 10:15 close could have failed.

`T0_STATE_CONTINUITY` records the status, delta, missing-bar count and
`setup_reset` in decision gate evidence. A safely handled gap passes this check;
invalid chronology instead produces the normal fail-closed error. The gate does
not grant execution permission. If another gate blocks the cycle, the setup is
frozen and `setup_reset` is false. Recovery measures spacing from the last actual
state update, including when intervening blocked snapshots were recorded.

Replay retains its input-sequence diagnostics and rejects backward/irregular
input chronology, but no longer resets state separately. This prevents its state
from diverging from live observation on a blocked gap. Reset state is persisted
through the existing service path; restart and duplicate polling cannot recreate
the discarded hypothesis. Prior-state dictionaries are not mutated.

Six regression tests cover one/multiple skipped candles, a skipped failure,
live/replay risk and state parity, blocked recovery, restart, duplicate polls,
fresh breakout identity and malformed chronology. No strategy thresholds,
credentials or execution routes change. Other items in issue #12 remain separate.
