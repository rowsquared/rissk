"""In-process driver for running the pipeline across a survey's questionnaires.

``run_survey`` takes the survey configuration in one of two forms:

* an **env name**: one survey = one Kedro env (``conf/<env>/``) in a checkout. The
  questionnaires are one small YAML each under ``conf/<env>/questionnaires/``; Kedro loads
  the env folder (globals and whatever else it holds) and the driver injects the selection
  per questionnaire as runtime params. A legacy env with a single questionnaire in
  ``globals.yml`` and no ``questionnaires/`` folder is run once, flat, with no combine.
* a **mapping** (the shape of the Windmill ``rissk_survey`` resource, see
  ``validate_survey_config``): nothing is read from any env folder. Everything becomes
  runtime params on an ``env="base"`` session, the outputs use the questionnaire sub-folder
  layout and the combine step runs. This form also works from an **installed** rissk (no
  checkout): the wheel carries ``conf/base`` as ``rissk/_conf/base`` and that is the conf
  source then; relative paths such as the default ``work_root`` resolve against the
  working directory.

Either way the static 3-stage pipeline runs once per questionnaire, then a catalog-backed
combine step unions the per-questionnaire microdata into the survey-level microdata file
via a plain ``catalog.load`` / pure function / ``catalog.save``. Logging is a plain
console format (this is the headless driver; ``kedro run`` keeps ``conf/logging.yml``).
"""
from __future__ import annotations

import copy
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Callable, Dict, Optional, Union

import pandas as pd
import yaml

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]

PLAIN_LOGGING: dict = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple",
                             "stream": "ext://sys.stdout"}},
    "loggers": {"kedro": {"level": "INFO"}, "rissk": {"level": "INFO"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}

# Runtime keys the driver sets itself; a `parameters` override may not name them.
RESERVED_RUNTIME_KEYS = frozenset(
    {"questionnaire", "qnr_subdir", "survey", "input_root", "output_root", "work_root"}
)
_SURVEY_KEYS = frozenset(
    {"survey", "input_root", "output_root", "work_root", "questionnaires", "zip_password", "parameters"}
)
_URL = re.compile(r"^[a-z][a-z0-9+.-]*://")


def _default_root() -> Path:
    """The checkout root when rissk is imported from ``<root>/src/rissk``."""
    import rissk
    return Path(rissk.__file__).resolve().parents[2]


def _packaged_conf() -> Path:
    """``rissk/_conf`` inside the installed package (absent in a checkout)."""
    return Path(str(files("rissk") / "_conf"))


def _project_root(project_root: Optional[PathLike] = None) -> Path:
    if project_root is not None:
        return Path(project_root)
    return _default_root()


def _is_checkout(root: Path) -> bool:
    return (root / "pyproject.toml").is_file() and (root / "conf" / "base").is_dir()


@dataclass(frozen=True)
class _Project:
    """Where Kedro runs from: a checkout (its ``conf/``) or an installed package."""

    root: Path                 # KedroSession project_path
    conf_source: Optional[str]  # None: the checkout's conf/; else the packaged rissk/_conf
    installed: bool


def _resolve_project(project_root: Optional[PathLike]) -> _Project:
    if project_root is not None:
        root = Path(project_root)
        if not _is_checkout(root):
            raise ValueError(f"{root} is not a rissk checkout (no pyproject.toml + conf/base)")
        return _Project(root=root, conf_source=None, installed=False)
    root = _default_root()
    if _is_checkout(root):
        return _Project(root=root, conf_source=None, installed=False)
    packaged = _packaged_conf()
    if packaged.is_dir():
        return _Project(root=Path.cwd(), conf_source=str(packaged), installed=True)
    raise RuntimeError(
        f"rissk is neither a checkout at {root} nor an installed package with its "
        f"configuration at {packaged}"
    )


def _prepare(project: _Project) -> None:
    """Configure Kedro once per call: plain logging, the project settings, the CWD."""
    from kedro.framework.project import LOGGING, configure_project
    from kedro.framework.startup import bootstrap_project

    LOGGING.configure(PLAIN_LOGGING)
    if project.installed:
        configure_project("rissk")
        return
    bootstrap_project(project.root)
    # Anchor the CWD to the project root. Kedro resolves relative dataset paths against
    # the project root, but stage_zips stages relative to the CWD; when a caller runs from
    # elsewhere (e.g. a JupyterHub Notebook Job executes a COPY of the notebook from
    # /jobs/<id>/) the two diverge and ingestion fails with "No partitions found". This
    # keeps every run_survey caller correct regardless of the launch directory.
    os.chdir(project.root)


def _run_pipeline(project: _Project, env: str, runtime_params: Optional[dict], pipeline: str) -> None:
    from kedro.framework.session import KedroSession

    # Kedro 1.x names this `runtime_params` (feeds both params:* and the
    # ${runtime_params:...} catalog resolver); it was `extra_params` pre-0.19.
    with KedroSession.create(project_path=project.root, env=env, runtime_params=runtime_params,
                             conf_source=project.conf_source) as session:
        session.run(pipeline_names=[pipeline])


def _combine(project: _Project, env: str, runtime_params: dict) -> None:
    from kedro.framework.session import KedroSession

    with KedroSession.create(project_path=project.root, env=env, runtime_params=runtime_params,
                             conf_source=project.conf_source) as session:
        catalog = session.load_context().catalog
        partitions = catalog.load("microdata_by_qnr")
        combined = combine_microdata(partitions)
        catalog.save("microdata_combined", combined)


def load_questionnaire_configs(env: str, project_root: Optional[PathLike] = None) -> list[dict]:
    """Parse ``conf/<env>/questionnaires/*.yml`` (sorted by filename). Empty if absent."""
    qdir = _project_root(project_root) / "conf" / env / "questionnaires"
    if not qdir.is_dir():
        return []
    configs = []
    for path in sorted(qdir.glob("*.yml")):
        with open(path) as fh:
            cfg = yaml.safe_load(fh) or {}
        if "name" not in cfg:
            raise ValueError(f"{path}: questionnaire yaml must set 'name'")
        configs.append(cfg)
    return configs


def _non_empty_str(cfg: Mapping, key: str) -> str:
    value = cfg.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"survey config: {key} must be a non-empty string")
    return value


