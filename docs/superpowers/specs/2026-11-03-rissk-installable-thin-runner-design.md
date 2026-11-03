# rissk as an installable package, configuration as data, a thin Windmill runner

**Date:** 2026-11-03
**Status:** Approved direction (decided by the user on 2026-11-03), design for implementation
**Repo:** rissk (Kedro 1.2.0, single `rissk` package), branch `kedro_pipeline`
**Supersedes:** sections 2, 3, 5 and 8 of
[2026-10-23-windmill-scheduling-design.md](2026-10-23-windmill-scheduling-design.md)
(the tarball fetch, the env materialisation and the private-repo token). Everything else
in that document (resources, credentials, schedules, smoke test, deployment of the
runner with the wmill CLI) stands.

## Intent and why

The Windmill runner `windmill/f/rissk/run_rissk.py` is 340 lines because it runs the
rissk **source tree** without installing it: it downloads a GitHub tarball at `ref`,
puts `src/` on `sys.path`, mirrors rissk's dependency list in its `#requirements:`
header, writes a throwaway Kedro env from the `rissk_survey` resource, writes a logging
config, sets telemetry variables and validates everything itself. The user's point: the
platform is supposed to hold the configuration and the glue should be a few lines.

The fix is in rissk, not in the runner. Make rissk a package that `pip`/`uv` can install
straight from GitHub, and give it an entry point that takes the survey configuration as
data. Then the runner is one dependency line plus a `main` that maps the two resources
and calls `rissk.run.run_survey`.

## Goals

1. `windmill/f/rissk/run_rissk.py` is about 25 lines: `# py313`, one `#requirements:`
   line for rissk, the two resource `TypedDict`s, `main(survey, storage, pipeline)`.
2. `rissk.run.run_survey` accepts the configuration as a mapping (the `rissk_survey`
   resource, verbatim) **or** an env name, and both run through the same loop, session
   factory, logging and outcome map.
3. An installed rissk (no checkout, any working directory) runs the pipeline: the wheel
   carries `conf/base`.
4. Every push to `kedro_pipeline` deploys the runner pinned to that commit, so Windmill
   always runs what is on the branch and the script page shows the commit.
5. Local runs, `kedro run --env <env>`, the notebooks and the committed `conf/<env>`
   folders behave exactly as today (output layout byte-for-byte).

## Non-goals

- No change to features, scores or output files. No entry in `Kedro_vs_Legacy_Changelog.md`.
- No container image for rissk; the Python worker path stays.
- `app/main.py` (GUI) stays as it is (already flagged as needing a rework).
- Private repositories: not supported by this design (the repo is public). If that ever
  changes, the dependency line needs a token or a private index; out of scope.

## Anti-goals (what would make this a failure even if it works)

- A runner that still carries any rissk logic (validation, config files, logging, paths).
- rissk importing `wmill` or knowing it runs in Windmill.
- Two sources of truth for `conf/base` (the wheel copies it at build time; the repo file
  stays the only one edited).
- A single-questionnaire env that silently switches to the `<qnr>/` sub-folder layout.

## Constraints

- Kedro 1.2.0: `OmegaConfigLoader` merges `runtime_params` into **parameters only**, not
  into globals (`omegaconf_config.py`, "Merge with runtime parameters only for
  parameters"). The catalog reads the roots and the survey through `${globals:...}`, so
  it must learn to read runtime params first. The `runtime_params:` resolver is not
  allowed inside `globals.yml`.
- The config loader needs an env folder that exists; `env="base"` works (verified: base
  loaded as base and as env, no duplicate error, templates resolve).
- `kedro-telemetry` and `rich` are hard dependencies of Kedro 1.2.0; Kedro picks
  `conf/logging.yml` relative to the **working directory at import time**, else rich.
- Windmill: `#requirements:` accepts standard pip specifiers including
  `pkg@git+https://...` (docs example); the lockfile is computed at deploy time by a
  dependency job; `uv` pins a branch ref to the commit SHA (verified locally:
  `rissk @ git+https://github.com/rowsquared/rissk@7bf1ff9ea336…`, 125 packages,
  `kedro==1.2.0`). The worker image has `git`, `uv`, Python 3.13 (Dockerfile verified).
  The job folder is the working directory and is deleted after the run.
- Windmill's handling of a git requirement, read from its source (`main` branch,
  2026-11-03): a `#requirements:` line is passed whole to `uv pip compile`
  (`workspace_dependencies.rs`, only the leading `#` is stripped); each lock line is kept
  whole by `requirement_from_lockfile_line` (`windmill-common/src/worker.rs`) and
  installed on its own with `uv pip install --no-deps` into a cache folder named after
  the line (`python_executor.rs`). `uv pip install --no-deps --target … "rissk @
  git+https://github.com/rowsquared/rissk@7bf1ff9…"` was reproduced locally and
  `import rissk` works from the target. Not yet exercised on the instance itself: the
  first deploy's dependency job (lockfile on the script page) and the smoke test are the
  end-to-end check, and they are the operator's.
