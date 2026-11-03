# rissk installable package + thin Windmill runner — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make rissk installable from GitHub with its base configuration inside the wheel, let `rissk.run.run_survey` take the survey configuration as a mapping, and shrink the Windmill runner to the glue between two resources and that call.

**Architecture:** The catalog reads `survey` and the roots from runtime params with the env globals as fallback, so a mapping becomes runtime params on an `env="base"` session and an env name keeps loading its folder. `run.py` resolves the project mode once (checkout vs installed package, told apart by the packaged `rissk/_conf`), then one loop runs the questionnaires and the combine step for both forms. The deploy workflow pins the pushed commit into the runner's one requirement line.

**Tech Stack:** Python 3.13, Kedro 1.2.0 (`OmegaConfigLoader`, `KedroSession`), hatchling, uv, pytest, Windmill CLI 1.856.0, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-03-rissk-installable-thin-runner-design.md`

## Global Constraints

- Kedro 1.2.0 merges runtime params into parameters only; the catalog must read `${runtime_params:x,${globals:x}}`; no `runtime_params:` resolver inside `globals.yml`.
- A mapping run uses `env="base"`; an env-name run keeps today's behaviour byte-for-byte (flat layout for an env without `questionnaires/`, no combine there).
- rissk never imports `wmill` or reads Windmill environment variables.
- The runner's `#requirements:` block is exactly one line: `#rissk@git+https://github.com/rowsquared/rissk@kedro_pipeline`; first line `# py313`.
- The only source of `conf/base` is `conf/base`; the wheel copies it to `rissk/_conf/base` at build time.
- No secrets in git; no Kedro hooks; static pipelines; empty/missing data handled by nodes (unchanged).
- Package version `0.2.0`. Tests run with `uv run --extra test pytest` from the repo root.
- Commits end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Work on branch `thin-runner` (from `kedro_pipeline`); never push to `kedro_pipeline` from this plan: merging deploys to production Windmill and is the user's call.

## Review Focus

- Roots given with a trailing slash (`s3://surveytool/`) must not produce `//` in paths → Task 3 strips them.
- `VERSION: null` in a hand-written resource must mean "all versions" like `[]`, not an error → Task 3.
- A `parameters` override that names a reserved runtime key (`survey`, `questionnaire`, …) must be rejected, not silently override the roots → Task 3.
- `run_survey("<env>")` from an installed package (no checkout) must fail with a message saying to pass a mapping → Task 4.
- `combine_survey_microdata(mapping)` standalone must resolve the survey-level paths (no `questionnaire`/`qnr_subdir` in its params) → Task 4.

---

### Task 1: Catalog reads runtime params first, globals as fallback

**Files:**
- Modify: `conf/base/catalog.yml`
- Test: `tests/test_catalog_resolution.py`

**Interfaces:**
- Produces: catalog templates `_survey`, `_work`, `_out`, `_qnr`; runtime keys `survey`, `work_root`, `output_root`, `qnr_subdir` override the env's globals for every dataset path.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_catalog_resolution.py`; add a `_path(dataset, rp)` helper returning `cl["catalog"][dataset]["path"]`)

```python
RP = {"survey": "pmpmd copy", "output_root": "s3://b", "work_root": "/w", "qnr_subdir": "hh/"}

def test_runtime_roots_override_globals():
    assert _filepath("microdata", RP) == "s3://b/pmpmd copy/latest/30_PROCESSED/hh/microdata.parquet"
    assert _filepath("unit_rissk_scores", RP) == "s3://b/pmpmd copy/latest/35_SCORES/hh/unit_rissk_scores.csv"
    assert _filepath("paradata_raw", RP) == "/w/pmpmd copy/latest/20_INTERIM/hh/paradata_raw.parquet"
    assert _path("survey_zip_partitions", RP) == "/w/pmpmd copy/latest/10_RAW"
    assert _path("microdata_by_qnr", RP) == "s3://b/pmpmd copy/latest/30_PROCESSED"
    assert _filepath("microdata_combined", RP) == "s3://b/pmpmd copy/latest/30_PROCESSED/microdata.parquet"

def test_templates_are_dropped_from_the_catalog():
    cl = OmegaConfigLoader(conf_source="conf", base_env="base", default_run_env="grdslchbs_test", runtime_params={})
    assert not [k for k in cl["catalog"] if k.startswith("_")]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --extra test pytest tests/test_catalog_resolution.py -v`
