"""Tests for the Windmill runner (windmill/f/rissk/run_rissk.py) and its deployment files.

The runner is not a package; it is loaded straight from its file. No network, no Windmill.
"""
import ast
import importlib.util
import json
import os
import pathlib
import re

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
RUNNER = REPO / "windmill/f/rissk/run_rissk.py"
_spec = importlib.util.spec_from_file_location("run_rissk", RUNNER)
run_rissk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_rissk)

SURVEY = {"survey": "grdslchbs", "input_root": "s3://surveytool", "output_root": "s3://surveytool",
          "questionnaires": [{"name": "slchbs_grenada_2627", "VERSION": [10, 11, 12, 13],
                              "filter_var": {"intrv_result": "1"}}]}
STORAGE = {"accessKey": "AK", "secretKey": "SK", "region": "eu-west-1", "endPoint": "s3.amazonaws.com",
           "useSSL": True}
AWS = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION", "AWS_ENDPOINT_URL")


# --- The script itself -----------------------------------------------------------------
def test_header_pins_python_and_installs_rissk_from_github():
    lines = RUNNER.read_text().splitlines()
    assert lines[:3] == ["# py313", "#requirements:",
                         "#rissk@git+https://github.com/rowsquared/rissk@kedro_pipeline"]
    assert not lines[3].startswith("#")  # exactly one requirement line


def test_runner_imports_only_the_glue():
    tree = ast.parse(RUNNER.read_text())
    names = {n.module if isinstance(n, ast.ImportFrom) else n.names[0].name
             for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))}
    assert names == {"os", "typing", "rissk.run"}


def _run(monkeypatch, storage, outcomes, **kw):
    for k in AWS:
        monkeypatch.delenv(k, raising=False)
    seen = {}
    monkeypatch.setattr(run_rissk, "run_survey",
                        lambda cfg, pipeline: seen.update(cfg=cfg, pipeline=pipeline) or outcomes)
    return run_rissk.main(SURVEY, storage, **kw), seen


def test_main_maps_storage_and_runs(monkeypatch):
    result, seen = _run(monkeypatch, STORAGE, {"q": "OK", "combine": "OK"}, pipeline="data_ingestion")
    assert seen == {"cfg": SURVEY, "pipeline": "data_ingestion"}
    assert (os.environ["AWS_ACCESS_KEY_ID"], os.environ["AWS_SECRET_ACCESS_KEY"],
            os.environ["AWS_DEFAULT_REGION"]) == ("AK", "SK", "eu-west-1")
    assert "AWS_ENDPOINT_URL" not in os.environ
    assert result == {"survey": "grdslchbs", "pipeline": "data_ingestion",
                      "outcomes": {"q": "OK", "combine": "OK"}}


def test_main_sets_endpoint_for_non_aws_storage_and_skips_missing_region(monkeypatch):
    _run(monkeypatch, {"accessKey": "AK", "secretKey": "SK", "endPoint": "nbg1.your-objectstorage.com"},
         {"q": "OK"})
    assert os.environ["AWS_ENDPOINT_URL"] == "https://nbg1.your-objectstorage.com"
    assert "AWS_DEFAULT_REGION" not in os.environ


def test_main_raises_when_a_questionnaire_failed(monkeypatch):
    with pytest.raises(RuntimeError, match=r"\['q1'\]"):
        _run(monkeypatch, STORAGE, {"q1": "FAILED (ValueError: boom)", "combine": "OK"})


# --- Script metadata and the resource type --------------------------------------------
def test_schema_and_metadata_are_consistent():
    from rissk.pipeline_registry import register_pipelines

    meta = yaml.safe_load((REPO / "windmill/f/rissk/run_rissk.script.yaml").read_text())
    props = meta["schema"]["properties"]
    assert set(props) == {"survey", "storage", "pipeline"}
    assert meta["schema"]["required"] == ["survey", "storage"]
    assert props["survey"]["format"] == "resource-rissk_survey" and props["storage"]["format"] == "resource-s3"
    assert props["pipeline"]["enum"] == list(register_pipelines()) and props["pipeline"]["default"] == "__default__"


