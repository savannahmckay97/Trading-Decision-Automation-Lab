# TDAL agent and code review instructions

Apply these rules to changes throughout this repository. Review for concrete violations and cite the affected code path; prioritize findings that could alter a market interpretation or trading decision over style.

1. **Preserve causal, trustworthy market data.** A decision may use only information available at its decision time. Check completed-candle gating, event and receipt timestamps, freshness, chronological replay, duplicate or missing bars, venue and symbol identity, units, currency, precision, and calculations. Flag look-ahead, stale or mixed live/cached/historical data, and silent API or normalization failures that could yield a plausible but false result.

2. **Fail closed at the observation boundary.** Missing, malformed, discontinuous, or unconfirmed required inputs must block eligibility instead of being silently substituted. Repeated polling or a restart must not invent state transitions. The current observer must remain observation only: no order route, credential use, or implied execution permission. Assess any proposed execution capability against an explicit new authorization and its own risk controls.

3. **Keep this project independent.** Follow `PROJECT_BOUNDARY.md`. Do not import earlier projects' thresholds, rules, data, state, results, credentials, or performance claims by similarity or convenience. Any deliberate adoption must be justified, revalidated against TDAL's contracts and tests, and recorded in `ADOPTION_LOG.md`.

When reporting a finding, explain the failure mode and its decision impact. Do not elevate cosmetic preferences to the severity of a correctness or safety error.
