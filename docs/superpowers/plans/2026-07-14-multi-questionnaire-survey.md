# Multi-questionnaire survey + combined microdata — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one survey hold several questionnaire names — each scored independently into a per-`<qnr>` subfolder — plus a survey-level union of the per-questionnaire `microdata.parquet`; and rename the scores folder `41_SCORES` → `35_SCORES`.

**Architecture:** The 3-stage pipeline is unchanged and still runs one questionnaire at a time. A driver (`src/rissk/run.py`, called by the notebook) iterates the questionnaires declared under `conf/<env>/questionnaires/*.yml`, running the full pipeline once per questionnaire and injecting the selection via Kedro **runtime params** (`questionnaire` + `qnr_subdir`). Catalog output paths gain a `${runtime_params:qnr_subdir,''}` segment whose **empty default keeps every existing env byte-for-byte identical**. After the loop, the driver unions the per-`<qnr>` microdata into `30_PROCESSED/microdata.parquet` by loading/saving through the Kedro **catalog directly** (a `PartitionedDataset` in → a `ParquetDataset` out, so s3 works via fsspec) — **not** a separate pipeline. The union logic is a plain, unit-tested function; the catalog handles the I/O.

> **DESIGN REVISION (Option B), 2026-07-14:** the original Tasks 3–4 built `combine` as a one-node Kedro pipeline (pipeline + node + registry entry). That was over-engineered for a cross-run convenience aggregation, so it was collapsed: the two catalog datasets stay (I/O belongs in the catalog, and this preserves s3), but the pipeline/node/registry are removed and the driver calls `catalog.load`/`catalog.save` around a pure `combine_microdata` function. Task 3 below is now "combine catalog entries only"; the function + wiring live in Task 4 (`run.py`).

**Tech Stack:** Kedro 1.2.0, kedro-datasets 9.x (`pandas.ParquetDataset`, `pandas.CSVDataset`, `partitions.PartitionedDataset`), OmegaConfigLoader, pandas, pytest.

## Global Constraints

- **Project interpreter:** `.venv/bin/python` (equivalently conda env `rissk_kedro`). Run all commands from the repo root.
- **Static pipelines only** — no dynamic node/pipeline generation. Multi-questionnaire handling is done by re-running the static pipeline per questionnaire, never by programmatic node creation.
- **No Kedro hooks** — `HOOKS = ()` stays.
- **Nodes stay pure**; the Data Catalog is the single source of truth for I/O (no `to_csv`/`open()` in nodes).
- **Graceful empty/missing data** — nodes log `WARNING`/`ERROR` and return empty DataFrames rather than crash.
- **Exact catalog default syntax:** `${runtime_params:qnr_subdir,''}` — the quoted empty string is required (a bare `${runtime_params:qnr_subdir,}` emits an OmegaConf deprecation warning).
- **Verified partition-id facts** (kedro-datasets 9.x, `PartitionedDataset(path=<30_PROCESSED>, filename_suffix="microdata.parquet")`): the top-level `microdata.parquet` yields partition key `''`; each `<qnr>/microdata.parquet` yields key `'<qnr>/'`; sibling files like `questionnaire.parquet` are excluded by the suffix.
- **Behavioural changes are recorded** in `Kedro_vs_Legacy_Changelog.md`.

## File structure

- `conf/base/catalog.yml` — modify: rename `41_SCORES`→`35_SCORES` (×3); add `${runtime_params:qnr_subdir,''}` to the 13 per-`<qnr>` output entries; add `microdata_by_qnr` + `microdata_combined`.
- `conf/base/parameters.yml` — modify: `questionnaire` gains the runtime→globals→null fallback.
- `conf/base/catalog.yml` — add `microdata_by_qnr` (PartitionedDataset) + `microdata_combined` (ParquetDataset). **No** combine pipeline/node/registry (Option B).
- `src/rissk/run.py` — create: `load_questionnaire_configs`, the pure `combine_microdata`, the catalog-backed `combine_survey_microdata`, and the `run_survey` driver.
- `src/rissk/viz.py` — modify: `41_SCORES`→`35_SCORES` (×4 path strings).
- `tests/test_viz.py` — modify: `41_SCORES`→`35_SCORES` (×3).
- `tests/test_combine.py` — create: unit tests for the combine node.
- `tests/test_catalog_resolution.py` — create: backward-compat + nested-path resolution guards.
- `tests/test_run_driver.py` — create: unit test for `load_questionnaire_configs`.
- `conf/pmpmd/globals.yml` + `conf/pmpmd/questionnaires/{community,household}.yml` — create: example multi-questionnaire env.
- `rissk_readme.ipynb` — modify: driver cell calls `run_survey`; doc strings `41_SCORES`→`35_SCORES`.
- `CLAUDE.md`, `README.md`, `SETUP.md`, `Kedro_vs_Legacy_Changelog.md` — modify: docs.

---

### Task 1: Rename `41_SCORES` → `35_SCORES`