Expected: the two new tests FAIL (paths still come from the env's globals); the four existing ones PASS.

- [ ] **Step 3: Rewrite `conf/base/catalog.yml`**

Add at the top (after the header comment, which now explains runtime params → globals):

```yaml
_survey: ${runtime_params:survey,${globals:survey}}
_work: ${runtime_params:work_root,${globals:work_root}}/${_survey}/latest
_out: ${runtime_params:output_root,${globals:output_root}}/${_survey}/latest
_qnr: ${runtime_params:qnr_subdir,''}
```

Then every `path:`/`filepath:` uses them, e.g. `${_work}/10_RAW`, `${_work}/20_INTERIM/${_qnr}paradata_raw.parquet`, `${_out}/30_PROCESSED/${_qnr}microdata.parquet`, `${_out}/35_SCORES/${_qnr}item_scores.parquet`, `${_out}/30_PROCESSED` (microdata_by_qnr), `${_out}/30_PROCESSED/microdata.parquet` (microdata_combined, no `_qnr`). Dataset types and options unchanged.

- [ ] **Step 4: Run the whole suite**

Run: `uv run --extra test pytest -q`
Expected: all pass (75 before this plan; the two new ones added).

- [ ] **Step 5: Commit**

```bash
git add conf/base/catalog.yml tests/test_catalog_resolution.py
git commit -m "feat(catalog): survey and roots from runtime params, env globals as fallback"
```

---

### Task 2: Package `conf/base` into the wheel; disable telemetry hooks

**Files:**
- Modify: `pyproject.toml` (build-system, `[tool.hatch…]`, version), `src/rissk/settings.py:12`, `uv.lock`
- Create: `tests/test_packaging.py`

**Interfaces:**
- Produces: an installed wheel exposes `importlib.resources.files("rissk") / "_conf" / "base"` with `catalog.yml`, `globals.yml`, `parameters.yml`; an editable install (the checkout) has no `src/rissk/_conf`.

- [ ] **Step 1: Write the failing tests** (`tests/test_packaging.py`)

```python
import shutil, subprocess, tomllib, zipfile, pathlib, pytest
REPO = pathlib.Path(__file__).resolve().parents[1]

def test_pyproject_ships_conf_base_in_the_wheel():
    py = tomllib.loads((REPO / "pyproject.toml").read_text())
    assert py["build-system"]["build-backend"] == "hatchling.build"
    wheel = py["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert wheel["packages"] == ["src/rissk"]
    assert wheel["force-include"] == {"conf/base": "rissk/_conf/base"}
    assert py["project"]["version"] == "0.2.0"

@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not installed")
def test_wheel_contains_conf_base_only(tmp_path):
    subprocess.run(["uv", "build", "--wheel", "--out-dir", str(tmp_path)], cwd=REPO, check=True, capture_output=True)
    names = set(zipfile.ZipFile(next(tmp_path.glob("rissk-*.whl"))).namelist())
    assert {"rissk/_conf/base/catalog.yml", "rissk/_conf/base/globals.yml", "rissk/_conf/base/parameters.yml"} <= names
    assert not [n for n in names if n.startswith("rissk/_conf/") and "/base/" not in n]

def test_telemetry_hooks_are_disabled():
    from rissk import settings
    assert "kedro-telemetry" in settings.DISABLE_HOOKS_FOR_PLUGINS
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run --extra test pytest tests/test_packaging.py -v`
Expected: all three FAIL (flit backend, no `_conf` in the wheel, telemetry hook not disabled).

- [ ] **Step 3: Edit `pyproject.toml`**

Replace the `[build-system]` + `[tool.flit.module]` blocks with the spec's hatchling blocks (section 1): `requires = ["hatchling"]`, `build-backend = "hatchling.build"`, `[tool.hatch.build.targets.wheel] packages = ["src/rissk"]`, `[tool.hatch.build.targets.wheel.force-include] "conf/base" = "rissk/_conf/base"`, `[tool.hatch.build.targets.sdist] include = ["src/rissk", "conf/base", "README.md", "pyproject.toml"]`. Set `version = "0.2.0"`. Keep every other table.

- [ ] **Step 4: Edit `src/rissk/settings.py`**: `DISABLE_HOOKS_FOR_PLUGINS = ("kedro-viz", "kedro-telemetry")` with a one-line comment (telemetry is a plugin hook; the headless driver never asks for consent).

- [ ] **Step 5: Refresh the environment and check the editable install**

Run: `uv lock && uv sync --extra test && uv run python -c "import rissk, importlib.resources as r; print(rissk.__file__); print((r.files('rissk')/'_conf').is_dir())"`
Expected: `uv.lock` updated (rissk 0.2.0), the path printed is `…/src/rissk/__init__.py`, and `False` (no packaged conf in the checkout). If `.venv/lib/python3.13/site-packages/rissk/_conf` exists after the editable install, note it in the commit message; it is harmless (the regular package in `src/` wins) and `files("rissk")` still resolves to `src/rissk`.

- [ ] **Step 6: Run the tests**

Run: `uv run --extra test pytest tests/test_packaging.py -v && uv run --extra test pytest -q`
Expected: PASS, whole suite green.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/rissk/settings.py tests/test_packaging.py
git commit -m "build: hatchling wheel carries conf/base; telemetry hooks off; version 0.2.0"
```

---

### Task 3: `validate_survey_config` and `_runtime_params` in `rissk.run`

**Files:**
- Modify: `src/rissk/run.py`
- Test: `tests/test_run_driver.py`

**Interfaces:**
- Produces: `validate_survey_config(config: Mapping) -> dict` returning the normalised copy with keys `survey`, `input_root`, `output_root`, `work_root`, `questionnaires` (each `{"name", "VERSION", "filter_var"}`), `zip_password` (`str | None`), `parameters` (`dict`); raises `ValueError`. `RESERVED_RUNTIME_KEYS = frozenset({"questionnaire", "qnr_subdir", "survey", "input_root", "output_root", "work_root"})`. `_runtime_params(cfg: dict, questionnaire: dict | None) -> dict`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_run_driver.py`)

