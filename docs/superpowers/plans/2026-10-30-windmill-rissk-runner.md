# Windmill rissk runner — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Windmill Python script that runs rissk for one survey from a `rissk_survey` resource and an `s3` resource, fetching the repo from GitHub, so the JupyterHub notebook job can be retired.

**Architecture:** One file, `windmill/f/rissk/run_rissk.py`, made of small pure functions (validate, materialise env, map credentials, download and extract a tarball) composed by `main`; rissk's own modules are untouched. The script lives in the repo in Windmill CLI layout and is pasted into Windmill (CLI push later). Tests load the file with `importlib` and never touch the network or Windmill.

**Tech Stack:** Python 3.13, stdlib (`urllib`, `tarfile`, `tomllib`, `json`), `pyyaml`, `wmill` (only inside Windmill), pytest.

**Spec:** [docs/superpowers/specs/2026-10-23-windmill-scheduling-design.md](../specs/2026-10-23-windmill-scheduling-design.md)

## Global Constraints

- Python 3.13 in Windmill: the runner's first line is `# py313`; the `#requirements:` block mirrors `pyproject.toml` `[project].dependencies` plus `wmill` (spec §5) and a test enforces it.
- `wmill` is imported lazily (inside `resolve_token`) so the file imports without Windmill.
- Nothing is written outside the job directory (`Path.cwd()` in Windmill); the token and the secret key are never printed.
- Inside Windmill (`WM_JOB_ID` set) `output_root` must contain `://`; outside, local roots are allowed (spec §2).
- Env files are written with `yaml.safe_dump(..., sort_keys=False)`; `work_root` is always `data` (spec §2).
- No changes under `src/rissk/`, `conf/base/`, or the committed survey envs.
- Commit after every task; conventional-commit messages (`feat:`, `test:`, `docs:`), ending with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

## Review Focus

1. A survey name with spaces or punctuation (`pmpmd copy`): the env folder must be sanitised while `globals.yml` keeps the raw name for the S3 paths → Task 1 (`sanitize_env_name`) and Task 2 (globals keeps raw `survey`).
2. `VERSION` typed as strings in the resource form (`["10"]`): rejected before any download, not silently accepted → Task 1.
3. `output_root` with a trailing slash (`s3://surveytool/`): stripped, otherwise the catalog builds `s3://surveytool//grdslchbs/...` → Task 2.
4. `ref` given as a commit SHA: the tarball's top folder is `<owner>-<repo>-<sha7>` either way; commit parsing must not assume a branch → Task 4.
5. Outside Windmill with `github_token_variable` naming an unset environment variable: a clear error instead of an anonymous 404 later → Task 4.

---

### Task 1: Runner file skeleton and input validation

**Files:**
- Create: `windmill/f/rissk/run_rissk.py`
- Test: `tests/test_windmill_runner.py`

