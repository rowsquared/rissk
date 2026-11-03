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