```python
import pytest
from rissk.run import validate_survey_config, _runtime_params

SURVEY = {"survey": "grdslchbs", "input_root": "s3://surveytool", "output_root": "s3://surveytool",
          "questionnaires": [{"name": "slchbs_grenada_2627", "VERSION": [10, 11], "filter_var": {"intrv_result": "1"}}]}

def test_validate_normalises_a_minimal_mapping():
    cfg = validate_survey_config({**SURVEY, "input_root": "s3://surveytool/", "output_root": "s3://surveytool/"})
    assert cfg == {"survey": "grdslchbs", "input_root": "s3://surveytool", "output_root": "s3://surveytool",
                   "work_root": "data", "zip_password": None, "parameters": {},
                   "questionnaires": [{"name": "slchbs_grenada_2627", "VERSION": [10, 11], "filter_var": {"intrv_result": "1"}}]}

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
    cfg = validate_survey_config({**SURVEY, "input_root": "/data/in", "output_root": "file:///data/out", "work_root": "/tmp/w"})
    assert (cfg["input_root"], cfg["output_root"], cfg["work_root"]) == ("/data/in", "file:///data/out", "/tmp/w")

def test_runtime_params_for_a_questionnaire_and_for_combine():
    cfg = validate_survey_config({**SURVEY, "zip_password": "pw", "parameters": {"features": {"gps": {"use": False}}}})
    q = cfg["questionnaires"][0]
    assert _runtime_params(cfg, q) == {"survey": "grdslchbs", "input_root": "s3://surveytool", "output_root": "s3://surveytool",
                                       "work_root": "data", "zip_password": "pw", "features": {"gps": {"use": False}},
                                       "questionnaire": q, "qnr_subdir": "slchbs_grenada_2627/"}
    assert _runtime_params(cfg, None) == {"survey": "grdslchbs", "input_root": "s3://surveytool", "output_root": "s3://surveytool",
                                          "work_root": "data", "zip_password": "pw", "features": {"gps": {"use": False}}}

def test_runtime_params_omit_zip_password_when_unset():
    assert "zip_password" not in _runtime_params(validate_survey_config(SURVEY), None)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --extra test pytest tests/test_run_driver.py -v`