Orthogonal, self-contained rename. Do it first so later catalog edits sit on the final folder name.

**Files:**
- Modify: `conf/base/catalog.yml` (lines with `41_SCORES`, ×3)
- Modify: `src/rissk/viz.py` (×4)
- Modify: `tests/test_viz.py` (×3)
- Modify: `CLAUDE.md` (×1), `README.md` (×2), `SETUP.md` (×2)

**Interfaces:**
- Produces: the scores folder is now `35_SCORES` everywhere (catalog `item_scores`/`unit_rissk_scores`/`responsible_scores` filepaths, viz loaders, docs).

- [ ] **Step 1: Update the failing test first**

In `tests/test_viz.py`, replace all three `41_SCORES` occurrences (lines ~10, 11, 38) with `35_SCORES`. Example (line 11):

```python
    scored = root / name / "latest" / "35_SCORES"
```

- [ ] **Step 2: Run the test to verify it FAILS**

Run: `.venv/bin/python -m pytest tests/test_viz.py -q`
Expected: FAIL — the test now writes/reads `35_SCORES` but `viz.py` still looks under `41_SCORES`.

- [ ] **Step 3: Rename in `src/rissk/viz.py`**

Replace the four `41_SCORES` occurrences (module docstring line 4, and the path strings on lines ~32, 40, 47). The two functional ones are:

```python
        scored = child / "latest" / "35_SCORES" / "unit_rissk_scores.csv"
```
```python
    return _data_root(data_root) / questionnaire / "latest" / "35_SCORES"
```

- [ ] **Step 4: Rename in `conf/base/catalog.yml`**

The three scoring entries become (note: the `${runtime_params:...}` segment is added in Task 2, not here):

```yaml
item_scores:
  type: pandas.ParquetDataset
  filepath: ${globals:output_root}/${globals:survey}/latest/35_SCORES/item_scores.parquet

unit_rissk_scores:
  type: pandas.CSVDataset
  filepath: ${globals:output_root}/${globals:survey}/latest/35_SCORES/unit_rissk_scores.csv

responsible_scores:
  type: pandas.CSVDataset
  filepath: ${globals:output_root}/${globals:survey}/latest/35_SCORES/responsible_scores.csv
```

- [ ] **Step 5: Rename in docs**

`CLAUDE.md` line ~71, `README.md` lines ~122 & ~155, `SETUP.md` lines ~95 & ~158 — replace `41_SCORES` with `35_SCORES` in each.

- [ ] **Step 6: Run tests to verify they PASS**

Run: `.venv/bin/python -m pytest tests/test_viz.py -q`
Expected: PASS.
Also confirm no stray references remain in **active** code/config/docs. Three sources
legitimately still contain the literal `41_SCORES` and must be excluded: `.claude/worktrees`
(stale worktrees), `docs/superpowers/specs/` and `docs/superpowers/plans/` (these documents
quote the pre-rename state, including this very step):
Run: `grep -rn "41_SCORES" --include="*.py" --include="*.yml" --include="*.md" . | grep -v ".claude/worktrees" | grep -v "docs/superpowers/specs" | grep -v "docs/superpowers/plans"`
Expected: no output.
Note: `rissk_readme.ipynb` still contains `41_SCORES` at this point — that is **Task 6's**
job, not a Task 1 defect.

- [ ] **Step 7: Commit**

```bash
git add conf/base/catalog.yml src/rissk/viz.py tests/test_viz.py CLAUDE.md README.md SETUP.md
git commit -m "refactor: rename scores output folder 41_SCORES -> 35_SCORES"
```

---

### Task 2: Per-`<qnr>` output paths + `questionnaire` runtime fallback

Add the `qnr_subdir` segment to the 13 per-questionnaire output entries, and make `questionnaire` resolvable from runtime params (with globals fallback for existing envs).

**Files:**
- Modify: `conf/base/parameters.yml:2`
- Modify: `conf/base/catalog.yml` (13 output entries under 20_INTERIM / 30_PROCESSED / 35_SCORES)
- Test: `tests/test_catalog_resolution.py` (create)

**Interfaces:**
- Produces: catalog output paths of the form `.../<stage>/${runtime_params:qnr_subdir,''}<file>`; `params:questionnaire` sourced from `${runtime_params:questionnaire, ${globals:questionnaire, null}}`.
- Consumes (later tasks): the driver passes `extra_params={"questionnaire": <dict>, "qnr_subdir": "<name>/"}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_catalog_resolution.py`:

