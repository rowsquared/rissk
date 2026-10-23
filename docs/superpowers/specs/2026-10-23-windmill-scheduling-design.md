# Scheduling RISSK on Windmill Community Edition

**Date:** 2026-10-23
**Status:** Approved design (pre-implementation)
**Repo:** rissk (Kedro 1.2.0, single `rissk` package), branch `kedro_pipeline`
**Supersedes:** the JupyterHub Notebook Jobs path described in
[rissk_readme.ipynb](../../../rissk_readme.ipynb) (kept until cut-over, then legacy).

## Problem / motivation

Today a scheduled RISSK run is a JupyterHub Notebook Job: cron fires
`rissk_readme.ipynb`, which calls `rissk.run.run_survey(env)` in-process. Every survey
is a **committed Kedro env** (`conf/<env>/globals.yml` + `questionnaires/*.yml`) in a
public repository, S3 credentials come from the hub machine's AWS credential chain, and
adding a survey means a commit, a push and a new Notebook Job.

We are replacing that with a self-hosted **Windmill Community Edition** instance
(deployed on Coolify, see "Deployment facts" below). Goals:

1. Same pipeline, same outputs, same bucket (`s3://surveytool`, written natively via
   `s3fs`). Nothing changes for downstream consumers of `35_SCORES/`.
2. **Configuration lives in Windmill, not in git.** Adding or editing a survey is done in
   the Windmill UI (resources + a schedule). S3 credentials are a Windmill resource.
3. **Code comes from GitHub.** rissk is public today; the same runner must work for a
   private repository with nothing more than a token stored as a Windmill secret.
4. No Windmill Enterprise features. CE limits that shaped the design: no worker-group
   UI config (no init scripts, no custom env vars pushed into jobs), no schedule or
   workspace error handlers, no dedicated workers. Schedules, resources, secret
   variables, custom resource types, per-script timeout and tag, and CLI sync are all
   available in CE.

### Confirmed requirements (from brainstorming)

- **Approach:** a Windmill-native **Python script** (the "runner"), not a Docker image.
  The GHCR / Docker-socket route (`windmill-worker-containers`) is explicitly **out of
  scope for now**; nothing in this design prevents adding it later.
- **No new compose services.** The rissk job runs on the general worker
  (`windmill-worker-1`, default tags, Python 3.13 available in the image). Its memory
  cap (currently 2048M) is the one number that may need raising after the smoke test.
- **Per-survey config = a Windmill resource**, credentials = a Windmill resource. The
  committed `conf/<env>` folders remain usable for local/dev runs and are ignored by the
  runner.
- **Private repos:** fine-grained GitHub token (read-only *Contents*) in a Windmill
  secret variable, referenced by name from the runner's arguments.
- **No change to rissk's pipeline modules.** The runner adapts Windmill's inputs to the
  existing `run_survey(env, project_root, pipeline)` contract by writing a throwaway
  Kedro env into the checked-out repo.

## Deployment facts (verified)

| Fact | Value / source |
|---|---|
| Windmill image | `ghcr.io/windmill-labs/windmill:latest`, Debian trixie-slim, runs as root, `git`, `uv`, docker CLI present; Python 3.11/3.12/3.13 pre-managed by uv |
| General worker | `windmill-worker-1`: `WORKER_GROUP=default`, `privileged`, memory limit **2048M**, volumes `worker-dependency-cache:/tmp/windmill/cache`, `worker-logs` |
| Other workers | `windmill-worker-containers` (tag `containers`, Docker socket, out of scope), `windmill-worker-native` (native jobs only) |
| Python deps | `#requirements:` header (supports `pkg[extra]>=x` and `git+https`), `# py313` version annotation, lockfile computed on save, packages cached per worker in `/tmp/windmill/cache` |
| Job dir | `/tmp/windmill/wk-*/<job_id>/`, CWD of the script, **deleted when the job ends** |
| Resources | built-in `s3` type fields: `bucket`, `region`, `endPoint`, `useSSL`, `pathStyle`, `accessKey`, `secretKey`; custom types defined by JSON Schema in the UI; a Python arg typed with a `TypedDict` class named like the type gets a resource picker and receives the resolved object |
| Timeouts | per-script custom timeout in script Settings → Runtime (CE); instance default job timeout in Instance settings |
| Retention | CE keeps run details 30 days |

## Design

### 1. Where configuration lives: two resources

**`f/rissk/s3` — built-in type `s3`** (one per bucket, shared by all surveys):
`accessKey`, `secretKey`, `region`, `endPoint` (`s3.amazonaws.com` for AWS), `bucket`
(informational; the roots below are authoritative), `useSSL`, `pathStyle`.

**`f/rissk/survey_<survey>` — custom type `rissk_survey`** (one per survey). JSON Schema
(kept in the repo at `windmill/rissk_survey.schema.json`, pasted once into the UI's
"As JSON" editor when creating the type):

