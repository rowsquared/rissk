# Running RISSK on Windmill

Scheduled RISSK runs are Windmill jobs. The script in this folder,
[`f/rissk/run_rissk.py`](f/rissk/run_rissk.py), runs the pipeline for one survey: it fetches
this repository from GitHub at a `ref`, writes a Kedro env from a `rissk_survey` resource,
maps an `s3` resource to the AWS environment variables and calls `rissk.run.run_survey`.
Survey configuration and credentials live in Windmill, not in committed `conf/<env>` folders
(those stay for local runs). Design: [the spec](../docs/superpowers/specs/2026-10-23-windmill-scheduling-design.md).

## What runs where

| Piece | Windmill path | Source in this repo |
|---|---|---|
| Script | `f/rissk/run_rissk` (Python 3.13, default worker tag, runs on `windmill-worker-1`) | `f/rissk/run_rissk.py`, settings in `f/rissk/run_rissk.script.yaml` |
| Resource type | `rissk_survey` | `rissk_survey.resource-type.yaml` (CLI and CI), `rissk_survey.schema.json` (same schema, to paste) |
| Survey resources | `f/rissk/survey_<survey>`, one per survey | filled from the schema (example below) |
| Bucket credentials | `f/rissk/s3`, built-in type `s3`, fields referencing the shared variables | — |
| Shared variables | `f/shared/aws_access_key_id`, `f/shared/aws_secret_access_key` (secret), `f/shared/aws_default_region` | — |
| Private-repo token | `f/rissk/github_token` (secret), only when the code repository is private | — |
| Deployment | the script and the resource type, from this folder (see [Deploying the runner](#deploying-the-runner)) | `wmill.yaml` (sync root), [`../.github/workflows/windmill-sync.yml`](../.github/workflows/windmill-sync.yml) |

The job folder is scratch: zips are staged and unzipped there and it is deleted when the job
ends. Durable state is the bucket, keyed by survey: `<output_root>/<survey>/latest/`.

## One-time setup (Windmill UI)

1. **Folders.** Create the folders `rissk` and `shared` (Folders page). A deployment never
   creates or changes folders.
2. **Shared variables.** Variables → New: `f/shared/aws_access_key_id`,
   `f/shared/aws_secret_access_key` (tick *secret*), `f/shared/aws_default_region`
   (e.g. `eu-west-1`).
3. **Bucket resource.** Resources → New, type `s3`, path `f/rissk/s3`:
   `accessKey` = `$var:f/shared/aws_access_key_id`, `secretKey` =
   `$var:f/shared/aws_secret_access_key`, `region` = `$var:f/shared/aws_default_region`,
   `endPoint` = `s3.amazonaws.com`, `useSSL` = true, `bucket` = `surveytool` (informational).
4. **Resource type and script.** Deploy them with one of the ways in
   [Deploying the runner](#deploying-the-runner); the GitHub Actions one is the intended
   one. To do it by hand instead: Resources → Resource types → *Add a resource type*, name
   `rissk_survey`, switch the editor to *As JSON* and paste
   [`rissk_survey.schema.json`](rissk_survey.schema.json); then Scripts → New → Python,
   path `f/rissk/run_rissk`, paste [`f/rissk/run_rissk.py`](f/rissk/run_rissk.py) whole
   (the `# py313` and `#requirements:` header lines included), summary "Run the RISSK
   pipeline for one survey", Deploy.
5. **Check the lockfile.** Either way, open the script's page: its lockfile must show
   Python 3.13 and `kedro==1.2.0`. If the Python version is wrong, make sure `# py313` is
   the very first line and deploy again.
6. **Timeout.** The job timeout is the `timeout` key (seconds) of
   [`f/rissk/run_rissk.script.yaml`](f/rissk/run_rissk.script.yaml): set it to about twice
   the longest known run for a survey and deploy. Unset, the instance default applies
   (Instance settings → job timeout). Do not set it in the UI when the script is deployed
   with the CLI or CI: a push resets every setting that is not in the yaml.

## Deploying the runner

Two different things get deployed. The **runner** (`f/rissk/run_rissk` and the
`rissk_survey` type) is deployed to Windmill by one of the three ways below. The **rissk
code it runs** is fetched from GitHub at `ref` on every job and is never deployed. With
way 2 in place a push to the branch updates both, and nothing is ever pasted.

This folder is in the Windmill CLI layout: `wmill.yaml` is the sync root, the script is
`f/rissk/run_rissk.py` plus `f/rissk/run_rissk.script.yaml` (summary, description,
argument schema, timeout: the whole script definition), the type is
`rissk_survey.resource-type.yaml`. The scope of a push is deliberately narrow (see
`wmill.yaml`): only `f/rissk/**`, and never variables, resources, secrets or folders. The
reason is that `wmill sync push` also **deletes** remote items that are in scope but
missing locally, and the workspace holds hand-made items (survey resources, credentials,
folders) and the ppp repository's. Resource types are workspace-wide, not under a folder,
and ignore that scope; so the sync skips them and `rissk_survey` is pushed on its own with
`wmill resource-type push`, which creates or updates and never deletes.
`tests/test_windmill_runner.py` fails if the scope or the workflow drifts from this.

### 1. `wmill sync push` from your machine (Community Edition)

Once per machine: install the CLI (Node 20 or newer) and register the workspace. The
workspace id is shown in Windmill's workspace switcher; the command then asks you to log
in through the browser or with a token (your username → Account settings → Tokens).

```bash
npm install -g windmill-cli@1.856.0
```

```bash
wmill workspace add rissk <workspace_id> https://<windmill-host>/
```

Then, from the repository root, after every change (the first command only shows what
would change):

```bash
cd windmill && wmill sync push --dry-run
```

```bash
cd windmill && wmill resource-type push rissk_survey.resource-type.yaml rissk_survey && wmill sync push --yes
```

The push creates or updates `f/rissk/run_rissk` with the metadata yaml as summary, schema
and settings. Windmill then computes the lockfile (a dependency job, visible on the
script's page) from the `#requirements:` header, exactly as when the script is saved in
the UI. The CLI warns that a `workspaces` section is recommended in `wmill.yaml`; that is
harmless (the section would hardcode the instance URL in the repository).

### 2. GitHub Actions doing the same push (Community Edition): the intended way

[`.github/workflows/windmill-sync.yml`](../.github/workflows/windmill-sync.yml) runs the
two commands above on every push to `kedro_pipeline` that touches `windmill/`, and on demand (Actions → *Deploy the Windmill runner* → *Run workflow*).
GitHub is the source of truth and Windmill follows it. One-time setup, in the GitHub
repository under Settings → Secrets and variables → Actions:

| Kind | Name | Value |
|---|---|---|
| Secret | `WMILL_TOKEN` | a Windmill user token (your username → Account settings → Tokens). An unscoped token is simplest; a scoped one needs `users:read` plus read and write on what the push touches, scripts and resource types ([User tokens](https://www.windmill.dev/docs/core_concepts/user_tokens)). |
| Variable | `WMILL_URL` | the instance URL, e.g. `https://windmill.example.org/` |
| Variable | `WMILL_WORKSPACE` | the workspace id |

The job fails with a clear message while any of the three is missing. Commits whose
message starts with `[WM]` are skipped: Windmill's own Git sync (way 3) makes those, and
pushing them back would loop. Only `kedro_pipeline` deploys: two branches deploying to the
same workspace would mean the last push wins.

### 3. Windmill's built-in Git sync

Windmill can commit every deployment made in its UI to a Git repository (Workspace
settings → Git sync, with a `git_repository` resource). On Community Edition this is
available for workspaces with up to two users. It is the *opposite* direction, Windmill →
Git. The automatic Git → Windmill deployment ("Automatically deploy changes from Git") is
Enterprise-only, so on this edition "Windmill follows GitHub" is way 2, and Windmill's
docs advise enabling either the CI action or the automatic sync, not both. Git sync is
not enabled for this repository: edits made in the UI would be committed back into
`windmill/` and compete with the code reviewed here. For a record of UI edits anyway,
point Git sync at a separate repository or branch.

## Adding a survey

Create a resource of type `rissk_survey` at `f/rissk/survey_<survey>`. For grdslchbs:

```json
{
  "survey": "grdslchbs",
  "input_root": "s3://surveytool",
  "output_root": "s3://surveytool",
  "questionnaires": [
    {"name": "slchbs_grenada_2627", "VERSION": [10, 11, 12, 13], "filter_var": {"intrv_result": "1"}}
  ]
}
```

`survey` is the folder under both roots; each questionnaire is a `<name>_*.zip` family under
`<input_root>/<survey>/latest/10_RAW/`; `VERSION: []` means every version found;
`filter_var` scores only the interviews whose paradata answer matches (grdslchbs and
svgslchbs use `{"intrv_result": "1"}`). Optional: `zip_password`, and `parameters`, whose
top-level keys are deep-merged over `conf/base/parameters.yml`, so
`{"features": {"gps": {"use": false}}}` disables GPS and keeps every other feature.

**Migrating a survey that has a committed `conf/<survey>/` env:** copy the roots from its
`globals.yml` and each `questionnaires/*.yml` (name, VERSION, filter_var) into the resource
verbatim; a dropped `filter_var` changes the scored population. Output layout is the questionnaire-subfolder one
(`35_SCORES/<name>/unit_rissk_scores.csv`, plus the survey-level
`30_PROCESSED/microdata.parquet` union).

The schedule lives on the **per-survey flow** (rissk, then ppp), described in the ppp
repository's spec
[`2026-10-30-windmill-ppp-and-survey-flow-design.md`](../../ppp/docs/superpowers/specs/2026-10-30-windmill-ppp-and-survey-flow-design.md).
Until that flow exists, a schedule can be set directly on the script with `survey` = the
survey resource and `storage` = `f/rissk/s3`.

## Smoke test (before the first real schedule)

1. Create `f/rissk/survey_windmill_smoke`: the grdslchbs resource above with
   `"output_root": "s3://surveytool/windmill_smoke"`. Same inputs as production, outputs
   isolated under the `windmill_smoke/` prefix.
2. Run the script with `survey` = that resource, `storage` = `f/rissk/s3`,
   `pipeline` = `data_ingestion`. Expected: the job succeeds, the log shows
   `Staging s3://surveytool/grdslchbs/latest/10_RAW/slchbs_grenada_2627_…zip`, and
   `aws s3 ls s3://surveytool/windmill_smoke/grdslchbs/latest/30_PROCESSED/slchbs_grenada_2627/`
   lists `microdata.parquet`, `paradata_processed.parquet`, `questionnaire.parquet`.
3. Run again with `pipeline` = `__default__`. Expected: every outcome `OK`. Compare
   `35_SCORES/slchbs_grenada_2627/unit_rissk_scores.csv` under the smoke prefix with the
   production one (same row count and columns as the last JupyterHub run).
4. Read the peak memory on the run page. If it is close to the worker's limit (2 GB on
   `windmill-worker-1` today), raise that limit in the Coolify compose before scheduling.
5. Delete the smoke resource, or keep it as a canary.

## Private repository

For a private fork or another private Kedro project: create a fine-grained GitHub token with
*Contents: read-only* on that repository, store it as the secret variable
`f/rissk/github_token`, and pass `github_token_variable` = `f/rissk/github_token` (and
`repo` / `ref`) when running or scheduling. Public repositories need no token.

## Running locally

The same script runs outside Windmill with the two resources as JSON files; a local
`output_root` is allowed there:

```bash
uv run python windmill/f/rissk/run_rissk.py --survey survey.json --storage storage.json --pipeline data_ingestion
```

Outside Windmill, `--github-token-variable` names an environment variable instead of a
Windmill variable.

## Updating

- **Code:** runs fetch the branch named by `ref` (default `kedro_pipeline`) at run time, so a
  push is live on the next run. Pin `ref` to a tag or commit on a schedule for reproducibility.
- **Runner and its dependencies:** edit `f/rissk/run_rissk.py` (when
  `[project].dependencies` in `pyproject.toml` changes, the `#requirements:` header with
  it; `tests/test_windmill_runner.py::test_requirements_header_matches_pyproject` fails
  until the header matches) and push: way 2 deploys it and Windmill re-locks. Pinning the
  lock is optional: after a deployment, `cd windmill && wmill sync pull --yes` writes
  `f/rissk/run_rissk.script.lock` and a `lock:` line in the metadata yaml; commit both and
  later pushes reuse exactly those package versions. A pinned lock is **not** recomputed
  when the header changes: delete the lock file in the same commit, push, pull it again.
- **Settings:** the timeout and the argument schema are in `f/rissk/run_rissk.script.yaml`;
  edit and push.
- **Survey configuration:** edit the resource. No redeploy.