def validate_survey_config(config: Mapping) -> dict:
    """Check a survey configuration given as data and return a normalised copy.

    The mapping has the shape of the Windmill ``rissk_survey`` resource: ``survey``,
    ``input_root``, ``output_root`` (a URL: a run configured as data has no project folder
    to write into; ``file:///abs/path`` for a local folder), ``questionnaires`` (each
    ``name``, optional ``VERSION`` list of ints, optional ``filter_var`` mapping), and
    optionally ``work_root`` (default ``data``, relative to the working directory),
    ``zip_password`` and ``parameters`` (top-level keys of ``parameters.yml``, deep-merged
    by Kedro over ``conf/base``). Unknown keys are rejected so a typo cannot pass silently.
    """
    if not isinstance(config, Mapping):
        raise ValueError(
            f"survey config must be a mapping (the rissk_survey resource); got {type(config).__name__}"
        )
    unknown = sorted(set(config) - _SURVEY_KEYS)
    if unknown:
        raise ValueError(f"survey config: unknown key {unknown[0]!r} (allowed: {sorted(_SURVEY_KEYS)})")

    survey = _non_empty_str(config, "survey")
    input_root = _non_empty_str(config, "input_root").rstrip("/")
    output_root = _non_empty_str(config, "output_root").rstrip("/")
    if not _URL.match(output_root):
        raise ValueError(
            "survey config: output_root must be a URL such as s3://bucket "
            f"(file:///abs/path for a local folder); got {output_root!r}"
        )
    work_root = _non_empty_str(config, "work_root") if config.get("work_root") is not None else "data"

    questionnaires = config.get("questionnaires")
    if not isinstance(questionnaires, list) or not questionnaires:
        raise ValueError("survey config: questionnaires must be a non-empty list")
    normalised = []
    for i, q in enumerate(questionnaires):
        if not isinstance(q, Mapping) or not isinstance(q.get("name"), str) or not q["name"].strip():
            raise ValueError(f"survey config: questionnaires[{i}].name must be a non-empty string")
        versions = q.get("VERSION")
        versions = [] if versions is None else versions
        if not isinstance(versions, list) or not all(
            isinstance(v, int) and not isinstance(v, bool) for v in versions
        ):
            raise ValueError(f"survey config: questionnaires[{i}].VERSION must be a list of integers")
        filter_var = q.get("filter_var")
        if filter_var is not None and not isinstance(filter_var, Mapping):
            raise ValueError(f"survey config: questionnaires[{i}].filter_var must be a mapping or null")
        normalised.append({"name": q["name"], "VERSION": list(versions),
                           "filter_var": copy.deepcopy(dict(filter_var)) if filter_var is not None else None})

    zip_password = config.get("zip_password")
    if zip_password is not None and not isinstance(zip_password, str):
        raise ValueError("survey config: zip_password must be a string or null")

    parameters = config.get("parameters")
    parameters = {} if parameters is None else parameters
    if not isinstance(parameters, Mapping):
        raise ValueError("survey config: parameters must be a mapping of parameters.yml keys")
    reserved = sorted(RESERVED_RUNTIME_KEYS & set(parameters))
    if reserved:
        raise ValueError(f"survey config: parameters key {reserved[0]!r} is reserved (set by the driver)")

    return {"survey": survey, "input_root": input_root, "output_root": output_root,
            "work_root": work_root, "questionnaires": normalised,
            "zip_password": zip_password or None, "parameters": copy.deepcopy(dict(parameters))}


