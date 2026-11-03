# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

RISSK generates a **Unit Risk Score (URS, 0–100)** for each interview in a [Survey Solutions](https://mysurvey.solutions/) export, flagging interviews likely to contain unwanted interviewer behaviour. It is a Kedro pipeline that ingests Survey Solutions ZIP exports (Main Survey Data + Paradata), engineers features from microdata and paradata, and runs anomaly-detection models to produce the score. See `README.md` for the methodology and `FEATURES_SCORES.md` for per-feature definitions.

## Repository layout — single `rissk` package

This is a standalone Kedro project rooted at the repo root: `pyproject.toml` (with `[tool.kedro]`), `conf/`, `tests/`, `app/` (GUI), and the package under `src/rissk/`. One package holds **both** the Kedro project *and* the ML/data logic:

- **Kedro project** — `src/rissk/pipelines/`, `datasets/`, `pipeline_registry.py`, `settings.py`; config under `conf/`; the GUI under `app/`.
- **ML / data logic** (where the real work lives — feature engineering, scoring functions, Survey Solutions import) — `src/rissk/core/` (`feature_processing.py`, `item_processing.py`, `unit_processing.py`, `detection_algorithms.py`) and `src/rissk/utils/` (`import_utils.py`, `file_process_utils.py`, `stats_utils.py`, plus `storage.py` — the s3/local zip-staging helper).

**Nodes are thin wrappers.** Each `src/rissk/pipelines/*/nodes.py` imports functions from `rissk.core.*` / `rissk.utils.*` and adapts them to Kedro's input/output contract (logging, empty-data guards). When changing pipeline *behaviour*, edit the underlying module; edit the node only for wiring, logging, or guards.

> History: the ML logic and the Kedro project used to be two packages (`rissk` + `rissk_kedro`) in a uv workspace, and the modules carried a `*_kedro.py` suffix (distinguishing them from a since-removed legacy Ploomber pipeline). Both the workspace split and the suffix are gone — it's one `rissk` package now (the conda env + Jupyter kernel keep the name `rissk_kedro`). `Kedro_vs_Legacy_Changelog.md` still documents every intentional behavioural difference vs that removed legacy code — consult it before "fixing" something that looks deliberately changed (e.g. NaN-vs-0 score init, GPS unit conversions, sentinel filtering).

## Commands

All dependency and run commands assume the repo root unless noted.

```bash
# Install — the rissk package + GUI & viz extras.
uv sync --extra gui --extra viz

# Launch the GUI (NiceGUI, opens http://localhost:8080)
bash run_gui.sh            # macOS / Linux
run_gui.bat                # Windows

# Run the pipeline from the CLI (from the repo root).
# --env <config> is REQUIRED: it selects a committed config env (conf/<config>/globals.yml);
# a bare `kedro run` uses the empty conf/base defaults and processes no data.
kedro run --env <config>                              # full pipeline (__default__)
kedro run --env <config> --pipeline data_ingestion    # individual stages:
kedro run --env <config> --pipeline feature_creation  #   data_ingestion → feature_creation → rissk_scoring
kedro run --env <config> --pipeline rissk_scoring

# Visualise the pipeline DAG
kedro viz

# Explore scores interactively (marimo, opens a browser) — needs the viz extra
uv run marimo edit notebooks/viz/unit_scores.py   # also: feature_scores.py, interview_scores.py

# Tests (pytest is configured via the `test` extra; testpaths = tests)
uv sync --extra test
pytest               # run all
pytest tests/path/to/test_x.py::test_name   # single test
```

`kedro` commands run **from the repo root** (where `pyproject.toml` declares `[tool.kedro]`). conda users: the equivalent install is `conda env create -f environment.yml && conda activate rissk_kedro`.

## Architecture

### Pipeline graph and data stages

Three pipelines run in sequence (registered in `src/rissk/pipeline_registry.py`):

```
data_ingestion → feature_creation → rissk_scoring
```

Data flows **only** through the Kedro Data Catalog (`conf/base/catalog.yml`) — nodes are pure functions with no shared state; no node does its own `to_csv`/`open`. Datasets are keyed by `<survey>`; `10_RAW` (zips + extraction) lives under the always-local `work_root`, stages 20→40 under `output_root` (local or `s3://`):

```
<work_root>/<survey>/latest/10_RAW/   # staged ZIPs + extracted folders (PathDataset)
<output_root>/<survey>/latest/
    20_INTERIM/    # raw paradata/microdata, base feature tables
    30_PROCESSED/  # processed paradata/microdata, final feature tables
    35_SCORES/     # item_scores.parquet, unit_rissk_scores.csv  ← final output
```

`src/rissk/datasets/path.py` defines `PathDataset`, a custom dataset that returns a `Path` (used so the unzip node can operate on files/dirs directly rather than loading content).

### Configuration system

Kedro `OmegaConfigLoader`. **Each run configuration is a committed env** `conf/<config>/globals.yml`, selected with `kedro run --env <config>` (`conf/base/` holds defaults + the shared catalog/parameters; `default_run_env` is `local`). There is no `conf/local` writing — pipeline, storage, and questionnaire all come from Kedro config; the only "driver" is the thin `rissk.run.run_survey` orchestrator (see below), which just loops questionnaires and injects them as runtime params.

- **`globals.yml`** (per env) — three storage roots whose *values* pick the mode (`input_root` = zip source, local or `s3://<bucket>`; `work_root` = always-local staging; `output_root` = stages 20→40, local or `s3://`), the `survey` folder, and the active `questionnaire` (`name`, `VERSION`, `filter_var`) — **omitted** in multi-questionnaire envs, which instead declare each questionnaire under `conf/<env>/questionnaires/`. Combinations of the roots give local / s3in / s3out / s3.
- **`parameters.yml`** (base) — the `features:` map (each feature `use:` flag + optional `parameters.contamination`; `auto` or a float 0.01–0.50), `zip_password`, and `input_root`/`survey`/`work_root` exposed to the staging node.

**Two ways to run.** A survey env either holds a **single** questionnaire in its `globals.yml` (`questionnaire.name/VERSION/filter_var`) — the classic one-questionnaire-per-env, outputs flat under `<survey>/latest/<stage>/` — **or** declares **several** questionnaires, one small yaml each under `conf/<env>/questionnaires/`. In the multi-questionnaire case the driver `rissk.run.run_survey(env)` iterates them, running the full pipeline once per questionnaire with the selection injected as **runtime params** (`questionnaire` + `qnr_subdir`), so each questionnaire's outputs land under a `<qnr>/` **sub-level** (`<survey>/latest/<stage>/<qnr>/…`); it then unions the per-questionnaire microdata into the survey-level `30_PROCESSED/microdata.parquet` (via `combine_survey_microdata`, a catalog load/save — no pipeline). The catalog gets the sub-level from `${runtime_params:qnr_subdir,''}`, whose **empty default keeps single-questionnaire envs byte-for-byte flat** (fully backward-compatible). `10_RAW` stays survey-level/shared (name-filtered), never subfoldered. `VERSION: []` means "all versions found". See `conf/grdslchbs_test/globals.yml` (single) and `conf/pmpmd/` (multi: `globals.yml` + `questionnaires/community.yml`, `household.yml`) for examples.

**Configuration as data.** `run_survey` also takes the configuration as a **mapping** instead of an env name — the shape of the Windmill `rissk_survey` resource: `survey`, `input_root`, `output_root`, `questionnaires` (each `name`, `VERSION`, `filter_var`), optional `zip_password`, `parameters` (top-level `parameters.yml` keys, deep-merged by Kedro over `conf/base`) and `work_root` (default `data`, relative to the working directory). `validate_survey_config` checks it (unknown keys rejected; `output_root` must be a URL such as `s3://…`, `file:///abs/path` for a local folder) and the run goes on an `env="base"` session with everything passed as **runtime params**: the catalog reads `survey`, `work_root`, `output_root` and `qnr_subdir` from runtime params first and from the env's globals as the fallback (`_`-prefixed templates at the top of `conf/base/catalog.yml`). A mapping always produces the `<qnr>/` sub-level layout plus the combine step. No env folder is read, not even `conf/local`.

**Checkout vs installed package.** The wheel (hatchling, see `pyproject.toml`) copies `conf/base` into the package as `rissk/_conf/base`, so `pip install git+https://github.com/rowsquared/rissk@<ref>` gives a rissk that runs without a checkout; `conf/base` in the repo stays the only source. `rissk.run` tells the two modes apart by that folder: in a checkout (editable install, no `src/rissk/_conf`) it bootstraps the project and chdirs to the root as before; installed, it calls `configure_project("rissk")` and starts sessions with `conf_source` = the packaged folder and `project_path` = the working directory. Env names work in a checkout only. `run_survey` sets a plain console logging config (`PLAIN_LOGGING`) — it is the headless driver; `kedro run` keeps `conf/logging.yml` — and `settings.py` disables the `kedro-telemetry` hooks.

### Feature and score dispatch

Feature creation dispatches through explicit dicts, **not** dynamic `getattr`: `ITEM_FEATURE_MAP` and `UNIT_FEATURE_MAP` in `src/rissk/core/feature_processing.py` map a feature key → a `feat_*` function. A key with no map entry is silently skipped — several features in `parameters.yml` are computed-but-not-scored or feature-only (see `Kedro_vs_Legacy_Changelog.md` §7). Item scores initialise to `np.nan` ("not evaluated"), resolved to `0` only at unit-level aggregation.

Scoring (per `README.md` "Process description"): item/unit features → Type 1/2/3 scores → aggregated via **PCA** (responsible-level) + **Isolation Forest** (unit-level), combined by normalised product, **winsorized**, and rescaled to 0–100.

### GUI

The scheduled-run path: `rissk_readme.ipynb` (repo root) is a thin shim that sets an `ENV` (one env name, or a list) and calls `rissk.run.run_survey(env)` per configuration in-process (via `KedroSession`, no subprocess). `run_survey` runs the single-questionnaire env once, or loops a multi-questionnaire env's questionnaires and then unions their microdata; per-questionnaire failures are isolated and re-raised in the summary so a bad questionnaire doesn't skip the rest. Storage is native — `output_root` may be `s3://…` so Kedro writes via `s3fs` (no `aws sync`); the local-only unzip means `stage_input_zips_node` (first node of `data_ingestion`) fetches the env's questionnaire zips (`<input_root>/<survey>/latest/10_RAW/<name>_*.zip`, local or s3) into the local `work_root` via `fsspec`, then `extract_zip_files_node` → `filter_extracted_survey_paths_node` run after it (ordered by flag outputs `input_staged`/`extracted_flag`).

Production scheduling is **Windmill** (see `windmill/README.md`): `windmill/f/rissk/run_rissk.py` is a Windmill Python script of a few lines that installs rissk from GitHub through its one `#requirements:` line (`rissk@git+https://github.com/rowsquared/rissk@kedro_pipeline`), passes the `rissk_survey` resource to `run_survey` as the configuration (see "Configuration as data" above) and exports the `s3` resource as AWS env vars. It holds no rissk logic: no validation, no config files, no logging setup. Survey configuration and credentials live in Windmill resources, **not** in committed envs — the committed `conf/<env>` folders are for local runs. The runner and the `rissk_survey` resource type are deployed to Windmill with the wmill CLI from `windmill/` (`wmill sync push`, scoped to `f/rissk/**` by `windmill/wmill.yaml`), by hand or by `.github/workflows/windmill-sync.yml` on **every** push to `kedro_pipeline`, which first rewrites the requirement line to the pushed commit so Windmill re-locks and runs exactly that commit; the script's settings (timeout, schema) live in `run_rissk.script.yaml`, not in the UI, because a push resets them.

`app/main.py` is a NiceGUI app that writes `conf/local` and shells out to `python -m kedro run`. **It still writes the old `data_root` globals schema and has NOT been migrated to the env model (`input_root`/`work_root`/`output_root`/`survey`) — it needs a rework before it will run.**

Interactive result-exploration notebooks live in `notebooks/viz/` (three [marimo](https://marimo.io) apps: `feature_scores.py`, `unit_scores.py`, `interview_scores.py`), backed by the read-only loaders in `src/rissk/viz.py`. They read the pipeline's output files only and import nothing from the ML modules — `viz.py` is the right home for *new* viz/helper code (read pipeline outputs; keep it decoupled from the feature/scoring logic).

## Project conventions

- **No Kedro hooks.** `HOOKS = ()` in `settings.py` and it stays that way — hooks caused confusion and data-lineage issues. Keep all logic in nodes (and in the `rissk` ML modules they call).
- **Static pipelines only.** Do not generate pipelines or nodes dynamically. The pipeline is a fixed DAG; multi-questionnaire handling is done by re-running with different config, not by programmatic node generation.
- **Graceful empty/missing data.** Ingestion and scoring nodes detect missing exports, header-only files, and corrupt files, logging `WARNING`/`ERROR` and returning empty DataFrames rather than crashing (see changelog §3.1). Preserve this when editing nodes.
- **Behavioural changes go in the changelog.** Any intentional divergence in feature/score values should be recorded in `Kedro_vs_Legacy_Changelog.md` with rationale.
