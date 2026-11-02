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
