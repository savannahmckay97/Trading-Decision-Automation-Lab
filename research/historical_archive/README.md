# Historical archive replay

Replays the production BREAKOUT_RETEST_V1 rule over Binance's public USD-M archive
(data.binance.vision), candle by candle. Data lives outside the repo.

```sh
python research/historical_archive/fetch_archive.py ~/tdal-history/TAOUSDT   # download + verify checksums
```

- `archive.py` loads the verified archive into replay inputs. It reads only files the
  manifest lists as `OK`, re-hashes each one, and rejects missing, unlisted or
  wrong-symbol files.
- `replay_archive.py` runs the rule (same assumptions as `research/setup_frequency/history.py`).
- `FIDELITY.md`: archive vs live API, and the setup count. No outcomes.
- `PREREGISTRATION.md`: the plan for the outcome analysis (frozen at `f166d3b`).
- `run.py`: the registered analysis. Checks the registered manifest fingerprint, never
  overwrites an output, writes `OUT.provenance.json` alongside. `RESULTS.md` reports it.
- `posthoc.py`: descriptive figures added after the run. **Not preregistered.**
- `fidelity_check.py`: reproduces the archive-vs-live-API comparison in `FIDELITY.md`.