```json
{
  "type": "object",
  "required": ["survey", "input_root", "output_root", "questionnaires"],
  "properties": {
    "survey": {"type": "string",
      "description": "Survey folder under the roots: <root>/<survey>/latest/..."},
    "input_root": {"type": "string",
      "description": "Where the export zips live, e.g. s3://surveytool (zips at <input_root>/<survey>/latest/10_RAW/<name>_*.zip)"},
    "output_root": {"type": "string",
      "description": "Where 30_PROCESSED and 35_SCORES are written, e.g. s3://surveytool. Must be remote: the job folder is deleted after the run."},
    "questionnaires": {"type": "array", "minItems": 1,
      "items": {"type": "object", "required": ["name"],
        "properties": {
          "name": {"type": "string", "description": "Questionnaire template name as in Survey Solutions"},
          "VERSION": {"type": "array", "items": {"type": "integer"}, "default": [],
            "description": "Versions to process; [] = all versions found"},
          "filter_var": {"type": "object",
            "description": "Optional paradata filter, e.g. {\"consent_q\": \"1\"}"}}}},
    "zip_password": {"type": "string", "description": "Optional password of the export zips"},
    "parameters": {"type": "object",
      "description": "Optional extra keys merged into conf/<env>/parameters.yml (e.g. feature overrides)"}
  }
}
```

Field names deliberately mirror `globals.yml` / `questionnaires/*.yml` so a resource is a
one-to-one image of an env.

### 2. How the runner turns a resource into a Kedro env

After the repo is extracted (section 3) the runner **materialises** an env:

```
<project_root>/conf/<env>/
    globals.yml            input_root, output_root, work_root: data, survey
    questionnaires/<name>.yml   name, VERSION, filter_var   (one per questionnaire)
    parameters.yml         only when zip_password and/or parameters are given
```

- `<env>` = `survey` sanitised to `[A-Za-z0-9_-]` (e.g. `pmpmd copy` → `pmpmd_copy`).
  The env name never affects output paths (those use `${globals:survey}`).
- `work_root` is always the relative `data`, i.e. `<project_root>/data` inside the job
  folder: zips are staged, unzipped and the `20_INTERIM` intermediates written there,
  and all of it disappears with the job. This matches the notebook's purge-after-run
  behaviour, so the cleanup cell has no equivalent and needs none.
- If `conf/<env>` already exists in the checkout (a committed env of the same name) the
  runner **deletes it first and logs a warning**, so there is never doubt about which
  configuration ran.
- Layout is always the **questionnaires-folder** form (per-`<qnr>` output subfolders +
  survey-level `microdata.parquet` union), which is what `grdslchbs` and `wbalkans`
  already use. The legacy flat single-questionnaire layout is not reproduced.
- Validation happens **before any download**: non-empty `survey`, roots and
  `questionnaires`; each questionnaire has a `name`; `VERSION` is a list of ints;
  `pipeline` ∈ {`__default__`, `data_ingestion`, `feature_creation`, `rissk_scoring`};
  `repo` matches `owner/name`. When running inside Windmill (`WM_JOB_ID` set),
  `output_root` must be remote (contain `://`), otherwise the run is refused with an
  explanation. Outside Windmill a local output root is allowed (local testing).

### 3. Where code comes from: GitHub tarball

- URL: `https://api.github.com/repos/<owner>/<repo>/tarball/<ref>` where `ref` is a
  branch, tag or commit SHA. Headers: `Accept: application/vnd.github+json`,
  `X-GitHub-Api-Version: 2022-11-28`, a `User-Agent`, and `Authorization: Bearer <token>`
  **only** when a token is configured. Public repos need no token.
- The token comes from the `github_token_variable` argument: inside Windmill it is a
  variable path (e.g. `f/rissk/github_token`, a secret) read with `wmill.get_variable`;
  outside Windmill it is the name of an environment variable. Empty = no token.
- Download streams to `<job_dir>/repo.tar.gz`; extraction uses `tarfile` with
  `filter="data"` into `<job_dir>/repo/`. The single top-level directory
  (`<owner>-<repo>-<sha7>`) is the **project root**; the short SHA is recorded in the
  job result. The runner refuses a tree without a `pyproject.toml` containing
  `[tool.kedro]`.
- Why a tarball and not `git clone`: no git in the hot path, exact-ref semantics for
  branches/tags/SHAs alike, and a token can never leak into the job log through a git
  error message that echoes the remote URL.
- HTTP errors are translated: 404 → "repo or ref not found, or the token cannot read
  it"; 401 → "token rejected"; 403 → "forbidden or rate-limited".

### 4. Credentials and environment

Set **in-process, before anything imports kedro or s3fs**, from the `storage` resource:

| env var | source |
|---|---|
| `AWS_ACCESS_KEY_ID` | `accessKey` (required) |
| `AWS_SECRET_ACCESS_KEY` | `secretKey` (required) |
| `AWS_DEFAULT_REGION`, `AWS_REGION` | `region` (when given) |
| `AWS_ENDPOINT_URL` | `https://<endPoint>` (or `http://` when `useSSL` is false) **only** when `endPoint` is set and does not end in `amazonaws.com` |
| `KEDRO_DISABLE_TELEMETRY=true`, `DO_NOT_TRACK=1` | constant |
| `KEDRO_LOGGING_CONFIG` | a plain console logging YAML the runner writes to the job dir (stdout `StreamHandler`, simple formatter, `kedro`/`rissk`/root at INFO) so Windmill's log view gets flat lines, not rich tables; Kedro reads this variable when `kedro.framework.project` is first imported, hence the ordering rule |

`pathStyle` is not mapped (no env-var equivalent in botocore); AWS S3 is the validated
target. Nothing is written outside the job directory; the token and secret key are never
printed.

### 5. The runner script `f/rissk/run_rissk`

Lives in the repo at `windmill/f/rissk/run_rissk.py` (Windmill CLI layout). Header:

```python
# py313
#requirements:
#kedro==1.2.0
#kedro-datasets[pandas]>=9.1.0
#s3fs>=2024.3.1
#pandas>=2.2.3
#numpy>=2.1.0
#pyod>=1.1.5
#scipy>=1.10
#scikit-learn>=1.5
#pyyaml>=6.0
#pythresh>=0.3.0
#wmill
```

The block mirrors `[project].dependencies` in `pyproject.toml` plus `wmill`; a unit test
fails when the two drift (section "Testing"). Windmill pins exact versions in its lockfile
when the script is saved; when rissk's dependencies change, update the header and re-save.
(Alternative rejected: `rissk@git+https://…` as the only requirement would track pyproject
automatically but needs git access at lock time, which breaks for private repos.)

Signature and behaviour:

```python
class rissk_survey(TypedDict, total=False): ...   # fields of section 1
class s3(TypedDict, total=False): ...             # built-in type fields

def main(
    survey: rissk_survey,                 # resource picker in the UI / schedule form
    storage: s3,                          # resource picker, default f/rissk/s3
    pipeline: str = "__default__",        # or data_ingestion | feature_creation | rissk_scoring
    repo: str = "RowSquared/rissk",
    ref: str = "kedro_pipeline",          # branch, tag or SHA
    github_token_variable: str = "",      # e.g. f/rissk/github_token for a private repo
) -> dict:
    1. validate inputs (section 2)                       — no network yet
    2. token = resolve_token(github_token_variable)
    3. root = extract_tarball(download_tarball(repo, ref, token))
    4. env = materialize_env(root, survey)
    5. apply AWS/Kedro env vars (section 4); write logging config
    6. outcomes = run_pipeline(root, env, pipeline)
         = sys.path.insert(0, root/"src"); from rissk.run import run_survey;
           run_survey(env, project_root=root, pipeline=pipeline)
    7. print a summary; return {"survey", "env", "repo", "ref", "commit",
       "pipeline", "outcomes"}; raise RuntimeError listing the failed labels
       if any outcome != "OK"  → the Windmill job ends *failed*
```

- Per-questionnaire failure isolation is inherited from `run_survey`; the runner raises
  only after every questionnaire (and the combine step) has run.
- Internals are small pure functions (`sanitize_env_name`, `validate_survey`,
  `materialize_env`, `aws_env_from_s3`, `download_tarball`, `extract_tarball`,
  `resolve_token`, `run_pipeline`) so they are unit-testable without Windmill or network;
  `main` composes them. A `__main__` block accepts `--survey survey.json --storage
  storage.json [--pipeline …] [--ref …]` for running the real thing locally.
- Script settings in the UI: **custom timeout** sized to about twice the longest known
  run; no worker tag (default `python3` → `windmill-worker-1`).

### 6. Schedules and the "add a survey" workflow

One schedule per survey, path `f/rissk/<survey>`, pointing at `f/rissk/run_rissk` with
`survey` = that survey's resource, `storage` = `f/rissk/s3`, defaults for the rest; cron
and timezone chosen per survey. Jobs on `windmill-worker-1` run one at a time, so two
surveys scheduled at the same minute serialize.

Adding a survey: create a `rissk_survey` resource, create a schedule. Editing a survey:
edit the resource. No git, no redeploy.

### 7. Smoke test and cut-over

- Resource `f/rissk/survey_windmill_smoke`: `survey: grdslchbs`, `input_root:
  s3://surveytool`, `output_root: s3://surveytool/windmill_smoke`, questionnaire
  `slchbs_grenada_2627` versions `[10, 11, 12, 13]`. Same inputs as production, outputs
  isolated under the `windmill_smoke/` prefix.
