# TDAL: standing project instructions

@AGENTS.md

## Purpose and priorities

Trading Decision Automation Lab is an independent cryptocurrency market-analysis and decision-support project. Its intended workflow is real market data → timestamped observations → evidence-based LONG, SHORT or WAIT assessments → recorded predictions and decisions → subsequent outcome evaluation.

Trade assessments should eventually include entries, invalidation, protective and trailing exits, bounded position sizing, trading costs and risk/reward where the evidence supports those calculations. These are intended capabilities, not claims about what currently works.

Prioritise integrated, observable operational progress. When implementation is requested, work through the necessary dependencies; planning documents, isolated components and passing unit tests alone do not establish completion of an operational workflow. Do not force a trade recommendation when data or evidence is insufficient.

## Technical judgment

- Inspect the actual code, relevant specifications, tests and README before changing behaviour. Treat documented implementation choices and milestone ordering as revisable; verify claims against the implementation.
- Choose suitable venues, instruments, dependencies, analytical methods, architecture and development sequence. No particular pair, indicator or model is mandatory.
- Challenge assumptions and explain consequential trade-offs. Reuse, extend, refactor or replace components when justified; preserve a recoverable baseline rather than preserving every design choice.
- Proceed with ordinary, reversible technical decisions within the current assignment. Seek clarification only for genuine blockers or decisions that materially change the intended product or authorised scope.
- Keep responsibilities clear without unnecessary abstraction. Follow `PROJECT_BOUNDARY.md`; record deliberate adoption from other projects in `ADOPTION_LOG.md`.

## Research integrity

- Preserve the causal-data and fail-closed rules imported from `AGENTS.md`. Keep future outcome labels separate from decision inputs. Preserve enough input provenance, configuration and code-version information to reproduce evaluations.
- Keep historical, live and simulated evidence distinguishable. Do not invent missing data, calibrated confidence, predictive value or profitability.
- Separate operational correctness, predictive value and performance after realistic costs. Use evaluation methods that test decisions, including appropriate baselines and unseen periods when assessing predictive performance.

## Authority and boundaries

- The current user assignment defines the work authorised for each session; this file supplies standing context, not an instruction to start a new implementation automatically.
- For authorised implementation, use a dedicated branch, preserve unrelated work and keep changes recoverable. Local edits, appropriate dependency installation and relevant tests do not require repeated approval.
- Obtain explicit approval before destructive operations, pushing or merging changes, or accessing private credentials. Never expose secrets in output, artifacts or commits.
- Keep live order submission and real-money trading disabled. Research and paper outputs must not imply execution permission. Technical autonomy does not override these boundaries.

## Verification and handoff

Current repository checks, run from the project root:

```sh
python -m pip install -e '.[dev]'
python -m unittest discover -s tests -v
python spec/validate_specifications.py
```

Run checks appropriate to the change and update these commands if the tooling changes. For market-data or workflow changes, also verify the integrated path with actual exchange data where available; disclose network restrictions and substitute fixtures explicitly. Do not treat a fixture run as proof of live integration.

Report what changed and why, demonstrated behaviour, checks run, unresolved failures, limitations and exact remaining dependencies. For operational changes, provide commands for launching, stopping and inspecting the system. Keep README usage and relevant specifications aligned with changed behaviour. Preserve useful implementation state and next steps in the repository rather than relying on conversation memory.
