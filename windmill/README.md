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
| Script | `f/rissk/run_rissk` (Python 3.13, default worker tag, runs on `windmill-worker-1`) | `f/rissk/run_rissk.py`, metadata `f/rissk/run_rissk.script.yaml` |
| Resource type | `rissk_survey` | `rissk_survey.schema.json` |
| Survey resources | `f/rissk/survey_<survey>`, one per survey | filled from the schema (example below) |
| Bucket credentials | `f/rissk/s3`, built-in type `s3`, fields referencing the shared variables | — |
| Shared variables | `f/shared/aws_access_key_id`, `f/shared/aws_secret_access_key` (secret), `f/shared/aws_default_region` | — |
| Private-repo token | `f/rissk/github_token` (secret), only when the code repository is private | — |
| CLI sync root | `wmill.yaml` (`wmill sync push` from this folder, optional) | — |

The job folder is scratch: zips are staged and unzipped there and it is deleted when the job
ends. Durable state is the bucket, keyed by survey: `<output_root>/<survey>/latest/`.

## One-time setup (Windmill UI)

1. **Folders.** Create the folders `rissk` and `shared` (Folders page).
2. **Shared variables.** Variables → New: `f/shared/aws_access_key_id`,
   `f/shared/aws_secret_access_key` (tick *secret*), `f/shared/aws_default_region`
   (e.g. `eu-west-1`).
3. **Bucket resource.** Resources → New, type `s3`, path `f/rissk/s3`:
   `accessKey` = `$var:f/shared/aws_access_key_id`, `secretKey` =
   `$var:f/shared/aws_secret_access_key`, `region` = `$var:f/shared/aws_default_region`,
   `endPoint` = `s3.amazonaws.com`, `useSSL` = true, `bucket` = `surveytool` (informational).
4. **Resource type.** Resources → Resource types → *Add a resource type*: name
   `rissk_survey`, switch the editor to *As JSON* and paste
   [`rissk_survey.schema.json`](rissk_survey.schema.json).
5. **Script.** Scripts → New → Python, path `f/rissk/run_rissk`, paste
   [`f/rissk/run_rissk.py`](f/rissk/run_rissk.py) whole (the `# py313` and `#requirements:`
   header lines included), summary "Run the RISSK pipeline for one survey". Deploy. Open the
   script's lockfile: it must show Python 3.13 and `kedro==1.2.0`. If the Python version is
   wrong, make sure `# py313` is the very first line and re-save.
6. **Timeout.** Script → Settings → Runtime → custom timeout: about twice the longest known
   run for a survey. Also check Instance settings for the default job timeout.

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
- **Dependencies:** when `[project].dependencies` in `pyproject.toml` changes, update the
  `#requirements:` header in `f/rissk/run_rissk.py` and re-save the script in Windmill so it
  re-locks. `tests/test_windmill_runner.py::test_requirements_header_matches_pyproject` fails
  until the header matches.
- **Survey configuration:** edit the resource. No redeploy.