Expected: ImportError on `validate_survey_config`.

- [ ] **Step 3: Implement in `src/rissk/run.py`**

`validate_survey_config(config)`: not a `Mapping` → `ValueError("survey config must be a mapping (the rissk_survey resource), got …")`; unknown top-level key → `ValueError("survey config: unknown key 'questionaires'")`; `survey`, `input_root`, `output_root` non-empty `str` (roots `rstrip("/")`, `survey` kept verbatim); `output_root` must match `^[a-z][a-z0-9+.-]*://` else `"survey config: output_root must be a URL such as s3://bucket (file:///abs/path for a local folder); got 'data'"`; `work_root` optional non-empty `str`, default `"data"`; `questionnaires` non-empty list, each a mapping with non-empty `str` `name`, `VERSION` `None`→`[]` or a list of `int` (bool excluded), `filter_var` `None` or a mapping; `zip_password` `None` or `str`; `parameters` `None`→`{}` or a mapping whose keys are not in `RESERVED_RUNTIME_KEYS` (`"survey config: parameters key 'survey' is reserved"`). Return a new dict (deep-copied lists/dicts).

`_runtime_params(cfg, questionnaire)`: the four roots/survey keys, `zip_password` only when truthy, then `cfg["parameters"]` keys, then `questionnaire` + `qnr_subdir = f"{name}/"` when a questionnaire is given.

- [ ] **Step 4: Run the tests**

Run: `uv run --extra test pytest tests/test_run_driver.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/rissk/run.py tests/test_run_driver.py
git commit -m "feat(run): validate a survey configuration given as data and map it to runtime params"
```

---

### Task 4: `run_survey(config | env)` with project modes, plain logging, combine

**Files:**
- Modify: `src/rissk/run.py` (`_project_root` → `_default_root`/`_packaged_conf`/`_resolve_project`; `run_survey`; `combine_survey_microdata`; new `_prepare`, `_run_pipeline`, `_combine`, `PLAIN_LOGGING`), `CLAUDE.md` ("Two ways to run" paragraph, packaging note)
- Test: `tests/test_run_driver.py`