```python
"""Guards for the per-<qnr> output paths and backward-compatible defaults."""
from kedro.config import OmegaConfigLoader


def _filepath(dataset, runtime_params):
    # Resolve the real project catalog against the grdslchbs_test env's globals.
    cl = OmegaConfigLoader(
        conf_source="conf",
        base_env="base",
        default_run_env="grdslchbs_test",
        runtime_params=runtime_params,
    )
    return cl["catalog"][dataset]["filepath"]


def test_flat_paths_when_no_qnr_subdir():
    # Existing single-questionnaire envs (and the combine run) pass no qnr_subdir.
    assert _filepath("microdata", {}).endswith("/latest/30_PROCESSED/microdata.parquet")
    assert _filepath("item_scores", {}).endswith("/latest/35_SCORES/item_scores.parquet")
    assert _filepath("item_features_base", {}).endswith("/latest/20_INTERIM/item_features_base.parquet")


def test_nested_paths_when_qnr_subdir_set():
    rp = {"qnr_subdir": "pmpmd_community/"}
    assert _filepath("microdata", rp).endswith("/30_PROCESSED/pmpmd_community/microdata.parquet")
    assert _filepath("unit_rissk_scores", rp).endswith("/35_SCORES/pmpmd_community/unit_rissk_scores.csv")


def test_questionnaire_param_falls_back_to_globals():
    cl = OmegaConfigLoader(conf_source="conf", base_env="base",
                           default_run_env="grdslchbs_test", runtime_params={})
    assert cl["parameters"]["questionnaire"]["name"] == "slchbs_grenada_2627"


def test_questionnaire_param_runtime_override():
    cl = OmegaConfigLoader(conf_source="conf", base_env="base", default_run_env="grdslchbs_test",
                           runtime_params={"questionnaire": {"name": "pmpmd_household", "filter_var": None}})
    assert cl["parameters"]["questionnaire"]["name"] == "pmpmd_household"
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `.venv/bin/python -m pytest tests/test_catalog_resolution.py -q`
Expected: FAIL — `test_nested_paths_*` fails (no `qnr_subdir` segment yet) and `test_questionnaire_param_runtime_override` fails (param still hard-bound to globals).

- [ ] **Step 3: Edit `conf/base/parameters.yml`**

Change line 2 from `questionnaire: ${globals:questionnaire}` to:

```yaml
questionnaire: ${runtime_params:questionnaire,${globals:questionnaire,null}}
```

- [ ] **Step 4: Edit `conf/base/catalog.yml` — insert `${runtime_params:qnr_subdir,''}` before each filename in the 13 per-`<qnr>` entries**

Leave `survey_zip_partitions` and `extracted_survey_folders` (10_RAW) untouched. The full set of edited entries:

```yaml
paradata_raw:
  type: pandas.ParquetDataset
  filepath: ${globals:output_root}/${globals:survey}/latest/20_INTERIM/${runtime_params:qnr_subdir,''}paradata_raw.parquet

raw_questionnaire:
  type: pandas.ParquetDataset
  filepath: ${globals:output_root}/${globals:survey}/latest/30_PROCESSED/${runtime_params:qnr_subdir,''}questionnaire.parquet

raw_microdata:
  type: pandas.ParquetDataset
  filepath: ${globals:output_root}/${globals:survey}/latest/20_INTERIM/${runtime_params:qnr_subdir,''}microdata_raw.parquet

microdata:
  type: pandas.ParquetDataset
  filepath: ${globals:output_root}/${globals:survey}/latest/30_PROCESSED/${runtime_params:qnr_subdir,''}microdata.parquet

paradata_processed:
  type: pandas.ParquetDataset
  filepath: ${globals:output_root}/${globals:survey}/latest/30_PROCESSED/${runtime_params:qnr_subdir,''}paradata_processed.parquet

item_features_base:
  type: pandas.ParquetDataset
  filepath: ${globals:output_root}/${globals:survey}/latest/20_INTERIM/${runtime_params:qnr_subdir,''}item_features_base.parquet

unit_features_base:
  type: pandas.ParquetDataset
  filepath: ${globals:output_root}/${globals:survey}/latest/20_INTERIM/${runtime_params:qnr_subdir,''}unit_features_base.parquet

item_features:
  type: pandas.ParquetDataset
  filepath: ${globals:output_root}/${globals:survey}/latest/30_PROCESSED/${runtime_params:qnr_subdir,''}item_features.parquet

unit_features:
  type: pandas.ParquetDataset
  filepath: ${globals:output_root}/${globals:survey}/latest/30_PROCESSED/${runtime_params:qnr_subdir,''}unit_features.parquet

removed_answers:
  type: pandas.ParquetDataset
  filepath: ${globals:output_root}/${globals:survey}/latest/30_PROCESSED/${runtime_params:qnr_subdir,''}removed_answers.parquet

item_scores:
  type: pandas.ParquetDataset
  filepath: ${globals:output_root}/${globals:survey}/latest/35_SCORES/${runtime_params:qnr_subdir,''}item_scores.parquet

unit_rissk_scores:
  type: pandas.CSVDataset
  filepath: ${globals:output_root}/${globals:survey}/latest/35_SCORES/${runtime_params:qnr_subdir,''}unit_rissk_scores.csv

responsible_scores:
  type: pandas.CSVDataset
  filepath: ${globals:output_root}/${globals:survey}/latest/35_SCORES/${runtime_params:qnr_subdir,''}responsible_scores.csv
