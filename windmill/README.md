# Running RISSK on Windmill

Scheduled RISSK runs are Windmill jobs. The script in this folder,
[`f/rissk/run_rissk.py`](f/rissk/run_rissk.py), runs the pipeline for one survey and is a
few lines of glue: rissk itself is installed from GitHub by the script's one
`#requirements:` line, the `rissk_survey` resource is the configuration and goes to
`rissk.run.run_survey` as is, and the `s3` resource becomes the AWS environment variables.
Survey configuration and credentials live in Windmill, not in committed `conf/<env>` folders
(those stay for local runs). Design: [the 2026-11-03 spec](../docs/superpowers/specs/2026-11-03-rissk-installable-thin-runner-design.md)
(resources, schedules and the smoke test come from [the 2026-10-23 spec](../docs/superpowers/specs/2026-10-23-windmill-scheduling-design.md)).

## What runs where

| Piece | Windmill path | Source in this repo |
|---|---|---|
| Script | `f/rissk/run_rissk` (Python 3.13, default worker tag, runs on `windmill-worker-1`); installs rissk from GitHub at the commit the deploy pinned | `f/rissk/run_rissk.py`, settings in `f/rissk/run_rissk.script.yaml` |
| Resource type | `rissk_survey` | `rissk_survey.resource-type.yaml` (CLI and CI), `rissk_survey.schema.json` (same schema, to paste) |
| Survey resources | `f/rissk/survey_<survey>`, one per survey | filled from the schema (example below) |
| Bucket credentials | `f/rissk/s3`, built-in type `s3`, fields referencing the shared variables | — |
| Shared variables | `f/shared/aws_access_key_id`, `f/shared/aws_secret_access_key` (secret), `f/shared/aws_default_region` | — |
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
   A non-AWS endpoint (MinIO, Hetzner, R2) is passed on to s3fs as `AWS_ENDPOINT_URL`.
4. **Resource type and script.** Deploy them with one of the ways in
   [Deploying the runner](#deploying-the-runner); the GitHub Actions one is the intended
   one. To do it by hand instead: Resources → Resource types → *Add a resource type*, name
   `rissk_survey`, switch the editor to *As JSON* and paste
   [`rissk_survey.schema.json`](rissk_survey.schema.json); then Scripts → New → Python,
   path `f/rissk/run_rissk`, paste [`f/rissk/run_rissk.py`](f/rissk/run_rissk.py) whole
   (the three header lines included: `# py313`, `#requirements:` and the `rissk@git+…`
   line, which by hand installs the `kedro_pipeline` branch as it is when the lockfile is
   computed), summary "Run the RISSK pipeline for one survey", Deploy.
5. **Check the lockfile.** Either way, open the script's page: its lockfile must show
   Python 3.13, `kedro==1.2.0` and `rissk @ git+https://github.com/rowsquared/rissk@<commit>`.
   The dependency job clones the repository to resolve that line; the first job after a
   deploy builds and installs the rissk wheel from the clone (seconds, cached per worker
   afterwards). If the Python version is wrong, make sure `# py313` is the very first line
   and deploy again.
6. **Timeout.** The job timeout is the `timeout` key (seconds) of
   [`f/rissk/run_rissk.script.yaml`](f/rissk/run_rissk.script.yaml): set it to about twice
   the longest known run for a survey and deploy. Unset, the instance default applies
   (Instance settings → job timeout). Do not set it in the UI when the script is deployed
   with the CLI or CI: a push resets every setting that is not in the yaml.

## Deploying the runner

One thing is deployed: the runner (`f/rissk/run_rissk` and the `rissk_survey` type), whose
one requirement line names the rissk commit it installs. The committed line says
`@kedro_pipeline`. The GitHub Actions workflow (way 2) rewrites it to the pushed commit
before pushing, so every push to `kedro_pipeline` deploys the runner **and** pins the rissk
code at that commit: nothing is ever pasted, and the script page shows which commit runs.
Way 1 pushes the line as committed, which Windmill's lockfile pins to the tip of
`kedro_pipeline` at that moment.

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
`tests/test_windmill_runner.py` fails if the scope, the workflow or the script's header
drifts from this.

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
the UI; `rissk` is pinned to the tip of `kedro_pipeline` at that moment, so push again to
move it. The CLI warns that a `workspaces` section is recommended in `wmill.yaml`; that is
harmless (the section would hardcode the instance URL in the repository).

### 2. GitHub Actions doing the same push (Community Edition): the intended way

[`.github/workflows/windmill-sync.yml`](../.github/workflows/windmill-sync.yml) runs the
two commands above on every push to `kedro_pipeline`, after rewriting the requirement line
to the pushed commit, and on demand (Actions → *Deploy the Windmill runner* → *Run
workflow*, which pins the tip of the chosen branch). GitHub is the source of truth and
Windmill follows it. One-time setup, in the GitHub repository under Settings → Secrets and
variables → Actions:

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
`output_root` must be a URL (`s3://…`): the job folder is deleted after the run, the
resource form rejects anything else, and so does rissk. Any other key is rejected by rissk
with a message naming it, so a typo cannot pass silently.

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

## Running the same configuration from Python

The resource is exactly what `rissk.run.run_survey` takes, so a run can be reproduced
outside Windmill with the same dict, from a checkout (`uv run python`) or from an installed
rissk (`pip install git+https://github.com/rowsquared/rissk@kedro_pipeline`) in any
directory. AWS credentials come from the usual environment variables or profile.

```python
from rissk.run import run_survey

run_survey({"survey": "grdslchbs", "input_root": "s3://surveytool",
            "output_root": "s3://surveytool/windmill_smoke",
            "questionnaires": [{"name": "slchbs_grenada_2627", "VERSION": [10, 11, 12, 13],
                                "filter_var": {"intrv_result": "1"}}]},
           pipeline="data_ingestion")
```

`output_root` must be a URL; `file:///abs/path` writes to a local folder. Zips are staged
under `data/` in the working directory (`work_root`, optional in the dict) and nothing is
written under `conf/`.

## Updating

- **Code and runner:** push to `kedro_pipeline`. The workflow deploys the runner pinned to
  that commit and Windmill re-locks; a change to the rissk code alone redeploys too. There
  is no dependency header to maintain: rissk's `pyproject.toml` declares its own.
  Rollback: `git revert` the commit and push, or run the workflow by hand on a branch whose
  tip is the wanted commit.
- **Settings:** the timeout and the argument schema are in `f/rissk/run_rissk.script.yaml`;
  edit and push. A schedule keeps the arguments it stored when the script's schema changes:
  after an argument is dropped (as `repo`, `ref` and `github_token_variable` were on
  2026-11-03, before any schedule existed), open each schedule once and save it so a stale
  argument does not fail the run.
- **Survey configuration:** edit the resource. No redeploy.