**Interfaces:**
- Consumes: Task 3's `validate_survey_config`, `_runtime_params`; Task 2's packaged `_conf`.
- Produces:
  - `@dataclass(frozen=True) class _Project: root: Path; conf_source: str | None; installed: bool`
  - `_default_root() -> Path` (today's `rissk.__file__` derivation), `_packaged_conf() -> Path` (`Path(str(importlib.resources.files("rissk") / "_conf"))`), `_resolve_project(project_root: PathLike | None) -> _Project`
  - `_prepare(project: _Project) -> None`, `_run_pipeline(project: _Project, env: str, runtime_params: dict | None, pipeline: str) -> None`, `_combine(project: _Project, env: str, runtime_params: dict) -> None`
  - `run_survey(config: str | Mapping, project_root=None, pipeline="__default__", run_combine=True) -> dict[str, str]`
  - `combine_survey_microdata(config: str | Mapping, project_root=None) -> None`
  - `PLAIN_LOGGING: dict` (version 1, `console` `logging.StreamHandler` on `ext://sys.stdout`, format `"%(asctime)s %(levelname)s %(name)s: %(message)s"`, loggers `kedro` and `rissk` INFO, root INFO with `[console]`, `disable_existing_loggers: False`).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_run_driver.py`)

```python
from pathlib import Path
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
    qdir = root / "conf" / "pmpmd" / "questionnaires"; qdir.mkdir(parents=True)
    (qdir / "hh.yml").write_text("name: hh\nVERSION: []\nfilter_var: null\n")
    project = run._Project(root=root, conf_source=None, installed=False)
    calls = _stub(monkeypatch, project)
    assert run.run_survey("pmpmd", project_root=root) == {"hh": "OK", "combine": "OK"}
    assert calls[1] == ("run", "pmpmd", {"questionnaire": {"name": "hh", "VERSION": [], "filter_var": None}, "qnr_subdir": "hh/"}, "__default__")
    assert calls[2] == ("combine", "pmpmd", {})

def test_run_survey_legacy_env_runs_once_without_combine(monkeypatch, tmp_path):
    root = _checkout(tmp_path); (root / "conf" / "solo").mkdir()
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --extra test pytest tests/test_run_driver.py -v`
Expected: AttributeError on `_resolve_project` / `_Project`.

- [ ] **Step 3: Implement in `src/rissk/run.py`**

- `_resolve_project(project_root)`: `project_root` given → must be a checkout (`pyproject.toml` file and `conf/base` dir) else `ValueError(f"{root} is not a rissk checkout (no pyproject.toml + conf/base)")`; `None` → `_default_root()` if it is a checkout, else `_packaged_conf()` if it is a dir → `_Project(Path.cwd(), str(packaged), True)`, else `RuntimeError("rissk is neither a checkout at … nor an installed package with its configuration at …")`.
- `_prepare(project)`: `LOGGING.configure(PLAIN_LOGGING)`; installed → `configure_project("rissk")`; checkout → `bootstrap_project(project.root)` then `os.chdir(project.root)` (keep today's comment on why).
- `_run_pipeline` / `_combine`: `KedroSession.create(project_path=project.root, env=env, runtime_params=runtime_params, conf_source=project.conf_source)`; `_combine` loads `microdata_by_qnr`, calls `combine_microdata`, saves `microdata_combined` (today's body).
- `run_survey`: `project = _resolve_project(project_root)`; `_prepare(project)`; `str` config → `if project.installed: raise ValueError("env 'x' needs a checkout; from an installed rissk pass the configuration as a mapping")`, `env = config`, `qnrs = load_questionnaire_configs(env, project.root)`, runs `[(q["name"], {"questionnaire": q, "qnr_subdir": f"{q['name']}/"})…]` or legacy `[(env, None)]`, combine params `{}` or `None` (legacy); mapping → `cfg = validate_survey_config(config)`, `env = "base"`, runs from `_runtime_params(cfg, q)`, combine params `_runtime_params(cfg, None)`. Loop as today (same prints, same `FAILED (<Type>: <msg>)` strings, isolation), then combine when `run_combine` and combine params is not `None`, label `"combine"`.
- `combine_survey_microdata(config, project_root=None)`: resolve + prepare, then `_combine` with `{}` for an env name or `_runtime_params(validate_survey_config(config), None)` for a mapping.
- Module docstring: the two forms, the modes, the layouts.

- [ ] **Step 4: Run the tests, then an end-to-end check in installed mode**

Run: `uv run --extra test pytest tests/test_run_driver.py tests/test_catalog_resolution.py -v`
Expected: PASS.

Then (throwaway, in the scratchpad): build the wheel (`uv build --wheel --out-dir <tmp>`), `uv venv <tmp>/venv --python 3.13 && uv pip install --python <tmp>/venv/bin/python <wheel>`, and from an empty directory run
`<tmp>/venv/bin/python -c "from rissk.run import run_survey; print(run_survey({'survey': 'nothing', 'input_root': '<empty dir>', 'output_root': 'file://<tmp>/out', 'questionnaires': [{'name': 'q'}]}, pipeline='data_ingestion'))"`
Expected: plain-format log lines (no rich tables), the ingestion runs on no zips (the `stage_zips` warning "no zips matching q_*.zip"), outcomes `{'q': 'OK' or 'FAILED (…)', 'combine': …}` printed, and no `conf/` written anywhere. The exact outcome strings don't matter here; what matters is that the session starts from the packaged conf outside a checkout.

- [ ] **Step 5: Update `CLAUDE.md`**: in "Two ways to run", add that `run_survey` also takes the configuration as a mapping (the `rissk_survey` shape: `survey`, `input_root`, `output_root`, `questionnaires`, optional `zip_password`, `parameters`, `work_root`) run on `env="base"` with runtime params, always the sub-folder layout plus combine, `output_root` must be a URL; add one paragraph on packaging (hatchling, `conf/base` copied into the wheel as `rissk/_conf/base`, which is how an installed rissk is told apart from a checkout; `run_survey` plain logging; telemetry hooks disabled in `settings.py`).

- [ ] **Step 6: Whole suite, commit**

Run: `uv run --extra test pytest -q`
Expected: PASS.

```bash
git add src/rissk/run.py tests/test_run_driver.py CLAUDE.md
git commit -m "feat(run): run_survey takes a mapping or an env; installed-package mode; plain logging"
```

---

### Task 5: The thin runner, its metadata and the resource schema

**Files:**
- Modify: `windmill/f/rissk/run_rissk.py` (replace whole file), `windmill/f/rissk/run_rissk.script.yaml`, `windmill/rissk_survey.schema.json`, `windmill/rissk_survey.resource-type.yaml`
- Test: `tests/test_windmill_runner.py` (replace whole file)

**Interfaces:**
- Consumes: `rissk.run.run_survey(mapping, pipeline=…)`, `rissk.pipeline_registry.register_pipelines()`.
- Produces: `main(survey: rissk_survey, storage: s3, pipeline: str = "__default__") -> dict` returning `{"survey", "pipeline", "outcomes"}`.

- [ ] **Step 1: Replace `tests/test_windmill_runner.py`** with: the importlib loader (as today); `SURVEY` (the README example) and `STORAGE = {"accessKey": "AK", "secretKey": "SK", "region": "eu-west-1", "endPoint": "s3.amazonaws.com", "useSSL": True}`; and these tests

```python
def test_header_pins_python_and_installs_rissk_from_github():
    lines = (REPO / "windmill/f/rissk/run_rissk.py").read_text().splitlines()
    assert lines[:3] == ["# py313", "#requirements:", "#rissk@git+https://github.com/rowsquared/rissk@kedro_pipeline"]
    assert not lines[3].startswith("#")

def test_runner_imports_only_the_glue():
    tree = ast.parse((REPO / "windmill/f/rissk/run_rissk.py").read_text())
    names = {n.module if isinstance(n, ast.ImportFrom) else n.names[0].name
             for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))}
    assert names == {"os", "typing", "rissk.run"}