- Run it manually with `pipeline=data_ingestion`, check
  `s3://surveytool/windmill_smoke/grdslchbs/latest/30_PROCESSED/slchbs_grenada_2627/`,
  then run the full pipeline and compare `35_SCORES/slchbs_grenada_2627/unit_rissk_scores.csv`
  with the latest JupyterHub output. Read the job's peak memory on the run page; raise
  `windmill-worker-1`'s limit in the Coolify compose if it is close to 2 GB.
- Then create the real schedules (`grdslchbs`, `wbalkans`, …) and disable the
  JupyterHub Notebook Jobs. Delete the smoke resource or keep it as a canary.

### 8. Private repositories (same runner)

Create a fine-grained GitHub PAT (resource owner RowSquared, access to the chosen repos,
permission *Contents: read-only*), store it as secret variable `f/rissk/github_token`,
and set `github_token_variable` on the schedule. For another private **Python** pipeline:
copy the runner, replace the requirements header and the `run_pipeline` body with that
repo's entrypoint. Pipelines that ship as Docker images (suso-data-etl, survey-framework)
are the `containers` worker's business and out of scope here.

## Out of scope / unchanged

- GHCR / Docker-socket runs (`windmill-worker-containers`) — deferred by decision.
- Failure notifications — CE has no schedule error handler; follow-up is a flow that
  wraps the script with an error-handler step posting to Slack/email.
- `rissk` pipeline modules, catalog, parameters, scoring: untouched.
- The NiceGUI app and `rissk_readme.ipynb`: untouched (the notebook is documented as
  legacy once the schedules are live).
- `wmill sync push` from GitHub Actions: the repo layout supports it; wiring CI is a
  follow-up.
- Removing client envs (`conf/grdslchbs`, `conf/wbalkans`) from the public repo now that
  config lives in Windmill: the user's call, listed as a follow-up.

## Repository deliverables

```
windmill/
  README.md                      step-by-step setup guide (UI clicks, resource JSON, smoke test, schedules, private repos)
  wmill.yaml                     CLI sync root (includes f/**)
  rissk_survey.schema.json       JSON Schema of the custom resource type
  f/rissk/run_rissk.py           the runner
  f/rissk/run_rissk.script.yaml  summary/description/schema metadata (regenerated by `wmill script generate-metadata` before a CLI push)
tests/test_windmill_runner.py    see Testing
README.md                        "Scheduling" paragraph points to windmill/README.md; notebook marked legacy
CLAUDE.md                        one pointer to windmill/ and the "config lives in Windmill" rule
```

## Testing

`tests/test_windmill_runner.py` loads the runner module from `windmill/f/rissk/run_rissk.py`
via `importlib` (it is not a package). No network, no Windmill:

1. `sanitize_env_name` and `validate_survey` (missing fields, bad `VERSION`, bad
   `pipeline`, local `output_root` rejected in Windmill mode, accepted otherwise).
2. `materialize_env` writes the exact files of section 2, including `parameters.yml`
   only when needed, and removes a pre-existing `conf/<env>`.
3. `aws_env_from_s3`: endpoint rule (AWS host → no `AWS_ENDPOINT_URL`; other host →
   `https://…`; `useSSL: false` → `http://…`); missing keys raise.
4. `download_tarball` with an injected opener: URL built from `repo`/`ref`, `Authorization`
   header present only with a token, HTTP errors translated.
5. `extract_tarball` on an in-memory tarball: returns the single top-level dir, parses the
   commit, rejects a tree without `[tool.kedro]`.
6. `main` with `download_tarball`/`extract_tarball`/`run_pipeline` monkeypatched: env vars
   set, logging config written, result shape, `RuntimeError` when any outcome is not `OK`.
7. Header drift: package names in the `#requirements:` block == names in
   `pyproject.toml` `[project].dependencies` ∪ {`wmill`}.

Manual verification is section 7 (smoke test) plus, after saving the script in Windmill,
checking that the computed lockfile reports Python 3.13 and the expected packages.

## Decisions log

- **Materialise an env rather than override globals with runtime params:** zero changes
  to rissk, identical to what a human does, and robust to how Kedro resolves globals.
- **Tarball over `git clone`:** exact refs, no token in error output, no git dependency.
- **Explicit requirements header over `rissk@git+https`:** works for private repos; drift
  guarded by a test.
- **Built-in `s3` resource type over three variables:** one object, reusable by other
  Windmill features, picker in the schedule form.
- **Env name = sanitised survey name, always questionnaires layout:** readable logs,
  matches production envs; the runner deletes a same-named committed env first.
- **No new worker, no tags:** a job is a script + schedule; compose is touched only to
  change the memory cap if the smoke test says so.

## Effort

About one day: runner + tests (half), docs and Windmill setup/smoke test (half).
