# Project boundary contract

## Identity

Project: **Trading Decision Automation Lab**  
Initial release: **project zero / v0.1**

This project's purpose is to derive an automated, inspectable trading decision process from first principles and the doctrine produced for this request. It intentionally approaches the problem from a different angle than earlier observer, predictor, backtesting, microstructure, SOL, TAO, and general Market Research Lab projects.

## Separation requirements

The following remain independent unless Savannah explicitly authorizes otherwise:

- goals and success criteria;
- rule registry and parameter values;
- source code and package identity;
- databases, raw data and experimental results;
- backtests, labels and performance claims;
- configuration and credentials;
- documentation and release history;
- deployment and operational state.

Similarity is not authorization to merge.

## Controlled adoption process

A prior idea can enter this project only through all four gates:

1. **Identify** the exact idea or implementation.
2. **Justify** how it improves correctness, safety, reliability, measurement or performance.
3. **Revalidate** it against this project's own data contracts and tests.
4. **Record** the decision and exclusions in `ADOPTION_LOG.md`.

Convenience, familiarity, code availability and saving time are not sufficient justifications.

## Prohibited inheritance

- No silent import of previous thresholds, support/resistance levels, labels or strategy scores.
- No transfer of historical profitability or validation claims.
- No shared database or state file.
- No assumption that the same symbol, venue, horizon or trading style should remain primary.
- No reuse of credentials.
- No artifact replacement across project identities.

## Change control

Any future proposal to combine projects must state the expected technical benefit, contamination risk, migration plan, rollback method and tests. Until approved, integration occurs only through documented interfaces or comparative experiments—not by combining repositories or files.
