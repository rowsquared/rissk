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