AWS = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION", "AWS_ENDPOINT_URL")

def _run(monkeypatch, storage, outcomes, **kw):
    for k in AWS: monkeypatch.delenv(k, raising=False)
    seen = {}
    monkeypatch.setattr(run_rissk, "run_survey", lambda cfg, pipeline: seen.update(cfg=cfg, pipeline=pipeline) or outcomes)
    return run_rissk.main(SURVEY, storage, **kw), seen

def test_main_maps_storage_and_runs(monkeypatch):
    result, seen = _run(monkeypatch, STORAGE, {"q": "OK", "combine": "OK"}, pipeline="data_ingestion")
    assert seen == {"cfg": SURVEY, "pipeline": "data_ingestion"}
    assert (os.environ["AWS_ACCESS_KEY_ID"], os.environ["AWS_SECRET_ACCESS_KEY"], os.environ["AWS_DEFAULT_REGION"]) == ("AK", "SK", "eu-west-1")
    assert "AWS_ENDPOINT_URL" not in os.environ
    assert result == {"survey": "grdslchbs", "pipeline": "data_ingestion", "outcomes": {"q": "OK", "combine": "OK"}}

def test_main_sets_endpoint_for_non_aws_storage_and_skips_missing_region(monkeypatch):
    _run(monkeypatch, {"accessKey": "AK", "secretKey": "SK", "endPoint": "nbg1.your-objectstorage.com"}, {"q": "OK"})
    assert os.environ["AWS_ENDPOINT_URL"] == "https://nbg1.your-objectstorage.com" and "AWS_DEFAULT_REGION" not in os.environ

def test_main_raises_when_a_questionnaire_failed(monkeypatch):
    with pytest.raises(RuntimeError, match=r"\['q1'\]"):
        _run(monkeypatch, STORAGE, {"q1": "FAILED (ValueError: boom)", "combine": "OK"})

def test_schema_and_metadata_are_consistent():
    from rissk.pipeline_registry import register_pipelines
    meta = yaml.safe_load((REPO / "windmill/f/rissk/run_rissk.script.yaml").read_text())
    props = meta["schema"]["properties"]
    assert set(props) == {"survey", "storage", "pipeline"} and meta["schema"]["required"] == ["survey", "storage"]
    assert props["survey"]["format"] == "resource-rissk_survey" and props["storage"]["format"] == "resource-s3"
    assert props["pipeline"]["enum"] == list(register_pipelines()) and props["pipeline"]["default"] == "__default__"

