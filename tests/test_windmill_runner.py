"""Tests for the Windmill runner script (windmill/f/rissk/run_rissk.py).

The runner is not a package; it is loaded straight from its file. No network, no Windmill.
"""
import importlib.util
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("run_rissk", REPO / "windmill/f/rissk/run_rissk.py")
run_rissk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_rissk)

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


# --- Task 2: materialise the Kedro env -------------------------------------------------
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
    old = tmp_path / "conf/grdslchbs"
    old.mkdir(parents=True)
    (old / "stale.yml").write_text("x: 1\n")
    run_rissk.materialize_env(tmp_path, SURVEY)
    assert not (old / "stale.yml").exists()
    assert "replacing committed env conf/grdslchbs" in capsys.readouterr().out


# --- Task 3: credentials and logging config --------------------------------------------
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
    storage = {"accessKey": "AK", "secretKey": "SK"}
    storage.pop(missing)
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


# --- Task 4: fetch the repository tarball ----------------------------------------------
import io
import tarfile
import urllib.error


def _tarball(top: str, pyproject: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, text in {f"{top}/pyproject.toml": pyproject, f"{top}/src/rissk/__init__.py": ""}.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
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
    archive = tmp_path / "r.tgz"
    archive.write_bytes(_tarball("RowSquared-rissk-8cb5540", KEDRO_PYPROJECT))
    root, commit = run_rissk.extract_tarball(archive, tmp_path / "repo")
    assert root == tmp_path / "repo" / "RowSquared-rissk-8cb5540" and commit == "8cb5540"
    assert (root / "src/rissk/__init__.py").exists()


def test_extract_tarball_rejects_non_kedro_project(tmp_path):
    archive = tmp_path / "r.tgz"
    archive.write_bytes(_tarball("Owner-repo-abc1234", "[project]\nname='x'\n"))
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


# --- Task 5: main, run_pipeline, local CLI ---------------------------------------------
import json
import subprocess
import sys


def _fake_fetch(monkeypatch, tmp_path, outcomes):
    root = tmp_path / "repo" / "RowSquared-rissk-8cb5540"
    monkeypatch.setattr(run_rissk, "download_tarball", lambda *a, **k: tmp_path / "r.tgz")

    def extract(archive, dest_dir):
        (root / "conf").mkdir(parents=True)
        (root / "src/rissk").mkdir(parents=True)
        return root, "8cb5540"

    monkeypatch.setattr(run_rissk, "extract_tarball", extract)
    calls = {}

    def run_pipeline(project_root, env, pipeline):
        calls.update(project_root=project_root, env=env, pipeline=pipeline)
        return outcomes

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
    out = subprocess.run([sys.executable, str(REPO / "windmill/f/rissk/run_rissk.py"), "--help"],
                         capture_output=True, text=True)
    assert out.returncode == 0 and "--survey" in out.stdout


# --- Task 6: requirements drift guard, script metadata, resource schema ----------------
import re
import tomllib


def _normalize(name):
    return name.lower().replace("_", "-")


def test_requirements_header_matches_pyproject():
    text = (REPO / "windmill/f/rissk/run_rissk.py").read_text().splitlines()
    start = text.index("#requirements:") + 1
    header = []
    for line in text[start:]:
        if not line.startswith("#") or line.strip() == "#":
            break
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