**Interfaces:**
- Produces: module `run_rissk` loaded by tests via `importlib.util.spec_from_file_location("run_rissk", REPO_ROOT / "windmill/f/rissk/run_rissk.py")`; `PIPELINES: tuple[str, ...] = ("__default__", "data_ingestion", "feature_creation", "rissk_scoring")`; `class rissk_survey(TypedDict, total=False)` with fields `survey: str, input_root: str, output_root: str, questionnaires: list[dict], zip_password: str, parameters: dict`; `class s3(TypedDict, total=False)` with `bucket, region, endPoint: str; useSSL, pathStyle: bool; accessKey, secretKey: str`; `in_windmill() -> bool` (`"WM_JOB_ID" in os.environ`); `sanitize_env_name(survey: str) -> str`; `validate_survey(cfg: dict, in_windmill: bool) -> None`; `validate_run_args(pipeline: str, repo: str, ref: str) -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_windmill_runner.py
import importlib.util, pathlib, pytest
REPO = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("run_rissk", REPO / "windmill/f/rissk/run_rissk.py")
run_rissk = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(run_rissk)

SURVEY = {"survey": "grdslchbs", "input_root": "s3://surveytool", "output_root": "s3://surveytool",
          "questionnaires": [{"name": "slchbs_grenada_2627", "VERSION": [10, 11], "filter_var": None}]}

def test_sanitize_env_name():
    assert run_rissk.sanitize_env_name("pmpmd copy") == "pmpmd_copy"
    assert run_rissk.sanitize_env_name("grdslchbs") == "grdslchbs"
    assert run_rissk.sanitize_env_name("a/b:c") == "a_b_c"

@pytest.mark.parametrize("bad, msg", [
    ({**SURVEY, "survey": ""}, "survey"),
    ({**SURVEY, "questionnaires": []}, "questionnaires"),
    ({**SURVEY, "questionnaires": [{"name": ""}]}, "name"),
    ({**SURVEY, "questionnaires": [{"name": "q", "VERSION": ["10"]}]}, "VERSION"),
    ({**SURVEY, "output_root": "data"}, "output_root must be remote"),
])
def test_validate_survey_rejects(bad, msg):
    with pytest.raises(ValueError, match=msg):
        run_rissk.validate_survey(bad, in_windmill=True)

def test_validate_survey_allows_local_output_outside_windmill():
    run_rissk.validate_survey({**SURVEY, "output_root": "data"}, in_windmill=False)

@pytest.mark.parametrize("pipeline, repo, ref, msg", [
    ("scoring", "RowSquared/rissk", "main", "pipeline"),
    ("__default__", "rissk", "main", "repo"),
    ("__default__", "RowSquared/rissk", "", "ref"),
])
def test_validate_run_args_rejects(pipeline, repo, ref, msg):
    with pytest.raises(ValueError, match=msg):
        run_rissk.validate_run_args(pipeline, repo, ref)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_windmill_runner.py -v`
Expected: FAIL at import (file not found) or `AttributeError` for the functions.

- [ ] **Step 3: Create `windmill/f/rissk/run_rissk.py` with the header, the TypedDicts, `PIPELINES`, `in_windmill`, and the three functions**

Header, verbatim (spec §5):

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

`sanitize_env_name`: `re.sub(r"[^A-Za-z0-9_-]+", "_", survey.strip())`. `validate_survey` checks, in order: `survey`, `input_root`, `output_root` non-empty strings; `questionnaires` a non-empty list of dicts each with a non-empty `name`, `VERSION` absent or a list of `int`, `filter_var` absent, `None` or a dict; when `in_windmill` and `"://" not in output_root` raise `ValueError("output_root must be remote (s3://...) when running in Windmill: the job folder is deleted after the run")`. `validate_run_args`: `pipeline in PIPELINES`, `re.fullmatch(r"[\w.-]+/[\w.-]+", repo)`, `ref.strip()` non-empty; each failure names the field in the message.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_windmill_runner.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add windmill/f/rissk/run_rissk.py tests/test_windmill_runner.py
git commit -m "feat(windmill): rissk runner skeleton with input validation"
```

### Task 2: Materialise the Kedro env from the survey resource

**Files:**
- Modify: `windmill/f/rissk/run_rissk.py`
- Test: `tests/test_windmill_runner.py`

**Interfaces:**
- Produces: `materialize_env(project_root: Path, cfg: dict) -> str` returning the env name; writes `conf/<env>/globals.yml`, `conf/<env>/questionnaires/<name>.yml` per questionnaire, and `conf/<env>/parameters.yml` only when `zip_password` or `parameters` is given; removes a pre-existing `conf/<env>` first and logs a warning line `warning: replacing committed env conf/<env> with the Windmill resource`.

- [ ] **Step 1: Write the failing tests**

```python
import yaml

def test_materialize_env_writes_globals_and_questionnaires(tmp_path):
    (tmp_path / "conf").mkdir()
    env = run_rissk.materialize_env(tmp_path, {**SURVEY, "output_root": "s3://surveytool/"})
    assert env == "grdslchbs"
    g = yaml.safe_load((tmp_path / "conf/grdslchbs/globals.yml").read_text())
    assert g == {"input_root": "s3://surveytool", "work_root": "data",
                 "output_root": "s3://surveytool", "survey": "grdslchbs"}
    q = yaml.safe_load((tmp_path / "conf/grdslchbs/questionnaires/slchbs_grenada_2627.yml").read_text())
    assert q == {"name": "slchbs_grenada_2627", "VERSION": [10, 11], "filter_var": None}
    assert not (tmp_path / "conf/grdslchbs/parameters.yml").exists()