def test_resource_type_file_matches_schema_json_and_requires_a_url_output():
    # The CLI/CI path pushes rissk_survey.resource-type.yaml; the manual path pastes the
    # JSON. They must describe the same type.
    rt = yaml.safe_load((REPO / "windmill/rissk_survey.resource-type.yaml").read_text())
    schema = json.loads((REPO / "windmill/rissk_survey.schema.json").read_text())
    assert rt["schema"] == schema and rt["description"]
    pattern = schema["properties"]["output_root"]["pattern"]
    assert re.match(pattern, "s3://surveytool")
    assert not re.match(pattern, "data") and not re.match(pattern, "/abs")


def test_readme_survey_example_matches_committed_env():
    # The operator copies the README example to create the production resource, so its
    # questionnaire must equal the committed env (name, VERSION, filter_var).
    readme = (REPO / "windmill/README.md").read_text()
    block = re.search(r"```json\n(\{.*?\n\})\n```", readme, re.S).group(1)
    example = json.loads(block)["questionnaires"][0]
    committed = yaml.safe_load((REPO / "conf/grdslchbs/questionnaires/slchbs_grenada_2627.yml").read_text())
    assert example == {"name": committed["name"], "VERSION": committed["VERSION"],
                       "filter_var": committed["filter_var"]}


# --- Deployment: CLI sync root and the GitHub Actions workflow --------------------------
def test_sync_root_is_scoped_to_this_repo_and_never_touches_config():
    # A `wmill sync push` deletes remote items that are in scope but missing locally, and
    # the workspace also holds hand-made items and the ppp repository's. Keep the scope to
    # f/rissk and keep variables, resources, secrets, folders and resource types out.
    conf = yaml.safe_load((REPO / "windmill/wmill.yaml").read_text())
    assert conf["includes"] == ["f/rissk/**"]
    for key in ("skipVariables", "skipResources", "skipSecrets", "skipFolders", "skipResourceTypes"):
        assert conf.get(key) is True, key
    assert not conf.get("includeSchedules") and not conf.get("includeTriggers")


def test_deploy_workflow_pins_the_commit_and_pushes_the_same_scope():
    wf = yaml.safe_load((REPO / ".github/workflows/windmill-sync.yml").read_text())
    on = wf[True] if True in wf else wf["on"]  # PyYAML reads the `on:` key as boolean True
    # Every push of the branch deploys (a rissk code change must redeploy too), no paths filter.
    assert on["push"] == {"branches": ["kedro_pipeline"]} and "workflow_dispatch" in on
    job = wf["jobs"]["sync"]
    assert job["defaults"]["run"]["working-directory"] == "windmill"
    steps = [(s.get("name", ""), s.get("run", "")) for s in job["steps"]]
    pin = next(r for n, r in steps if n == "Pin the rissk commit")
    assert "GITHUB_SHA" in pin and "#rissk@git+https://github.com/rowsquared/rissk@" in pin and "grep" in pin
    runs = [r for _, r in steps]
    rt_push = next(r for r in runs if r.startswith("wmill resource-type push"))
    sync_push = next(r for r in runs if r.startswith("wmill sync push"))
    assert runs.index(pin) < runs.index(sync_push)
    assert "rissk_survey.resource-type.yaml rissk_survey" in rt_push
    for flag in ("--yes", "--skip-variables", "--skip-secrets", "--skip-resources",
                 "--skip-resource-types", "--skip-folders"):
        assert flag in sync_push, flag
    for cmd in (rt_push, sync_push):
        assert '--workspace "$WMILL_WORKSPACE"' in cmd and '--token "$WMILL_TOKEN"' in cmd \
            and '--base-url "$WMILL_URL"' in cmd
    # The secret and the variables are read from the repository settings, never hardcoded.
    assert job["env"] == {"WMILL_URL": "${{ vars.WMILL_URL }}", "WMILL_WORKSPACE": "${{ vars.WMILL_WORKSPACE }}",
                          "WMILL_TOKEN": "${{ secrets.WMILL_TOKEN }}"}