- `wmill sync push` deploys a script only when its content or metadata changed; a push
  with an unchanged script does not re-lock. Hence the commit is written into the
  requirement line by the workflow, which changes the content on every push.
- Superseded design rules still in force: no Kedro hooks, static pipelines, graceful
  empty data, secrets never in git, survey configuration never in committed envs for
  production.

## Design

### 1. Packaging: the wheel carries `conf/base`

`pyproject.toml` switches the build backend from flit to **hatchling** (flit cannot place
files from outside the module into the package). The repository layout does not change.

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/rissk"]

[tool.hatch.build.targets.wheel.force-include]
"conf/base" = "rissk/_conf/base"          # copied at build time; conf/base stays the source

[tool.hatch.build.targets.sdist]
include = ["src/rissk", "conf/base", "README.md", "pyproject.toml"]
```

Version `0.1.2` → `0.2.0` (new public API, new packaging). `uv sync`, `uv build`,
`pip install -e .` (conda `environment.yml`) all work with hatchling; verified by
building the wheel and installing it in a fresh venv: it contains
`rissk/_conf/base/{catalog,globals,parameters}.yml`, and
`KedroSession.create(project_path=<any dir>, env="base", conf_source=<packaged>,
runtime_params=...)` after `configure_project("rissk")` loads the context, 102 catalog
entries, params from the runtime params.

In an editable install (the checkout) `src/rissk/_conf` does not exist; that is how the
two modes below are told apart.

### 2. `rissk.run`: one driver, two configuration sources

```python
def run_survey(config: str | Mapping, project_root=None, pipeline="__default__",
               run_combine=True) -> dict[str, str]
