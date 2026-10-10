# Collection completion and freshness — issue #12 item 2

An optional request can fail after required inputs have arrived. Previously,
`observed_at_ms` considered only successful response receipts, so a cycle could
use quotes and derivatives tens of seconds after they had become stale.

`PublicCollectors.collect` now records `completed_at_ms` after all futures and
executor shutdown finish, including failures and retries. It clears the value
before each batch. `ObserverService` captures this boundary before journaling and
supplies it to normalization. Custom collectors without completion metadata use
the service clock immediately after `collect` returns. Deterministic test
collectors supply explicit completion metadata rather than using the wall clock.

Normalization rejects malformed completion clocks and clocks before any success
or failure receipt. Direct callers without explicit completion metadata now at
least include error receipt times in the observation boundary. Callers that own a
request batch should supply its actual completion time, as the live probe does.
No source timestamp is rewritten, and existing freshness budgets are unchanged.

For a required input received at T and an optional failure finishing at T + 30s,
OI/taker receipt age is now 30s, exceeding the 15s budget. The cycle emits
DATA_STALE and preserves the previous setup. This also catches delayed batch
completion after the final successful response, without requiring an error.
The next normal batch uses its own completion boundary; none is reused.

Six regression tests cover late optional errors, actual collector completion,
service fallback timing, state preservation, error-to-completion delay and invalid
completion clocks. All 160 tests and specification checks pass on this independent
main-based branch. Future timestamps still fail closed. This change does not
address the separate gap reset or replay decoder PRs, nor HTTP retry/empty-series
handling (issue #12 items 1, 5, 3 and 4 respectively).