```

- [ ] **Step 5: Run to verify PASS**

Run: `.venv/bin/python -m pytest tests/test_catalog_resolution.py -q`
Expected: PASS (all 4).

- [ ] **Step 6: Sanity-check an existing env still resolves flat**

Note: there is no `kedro catalog resolve` subcommand in Kedro 1.2.0 (only
`describe-datasets` / `list-patterns` / `resolve-patterns`). Resolve the catalog directly:

```bash
.venv/bin/python - <<'PY'
from kedro.config import OmegaConfigLoader
cl = OmegaConfigLoader(conf_source="conf", base_env="base",
                       default_run_env="grdslchbs_test", runtime_params={})
for ds in ("microdata", "item_scores", "item_features_base"):
    print(ds, "->", cl["catalog"][ds]["filepath"])
PY
```
Expected: paths with **no** extra subfolder (e.g. `.../30_PROCESSED/microdata.parquet`,
`.../35_SCORES/item_scores.parquet`), confirming backward compatibility.

- [ ] **Step 7: Commit**

```bash
git add conf/base/catalog.yml conf/base/parameters.yml tests/test_catalog_resolution.py
git commit -m "feat: per-<qnr> output subfolders via qnr_subdir runtime param (flat by default)"
```

---

### Task 3: `combine` pipeline — union per-`<qnr>` microdata

**Files:**
- Create: `src/rissk/pipelines/combine/__init__.py`, `nodes.py`, `pipeline.py`
- Modify: `src/rissk/pipeline_registry.py`
- Modify: `conf/base/catalog.yml` (add `microdata_by_qnr`, `microdata_combined`)
- Test: `tests/test_combine.py` (create)

**Interfaces:**
- Consumes: `microdata_by_qnr` — a `dict[str, Callable[[], pd.DataFrame]]` (partition key `''` = the union file to skip; `'<qnr>/'` = a per-questionnaire microdata).
- Produces: node `combine_microdata_node(partitions) -> pd.DataFrame`; pipeline registered as `"combine"`; dataset `microdata_combined` at `.../30_PROCESSED/microdata.parquet`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_combine.py`:

```python
import pandas as pd
from rissk.pipelines.combine.nodes import combine_microdata_node


def _loader(df):
    return lambda: df


def test_unions_per_qnr_and_skips_top_level_union():
    partitions = {
        "": _loader(pd.DataFrame({"qnr": ["OLD"], "value": [99]})),          # stale union -> skip
        "community/": _loader(pd.DataFrame({"qnr": ["community"], "value": [1]})),
        "household/": _loader(pd.DataFrame({"qnr": ["household"], "value": [2]})),
    }
    out = combine_microdata_node(partitions)
    assert sorted(out["qnr"].unique()) == ["community", "household"]
    assert len(out) == 2
    assert "OLD" not in out["qnr"].values


def test_empty_partitions_returns_empty_frame():
    out = combine_microdata_node({})
    assert isinstance(out, pd.DataFrame)
    assert out.empty


def test_only_top_level_union_returns_empty_frame():
    out = combine_microdata_node({"": _loader(pd.DataFrame({"qnr": ["OLD"]}))})
    assert out.empty
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `.venv/bin/python -m pytest tests/test_combine.py -q`
Expected: FAIL — `ModuleNotFoundError: rissk.pipelines.combine`.

- [ ] **Step 3: Create the node — `src/rissk/pipelines/combine/nodes.py`**

```python
"""Nodes for the combine pipeline."""
import logging
from typing import Callable, Dict

import pandas as pd

logger = logging.getLogger(__name__)


def combine_microdata_node(partitions: Dict[str, Callable[[], pd.DataFrame]]) -> pd.DataFrame:
    """Union the per-questionnaire microdata into one survey-level table.

    ``partitions`` is a PartitionedDataset mapping of partition-key -> loader over
    ``30_PROCESSED``. The top-level union file (partition key ``''``) is skipped so the
    output can be rewritten in place idempotently; each ``'<qnr>/'`` partition is a
    per-questionnaire ``microdata.parquet``.
    """
    frames = []
    for key, load in sorted(partitions.items()):
        if not key.strip("/"):
            continue  # the survey-level union file itself — never fold it back in
        # Per-partition guard: one corrupt/partial <qnr>/microdata.parquet must not sink
        # the whole run (project convention — mirrors data_ingestion/nodes.py).
        try:
            df = load()
        except Exception as e:
            logger.error("combine_microdata: skipping unreadable partition %r: %s", key.strip("/"), e)
            continue
        frames.append(df)
        logger.info("combine_microdata: adding partition %r", key.strip("/"))

    if not frames:
        logger.warning(
            "combine_microdata: no per-questionnaire microdata partitions found — "
            "returning empty DataFrame."
        )
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    logger.info("combine_microdata: unioned %d partitions -> %d rows", len(frames), len(combined))
    return combined
