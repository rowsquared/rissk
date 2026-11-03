from pathlib import Path

import pytest

from rissk.run import _runtime_params, load_questionnaire_configs, validate_survey_config


def test_returns_empty_when_no_questionnaires_dir(tmp_path):
    (tmp_path / "conf" / "solo").mkdir(parents=True)
    assert load_questionnaire_configs("solo", tmp_path) == []


def test_parses_and_sorts_questionnaire_yamls(tmp_path):
    qdir = tmp_path / "conf" / "pmpmd" / "questionnaires"
    qdir.mkdir(parents=True)
    (qdir / "household.yml").write_text("name: pmpmd_household\nVERSION: []\nfilter_var: null\n")
    (qdir / "community.yml").write_text("name: pmpmd_community\nVERSION: [2, 3]\nfilter_var: null\n")
    got = load_questionnaire_configs("pmpmd", tmp_path)
    assert [q["name"] for q in got] == ["pmpmd_community", "pmpmd_household"]  # sorted by filename
    assert got[0]["VERSION"] == [2, 3]


# --- Configuration as data: validation and runtime params ----------------------------
SURVEY = {"survey": "grdslchbs", "input_root": "s3://surveytool", "output_root": "s3://surveytool",
          "questionnaires": [{"name": "slchbs_grenada_2627", "VERSION": [10, 11],
                              "filter_var": {"intrv_result": "1"}}]}


def test_validate_normalises_a_minimal_mapping():
    cfg = validate_survey_config({**SURVEY, "input_root": "s3://surveytool/", "output_root": "s3://surveytool/"})
    assert cfg == {"survey": "grdslchbs", "input_root": "s3://surveytool", "output_root": "s3://surveytool",
                   "work_root": "data", "zip_password": None, "parameters": {},
                   "questionnaires": [{"name": "slchbs_grenada_2627", "VERSION": [10, 11],
                                       "filter_var": {"intrv_result": "1"}}]}


def test_validate_defaults_version_and_filter_var():
    cfg = validate_survey_config({**SURVEY, "questionnaires": [{"name": "q"}, {"name": "r", "VERSION": None}]})
    assert cfg["questionnaires"] == [{"name": "q", "VERSION": [], "filter_var": None},
                                     {"name": "r", "VERSION": [], "filter_var": None}]


@pytest.mark.parametrize("bad, msg", [
    ({**SURVEY, "survey": ""}, "survey"),
    ({**SURVEY, "questionnaires": []}, "questionnaires"),
    ({**SURVEY, "questionnaires": [{"name": ""}]}, "name"),
    ({**SURVEY, "questionnaires": [{"name": "q", "VERSION": ["10"]}]}, "VERSION"),
    ({**SURVEY, "questionnaires": [{"name": "q", "filter_var": "x"}]}, "filter_var"),
    ({**SURVEY, "questionnaires": [{"name": "q", "filter_var": {"a": "1", "b": "2"}}]}, "exactly one"),
    ({**SURVEY, "questionnaires": [{"name": "q", "version": [12]}]}, r"questionnaires\[0\]: unknown key 'version'"),
    ({**SURVEY, "output_root": "data"}, "output_root must be a URL"),
    ({**SURVEY, "output_root": "/abs/path"}, "output_root must be a URL"),
    ({**SURVEY, "zip_password": 1234}, "zip_password"),
    ({**SURVEY, "parameters": {"survey": "x"}}, "reserved"),
    ({**SURVEY, "parameters": []}, "parameters"),
    ({**SURVEY, "questionaires": []}, "unknown key"),
    ("grdslchbs", "mapping"),
])
def test_validate_rejects(bad, msg):
    with pytest.raises(ValueError, match=msg):
        validate_survey_config(bad)


def test_validate_treats_an_empty_filter_var_as_no_filter():
    # An untouched optional object field in a form is stored as {}; the scoring node needs
    # exactly one key, so {} means "no filter" rather than a StopIteration after two stages.
    cfg = validate_survey_config({**SURVEY, "questionnaires": [{"name": "q", "filter_var": {}}]})
    assert cfg["questionnaires"][0]["filter_var"] is None


def test_validate_accepts_file_url_output_and_local_input():
    cfg = validate_survey_config({**SURVEY, "input_root": "/data/in", "output_root": "file:///data/out",
                                  "work_root": "/tmp/w"})
    assert (cfg["input_root"], cfg["output_root"], cfg["work_root"]) == ("/data/in", "file:///data/out", "/tmp/w")


def test_runtime_params_for_a_questionnaire_and_for_combine():
    cfg = validate_survey_config({**SURVEY, "zip_password": "pw",
                                  "parameters": {"features": {"gps": {"use": False}}}})
    q = cfg["questionnaires"][0]
    assert _runtime_params(cfg, q) == {
        "survey": "grdslchbs", "input_root": "s3://surveytool", "output_root": "s3://surveytool",
        "work_root": "data", "zip_password": "pw", "features": {"gps": {"use": False}},
        "questionnaire": q, "qnr_subdir": "slchbs_grenada_2627/"}
    assert _runtime_params(cfg, None) == {
        "survey": "grdslchbs", "input_root": "s3://surveytool", "output_root": "s3://surveytool",
        "work_root": "data", "zip_password": "pw", "features": {"gps": {"use": False}}}


