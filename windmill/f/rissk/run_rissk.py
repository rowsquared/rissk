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
"""Windmill script f/rissk/run_rissk — run the RISSK pipeline for one survey.

Inputs are two Windmill resources: a `rissk_survey` (the survey's configuration) and an
`s3` (the bucket credentials). The script fetches the rissk repository from GitHub at
`ref`, writes a throwaway Kedro env from the survey resource into that checkout, exports
the bucket credentials as the standard AWS environment variables and calls
`rissk.run.run_survey`. The job fails when any questionnaire failed.

Design: docs/superpowers/specs/2026-10-23-windmill-scheduling-design.md (rissk repo).
Runs outside Windmill too (`python run_rissk.py --help`), with plain dicts as resources.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tarfile
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import TypedDict

import yaml

PIPELINES: tuple[str, ...] = ("__default__", "data_ingestion", "feature_creation", "rissk_scoring")


class rissk_survey(TypedDict, total=False):
    """Windmill resource type `rissk_survey` (windmill/rissk_survey.schema.json)."""

    survey: str
    input_root: str
    output_root: str
    questionnaires: list[dict]
    zip_password: str
    parameters: dict


class s3(TypedDict, total=False):
    """Windmill's built-in resource type `s3`."""

    bucket: str
    region: str
    endPoint: str
    useSSL: bool
    pathStyle: bool
    accessKey: str
    secretKey: str


def in_windmill() -> bool:
    """True when running as a Windmill job (Windmill sets WM_JOB_ID)."""
    return "WM_JOB_ID" in os.environ


def sanitize_env_name(survey: str) -> str:
    """Kedro env folder name for a survey: anything but [A-Za-z0-9_-] becomes `_`."""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", survey.strip())


def validate_survey(cfg: dict, in_windmill: bool) -> None:
    """Reject an incomplete or inconsistent `rissk_survey` resource before any download."""
    for key in ("survey", "input_root", "output_root"):
        value = cfg.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"rissk_survey.{key} must be a non-empty string")
    questionnaires = cfg.get("questionnaires")
    if not isinstance(questionnaires, list) or not questionnaires:
        raise ValueError("rissk_survey.questionnaires must be a non-empty list")
    for i, q in enumerate(questionnaires):
        if not isinstance(q, dict) or not isinstance(q.get("name"), str) or not q["name"].strip():
            raise ValueError(f"rissk_survey.questionnaires[{i}].name must be a non-empty string")
        versions = q.get("VERSION", [])
        if not isinstance(versions, list) or not all(isinstance(v, int) and not isinstance(v, bool) for v in versions):
            raise ValueError(f"rissk_survey.questionnaires[{i}].VERSION must be a list of integers")
        filter_var = q.get("filter_var")
        if filter_var is not None and not isinstance(filter_var, dict):
            raise ValueError(f"rissk_survey.questionnaires[{i}].filter_var must be a mapping or null")
    if in_windmill and "://" not in cfg["output_root"]:
        raise ValueError(
            "output_root must be remote (s3://...) when running in Windmill: "
            "the job folder is deleted after the run"
        )


def validate_run_args(pipeline: str, repo: str, ref: str) -> None:
    """Reject an unknown pipeline name, a malformed `owner/name` repo, or an empty ref."""
    if pipeline not in PIPELINES:
        raise ValueError(f"pipeline must be one of {', '.join(PIPELINES)}; got {pipeline!r}")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo or ""):
        raise ValueError(f"repo must be 'owner/name'; got {repo!r}")
    if not (ref or "").strip():
        raise ValueError("ref must be a branch, tag or commit SHA")