def test_materialize_env_keeps_raw_survey_name_in_globals(tmp_path):
    (tmp_path / "conf").mkdir()
    env = run_rissk.materialize_env(tmp_path, {**SURVEY, "survey": "pmpmd copy"})
    assert env == "pmpmd_copy"
    g = yaml.safe_load((tmp_path / "conf/pmpmd_copy/globals.yml").read_text())
    assert g["survey"] == "pmpmd copy"

def test_materialize_env_writes_parameters_when_given(tmp_path):
    (tmp_path / "conf").mkdir()
    run_rissk.materialize_env(tmp_path, {**SURVEY, "zip_password": "pw",
                                         "parameters": {"features": {"gps": {"use": False}}}})
    p = yaml.safe_load((tmp_path / "conf/grdslchbs/parameters.yml").read_text())
    assert p == {"zip_password": "pw", "features": {"gps": {"use": False}}}

def test_materialize_env_replaces_existing_env(tmp_path, capsys):
    old = tmp_path / "conf/grdslchbs"; old.mkdir(parents=True)
    (old / "stale.yml").write_text("x: 1\n")
    run_rissk.materialize_env(tmp_path, SURVEY)
    assert not (old / "stale.yml").exists()
    assert "replacing committed env conf/grdslchbs" in capsys.readouterr().out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_windmill_runner.py -k materialize -v`
Expected: FAIL with `AttributeError: ... has no attribute 'materialize_env'`.

- [ ] **Step 3: Implement `materialize_env`**

Defaults per questionnaire: `VERSION` → `[]`, `filter_var` → `None`. Roots: `rstrip("/")`. `parameters.yml` = `{"zip_password": ...}` (only if given) merged with `cfg.get("parameters") or {}`. Print the warning with `flush=True`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_windmill_runner.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add windmill/f/rissk/run_rissk.py tests/test_windmill_runner.py
git commit -m "feat(windmill): materialise a Kedro env from the rissk_survey resource"
```

### Task 3: Credentials and logging config

**Files:**
- Modify: `windmill/f/rissk/run_rissk.py`
- Test: `tests/test_windmill_runner.py`

**Interfaces:**
- Produces: `aws_env_from_s3(storage: dict) -> dict[str, str]`; `kedro_env(logging_config: Path) -> dict[str, str]` returning `{"KEDRO_DISABLE_TELEMETRY": "true", "DO_NOT_TRACK": "1", "KEDRO_LOGGING_CONFIG": str(logging_config)}`; `write_logging_config(job_dir: Path) -> Path` writing `job_dir / "kedro_logging.yml"`; `apply_env(mapping: dict[str, str]) -> None` (`os.environ.update`, the only place the runner touches the environment).

- [ ] **Step 1: Write the failing tests**

```python
def test_aws_env_for_aws_host_has_no_endpoint():
    env = run_rissk.aws_env_from_s3({"accessKey": "AK", "secretKey": "SK", "region": "eu-west-1",
                                     "endPoint": "s3.amazonaws.com", "useSSL": True})
    assert env == {"AWS_ACCESS_KEY_ID": "AK", "AWS_SECRET_ACCESS_KEY": "SK",
                   "AWS_DEFAULT_REGION": "eu-west-1", "AWS_REGION": "eu-west-1"}

def test_aws_env_for_custom_endpoint():
    env = run_rissk.aws_env_from_s3({"accessKey": "AK", "secretKey": "SK", "endPoint": "nbg1.your-objectstorage.com"})
    assert env["AWS_ENDPOINT_URL"] == "https://nbg1.your-objectstorage.com"
    env = run_rissk.aws_env_from_s3({"accessKey": "AK", "secretKey": "SK", "endPoint": "minio:9000", "useSSL": False})
    assert env["AWS_ENDPOINT_URL"] == "http://minio:9000"

@pytest.mark.parametrize("missing", ["accessKey", "secretKey"])
def test_aws_env_requires_keys(missing):
    storage = {"accessKey": "AK", "secretKey": "SK"}; storage.pop(missing)
    with pytest.raises(ValueError, match=missing):
        run_rissk.aws_env_from_s3(storage)

def test_logging_config_is_plain_console(tmp_path):
    path = run_rissk.write_logging_config(tmp_path)
    cfg = yaml.safe_load(path.read_text())
    assert path.name == "kedro_logging.yml"
    assert cfg["handlers"]["console"]["class"] == "logging.StreamHandler"
    assert cfg["root"]["handlers"] == ["console"]
    assert cfg["loggers"]["kedro"]["level"] == "INFO" and cfg["loggers"]["rissk"]["level"] == "INFO"
    assert run_rissk.kedro_env(path) == {"KEDRO_DISABLE_TELEMETRY": "true", "DO_NOT_TRACK": "1",
                                         "KEDRO_LOGGING_CONFIG": str(path)}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_windmill_runner.py -k "aws_env or logging" -v`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement the four functions**

