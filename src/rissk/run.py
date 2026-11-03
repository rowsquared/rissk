"""In-process driver for running the pipeline across a survey's questionnaires.

One survey = one Kedro env (``conf/<env>/``). The questionnaires belonging to that
survey are declared as one small YAML each under ``conf/<env>/questionnaires/``. This
module enumerates them and runs the static 3-stage pipeline once per questionnaire
(injecting the selection via runtime params), then runs a catalog-backed combine step
once: it unions the per-questionnaire microdata into the survey-level microdata file
via a plain ``catalog.load`` / pure function / ``catalog.save``, rather than a
dedicated one-node Kedro pipeline.

A legacy env with a single questionnaire in ``globals.yml`` and no ``questionnaires/``
folder is run once, unchanged, with no combine step.
"""
from __future__ import annotations

import copy
import logging
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Callable, Dict, Optional, Union

import pandas as pd
import yaml

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]

# Runtime keys the driver sets itself; a `parameters` override may not name them.
RESERVED_RUNTIME_KEYS = frozenset(
    {"questionnaire", "qnr_subdir", "survey", "input_root", "output_root", "work_root"}
)
_SURVEY_KEYS = frozenset(
    {"survey", "input_root", "output_root", "work_root", "questionnaires", "zip_password", "parameters"}
)
_URL = re.compile(r"^[a-z][a-z0-9+.-]*://")


def _project_root(project_root: Optional[PathLike] = None) -> Path:
    if project_root is not None:
        return Path(project_root)
    import rissk
    return Path(rissk.__file__).resolve().parents[2]


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


def run_survey(
    env: str,
    project_root: Optional[PathLike] = None,
    pipeline: str = "__default__",
    run_combine: bool = True,
) -> dict[str, str]:
    """Run the pipeline for every questionnaire in ``env``, then combine once.

    Returns an outcome map; failures are isolated per questionnaire so one bad
    questionnaire does not skip the rest.
    """
    from kedro.framework.session import KedroSession
    from kedro.framework.startup import bootstrap_project

    root = _project_root(project_root)
    bootstrap_project(root)
    # Anchor the CWD to the project root. Kedro resolves relative dataset paths against
    # the project root, but stage_zips stages relative to the CWD; when a caller runs from
    # elsewhere (e.g. a JupyterHub Notebook Job executes a COPY of the notebook from
    # /jobs/<id>/) the two diverge and ingestion fails with "No partitions found". This
    # keeps every run_survey caller correct regardless of the launch directory.
    os.chdir(root)
    qnrs = load_questionnaire_configs(env, root)
    results: dict[str, str] = {}

    def _run(label: str, runtime_params: Optional[dict], pipe: str) -> None:
        print(f"=== run --env {env} --pipeline {pipe} [{label}] ===", flush=True)
        try:
            # Kedro 1.x names this `runtime_params` (feeds both params:* and the
            # ${runtime_params:...} catalog resolver); it was `extra_params` pre-0.19.
            with KedroSession.create(project_path=root, env=env, runtime_params=runtime_params) as session:
                session.run(pipeline_names=[pipe])
            results[label] = "OK"
        except Exception as exc:  # isolate failures across questionnaires
            results[label] = f"FAILED ({type(exc).__name__}: {exc})"
        print(f"--- {label}: {results[label]} ---", flush=True)

    if not qnrs:
        # Legacy single-questionnaire env: questionnaire comes from globals, no combine.
        _run(env, None, pipeline)
        return results

    for q in qnrs:
        name = q["name"]
        _run(name, {"questionnaire": q, "qnr_subdir": f"{name}/"}, pipeline)

    if run_combine:
        label = "combine"
        print(f"=== combine microdata --env {env} [{label}] ===", flush=True)
        try:
            combine_survey_microdata(env, root)
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


def combine_survey_microdata(env: str, project_root: Optional[PathLike] = None) -> None:
    """Union the survey's per-<qnr> microdata into the survey-level microdata.parquet.

    Loads the ``microdata_by_qnr`` PartitionedDataset and saves ``microdata_combined``
    through the Kedro catalog, so it works for local and ``s3://`` output roots alike.
    Runs standalone (used after the per-questionnaire loop, or on its own to rebuild the
    union).
    """
    from kedro.framework.session import KedroSession
    from kedro.framework.startup import bootstrap_project

    root = _project_root(project_root)
    bootstrap_project(root)
    with KedroSession.create(project_path=root, env=env) as session:
        catalog = session.load_context().catalog
        partitions = catalog.load("microdata_by_qnr")
        combined = combine_microdata(partitions)
        catalog.save("microdata_combined", combined)