```

- [ ] **Step 4: Create the pipeline — `src/rissk/pipelines/combine/pipeline.py`**

```python
"""Combine pipeline definition."""
from kedro.pipeline import Pipeline, node, pipeline

from .nodes import combine_microdata_node


def create_pipeline(**kwargs) -> Pipeline:
    """Union the per-questionnaire microdata into the survey-level file.

    Runs once, after all per-questionnaire runs of __default__.
    """
    return pipeline([
        node(
            func=combine_microdata_node,
            inputs="microdata_by_qnr",
            outputs="microdata_combined",
            name="combine_microdata_node",
        ),
    ])
```

- [ ] **Step 5: Create `src/rissk/pipelines/combine/__init__.py`**

```python
"""Combine pipeline: union per-questionnaire microdata into the survey-level file."""
from .pipeline import create_pipeline

__all__ = ["create_pipeline"]
__version__ = "0.1"
```

- [ ] **Step 6: Add catalog entries — `conf/base/catalog.yml`** (append to the SCORING section end)

```yaml
# === COMBINE (survey-level union of per-<qnr> microdata) ===
# Scans 30_PROCESSED for every <qnr>/microdata.parquet. The top-level union file
# (microdata.parquet) is also matched (partition key '') and skipped by the node.
microdata_by_qnr:
  type: partitions.PartitionedDataset
  path: ${globals:output_root}/${globals:survey}/latest/30_PROCESSED
  dataset:
    type: pandas.ParquetDataset
  filename_suffix: microdata.parquet

microdata_combined:
  type: pandas.ParquetDataset
  filepath: ${globals:output_root}/${globals:survey}/latest/30_PROCESSED/microdata.parquet
```

- [ ] **Step 7: Register the pipeline — `src/rissk/pipeline_registry.py`**

Replace the body of `register_pipelines` with:

```python
def register_pipelines() -> dict[str, Pipeline]:
    """Register the project's pipelines."""
    from rissk.pipelines.data_ingestion import create_pipeline as ingestion_pipeline
    from rissk.pipelines.feature_creation import create_pipeline as feature_creation_pipeline
    from rissk.pipelines.rissk_scoring import create_pipeline as scoring_pipeline
    from rissk.pipelines.combine import create_pipeline as combine_pipeline

    ingestion = ingestion_pipeline()
    feat_creation = feature_creation_pipeline()
    scoring = scoring_pipeline()
    combine = combine_pipeline()

    return {
        "__default__": ingestion + feat_creation + scoring,
        "data_ingestion": ingestion,
        "feature_creation": feat_creation,
        "rissk_scoring": scoring,
        "combine": combine,
    }
