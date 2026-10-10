# Public-provider errors — issue #12 items 3 and 4

The HTTP client now separates transient failures from deterministic responses:

- HTTP 4xx other than 429/418: fail immediately; no retry/backoff.
- HTTP 429 or 418: fail this request immediately and establish a per-origin
  cooldown shared by this client across endpoints and polling cycles.
  `Retry-After` supports delay seconds and HTTP dates. Missing, invalid, zero or
  expired guidance defaults to 60 seconds. Concurrent longer deadlines cannot
  be shortened. Other provider origins are unaffected.
- During cooldown, new calls fail before reaching the network. After expiry,
  calls may proceed. No worker sleeps for an unbounded provider deadline.
  Already in-flight requests cannot be recalled. Cooldowns are in memory and
  do not survive a client/process restart; restart is not a rate-limit remedy.
- Rate limits reported inside an HTTP 200 body start the same per-origin
  cooldown, using the 60-second default: Kraken `EAPI:Rate limit exceeded`,
  `EGeneral:Too many requests`, `EService:Throttled`, and Binance `-1003`.
- Transport failures and HTTP 5xx use the existing bounded attempts and 1s/2s
  backoff at the default three-attempt setting. Transport failures include
  `http.client.HTTPException` (truncated bodies, bad status lines), which are
  not `OSError`s. Other provider-declared JSON errors and malformed JSON fail
  immediately.

HTTP errors retain at most 1024 bytes of response body, decoded with replacement
and marked when truncated; response handles are closed. Thus Binance's
`-1121 Invalid symbol` reaches `CollectionError.detail` instead of being lost.
Rate-limited critical inputs stay missing and block observation eligibility.
The code does not exercise real provider bans to test its rate-limit behavior.

At collection, required Binance klines, OI history and taker flow must be
non-empty arrays. Empty HTTP-200 responses or wrong shapes become explicit
critical collection errors with endpoint identity, and the endpoint appears in
`critical_missing`. Optional empty funding/positioning annotations remain
unavailable without being falsely reported as critical failures. Downstream
causal, alignment and numeric validation remain in place.

Eleven focused regression tests cover HTTP status/body behavior, both Retry-After
formats and invalid guidance, cooldown expiry and origin isolation, long bans,
transient retry bounds, malformed responses, and required/optional empty series.
All 165 tests and specification validation pass on this main-based branch.

A live `NOTACOIN` probe returned DATA_STALE / NO_ORDER with no setup update.
Binance 400 errors failed after one attempt and retained `-1121`; empty OI/taker
responses became named critical collection errors. Kraken's provider error also
failed without repeated deterministic retries. The decision is committed in
`ISSUE_12_PROVIDER_ERRORS_CHECK.json`; its dedicated SQLite journal is retained at
`/tmp/tdal-issue12-invalid.sqlite`. No credentials or order routes were used.

Items 1, 5 and 2 are handled independently in PRs #14, #15 and #16. This branch
does not claim that other outstanding fixes are already deployed or merged.