def test_resource_type_file_matches_schema_json_and_requires_a_url_output():
    rt = yaml.safe_load((REPO / "windmill/rissk_survey.resource-type.yaml").read_text())
    schema = json.loads((REPO / "windmill/rissk_survey.schema.json").read_text())
    assert rt["schema"] == schema and rt["description"]
    pattern = schema["properties"]["output_root"]["pattern"]
    assert re.match(pattern, "s3://surveytool") and not re.match(pattern, "data") and not re.match(pattern, "/abs")
```
plus, unchanged from today: `test_sync_root_is_scoped_to_this_repo_and_never_touches_config`, `test_readme_survey_example_matches_committed_env`, and (until Task 6 rewrites it) `test_deploy_workflow_pushes_the_same_scope`.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --extra test pytest tests/test_windmill_runner.py -v`
Expected: header/imports/main/schema tests FAIL against the old runner; the three kept ones PASS.

- [ ] **Step 3: Replace `windmill/f/rissk/run_rissk.py`** with the spec's section-4 runner: header, docstring, `TypedDict`s (`rissk_survey`: `survey`, `input_root`, `output_root`, `questionnaires`, `zip_password`, `parameters`, one per line; `s3`: `accessKey`, `secretKey`, `region`, `endPoint`), `main` as in the spec (`AWS_ENDPOINT_URL` = `https://<endPoint>` only when `endPoint` is set and does not end with `amazonaws.com`; empty values dropped).

- [ ] **Step 4: Update `run_rissk.script.yaml`**: description "Runs rissk.run.run_survey with the `survey` resource (type rissk_survey) as the configuration and the `storage` resource (type s3) exported as the AWS environment variables. rissk is installed from GitHub at the commit the deploy pinned (see the #requirements: line). Fails when any questionnaire failed; results land under <output_root>/<survey>/latest/."; `order` and `properties` keep `survey`, `storage`, `pipeline` only; `required` unchanged; the timeout comment stays.

- [ ] **Step 5: Update both schema files**: `output_root` gets `"pattern": "^[a-z][a-z0-9+.-]*://"` and description "Where 30_PROCESSED and 35_SCORES are written: a URL such as s3://surveytool. Must be remote: the job folder is deleted after the run." (same text in the yaml; keep the two files equal).

- [ ] **Step 6: Run the tests, commit**

Run: `uv run --extra test pytest tests/test_windmill_runner.py -v && uv run --extra test pytest -q`
Expected: PASS.

```bash
git add windmill/f/rissk/run_rissk.py windmill/f/rissk/run_rissk.script.yaml windmill/rissk_survey.schema.json windmill/rissk_survey.resource-type.yaml tests/test_windmill_runner.py
git commit -m "feat(windmill): the runner installs rissk from GitHub and passes the resource as configuration"
```

---

### Task 6: Deploy every push of `kedro_pipeline`, pinned to its commit; operator docs

**Files:**
- Modify: `.github/workflows/windmill-sync.yml`, `windmill/README.md`, `CLAUDE.md` (Windmill paragraph), `docs/superpowers/specs/2026-10-23-windmill-scheduling-design.md` (pointer at the top)
- Test: `tests/test_windmill_runner.py`

**Interfaces:**
- Consumes: the runner's requirement line from Task 5.

- [ ] **Step 1: Replace `test_deploy_workflow_pushes_the_same_scope`** with

```python
def test_deploy_workflow_pins_the_commit_and_pushes_the_same_scope():
    wf = yaml.safe_load((REPO / ".github/workflows/windmill-sync.yml").read_text())
    on = wf[True] if True in wf else wf["on"]
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
    for flag in ("--yes", "--skip-variables", "--skip-secrets", "--skip-resources", "--skip-resource-types", "--skip-folders"):
        assert flag in sync_push, flag
    for cmd in (rt_push, sync_push):
        assert '--workspace "$WMILL_WORKSPACE"' in cmd and '--token "$WMILL_TOKEN"' in cmd and '--base-url "$WMILL_URL"' in cmd
    assert job["env"] == {"WMILL_URL": "${{ vars.WMILL_URL }}", "WMILL_WORKSPACE": "${{ vars.WMILL_WORKSPACE }}", "WMILL_TOKEN": "${{ secrets.WMILL_TOKEN }}"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --extra test pytest tests/test_windmill_runner.py -k workflow -v`