Endpoint rule (spec §4): set `AWS_ENDPOINT_URL` only when `endPoint` is non-empty and does not end with `amazonaws.com`; scheme `http` only when `useSSL is False`. Logging YAML: `version: 1`, `disable_existing_loggers: false`, formatter `simple` with `"%(asctime)s %(levelname)s %(name)s: %(message)s"`, handler `console` → `logging.StreamHandler` on `ext://sys.stdout`, loggers `kedro` and `rissk` at INFO, root INFO with `[console]`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_windmill_runner.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add windmill/f/rissk/run_rissk.py tests/test_windmill_runner.py
git commit -m "feat(windmill): map the s3 resource to AWS env vars; plain Kedro logging"
```

### Task 4: Fetch the repository tarball

**Files:**
- Modify: `windmill/f/rissk/run_rissk.py`
- Test: `tests/test_windmill_runner.py`

**Interfaces:**
- Produces: `tarball_url(repo: str, ref: str) -> str`; `download_tarball(repo: str, ref: str, token: str | None, dest: Path, opener=urllib.request.urlopen) -> Path` (streams the response to `dest` with `shutil.copyfileobj`; translates `urllib.error.HTTPError` 404/401/403 into `RuntimeError` whose message contains `not found`, `token rejected`, `forbidden` respectively); `extract_tarball(archive: Path, dest_dir: Path) -> tuple[Path, str]` returning `(project_root, commit)`; `resolve_token(github_token_variable: str) -> str | None`.

- [ ] **Step 1: Write the failing tests**

```python
import io, tarfile, urllib.error, urllib.request