def _runtime_params(cfg: dict, questionnaire: Optional[dict]) -> dict:
    """Kedro runtime params for one questionnaire of a validated config (``None``: the
    survey-level combine step). ``parameters`` overrides are folded in as top-level keys;
    Kedro deep-merges them over ``conf/base/parameters.yml``."""
    params = {"survey": cfg["survey"], "input_root": cfg["input_root"],
              "output_root": cfg["output_root"], "work_root": cfg["work_root"]}
    if cfg.get("zip_password"):
        params["zip_password"] = cfg["zip_password"]
    params.update(copy.deepcopy(cfg["parameters"]))
    if questionnaire is not None:
        params["questionnaire"] = questionnaire
        params["qnr_subdir"] = f"{questionnaire['name']}/"
    return params


def _plan_runs(config: Union[str, Mapping], project: _Project):
    """(env, [(label, runtime params or None)…], combine params or None) for a config."""
    if isinstance(config, str):
        if project.installed:
            raise ValueError(
                f"env {config!r} needs a checkout (conf/{config}/); from an installed rissk "
                "pass the configuration as a mapping"
            )
        qnrs = load_questionnaire_configs(config, project.root)
        if not qnrs:
            # Legacy single-questionnaire env: questionnaire comes from globals, no combine.
            return config, [(config, None)], None
        runs = [(q["name"], {"questionnaire": q, "qnr_subdir": f"{q['name']}/"}) for q in qnrs]
        return config, runs, {}
    cfg = validate_survey_config(config)
    runs = [(q["name"], _runtime_params(cfg, q)) for q in cfg["questionnaires"]]
    return "base", runs, _runtime_params(cfg, None)


def run_survey(
    config: Union[str, Mapping],
    project_root: Optional[PathLike] = None,
    pipeline: str = "__default__",
    run_combine: bool = True,
) -> dict[str, str]:
    """Run the pipeline for every questionnaire of ``config`` (an env name or a mapping),
    then combine once.

    Returns an outcome map; failures are isolated per questionnaire so one bad
    questionnaire does not skip the rest.
    """
    project = _resolve_project(project_root)
    _prepare(project)
    env, runs, combine_params = _plan_runs(config, project)
    results: dict[str, str] = {}

    for label, runtime_params in runs:
        print(f"=== run --env {env} --pipeline {pipeline} [{label}] ===", flush=True)
        try:
            _run_pipeline(project, env, runtime_params, pipeline)
            results[label] = "OK"
        except Exception as exc:  # isolate failures across questionnaires
            results[label] = f"FAILED ({type(exc).__name__}: {exc})"
        print(f"--- {label}: {results[label]} ---", flush=True)

    if run_combine and combine_params is not None:
        label = "combine"
        print(f"=== combine microdata --env {env} [{label}] ===", flush=True)
        try:
            _combine(project, env, combine_params)
            results[label] = "OK"
        except Exception as exc:  # keep failure isolated, like the per-questionnaire runs
            results[label] = f"FAILED ({type(exc).__name__}: {exc})"
        print(f"--- {label}: {results[label]} ---", flush=True)

    return results


def combine_microdata(partitions: Dict[str, Callable[[], pd.DataFrame]]) -> pd.DataFrame:
    """Union the per-questionnaire microdata into one survey-level table.

    ``partitions`` is a PartitionedDataset mapping of partition-key -> loader over
    ``30_PROCESSED``. The top-level union file (partition key ``''``) is skipped so the
    output can be rewritten in place idempotently; each ``'<qnr>/'`` partition is a
    per-questionnaire ``microdata.parquet``. A partition that fails to load is logged and
    skipped so one corrupt file does not sink the whole union.
    """
    frames = []
    for key, load in sorted(partitions.items()):
        if not key.strip("/"):
            continue  # the survey-level union file itself — never fold it back in
        try:
            df = load()
        except Exception as e:
            logger.error(
                "combine_microdata: failed to load partition %r. Skipping. Error: %s",
                key.strip("/"), str(e),
            )
            continue
        frames.append(df)
        logger.info("combine_microdata: adding partition %r", key.strip("/"))

    if not frames:
        logger.warning(
            "combine_microdata: no per-questionnaire microdata partitions found — "
            "returning empty DataFrame."
        )
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)
    logger.info("combine_microdata: unioned %d partitions -> %d rows", len(frames), len(combined))
    return combined


def combine_survey_microdata(config: Union[str, Mapping], project_root: Optional[PathLike] = None) -> None:
    """Union the survey's per-<qnr> microdata into the survey-level microdata.parquet.

    Loads the ``microdata_by_qnr`` PartitionedDataset and saves ``microdata_combined``
    through the Kedro catalog, so it works for local and ``s3://`` output roots alike.
    Runs standalone, on its own, to rebuild the union for an env name or a mapping.
    """
    project = _resolve_project(project_root)
    _prepare(project)
    if isinstance(config, str):
        env, runtime_params = config, {}
    else:
        env, runtime_params = "base", _runtime_params(validate_survey_config(config), None)
    _combine(project, env, runtime_params)