def materialize_env(project_root: Path, cfg: dict) -> str:
    """Write the Kedro env `conf/<env>/` that mirrors a `rissk_survey` resource.

    globals.yml (roots + survey), one questionnaires/<name>.yml per questionnaire, and
    parameters.yml only when a zip password or parameter overrides are given (overrides are
    deep-merged over conf/base/parameters.yml, key by key). A committed
    env of the same name in the checkout is replaced, with a warning, so the resource is
    always what runs. Returns the env name.
    """
    env = sanitize_env_name(cfg["survey"])
    env_dir = project_root / "conf" / env
    if env_dir.exists():
        print(f"warning: replacing committed env conf/{env} with the Windmill resource", flush=True)
        shutil.rmtree(env_dir)
    (env_dir / "questionnaires").mkdir(parents=True)

    def dump(path: Path, data: dict) -> None:
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    dump(env_dir / "globals.yml", {
        "input_root": cfg["input_root"].rstrip("/"),
        "work_root": "data",
        "output_root": cfg["output_root"].rstrip("/"),
        "survey": cfg["survey"],
    })
    for q in cfg["questionnaires"]:
        dump(env_dir / "questionnaires" / f"{q['name']}.yml", {
            "name": q["name"],
            "VERSION": list(q.get("VERSION") or []),
            "filter_var": q.get("filter_var"),
        })
    parameters = {}
    if cfg.get("zip_password"):
        parameters["zip_password"] = cfg["zip_password"]
    overrides = cfg.get("parameters") or {}
    if overrides:
        # Kedro's default env merge replaces a top-level key wholesale, so a partial
        # override (e.g. {"features": {"gps": {"use": false}}}) is merged over the base
        # value of that key here; only the overridden keys are written.
        base_path = project_root / "conf" / "base" / "parameters.yml"
        base = (yaml.safe_load(base_path.read_text(encoding="utf-8")) or {}) if base_path.exists() else {}
        for key, value in overrides.items():
            parameters[key] = _deep_merge(base.get(key), value)
    if parameters:
        dump(env_dir / "parameters.yml", parameters)
    return env


def _deep_merge(base, override):
    """`override` wins; nested mappings are merged key by key instead of replaced."""
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            merged[key] = _deep_merge(base.get(key), value)
        return merged
    return override


def aws_env_from_s3(storage: dict) -> dict[str, str]:
    """Map Windmill's `s3` resource to the AWS environment variables s3fs/botocore read.

    `AWS_ENDPOINT_URL` is set only for a non-AWS endpoint (MinIO, Hetzner, R2), with
    `http://` when `useSSL` is explicitly false. `pathStyle` has no env-var equivalent and
    is ignored.
    """
    for key in ("accessKey", "secretKey"):
        if not storage.get(key):
            raise ValueError(f"s3 resource field {key} is required")
    env = {"AWS_ACCESS_KEY_ID": storage["accessKey"], "AWS_SECRET_ACCESS_KEY": storage["secretKey"]}
    if storage.get("region"):
        env["AWS_DEFAULT_REGION"] = storage["region"]
        env["AWS_REGION"] = storage["region"]
    endpoint = (storage.get("endPoint") or "").strip()
    if endpoint and not endpoint.endswith("amazonaws.com"):
        scheme = "http" if storage.get("useSSL") is False else "https"
        env["AWS_ENDPOINT_URL"] = f"{scheme}://{endpoint}"
    return env


def write_logging_config(job_dir: Path) -> Path:
    """Write a plain console logging config for Kedro (no rich tables in the Windmill log)."""
    path = job_dir / "kedro_logging.yml"
    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"simple": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
        "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple",
                                 "stream": "ext://sys.stdout"}},
        "loggers": {"kedro": {"level": "INFO"}, "rissk": {"level": "INFO"}},
        "root": {"handlers": ["console"], "level": "INFO"},
    }
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path


def kedro_env(logging_config: Path) -> dict[str, str]:
    """Environment Kedro reads at import time: no telemetry, our logging config."""
    return {"KEDRO_DISABLE_TELEMETRY": "true", "DO_NOT_TRACK": "1",
            "KEDRO_LOGGING_CONFIG": str(logging_config)}


def apply_env(mapping: dict[str, str]) -> None:
    """The one place the runner touches the process environment."""
    os.environ.update(mapping)


def tarball_url(repo: str, ref: str) -> str:
    """GitHub's tarball endpoint; `ref` is a branch, tag or commit SHA."""
    return f"https://api.github.com/repos/{repo}/tarball/{ref}"


def download_tarball(repo: str, ref: str, token: str | None, dest: Path,
                     opener=urllib.request.urlopen) -> Path:
    """Stream the repository tarball at `ref` to `dest`; a token is sent only when given."""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "rissk-windmill-runner",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(tarball_url(repo, ref), headers=headers)
    try:
        with opener(request) as response, open(dest, "wb") as fh:
            shutil.copyfileobj(response, fh)
    except urllib.error.HTTPError as exc:
        reasons = {
            404: "repo or ref not found, or the token cannot read it",
            401: "token rejected by GitHub",
            403: "forbidden or rate-limited by GitHub",
        }
        raise RuntimeError(f"GitHub {exc.code} for {repo}@{ref}: {reasons.get(exc.code, exc.reason)}") from exc
    return dest


