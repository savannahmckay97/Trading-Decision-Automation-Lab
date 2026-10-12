# Historical archive replay

Replays the production BREAKOUT_RETEST_V1 rule over Binance's public USD-M archive
(data.binance.vision), candle by candle. Data lives outside the repo.

```sh
python research/historical_archive/fetch_archive.py ~/tdal-history/TAOUSDT   # download + verify checksums
```

- `archive.py` loads the verified archive into replay inputs.
- `replay_archive.py` runs the rule (same assumptions as `research/setup_frequency/history.py`).
- `FIDELITY.md`: archive vs live API, and the setup count. No outcomes.
- `PREREGISTRATION.md`: the plan for the outcome analysis (draft until frozen).