def _tarball(top: str, pyproject: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, text in {f"{top}/pyproject.toml": pyproject, f"{top}/src/rissk/__init__.py": ""}.items():
            data = text.encode(); info = tarfile.TarInfo(name); info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()

KEDRO_PYPROJECT = '[tool.kedro]\npackage_name = "rissk"\nsource_dir = "src"\n'

def test_download_tarball_builds_request(tmp_path):
    seen = {}
    def opener(req):
        seen["url"] = req.full_url
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        return io.BytesIO(b"bytes")
    out = run_rissk.download_tarball("RowSquared/rissk", "kedro_pipeline", "tok", tmp_path / "r.tgz", opener=opener)
    assert out.read_bytes() == b"bytes"
    assert seen["url"] == "https://api.github.com/repos/RowSquared/rissk/tarball/kedro_pipeline"
    assert seen["headers"]["authorization"] == "Bearer tok"
    assert seen["headers"]["accept"] == "application/vnd.github+json"
    assert seen["headers"]["x-github-api-version"] == "2022-11-28"

def test_download_tarball_without_token_sends_no_authorization(tmp_path):
    seen = {}
    def opener(req):
        seen["headers"] = {k.lower() for k, _ in req.header_items()}
        return io.BytesIO(b"x")
    run_rissk.download_tarball("RowSquared/rissk", "main", None, tmp_path / "r.tgz", opener=opener)
    assert "authorization" not in seen["headers"]

@pytest.mark.parametrize("code, msg", [(404, "not found"), (401, "token rejected"), (403, "forbidden")])
def test_download_tarball_translates_http_errors(tmp_path, code, msg):
    def opener(req):
        raise urllib.error.HTTPError(req.full_url, code, "x", {}, None)
    with pytest.raises(RuntimeError, match=msg):
        run_rissk.download_tarball("RowSquared/private", "main", None, tmp_path / "r.tgz", opener=opener)

def test_extract_tarball_returns_root_and_commit(tmp_path):
    archive = tmp_path / "r.tgz"; archive.write_bytes(_tarball("RowSquared-rissk-8cb5540", KEDRO_PYPROJECT))
    root, commit = run_rissk.extract_tarball(archive, tmp_path / "repo")
    assert root == tmp_path / "repo" / "RowSquared-rissk-8cb5540" and commit == "8cb5540"
    assert (root / "src/rissk/__init__.py").exists()

def test_extract_tarball_rejects_non_kedro_project(tmp_path):
    archive = tmp_path / "r.tgz"; archive.write_bytes(_tarball("Owner-repo-abc1234", "[project]\nname='x'\n"))
    with pytest.raises(RuntimeError, match="tool.kedro"):
        run_rissk.extract_tarball(archive, tmp_path / "repo")

def test_resolve_token_outside_windmill(monkeypatch):
    monkeypatch.delenv("WM_JOB_ID", raising=False)
    assert run_rissk.resolve_token("") is None
    monkeypatch.setenv("GH_TOKEN_TEST", "abc")
    assert run_rissk.resolve_token("GH_TOKEN_TEST") == "abc"
    monkeypatch.delenv("GH_TOKEN_TEST")
    with pytest.raises(ValueError, match="GH_TOKEN_TEST"):
        run_rissk.resolve_token("GH_TOKEN_TEST")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_windmill_runner.py -k "tarball or token" -v`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Implement the four functions**

`download_tarball`: `urllib.request.Request(url, headers={...})` with `Accept`, `X-GitHub-Api-Version`, `User-Agent: rissk-windmill-runner`, plus `Authorization: Bearer <token>` when `token`; `with opener(req) as resp, open(dest, "wb") as fh: shutil.copyfileobj(resp, fh)`. `extract_tarball`: `tarfile.open(archive)` → `extractall(dest_dir, filter="data")`; exactly one top-level directory else `RuntimeError`; `tomllib.loads((root / "pyproject.toml").read_text())` must have `tool.kedro` else `RuntimeError("... no [tool.kedro] ...")`; `commit = root.name.rsplit("-", 1)[-1]`. `resolve_token`: `""` → `None`; `in_windmill()` → `import wmill; return wmill.get_variable(name)`; else `os.environ[name]` with `ValueError(f"environment variable {name} is not set")` when missing.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_windmill_runner.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add windmill/f/rissk/run_rissk.py tests/test_windmill_runner.py
git commit -m "feat(windmill): fetch the rissk repo tarball from GitHub, public or private"
```

### Task 5: `main`, `run_pipeline`, and the local CLI

**Files:**
- Modify: `windmill/f/rissk/run_rissk.py`
- Test: `tests/test_windmill_runner.py`

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces: `run_pipeline(project_root: Path, env: str, pipeline: str) -> dict[str, str]` (`sys.path.insert(0, str(project_root / "src"))`, `from rissk.run import run_survey`, `return run_survey(env, project_root=project_root, pipeline=pipeline)`); `main(survey: rissk_survey, storage: s3, pipeline: str = "__default__", repo: str = "RowSquared/rissk", ref: str = "kedro_pipeline", github_token_variable: str = "") -> dict` returning `{"survey", "env", "repo", "ref", "commit", "pipeline", "outcomes"}` and raising `RuntimeError` naming the failed labels when any outcome is not `"OK"`; a `__main__` block with `argparse` (`--survey FILE.json`, `--storage FILE.json`, `--pipeline`, `--repo`, `--ref`, `--github-token-variable`) printing the result as JSON.

- [ ] **Step 1: Write the failing tests**

```python
import json, os

def _fake_fetch(monkeypatch, tmp_path, outcomes):
    root = tmp_path / "repo" / "RowSquared-rissk-8cb5540"
    monkeypatch.setattr(run_rissk, "download_tarball", lambda *a, **k: tmp_path / "r.tgz")
    def extract(archive, dest_dir):
        (root / "conf").mkdir(parents=True); (root / "src/rissk").mkdir(parents=True)
        return root, "8cb5540"
    monkeypatch.setattr(run_rissk, "extract_tarball", extract)
    calls = {}
    def run_pipeline(project_root, env, pipeline):
        calls.update(project_root=project_root, env=env, pipeline=pipeline); return outcomes
    monkeypatch.setattr(run_rissk, "run_pipeline", run_pipeline)
    applied = {}
    monkeypatch.setattr(run_rissk, "apply_env", applied.update)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("WM_JOB_ID", raising=False)
    return root, calls, applied

STORAGE = {"accessKey": "AK", "secretKey": "SK", "region": "eu-west-1", "endPoint": "s3.amazonaws.com"}

def test_main_runs_and_reports(monkeypatch, tmp_path):
    root, calls, applied = _fake_fetch(monkeypatch, tmp_path, {"slchbs_grenada_2627": "OK", "combine": "OK"})
    result = run_rissk.main(SURVEY, STORAGE)
    assert result == {"survey": "grdslchbs", "env": "grdslchbs", "repo": "RowSquared/rissk", "ref": "kedro_pipeline",
                      "commit": "8cb5540", "pipeline": "__default__",
                      "outcomes": {"slchbs_grenada_2627": "OK", "combine": "OK"}}
    assert calls == {"project_root": root, "env": "grdslchbs", "pipeline": "__default__"}
    assert (root / "conf/grdslchbs/globals.yml").exists()
    assert applied["AWS_ACCESS_KEY_ID"] == "AK" and applied["KEDRO_DISABLE_TELEMETRY"] == "true"
    assert applied["KEDRO_LOGGING_CONFIG"] == str(tmp_path / "kedro_logging.yml")

def test_main_raises_when_a_questionnaire_failed(monkeypatch, tmp_path):
    _fake_fetch(monkeypatch, tmp_path, {"q1": "FAILED (ValueError: boom)", "combine": "OK"})
    with pytest.raises(RuntimeError, match="q1"):
        run_rissk.main(SURVEY, STORAGE)

def test_main_validates_before_fetching(monkeypatch, tmp_path):
    monkeypatch.setattr(run_rissk, "download_tarball", lambda *a, **k: pytest.fail("fetched"))
    with pytest.raises(ValueError, match="pipeline"):
        run_rissk.main(SURVEY, STORAGE, pipeline="nope")

def test_cli_help_runs():
    import subprocess, sys
    out = subprocess.run([sys.executable, str(REPO / "windmill/f/rissk/run_rissk.py"), "--help"],
                         capture_output=True, text=True)
    assert out.returncode == 0 and "--survey" in out.stdout
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_windmill_runner.py -k "main or cli" -v`
Expected: FAIL with `AttributeError: ... 'main'` / non-zero `--help`.

- [ ] **Step 3: Implement `run_pipeline`, `main`, and the `__main__` block**

`main` order (spec §5): `validate_run_args` → `validate_survey(survey, in_windmill())` → `token = resolve_token(...)` → `job_dir = Path.cwd()` → `archive = download_tarball(repo, ref, token, job_dir / "repo.tar.gz")` → `root, commit = extract_tarball(archive, job_dir / "repo")` → `env = materialize_env(root, survey)` → `apply_env({**aws_env_from_s3(storage), **kedro_env(write_logging_config(job_dir))})` → `outcomes = run_pipeline(root, env, pipeline)` → print one summary line per outcome → return/raise. Print the project root, commit and env before running; never print the token or the secret key.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_windmill_runner.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add windmill/f/rissk/run_rissk.py tests/test_windmill_runner.py
git commit -m "feat(windmill): main entry point composing fetch, env, credentials and run_survey"
```

### Task 6: Requirements drift guard, script metadata, resource schema, CLI root

**Files:**
- Create: `windmill/f/rissk/run_rissk.script.yaml`, `windmill/rissk_survey.schema.json`, `windmill/wmill.yaml`
- Test: `tests/test_windmill_runner.py`

**Interfaces:**
- Produces: the three files the operator pastes or pushes (spec §1, §5, "Repository deliverables").

- [ ] **Step 1: Write the failing tests**

```python
import re, tomllib

def _normalize(name): return name.lower().replace("_", "-")

def test_requirements_header_matches_pyproject():
    text = (REPO / "windmill/f/rissk/run_rissk.py").read_text().splitlines()
    start = text.index("#requirements:") + 1
    header = []
    for line in text[start:]:
        if not line.startswith("#") or line.strip() == "#": break
        header.append(_normalize(re.match(r"#([A-Za-z0-9_.-]+)", line).group(1)))
    deps = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["dependencies"]
    expected = {_normalize(re.match(r"[A-Za-z0-9_.-]+", d).group(0)) for d in deps} | {"wmill"}
    assert set(header) == expected

def test_schema_and_metadata_are_consistent():
    schema = json.loads((REPO / "windmill/rissk_survey.schema.json").read_text())
    assert set(schema["required"]) == {"survey", "input_root", "output_root", "questionnaires"}
    meta = yaml.safe_load((REPO / "windmill/f/rissk/run_rissk.script.yaml").read_text())
    props = meta["schema"]["properties"]
    assert props["survey"]["format"] == "resource-rissk_survey" and props["storage"]["format"] == "resource-s3"
    assert props["pipeline"]["enum"] == list(run_rissk.PIPELINES)
    assert props["ref"]["default"] == "kedro_pipeline" and props["repo"]["default"] == "RowSquared/rissk"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_windmill_runner.py -k "requirements or schema" -v`
Expected: FAIL (files missing).

- [ ] **Step 3: Create the three files**

`windmill/rissk_survey.schema.json`: the JSON Schema from spec §1, verbatim. `windmill/f/rissk/run_rissk.script.yaml`: `summary: Run the RISSK pipeline for one survey`, a two-sentence `description`, `kind: script`, and a `schema` (`type: object`, `order: [survey, storage, pipeline, repo, ref, github_token_variable]`, `required: [survey, storage]`) whose properties are `survey` (`type: object`, `format: resource-rissk_survey`), `storage` (`type: object`, `format: resource-s3`), `pipeline` (`type: string`, `default: __default__`, `enum` = the four names), `repo` (default `RowSquared/rissk`), `ref` (default `kedro_pipeline`), `github_token_variable` (default `''`). `windmill/wmill.yaml`: `defaultTs: bun`, `includes: ["f/**"]`, `excludes: []`.

- [ ] **Step 4: Run the whole test suite**

Run: `uv run pytest -q`
Expected: all tests pass, including the pre-existing ones.

- [ ] **Step 5: Commit**

```bash
git add windmill/ tests/test_windmill_runner.py
git commit -m "feat(windmill): script metadata, rissk_survey schema, CLI sync root, drift guard"
```

### Task 7: Operator guide and documentation

**Files:**
- Create: `windmill/README.md`
- Modify: `README.md` (the "Scheduling (JupyterHub Notebook Jobs)" paragraph under "4. Run RISSK"), `CLAUDE.md` (the "GUI" section's scheduled-run paragraph)

- [ ] **Step 1: Write `windmill/README.md`**

Sections, in order: *What runs where* (one table: script, resources, variables, worker); *One-time setup* — numbered UI steps: create folder `rissk` and the three shared variables `f/shared/aws_access_key_id`, `f/shared/aws_secret_access_key` (secret), `f/shared/aws_default_region`; create resource `f/rissk/s3` of type `s3` whose `accessKey`, `secretKey`, `region` are `$var:f/shared/...` references and `endPoint` is `s3.amazonaws.com`; create resource type `rissk_survey` from `rissk_survey.schema.json` via "As JSON"; create script `f/rissk/run_rissk` by pasting `f/rissk/run_rissk.py`, then in Settings → Runtime set a custom timeout (twice the longest known run); *Adding a survey* — a `rissk_survey` resource `f/rissk/survey_<survey>` with a filled example for grdslchbs (survey `grdslchbs`, both roots `s3://surveytool`, questionnaire `slchbs_grenada_2627` versions `[10, 11, 12, 13]`) and the note that the schedule lives on the per-survey flow (link to the ppp spec); *Smoke test* (spec §7 verbatim: the `windmill_smoke` resource with output root `s3://surveytool/windmill_smoke`, ingestion-only run, full run, comparison, peak memory); *Private repository* (fine-grained token, secret variable `f/rissk/github_token`, the `github_token_variable` argument); *Running locally* (`uv run python windmill/f/rissk/run_rissk.py --survey survey.json --storage storage.json`); *Updating* (when `pyproject.toml` dependencies change, update the header and re-save the script; the test `test_requirements_header_matches_pyproject` fails until you do).

- [ ] **Step 2: Update `README.md` and `CLAUDE.md`**

README: replace the "Scheduling (JupyterHub Notebook Jobs)" paragraph with "**Scheduling (Windmill).** Scheduled runs are Windmill jobs: see [windmill/README.md](windmill/README.md). `rissk_readme.ipynb` remains a local, in-process runner." CLAUDE.md: in the GUI section, after the notebook paragraph, add one paragraph: production scheduling is Windmill (`windmill/` folder, runner + operator guide); survey configuration and credentials live in Windmill resources, not committed envs; the committed `conf/<env>` folders are for local runs.

- [ ] **Step 3: Verify the links and run the suite**

Run: `uv run pytest -q && grep -n "windmill/README.md" README.md CLAUDE.md`
Expected: tests pass; both files reference the guide.

- [ ] **Step 4: Commit**

```bash
git add windmill/README.md README.md CLAUDE.md
git commit -m "docs: Windmill operator guide; scheduling now points at Windmill"
```

### Task 8: Windmill setup and smoke test (manual, verified)

**Files:** none in the repo; Windmill UI and the bucket.

**Interfaces:**
- Consumes: `windmill/README.md` (Task 7) as the checklist; `windmill/rissk_survey.schema.json`.
- Produces: a working `f/rissk/run_rissk` script and the resources the per-survey flow (ppp plan) needs.

- [ ] **Step 1: Do the one-time setup** from `windmill/README.md` (variables, `f/rissk/s3`, resource type, script, timeout).

- [ ] **Step 2: Verify the saved script's lockfile**

In the script's page open the lockfile: it must list Python 3.13 and `kedro==1.2.0`. If the Python version is not 3.13, the `# py313` line is not being honoured: move it to be the very first line and re-save.

- [ ] **Step 3: Create the smoke resource `f/rissk/survey_windmill_smoke`** (survey `grdslchbs`, input root `s3://surveytool`, output root `s3://surveytool/windmill_smoke`, questionnaire `slchbs_grenada_2627`, versions `[10, 11, 12, 13]`).

- [ ] **Step 4: Run ingestion only**

Run the script with `survey` = the smoke resource, `storage` = `f/rissk/s3`, `pipeline` = `data_ingestion`.
Expected: job succeeds; the log shows `Staging s3://surveytool/grdslchbs/latest/10_RAW/slchbs_grenada_2627_...zip`; then
`aws s3 ls s3://surveytool/windmill_smoke/grdslchbs/latest/30_PROCESSED/slchbs_grenada_2627/` lists `microdata.parquet`, `paradata_processed.parquet`, `questionnaire.parquet`.

- [ ] **Step 5: Run the full pipeline and compare**

Run with `pipeline` = `__default__`. Expected: job succeeds with every outcome `OK`. Then:

```bash
aws s3 cp s3://surveytool/windmill_smoke/grdslchbs/latest/35_SCORES/slchbs_grenada_2627/unit_rissk_scores.csv /tmp/smoke.csv
aws s3 cp s3://surveytool/grdslchbs/latest/35_SCORES/slchbs_grenada_2627/unit_rissk_scores.csv /tmp/prod.csv
uv run python -c "import pandas as pd; a=pd.read_csv('/tmp/smoke.csv'); b=pd.read_csv('/tmp/prod.csv'); print(len(a), len(b), a.columns.equals(b.columns))"
```
Expected: same row count and columns as the last JupyterHub run (scores may differ slightly if the inputs changed since).

- [ ] **Step 6: Read the peak memory on the run page.** If it exceeds 1.5 GB, raise `windmill-worker-1`'s `memory` limit in the Coolify compose and redeploy before scheduling.

- [ ] **Step 7: Record the outcome** in `windmill/README.md` under a "Verified on" line (date, commit, peak memory) and commit:

```bash
git add windmill/README.md
git commit -m "docs: record the Windmill smoke test result"
```