def extract_tarball(archive: Path, dest_dir: Path) -> tuple[Path, str]:
    """Extract the tarball; return the project root (its single top folder) and the commit.

    The top folder is named `<owner>-<repo>-<sha7>` by GitHub whatever kind of ref was
    requested. The tree must be a Kedro project (`[tool.kedro]` in pyproject.toml).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        tar.extractall(dest_dir, filter="data")
    tops = [p for p in dest_dir.iterdir() if p.is_dir()]
    if len(tops) != 1:
        raise RuntimeError(f"expected one top-level folder in the tarball, found {len(tops)}")
    root = tops[0]
    pyproject = root / "pyproject.toml"
    tool = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("tool", {}) if pyproject.exists() else {}
    if "kedro" not in tool:
        raise RuntimeError(f"{root.name} is not a Kedro project: no [tool.kedro] in pyproject.toml")
    return root, root.name.rsplit("-", 1)[-1]


def resolve_token(github_token_variable: str) -> str | None:
    """A GitHub token, or None. Inside Windmill the argument is a variable path; outside
    it is the name of an environment variable."""
    name = (github_token_variable or "").strip()
    if not name:
        return None
    if in_windmill():
        import wmill  # only available inside a Windmill job

        return wmill.get_variable(name)
    try:
        return os.environ[name]
    except KeyError:
        raise ValueError(f"environment variable {name} is not set (github_token_variable)") from None


def run_pipeline(project_root: Path, env: str, pipeline: str) -> dict[str, str]:
    """Run rissk from the checkout: its `src/` goes first on sys.path, then run_survey."""
    sys.path.insert(0, str(project_root / "src"))
    from rissk.run import run_survey  # from the checkout, imported only now (after the env is set)

    return run_survey(env, project_root=project_root, pipeline=pipeline)


def main(
    survey: rissk_survey,
    storage: s3,
    pipeline: str = "__default__",
    repo: str = "RowSquared/rissk",
    ref: str = "kedro_pipeline",
    github_token_variable: str = "",
) -> dict:
    """Run rissk for one survey. Fails the job when any questionnaire failed."""
    validate_run_args(pipeline, repo, ref)
    validate_survey(survey, in_windmill())
    token = resolve_token(github_token_variable)

    job_dir = Path.cwd()
    print(f"fetching {repo}@{ref}", flush=True)
    archive = download_tarball(repo, ref, token, job_dir / "repo.tar.gz")
    root, commit = extract_tarball(archive, job_dir / "repo")
    env = materialize_env(root, survey)
    print(f"project root {root} (commit {commit}), env {env}, pipeline {pipeline}", flush=True)

    apply_env({**aws_env_from_s3(storage), **kedro_env(write_logging_config(job_dir))})
    outcomes = run_pipeline(root, env, pipeline)

    for label, outcome in outcomes.items():
        print(f"{label}: {outcome}", flush=True)
    result = {"survey": survey["survey"], "env": env, "repo": repo, "ref": ref, "commit": commit,
              "pipeline": pipeline, "outcomes": dict(outcomes)}
    failed = [label for label, outcome in outcomes.items() if outcome != "OK"]
    if failed:
        raise RuntimeError(f"rissk run failed for: {failed}")
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run rissk for one survey outside Windmill.")
    parser.add_argument("--survey", required=True, help="JSON file with the rissk_survey resource content")
    parser.add_argument("--storage", required=True, help="JSON file with the s3 resource content")
    parser.add_argument("--pipeline", default="__default__", choices=PIPELINES)
    parser.add_argument("--repo", default="RowSquared/rissk")
    parser.add_argument("--ref", default="kedro_pipeline")
    parser.add_argument("--github-token-variable", default="",
                        help="name of an environment variable holding a GitHub token (private repos)")
    args = parser.parse_args()
    with open(args.survey, encoding="utf-8") as fh:
        survey_cfg = json.load(fh)
    with open(args.storage, encoding="utf-8") as fh:
        storage_cfg = json.load(fh)
    print(json.dumps(main(survey_cfg, storage_cfg, args.pipeline, args.repo, args.ref,
                          args.github_token_variable), indent=2))
