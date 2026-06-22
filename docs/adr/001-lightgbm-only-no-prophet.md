# ADR 001 — Use LightGBM only; remove Prophet from the training pipeline

**Status:** Accepted  
**Date:** 2026-06-22  
**Deciders:** Engineering team  

---

## Context

The forecasting pipeline was initially designed to train two models per segment — Facebook Prophet and LightGBM — then deploy whichever achieved a lower hold-out MAPE. Prophet is well-regarded for time-series with strong seasonality and holiday effects and was included as a natural complement to LightGBM's tabular gradient-boosting approach.

During containerised deployment, every Prophet training call failed with:

```
'Prophet' object has no attribute 'stan_backend'
```

Prophet 1.1.x migrated its Stan backend from PyStan to CmdStanPy. CmdStanPy requires a compiled CmdStan binary to be installed separately (`cmdstanpy.install_cmdstan()`). The `python:3.11-slim` base image used for the API container does not include this binary, and adding it would require a multi-stage build, a ~200 MB addition to the image, and a non-trivial runtime install step during container startup.

Meanwhile, LightGBM on the same data achieved **10.3% MAPE** on a 30-day hold-out across the global segment — within the acceptable range for CPG demand forecasting (industry benchmark: <15% MAPE).

---

## Decision

Remove Prophet from all default model lists. LightGBM is the sole deployed model.

Specifically:
- `TrainingRequest.model_names` defaults to `["lightgbm"]`
- The schema validator silently strips `"prophet"` from any incoming request
- `run_training_pipeline()` strips `"prophet"` from `model_names` at runtime as a defence-in-depth measure
- `_get_model("prophet")` raises a `ValueError` with a clear message rather than attempting instantiation
- `prophet==1.1.6` remains in `pyproject.toml` so the dependency is visible, but CmdStan is not installed

---

## Alternatives considered

| Option | Outcome |
|---|---|
| Install CmdStan in the Docker image | Adds ~200 MB, complicates the build, and requires a runtime install step that can fail; deferred to future work |
| Pin Prophet to an older PyStan-based version (≤1.0.1) | PyStan 3.x also has breaking changes; version matrix fragile |
| Use a pre-built Prophet Docker base image | Introduces a dependency on a third-party maintained image; increases supply-chain risk |
| Keep Prophet but catch the error and continue | Already done implicitly — but the error still appears in logs on every training run, confusing operators |

---

## Consequences

**Positive:**
- Training pipeline is reliable and fully reproducible in the slim container
- No unexplained errors in production logs
- LightGBM trains in seconds per segment; Prophet took 30–90 seconds per segment even when working

**Negative:**
- Loss of Prophet's interpretable trend/seasonality decomposition components (`trendComponent`, `seasonalWeekly`, `seasonalYearly` columns in `forecast_results` are always `null`)
- No holiday-effect modelling (LightGBM uses calendar features as engineered columns instead)

**Future work:**
- Add a `Dockerfile.api.prophet` variant with CmdStan pre-installed for teams that want Prophet
- Re-evaluate `neuralprophet` as a drop-in replacement (pure PyTorch, no Stan dependency)
