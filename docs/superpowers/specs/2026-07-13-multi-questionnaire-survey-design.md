# Multi-questionnaire survey: per-`<qnr>` outputs + combined microdata

**Date:** 2026-07-13
**Status:** Approved design (pre-implementation)
**Repo:** rissk (Kedro 1.2.0, single `rissk` package)

## Problem / motivation

A Survey Solutions survey can contain several **distinct questionnaire names** exported
together into one `10_RAW` (e.g. `pmpmd_community_*.zip` and `pmpmd_household_*.zip`).
Today the pipeline is one-questionnaire-per-env and keys every output by `<survey>` with
**no questionnaire sub-level** ([conf/base/catalog.yml](../../../conf/base/catalog.yml)),
so two questionnaires sharing a survey folder would overwrite each other's outputs.

We want, within a single survey:

1. Each questionnaire scored independently, with its outputs under a per-questionnaire
   subfolder in `20_INTERIM` / `30_PROCESSED` / `35_SCORES`.
2. In addition, a **survey-level union of the per-questionnaire `microdata.parquet`**
   files at `30_PROCESSED/microdata.parquet` — a pure data union for downstream use.

### Confirmed requirements (from brainstorming)

- **Union scope:** `microdata` only. Not questionnaire/features/scores.
- **Union semantics:** a pure row-union for the user's downstream analysis. It does **not**
  feed scoring; feature/scoring code is untouched. (Scoring two different instruments as one
  pool would be statistically wrong, so this is deliberate.)
- **Config model (decision):** *one env per survey; the driver iterates questionnaires.*
  `conf/<survey>/` is the env; per-questionnaire yamls live under
  `conf/<survey>/questionnaires/`; the notebook enumerates them and runs the pipeline once
  per questionnaire, passing the selection at runtime.
- **10_RAW layout (decision):** stays **survey-level / shared / flat** — raw zips arrive
  together as Survey Solutions exports them and staging already isolates each questionnaire
  by its `<name>_*.zip` glob. Only 20/30/35 get the `<qnr>` subfolder.

## Design

### 1. Runtime injections (the crux)

The driver supplies two values via Kedro **`extra_params`** (runtime params) per run:

- `questionnaire` → flows into `params:questionnaire` (name / VERSION / filter_var),
  consumed by the ingestion nodes exactly as today.
- `qnr_subdir` (e.g. `"pmpmd_community/"`) → consumed by the **catalog** output paths.

**Backward compatibility** is achieved with fallback defaults so existing single-questionnaire
envs are byte-for-byte unchanged:

- `parameters.yml`:
  `questionnaire: ${runtime_params:questionnaire, ${globals:questionnaire, null}}`
  — runtime wins; else globals (today's envs); else null.
- catalog output paths:
  `.../30_PROCESSED/${runtime_params:qnr_subdir,}microdata.parquet`
  — **empty default** ⇒ existing envs (`grdslchbs_test`, `s3*`) resolve to the exact same
  flat paths as today. `viz.py` and existing on-disk data are untouched.

**To verify during implementation (fallbacks are trivial if either misbehaves):**
- `runtime_params` cleanly passes a nested dict for `questionnaire`.
- Per-questionnaire yamls under `conf/<env>/questionnaires/` are ignored by Kedro's
  `OmegaConfigLoader` (they do not match `globals*`/`catalog*`/`parameters*` patterns). If
  not, move them outside `conf/` or rename so they are not matched.

### 2. Catalog changes ([conf/base/catalog.yml](../../../conf/base/catalog.yml))

- The ~13 output entries under **20_INTERIM / 30_PROCESSED / 35_SCORES** gain the
  `${runtime_params:qnr_subdir,}` path segment.
- The **two 10_RAW** PartitionedDataset entries (`survey_zip_partitions`,
  `extracted_survey_folders`) stay survey-level — **unchanged**.
- **New** `microdata_by_qnr` — `partitions.PartitionedDataset` over
  `${globals:output_root}/${globals:survey}/latest/30_PROCESSED` with
  `filename_suffix: microdata.parquet` (matches each `<qnr>/microdata.parquet`).
- **New** `microdata_combined` — `pandas.ParquetDataset` at
  `${globals:output_root}/${globals:survey}/latest/30_PROCESSED/microdata.parquet`
  (the union; the survey-level "global" file the user wants preserved at this path).

### 3. The `combine` pipeline (new)

A new modular pipeline `src/rissk/pipelines/combine/` with a single node:

```python
def combine_microdata_node(partitions: dict[str, Callable]) -> pd.DataFrame:
    # load each partition; SKIP the top-level "microdata" partition so the union
    # file cannot re-consume itself on a rerun; pd.concat the rest.
```

- Input: `microdata_by_qnr`. Output: `microdata_combined`.
- The top-level `microdata.parquet` (the union itself) is skipped by partition id so reruns
  are idempotent and never fold the union back into itself.
- The union is a clean row-stack: `microdata` is long-format with `qnr` / `qnr_version`
  columns distinguishing rows, so no schema reconciliation is needed.
- Registered in [pipeline_registry.py](../../../src/rissk/pipeline_registry.py) as a
  **separate** pipeline `"combine"` — **not** part of `__default__`, because it must run once
  *after* all per-questionnaire runs.
- Empty-data guard consistent with project convention: if there are no partitions, log a
  WARNING and return an empty DataFrame.

### 4. Driver (notebook) orchestration ([rissk_readme.ipynb](../../../rissk_readme.ipynb))

```
ENV = "pmpmd"                        # one survey env
qnrs = load conf/pmpmd/questionnaires/*.yml
for q in qnrs:
    KedroSession.create(env=ENV,
        extra_params={"questionnaire": q, "qnr_subdir": q["name"] + "/"}
    ).run("__default__")             # per-questionnaire full pipeline
KedroSession.create(env=ENV).run("combine")   # union once; no extra params needed
```

- Per-questionnaire failure isolation as today (one bad questionnaire does not skip the rest;
  outcomes collected and re-raised at the end).
- The `combine` run needs no extra params — its datasets key only on `${globals:survey}`.
- Existing single-questionnaire notebooks/CLI keep working unchanged (an old `ENV`, never
  passing `qnr_subdir`, resolves to flat legacy paths).

### 5. Example env layout for `pmpmd`

```
conf/pmpmd/
  globals.yml                # survey: pmpmd, storage roots (no single questionnaire)
  questionnaires/
    community.yml            # name: pmpmd_community, VERSION: [...], filter_var: null
    household.yml            # name: pmpmd_household, VERSION: [...], filter_var: null

data/pmpmd/latest/
  10_RAW/                    # shared, flat — pmpmd_community_*.zip, pmpmd_household_*.zip
  20_INTERIM/<qnr>/...
  30_PROCESSED/<qnr>/...     + 30_PROCESSED/microdata.parquet   (union)
  35_SCORES/<qnr>/...
```

### 6. Rename `41_SCORES` → `35_SCORES`

Folded into this change (the folder was already renamed once, `40_SCORED` → `41_SCORES`,
in commit `bf7fe6f`). A straight folder-name change applied everywhere the literal
`41_SCORES` appears:

- The three scoring entries in [conf/base/catalog.yml](../../../conf/base/catalog.yml)
  (`item_scores`, `unit_rissk_scores`, `responsible_scores`) — combined with §2 these become
  `.../35_SCORES/${runtime_params:qnr_subdir,}<file>`.
- Path strings in [src/rissk/viz.py](../../../src/rissk/viz.py) (the `41_SCORES` folder name
  in the scored-file loaders / survey discovery).
- Doc/comment references: CLAUDE.md, [rissk_readme.ipynb](../../../rissk_readme.ipynb),
  README, `Kedro_vs_Legacy_Changelog.md`.

Old `41_SCORES` output folders on disk are not migrated by the pipeline — they are simply
re-created under `35_SCORES` on the next run (stale `41_SCORES` folders can be deleted
manually).

## Out of scope / unchanged

- Every existing env; all feature-creation and scoring code.
- `viz.py` and the marimo apps — they keep reading the flat single-questionnaire layout
  (only their `41_SCORES` → `35_SCORES` folder string is updated, per §6). Updating them to
  browse per-`<qnr>` scores is a **separate, deferred** task.
- No combined version of any stage other than `microdata`.
- No re-scoring of the union.
- Static DAG only — no dynamic node/pipeline generation (respects project convention).

## Documentation updates

- CLAUDE.md currently states outputs are keyed by `<survey>` with no questionnaire sub-level;
  this design revises that to an **opt-in** per-`<qnr>` sub-level and adds the driver-iterates
  multi-questionnaire model. Update CLAUDE.md accordingly.
- Record the behavioural addition in
  [Kedro_vs_Legacy_Changelog.md](../../../Kedro_vs_Legacy_Changelog.md).

## Testing

- **Unit** `combine_microdata_node`: several fake partitions incl. a top-level `microdata`
  file → correct union, top-level skipped; empty input → empty DataFrame + WARNING.
- **Catalog resolution:** no `qnr_subdir` → flat legacy paths (backward-compat guard);
  `qnr_subdir="x/"` → nested paths.
- **End-to-end (small):** two tiny questionnaires under one survey → two `<qnr>/` output
  trees + one union `30_PROCESSED/microdata.parquet`; existing single-questionnaire env still
  produces the flat layout.

## Effort

Small–moderate: catalog edits + `parameters.yml` fallback, one tiny `combine` pipeline, and
the driver rewrite. Also retires the earlier output-collision problem (multiple questionnaires
can now share one survey folder safely).