def combine_survey_microdata(config: str | Mapping, project_root=None) -> None
def validate_survey_config(config: Mapping) -> dict      # returns the normalised copy
def load_questionnaire_configs(env, project_root=None)   # unchanged, public
```

**Project modes.** A private helper resolves once per call:

- *checkout*: `project_root` given, or derived from `rissk.__file__` as today, and
  `<root>/pyproject.toml` plus `<root>/conf/base` exist → `bootstrap_project(root)`,
  `os.chdir(root)` (kept, see the comment in `run.py`), sessions with
  `project_path=root` and the default conf source.
- *installed*: otherwise, when `importlib.resources.files("rissk") / "_conf"` is a
  directory → `configure_project("rissk")`, sessions with `project_path=Path.cwd()`,
  `conf_source=<that directory>`. No chdir: relative paths (the `data` work root)
  resolve against the working directory, which in Windmill is the job folder.
- neither → `RuntimeError` naming both places looked at.

**Env name (`str`).** Allowed in checkout mode only (an env is a folder in the checkout;
in installed mode it raises `ValueError` telling the caller to pass a mapping). Behaviour
is byte-for-byte today's: Kedro loads `conf/<env>/` (globals, and any `parameters.yml`
or catalog override the env carries), runtime params are `{questionnaire, qnr_subdir}`
per questionnaire from `questionnaires/*.yml`, and an env without that folder is the
legacy single-questionnaire run: one run, no runtime params, flat layout, no combine.
This is why an env is **not** converted into a mapping: an env folder may hold more than
`globals.yml`, and only Kedro's env loading honours it.

**Mapping.** `validate_survey_config` (ported from the runner's `validate_survey`, same
messages) checks: `survey`, `input_root`, `output_root` non-empty strings;
`questionnaires` a non-empty list of mappings with a non-empty `name`, `VERSION` a list
of ints (default `[]`), `filter_var` a mapping or null; optional `zip_password` string;
optional `parameters` mapping whose top-level keys must not be one of the reserved
runtime keys (`questionnaire`, `qnr_subdir`, `survey`, `input_root`, `output_root`,
`work_root`); optional `work_root` string, default `"data"`. **`output_root` must be a
URL** (`scheme://`): a run configured as data has no project folder to write into, and
the Windmill job folder is deleted after the run. `file:///abs/path` is the local escape
hatch (fsspec handles it). `input_root` may be a path or a URL. Unknown keys are rejected
(a typo in a resource must not pass silently).

The run uses **`env="base"`** (no env folder is read, not even `conf/local`) and, per
questionnaire `q`, the runtime params

```python
{"survey": ..., "input_root": ..., "output_root": ..., "work_root": ...,
 "questionnaire": {"name": q.name, "VERSION": q.VERSION, "filter_var": q.filter_var},
 "qnr_subdir": f"{q.name}/",
 **({"zip_password": pw} if pw else {}),
 **parameters}                       # Kedro deep-merges these over conf/base/parameters.yml
```

Kedro's merge of runtime params into parameters is a deep merge (verified:
`{"features": {"a": {"use": False}}}` keeps `features.a.parameters` and `features.b`),
so the runner's `_deep_merge` is not needed. A mapping always produces the
questionnaire sub-folder layout plus the combine step, as the runner did.

**Combine** uses the same session factory with the same runtime params minus
`questionnaire`/`qnr_subdir` (so `microdata_combined` resolves at survey level).

**Logging.** `run_survey` calls `kedro.framework.project.LOGGING.configure(PLAIN_LOGGING)`
first thing: `logging.StreamHandler` on stdout, format
`%(asctime)s %(levelname)s %(name)s: %(message)s`, `kedro` and `rissk` at INFO. This is
the headless driver (notebooks, schedulers); `kedro run` on the CLI keeps
`conf/logging.yml` and rich. The wheel does not carry a logging file.

**Telemetry.** `settings.py`: `DISABLE_HOOKS_FOR_PLUGINS = ("kedro-viz", "kedro-telemetry")`.
No environment variables anywhere.

### 3. Catalog: runtime params first, globals as fallback

`conf/base/catalog.yml` defines templates in `_`-prefixed keys (dropped by Kedro after
resolution) and every entry uses them:

```yaml
_survey: ${runtime_params:survey,${globals:survey}}
_work:   ${runtime_params:work_root,${globals:work_root}}/${_survey}/latest
_out:    ${runtime_params:output_root,${globals:output_root}}/${_survey}/latest
_qnr:    ${runtime_params:qnr_subdir,''}

microdata:
  type: pandas.ParquetDataset
  filepath: ${_out}/30_PROCESSED/${_qnr}microdata.parquet
```

Verified with the Kedro 1.2.0 loader: no runtime params →
`g_out/from_globals/latest/30_PROCESSED/microdata.parquet`; with
`{survey, output_root, qnr_subdir}` → `s3://b/from_runtime/latest/30_PROCESSED/hh/…`.
`conf/base/parameters.yml` needs no change: `input_root`, `work_root`, `survey` and
`questionnaire` there are replaced by the runtime params before resolution (the file's
own comment documents this for `questionnaire`). `tests/test_catalog_resolution.py`
keeps proving the flat and nested layouts for envs and gains the runtime-root cases.

### 4. The runner

```python
# py313
#requirements:
#rissk@git+https://github.com/rowsquared/rissk@kedro_pipeline
"""Windmill script f/rissk/run_rissk: run the RISSK pipeline for one survey.

`survey` is the rissk_survey resource (the configuration, passed to rissk as is),
`storage` the s3 resource (bucket credentials, exported as the AWS environment
variables s3fs reads). The deploy workflow replaces `kedro_pipeline` above with the
pushed commit, so the lockfile pins the exact rissk version that runs.
"""
import os
from typing import TypedDict

from rissk.run import run_survey


class rissk_survey(TypedDict, total=False):    # resource type rissk_survey (windmill/rissk_survey.schema.json)
    survey: str; input_root: str; output_root: str; questionnaires: list; zip_password: str; parameters: dict


class s3(TypedDict, total=False):              # Windmill's built-in resource type s3
    accessKey: str; secretKey: str; region: str; endPoint: str


def main(survey: rissk_survey, storage: s3, pipeline: str = "__default__") -> dict:
    endpoint = (storage.get("endPoint") or "").strip()
    aws = {"AWS_ACCESS_KEY_ID": storage["accessKey"], "AWS_SECRET_ACCESS_KEY": storage["secretKey"],
           "AWS_DEFAULT_REGION": storage.get("region"),
           # s3fs targets AWS unless told otherwise; a non-AWS endpoint (MinIO, Hetzner, R2) is passed on.
           "AWS_ENDPOINT_URL": f"https://{endpoint}" if endpoint and not endpoint.endswith("amazonaws.com") else ""}
    os.environ.update({k: v for k, v in aws.items() if v})
    outcomes = run_survey(survey, pipeline=pipeline)
    failed = [label for label, outcome in outcomes.items() if outcome != "OK"]
    if failed:
        raise RuntimeError(f"rissk run failed for: {failed}")
    return {"survey": survey["survey"], "pipeline": pipeline, "outcomes": outcomes}
```

(The builder lays the `TypedDict` fields out one per line; the class names are what
Windmill maps to the resource types, the explicit schema in the metadata yaml stays
authoritative on push.) Everything that was in the old runner and is not here is gone:
tarball download/extraction, token resolution, `repo`/`ref`/`github_token_variable`,
`sanitize_env_name`, `materialize_env`, `_deep_merge`, `write_logging_config`,
`kedro_env`, `apply_env`, `validate_*`, `run_pipeline`, the `__main__` block, the
mirrored dependency list and `wmill`.

`run_rissk.script.yaml`: the schema keeps `survey`, `storage`, `pipeline` (same enum,
same `format: resource-…`), drops `repo`, `ref`, `github_token_variable`; description
rewritten; the `timeout` comment stays.

`rissk_survey.schema.json` and `rissk_survey.resource-type.yaml` (kept equal by the
existing test): `output_root` gains `"pattern": "^[a-z][a-z0-9+.-]*://"` and the
description says it must be a URL such as `s3://surveytool`. (Windmill's form has the
*Pattern (Regex)* field setting; rissk's validation is the real check.)

### 5. Deployment: every push to `kedro_pipeline` deploys that commit

`.github/workflows/windmill-sync.yml`:

- trigger: `push` to `kedro_pipeline` with **no `paths` filter** (a rissk code change
  must redeploy too), plus `workflow_dispatch`; the `[WM]` skip and the concurrency
  group stay.
- new step before `Push f/rissk`, **Pin the rissk commit**:
  `sed -i -E 's|^#rissk@git\+https://github\.com/rowsquared/rissk@.*$|#rissk@git+https://github.com/rowsquared/rissk@'"$GITHUB_SHA"'|' f/rissk/run_rissk.py`
  followed by a `grep -q "rissk@$GITHUB_SHA\$"` that fails the job if the line was not
  found (the committed file keeps `@kedro_pipeline`; the rewrite lives only in the
  runner's checkout).
- the `--message` already carries `<repo>@<sha>`.

Effects: Windmill re-locks on every push, the lock pins `rissk@<sha>`, and the script
page shows which commit runs. **Rollback** = `git revert` + push (or run the workflow by
hand on a branch whose tip is the wanted commit). A local `wmill sync push` (way 1)
deploys `@kedro_pipeline`, which uv pins to the branch tip at lock time. The first job
after a deploy builds the rissk wheel from the git clone on the worker (seconds; cached
per worker afterwards).

### 6. Tests

- `tests/test_windmill_runner.py` rewritten: the script's first line is `# py313`; the
  `#requirements:` block is exactly one line matching
  `^#rissk@git\+https://github\.com/rowsquared/rissk@kedro_pipeline$`; the module imports
  only `os`, `typing` and `rissk.run`; `main` sets the AWS variables (region only when
  given; `AWS_ENDPOINT_URL` only for a non-AWS endpoint), calls `run_survey(survey, pipeline=…)` (monkeypatched), raises `RuntimeError`
  naming the failed labels, returns the outcomes; metadata yaml: properties are exactly
  `survey`, `storage`, `pipeline`, the enum equals the keys of
  `rissk.pipeline_registry.register_pipelines()`, formats `resource-rissk_survey` /
  `resource-s3`; resource-type yaml == schema JSON and `output_root` has the pattern;
  `wmill.yaml` scope test unchanged; workflow test: no `paths` under `push`, the pin
  step references `GITHUB_SHA` and the requirement line, the resource-type push and the
  sync push flags unchanged; README example matches the committed env (unchanged).
- `tests/test_run_driver.py` extended: `validate_survey_config` accepts the README
  example and rejects each bad case (ported from the runner tests, plus reserved
  parameter keys, unknown keys, non-URL `output_root`); the runtime params built for a
  mapping (per questionnaire, with `zip_password` and `parameters` folded in) and for an
  env (`{questionnaire, qnr_subdir}` / `None` for the legacy env); mode detection on
  temp folders (checkout vs installed vs neither); the loop with a stubbed session
  factory: labels, order, isolation of a failing questionnaire, combine runs last with
  the survey-level params; an env name in installed mode raises.
- `tests/test_catalog_resolution.py` extended: runtime `survey`/`output_root`/`work_root`
  override the env's globals for `microdata`, `unit_rissk_scores`, `survey_zips` (work
  root) and `microdata_by_qnr`/`microdata_combined`; the existing flat/nested tests stay.
- `tests/test_packaging.py` new: `uv build --wheel` into a temp dir (skipped when `uv`
  is not on PATH) and the wheel lists `rissk/_conf/base/catalog.yml`, `globals.yml`,
  `parameters.yml`; plus a static check that `pyproject.toml` has the force-include.
- Removed: the header drift test, the tarball/token tests, the materialise-env tests,
  the CLI `--help` test.

### 7. Documentation

- `windmill/README.md`: intro (what the runner is now); "What runs where" (drop the
  private-repo token row); setup steps 4–5 (deploy; lockfile shows Python 3.13,
  `kedro==1.2.0` and `rissk @ git+…@<commit>`); "Deploying the runner" (the runner and
  the rissk code are now deployed together: a push pins the commit; way 1 pins the
  branch tip at lock time); drop "Private repository" and "Running locally"; add
  "Running the same configuration from Python" (`run_survey(dict)`); "Updating": code →
  push to `kedro_pipeline`; rollback; the lock-pinning paragraph becomes moot (Windmill
  re-locks on every deploy) and is removed; a note that a schedule keeps its stored
  arguments when the script's schema changes, so after this change (or any later one
  that drops an argument) a schedule's arguments are checked once (none exists today).
- `CLAUDE.md`: the Windmill paragraph (installed from GitHub, config as data, deploy
  pins the commit), the "Two ways to run" paragraph (+ `run_survey(mapping)`), the
  packaging note (hatchling, `conf/base` in the wheel, `_conf` marks the installed mode).
- The 2026-10-23 spec gets a one-line pointer at the top to this document.
- This document records the decisions; the plan lives in `docs/superpowers/plans/`.

## Left to the builder

- Internal names and the shape of the mode helper (a small dataclass or two functions).
- Exact error messages beyond the ones ported from the runner.
- The order of the catalog rewrite (templates first, then entries) and comment wording.
- Whether the runner's `TypedDict`s list every resource field or only the used ones.
- README prose, within the section list above.

## Calls made here (not decided by the user; say so when handing over)

1. Env-name runs stay file-backed instead of being converted to a mapping (reason above).
2. "Output must be remote" becomes "output_root must be a URL" in rissk's validation,
   `file://` for local; the Windmill schema repeats it as a pattern.
3. Logging config is a dict in code, not a packaged yaml; the wheel carries only
   `conf/base`.
4. Build backend flit → hatchling; version 0.2.0.
5. The requirement line uses the no-space form from Windmill's docs
   (`rissk@git+https://…`).
6. Unknown keys in a mapping are rejected.
7. The runner keeps a three-line `AWS_ENDPOINT_URL` mapping for a non-AWS S3 endpoint
   (the production bucket is on AWS; dropping the mapping silently would be a
   regression for any other endpoint). `useSSL=false` (plain http) is not supported.

## Builder check (2026-11-03)

A reviewer read this document cold and asked three things; none needed the user:
the endpoint mapping (call 7 above), whether a git requirement line really works
through Windmill's own install step (answered from Windmill's source and a local
reproduction, see Constraints; the instance-level check is the operator's first
deploy), and stale schedule arguments (no schedule exists yet; a README note covers
later changes).