Expected: FAIL (`paths` still present, no pin step).

- [ ] **Step 3: Edit the workflow**: drop `paths:` under `push` (comment: a rissk code change must redeploy too, the runner installs rissk at this commit); add before "Push f/rissk":

```yaml
      # The committed line installs the branch; the deployed script installs this exact
      # commit, so Windmill re-locks on every push and its script page shows what runs.
      - name: Pin the rissk commit
        run: |
          sed -i -E 's|^#rissk@git\+https://github\.com/rowsquared/rissk@.*$|#rissk@git+https://github.com/rowsquared/rissk@'"$GITHUB_SHA"'|' f/rissk/run_rissk.py
          grep -q "^#rissk@git+https://github.com/rowsquared/rissk@$GITHUB_SHA\$" f/rissk/run_rissk.py
```

Update the header comment of the file (what gets deployed and when).

- [ ] **Step 4: Rewrite the affected parts of `windmill/README.md`** per the spec's section 7: intro paragraph; "What runs where" (drop the private-repo token row; the script row says rissk is installed from GitHub at the pinned commit); setup step 4 (paste-by-hand still works; the header is three lines) and step 5 (lockfile shows Python 3.13, `kedro==1.2.0` and `rissk @ git+…@<commit>`; the first job after a deploy builds rissk from the clone, seconds, cached per worker); "Deploying the runner" intro (one thing is deployed now: the runner with the rissk commit pinned; way 1 pins the branch tip at lock time); way 2 text (every push to `kedro_pipeline`, not only `windmill/`); remove "Private repository" and "Running locally"; add "Running the same configuration from Python" (`from rissk.run import run_survey; run_survey(<the JSON as a dict>, pipeline="data_ingestion")` from a checkout or an installed rissk, `output_root` a URL, `file:///…` for a local folder); "Updating": *Code* → push to `kedro_pipeline` (deploys and re-locks; rollback = revert and push), *Runner* → same, no header to maintain, *Settings* unchanged, *Survey configuration* unchanged, and the schedule-arguments note (a schedule keeps stored arguments when the schema changes; after an argument is dropped, open each schedule once and save it). Keep the JSON example block byte-for-byte (a test parses it).

- [ ] **Step 5: `CLAUDE.md` Windmill paragraph**: the runner installs rissk from GitHub through its one `#requirements:` line and passes the `rissk_survey` resource to `run_survey` as is; the workflow deploys on every push to `kedro_pipeline` with the commit pinned into that line; settings still live in `run_rissk.script.yaml`. **Old spec**: add under its title `> Superseded in part by [2026-11-03-rissk-installable-thin-runner-design.md](2026-11-03-rissk-installable-thin-runner-design.md): sections 2, 3, 5 and 8 (tarball fetch, env materialisation, the runner's body, private repositories) no longer describe the code.`

- [ ] **Step 6: Whole suite, commit**

Run: `uv run --extra test pytest -q`
Expected: PASS.

```bash
git add .github/workflows/windmill-sync.yml windmill/README.md CLAUDE.md docs/superpowers/specs/2026-10-23-windmill-scheduling-design.md tests/test_windmill_runner.py
git commit -m "ci(windmill): deploy every push of kedro_pipeline with the rissk commit pinned; docs"
```

---

### Task 7: Final verification

- [ ] **Step 1: Full suite and a wheel-based smoke run**

Run: `uv run --extra test pytest -q` → PASS. Then repeat Task 4 Step 4's installed-mode check against the final branch (build, install into a fresh venv, run `run_survey(mapping)` from an empty directory) → session starts, plain logging, no `conf/` written.

- [ ] **Step 2: Deploy dry run**

Run (needs the wmill CLI and a registered workspace; skip if not available and say so): `cd windmill && wmill sync push --dry-run` → shows `f/rissk/run_rissk` as changed, nothing else.

- [ ] **Step 3: Push the branch**

```bash
git push -u origin thin-runner
```
Do not merge: merging into `kedro_pipeline` deploys to Windmill (the user's call). Report: the branch, what the first deploy will do (dependency job, lockfile line to check), and the smoke test from `windmill/README.md`.