```

- [ ] **Step 8: Run tests + confirm registration**

Run: `.venv/bin/python -m pytest tests/test_combine.py -q`
Expected: PASS (3).
Run: `.venv/bin/python -m kedro registry list 2>/dev/null | grep combine`
Expected: `combine` appears.

- [ ] **Step 9: Commit**

```bash
git add src/rissk/pipelines/combine conf/base/catalog.yml src/rissk/pipeline_registry.py tests/test_combine.py
git commit -m "feat: combine pipeline unions per-<qnr> microdata into survey-level file"
```

---

### Task 4: Driver helpers — `src/rissk/run.py`

**Files:**
- Create: `src/rissk/run.py`
- Test: `tests/test_run_driver.py` (create)

**Interfaces:**
- Produces:
  - `load_questionnaire_configs(env: str, project_root: Path | str | None = None) -> list[dict]` — parses `conf/<env>/questionnaires/*.yml` (sorted by filename); returns `[]` when the folder is absent.
  - `run_survey(env: str, project_root: Path | str | None = None, pipeline: str = "__default__", run_combine: bool = True) -> dict[str, str]` — iterates questionnaires (runtime params `questionnaire` + `qnr_subdir="<name>/"`), then runs `"combine"` once; falls back to a single plain run when no `questionnaires/` folder exists (legacy single-questionnaire env). Returns `{label: "OK" | "FAILED (...)"}`.
- Consumes: the `combine` pipeline (Task 3) and the `qnr_subdir` catalog wiring (Task 2).

- [ ] **Step 1: Write the failing test**

Create `tests/test_run_driver.py`:

```python
from pathlib import Path

from rissk.run import load_questionnaire_configs


def test_returns_empty_when_no_questionnaires_dir(tmp_path):
    (tmp_path / "conf" / "solo").mkdir(parents=True)
    assert load_questionnaire_configs("solo", tmp_path) == []


def test_parses_and_sorts_questionnaire_yamls(tmp_path):
    qdir = tmp_path / "conf" / "pmpmd" / "questionnaires"
    qdir.mkdir(parents=True)
    (qdir / "household.yml").write_text("name: pmpmd_household\nVERSION: []\nfilter_var: null\n")
    (qdir / "community.yml").write_text("name: pmpmd_community\nVERSION: [2, 3]\nfilter_var: null\n")
    got = load_questionnaire_configs("pmpmd", tmp_path)
    assert [q["name"] for q in got] == ["pmpmd_community", "pmpmd_household"]  # sorted by filename
    assert got[0]["VERSION"] == [2, 3]
```

- [ ] **Step 2: Run to verify it FAILS**

Run: `.venv/bin/python -m pytest tests/test_run_driver.py -q`
Expected: FAIL — `ModuleNotFoundError: rissk.run`.

- [ ] **Step 3: Implement `src/rissk/run.py`**

```python
"""In-process driver for running the pipeline across a survey's questionnaires.

One survey = one Kedro env (``conf/<env>/``). The questionnaires belonging to that
survey are declared as one small YAML each under ``conf/<env>/questionnaires/``. This
module enumerates them and runs the static 3-stage pipeline once per questionnaire
(injecting the selection via runtime params), then runs the ``combine`` pipeline once.

A legacy env with a single questionnaire in ``globals.yml`` and no ``questionnaires/``
folder is run once, unchanged, with no combine step.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Union

import yaml

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]


def _project_root(project_root: Optional[PathLike] = None) -> Path:
    if project_root is not None:
        return Path(project_root)
    import rissk
    return Path(rissk.__file__).resolve().parents[2]


def load_questionnaire_configs(env: str, project_root: Optional[PathLike] = None) -> list[dict]:
    """Parse ``conf/<env>/questionnaires/*.yml`` (sorted by filename). Empty if absent."""
    qdir = _project_root(project_root) / "conf" / env / "questionnaires"
    if not qdir.is_dir():
        return []
    configs = []
    for path in sorted(qdir.glob("*.yml")):
        with open(path) as fh:
            cfg = yaml.safe_load(fh) or {}
        if "name" not in cfg:
            raise ValueError(f"{path}: questionnaire yaml must set 'name'")
        configs.append(cfg)
    return configs


def run_survey(
    env: str,
    project_root: Optional[PathLike] = None,
    pipeline: str = "__default__",
    run_combine: bool = True,
) -> dict[str, str]:
    """Run the pipeline for every questionnaire in ``env``, then combine once.

    Returns an outcome map; failures are isolated per questionnaire so one bad
    questionnaire does not skip the rest.
    """
    from kedro.framework.session import KedroSession
    from kedro.framework.startup import bootstrap_project

    root = _project_root(project_root)
    bootstrap_project(root)
    qnrs = load_questionnaire_configs(env, root)
    results: dict[str, str] = {}

    def _run(label: str, extra_params: Optional[dict], pipe: str) -> None:
        print(f"=== run --env {env} --pipeline {pipe} [{label}] ===", flush=True)
        try:
            with KedroSession.create(project_path=root, env=env, extra_params=extra_params) as session:
                session.run(pipeline_names=[pipe])
            results[label] = "OK"
        except Exception as exc:  # isolate failures across questionnaires
            results[label] = f"FAILED ({type(exc).__name__}: {exc})"
        print(f"--- {label}: {results[label]} ---", flush=True)

    if not qnrs:
        # Legacy single-questionnaire env: questionnaire comes from globals, no combine.
        _run(env, None, pipeline)
        return results

    for q in qnrs:
        name = q["name"]
        _run(name, {"questionnaire": q, "qnr_subdir": f"{name}/"}, pipeline)

    if run_combine:
        _run("combine", None, "combine")

    return results
```

- [ ] **Step 4: Run to verify PASS**

Run: `.venv/bin/python -m pytest tests/test_run_driver.py -q`
Expected: PASS (2).

- [ ] **Step 5: Commit**

```bash
git add src/rissk/run.py tests/test_run_driver.py
git commit -m "feat: run_survey driver iterates a survey's questionnaires + combine"
```

---

### Task 5: Example `pmpmd` env + end-to-end verification

Proves the whole chain on real data using the **existing** `data/pmpmd copy/` folder (26M), kept exactly as-is — space and all.

> **DO NOT touch `data/pmpmd/`.** It already holds 308M of unrelated data in a different
> layout (`00_EXTERNAL/`, `00_AUDIO/`, top-level `20_INTERIM`/`30_PROCESSED`,
> `instrument_master.parquet`, `pmpmd_17census_1_*`) belonging to another project. Never
> copy into, write to, or delete it.

The survey name is therefore the literal string `pmpmd copy`. This is safe: `stage_zips`
runs `glob.escape(name)` on the questionnaire name and the survey segment is a plain path
component, so the space needs no special handling in Python/fsspec — only shell commands
need quoting.

`VERSION: []` (= all versions found) covers both questionnaires (community has 2,3,4,5;
household has 4,5,6) with no hand-entered version list.

Note: unlike `grdslchbs_test`, this export **includes Paradata** zips
(`pmpmd_community_2_Paradata_All.zip`, …), so scores will be genuinely non-empty — this is
the first real end-to-end proof of the feature.

**Files:**
- Create: `conf/pmpmd/globals.yml`, `conf/pmpmd/questionnaires/community.yml`, `conf/pmpmd/questionnaires/household.yml`
- Delete: `data/pmpmd_community/` (empty 0B leftover), `conf/pmpmd copy/` (untracked, superseded)

**Interfaces:**
- Consumes: `run_survey` (Task 4), the `combine` pipeline (Task 3), catalog wiring (Task 2).

- [ ] **Step 1: Remove the superseded leftovers and confirm the input zips**

```bash
rm -rf data/pmpmd_community            # empty 0B scaffold from the earlier approach
rm -rf "conf/pmpmd copy"               # untracked globals.yml superseded by conf/pmpmd/
ls "data/pmpmd copy/latest/10_RAW/"*.zip | xargs -n1 basename
```
Expected: lists `pmpmd_community_{2,3,4,5}_{Tabular,Paradata}_All.zip` and
`pmpmd_household_{4,5,6}_{Tabular,Paradata}_All.zip`. `data/pmpmd/` must remain untouched.

- [ ] **Step 2: Create `conf/pmpmd/globals.yml`**

```yaml
# Multi-questionnaire survey env — run via rissk.run.run_survey("pmpmd").
# One survey folder ("data/pmpmd copy") holds both questionnaires' zips in a shared 10_RAW.
# The active questionnaire is injected per-run by the driver (runtime params), so there is
# no single `questionnaire:` block here (the conf/base defaults are overridden at runtime).
# NOTE: the survey name contains a space and must stay quoted. Do NOT use "pmpmd" —
# data/pmpmd belongs to an unrelated project.
input_root: "data"
output_root: "data"
work_root: "data"
survey: "pmpmd copy"
```

- [ ] **Step 3: Create `conf/pmpmd/questionnaires/community.yml`**

```yaml
name: pmpmd_community
VERSION: []          # [] = all versions found in 10_RAW
filter_var: null
```

- [ ] **Step 4: Create `conf/pmpmd/questionnaires/household.yml`**

```yaml
name: pmpmd_household
VERSION: []
filter_var: null
```

- [ ] **Step 5: Run the full survey end-to-end**

```bash
.venv/bin/python -c "from rissk.run import run_survey; print(run_survey('pmpmd'))"
```
Expected: prints per-questionnaire `OK` for `pmpmd_community` and `pmpmd_household`, then `combine: OK`.

- [ ] **Step 6: Verify the output layout**

```bash
find "data/pmpmd copy/latest/30_PROCESSED" -name "microdata.parquet"
```
Expected exactly three:
```text
data/pmpmd copy/latest/30_PROCESSED/pmpmd_community/microdata.parquet
data/pmpmd copy/latest/30_PROCESSED/pmpmd_household/microdata.parquet
data/pmpmd copy/latest/30_PROCESSED/microdata.parquet          <- the union
```
And per-`<qnr>` scores exist and are NON-empty (this export has Paradata):
```bash
ls "data/pmpmd copy/latest/35_SCORES/pmpmd_community" "data/pmpmd copy/latest/35_SCORES/pmpmd_household"
wc -l "data/pmpmd copy/latest/35_SCORES/pmpmd_community/unit_rissk_scores.csv"
```
Expected: `unit_rissk_scores.csv` has more than 1 line (header + real scored interviews).

- [ ] **Step 7: Verify the union = sum of parts and is idempotent**

```bash
.venv/bin/python - <<'PY'
import pyarrow.parquet as pq
base = "data/pmpmd copy/latest/30_PROCESSED"
c = pq.read_metadata(f"{base}/pmpmd_community/microdata.parquet").num_rows
h = pq.read_metadata(f"{base}/pmpmd_household/microdata.parquet").num_rows
u = pq.read_metadata(f"{base}/microdata.parquet").num_rows
print("community", c, "household", h, "union", u, "-> OK" if u == c + h else "-> MISMATCH")
PY
# Idempotency: re-run ONLY the combine step (Option B — a driver helper that loads/saves
# through the catalog), then re-check the union row count. Proves combine works standalone
# and does not fold its own output back in.
.venv/bin/python -c "from rissk.run import combine_survey_microdata; combine_survey_microdata('pmpmd')"
.venv/bin/python - <<'PY'
import pyarrow.parquet as pq
base = "data/pmpmd copy/latest/30_PROCESSED"
c = pq.read_metadata(f"{base}/pmpmd_community/microdata.parquet").num_rows
h = pq.read_metadata(f"{base}/pmpmd_household/microdata.parquet").num_rows
u = pq.read_metadata(f"{base}/microdata.parquet").num_rows
print("after re-combine: union", u, "-> IDEMPOTENT" if u == c + h else "-> DOUBLED (bug)")
PY
```
Expected: `union == community + household` (top-level union file was skipped, not double-counted).

- [ ] **Step 8: Commit the env (not the data)**

```bash
git add conf/pmpmd
git commit -m "test: example pmpmd multi-questionnaire env + end-to-end validation"
```

---

### Task 6: Wire the notebook driver + documentation

**Files:**
- Modify: `rissk_readme.ipynb` (driver cell → `run_survey`; doc-markdown `41_SCORES`→`35_SCORES`)
- Modify: `CLAUDE.md`, `Kedro_vs_Legacy_Changelog.md`

**Interfaces:**
- Consumes: `rissk.run.run_survey` (Task 4).

- [ ] **Step 1: Update the notebook run cell**

Replace the body of the run cell (In[3]) so it delegates to the driver (keeps the existing `ENV`/`PIPELINE` parameters cell and the failure-summary/re-raise behaviour):

```python
from rissk.run import run_survey

envs = ENV if isinstance(ENV, (list, tuple)) else [ENV]
results = {}
for env in envs:
    outcomes = run_survey(env, project_root=PROJECT_ROOT, pipeline=PIPELINE)
    for label, outcome in outcomes.items():
        results[f"{env}:{label}"] = outcome

ok = sum(r == "OK" for r in results.values())
print(f"\nSummary: {ok}/{len(results)} run(s) succeeded.", flush=True)
failed = [k for k, r in results.items() if r != "OK"]
if failed:
    raise RuntimeError(f"kedro run failed for: {failed}")
```

- [ ] **Step 2: Update notebook markdown**

In the "Where results land" markdown cell, change `41_SCORES` → `35_SCORES` (2 occurrences) and note that a survey with a `questionnaires/` folder writes per-`<qnr>` subfolders plus the survey-level `30_PROCESSED/microdata.parquet` union.

- [ ] **Step 3: Verify the notebook executes**

Run: `.venv/bin/jupyter nbconvert --to notebook --execute rissk_readme.ipynb --output /tmp/rissk_readme_check.ipynb`
(Default `ENV = "grdslchbs_test"` — a legacy single-questionnaire env — must still succeed and write the flat layout, proving backward compatibility of the driver.)
Expected: executes without error; `data/grdslchbs_test/latest/35_SCORES/unit_rissk_scores.csv` exists.

- [ ] **Step 4: Update `CLAUDE.md`**

Revise the two relevant spots:
- The data-stages block: `35_SCORES/` and note outputs may carry a `<qnr>` sub-level.
- The "One questionnaire per env" paragraph: replace with the new model — a survey env may declare multiple questionnaires under `conf/<env>/questionnaires/`, the driver (`rissk.run.run_survey`) iterates them writing per-`<qnr>` subfolders, and a `combine` pipeline unions their microdata into `30_PROCESSED/microdata.parquet`. Existing single-questionnaire envs (questionnaire in `globals.yml`, no `questionnaires/` folder) are unchanged and stay flat.

- [ ] **Step 5: Record the change in `Kedro_vs_Legacy_Changelog.md`**

Add an entry: per-`<qnr>` output sub-level (opt-in via `qnr_subdir` runtime param; empty default = legacy flat layout); new `combine` pipeline producing the survey-level microdata union; scores folder renamed `41_SCORES`→`35_SCORES`.

- [ ] **Step 6: Full test sweep + commit**

Run: `.venv/bin/python -m pytest -q`
Expected: all tests pass.

```bash
git add rissk_readme.ipynb CLAUDE.md Kedro_vs_Legacy_Changelog.md
git commit -m "feat: notebook driver iterates questionnaires; docs for multi-questionnaire + 35_SCORES"
```

---

## Self-Review

**Spec coverage:**
- Per-`<qnr>` outputs for 20/30/35 → Task 2. ✓
- Survey-level microdata union → Task 3 (+ verified in Task 5). ✓
- Union is microdata-only, no scoring impact → Task 3 (combine is a separate pipeline touching only microdata). ✓
- One env, driver iterates questionnaires (3a) → Task 4 + Task 5 (`conf/<env>/questionnaires/*.yml`). ✓
- 10_RAW stays survey-level → Task 2 leaves the two 10_RAW entries untouched. ✓
- Backward compatibility (empty-default `qnr_subdir`, globals fallback) → Task 2 tests + Task 6 Step 3 (legacy notebook run). ✓
- `41_SCORES`→`35_SCORES` rename → Task 1 (+ notebook/docs in Task 6). ✓
- Docs (CLAUDE.md, changelog) → Task 6. ✓

**Placeholder scan:** No TBD/TODO; every code step shows full code; `VERSION: []` avoids hand-entered version lists.

**Type consistency:** `combine_microdata_node(partitions: dict[str, Callable[[], DataFrame]]) -> DataFrame` is consumed by `pipeline.py` (`inputs="microdata_by_qnr"`, `outputs="microdata_combined"`) and both catalog names exist (Task 3 Step 6). `load_questionnaire_configs` / `run_survey` signatures match their tests (Task 4) and the notebook call (Task 6). Catalog dataset names referenced in tests (`microdata`, `item_scores`, `unit_rissk_scores`, `item_features_base`) all exist in `catalog.yml`.