def test_runtime_params_omit_zip_password_when_unset():
    assert "zip_password" not in _runtime_params(validate_survey_config(SURVEY), None)


# --- run_survey: project modes, the loop, combine -----------------------------------
import rissk.run as run


def _checkout(tmp_path):
    (tmp_path / "conf" / "base").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text('[tool.kedro]\npackage_name = "rissk"\n')
    return tmp_path


def test_resolve_project_checkout(tmp_path):
    p = run._resolve_project(_checkout(tmp_path))
    assert p == run._Project(root=tmp_path, conf_source=None, installed=False)


def test_resolve_project_rejects_a_non_checkout_root(tmp_path):
    with pytest.raises(ValueError, match="not a rissk checkout"):
        run._resolve_project(tmp_path)


def test_resolve_project_installed(tmp_path, monkeypatch):
    packaged = tmp_path / "site" / "rissk" / "_conf"
    (packaged / "base").mkdir(parents=True)
    monkeypatch.setattr(run, "_default_root", lambda: tmp_path / "site")
    monkeypatch.setattr(run, "_packaged_conf", lambda: packaged)
    monkeypatch.chdir(tmp_path)
    assert run._resolve_project(None) == run._Project(root=Path.cwd(), conf_source=str(packaged), installed=True)


def test_resolve_project_neither(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "_default_root", lambda: tmp_path)
    monkeypatch.setattr(run, "_packaged_conf", lambda: tmp_path / "missing")
    with pytest.raises(RuntimeError, match="neither"):
        run._resolve_project(None)


def _stub(monkeypatch, project, fail=()):
    calls = []
    monkeypatch.setattr(run, "_resolve_project", lambda root: project)
    monkeypatch.setattr(run, "_prepare", lambda p: calls.append(("prepare", p)))

    def run_pipeline(p, env, rp, pipeline):
        calls.append(("run", env, rp, pipeline))
        if rp and rp.get("questionnaire", {}).get("name") in fail:
            raise ValueError("boom")

    monkeypatch.setattr(run, "_run_pipeline", run_pipeline)
    monkeypatch.setattr(run, "_combine", lambda p, env, rp: calls.append(("combine", env, rp)))
    return calls


def test_run_survey_mapping_runs_each_questionnaire_then_combines(monkeypatch, tmp_path):
    project = run._Project(root=tmp_path, conf_source="pkg", installed=True)
    calls = _stub(monkeypatch, project, fail={"b"})
    cfg = {**SURVEY, "questionnaires": [{"name": "a"}, {"name": "b"}]}
    outcomes = run.run_survey(cfg, pipeline="data_ingestion")
    assert outcomes == {"a": "OK", "b": "FAILED (ValueError: boom)", "combine": "OK"}
    assert calls[0] == ("prepare", project)
    assert [c[1] for c in calls[1:]] == ["base", "base", "base"]
    assert calls[1][2]["qnr_subdir"] == "a/" and calls[1][3] == "data_ingestion"
    assert calls[3] == ("combine", "base", run._runtime_params(run.validate_survey_config(cfg), None))


def test_run_survey_env_keeps_file_backed_behaviour(monkeypatch, tmp_path):
    root = _checkout(tmp_path)
    qdir = root / "conf" / "pmpmd" / "questionnaires"
    qdir.mkdir(parents=True)
    (qdir / "hh.yml").write_text("name: hh\nVERSION: []\nfilter_var: null\n")
    project = run._Project(root=root, conf_source=None, installed=False)
    calls = _stub(monkeypatch, project)
    assert run.run_survey("pmpmd", project_root=root) == {"hh": "OK", "combine": "OK"}
    assert calls[1] == ("run", "pmpmd", {"questionnaire": {"name": "hh", "VERSION": [], "filter_var": None},
                                         "qnr_subdir": "hh/"}, "__default__")
    assert calls[2] == ("combine", "pmpmd", {})


def test_run_survey_legacy_env_runs_once_without_combine(monkeypatch, tmp_path):
    root = _checkout(tmp_path)
    (root / "conf" / "solo").mkdir()
    calls = _stub(monkeypatch, run._Project(root=root, conf_source=None, installed=False))
    assert run.run_survey("solo", project_root=root) == {"solo": "OK"}
    assert calls[1] == ("run", "solo", None, "__default__") and len(calls) == 2


def test_run_survey_env_name_needs_a_checkout(monkeypatch, tmp_path):
    _stub(monkeypatch, run._Project(root=tmp_path, conf_source="pkg", installed=True))
    with pytest.raises(ValueError, match="pass the configuration as a mapping"):
        run.run_survey("pmpmd")


def test_combine_survey_microdata_mapping_uses_survey_level_params(monkeypatch, tmp_path):
    calls = _stub(monkeypatch, run._Project(root=tmp_path, conf_source="pkg", installed=True))
    run.combine_survey_microdata(SURVEY)
    assert calls[1][0] == "combine" and "questionnaire" not in calls[1][2] and calls[1][2]["survey"] == "grdslchbs"


def test_plain_logging_config():
    assert run.PLAIN_LOGGING["handlers"]["console"]["class"] == "logging.StreamHandler"
    assert run.PLAIN_LOGGING["root"]["handlers"] == ["console"]
    assert run.PLAIN_LOGGING["loggers"]["rissk"]["level"] == "INFO"
