"""Storage helpers for the Kedro pipeline.

This is Kedro storage plumbing (the input_root/work_root model), the same category as
``datasets/PathDataset`` — kept here rather than in the survey-domain ``rissk/`` package.
Survey Solutions exports may live locally or on ``s3://``; ``fsspec`` makes both the same
code path. The downstream unzip + folder readers only work on the local filesystem, so
staging copies the relevant zips into the always-local ``work_root`` first.
"""
from __future__ import annotations

import glob
import logging
import shutil
from pathlib import Path

import fsspec

logger = logging.getLogger(__name__)


def stage_zips(input_root: str, survey: str, name: str, work_root: str) -> list[str]:
    """Stage this questionnaire's export zips into the LOCAL work area.

    Survey Solutions exports are survey-level — all questionnaires' zips sit together in
    ``<input_root>/<survey>/latest/10_RAW/`` (``input_root`` may be a local path or
    ``s3://<bucket>``). This copies the ones matching ``<name>_*.zip`` into the local
    ``<work_root>/<survey>/latest/10_RAW/`` (via fsspec, so local + s3 both work), because
    the unzip + folder reads downstream only work on the local filesystem. When
    ``input_root == work_root`` this destination IS the input dir — handled below by a
    questionnaire-scoped cleanup + a size-match skip (so input zips are never clobbered).

    Returns the sorted names of the zips wanted this run (for the caller to log).
    """
    if not survey or not name:
        raise ValueError(
            "stage_zips: both 'survey' and 'questionnaire.name' must be set "
            f"(got survey={survey!r}, name={name!r}). Select a run configuration with "
            "`kedro run --env <config>` whose globals.yml sets them — a bare `kedro run` "
            "uses the empty conf/base defaults and would silently process no data."
        )

    src = f"{str(input_root).rstrip('/')}/{survey}/latest/10_RAW"
    dest = Path(work_root) / survey / "latest" / "10_RAW"
    dest.mkdir(parents=True, exist_ok=True)

    fs, src_path = fsspec.core.url_to_fs(src)
    # Drop any cached directory listing before globbing. run_survey stages every
    # questionnaire in ONE process reusing a cached fsspec filesystem; for s3://, s3fs
    # caches listings, and the listing cached by the FIRST questionnaire's glob shadows
    # later questionnaires — so their glob returns nothing, only the first questionnaire is
    # ever staged, and the rest silently produce empty output. Local filesystems have no
    # such cache, hence this only bites s3://. Invalidating forces a fresh listing per call.
    fs.invalidate_cache(src_path)
    # Escape glob metacharacters in the name so a name containing [, ], ?, * matches
    # literally — only the trailing _*.zip is a wildcard.
    zips = fs.glob(f"{src_path}/{glob.escape(name)}_*.zip")
    if not zips:
        logger.warning(f"stage_zips: no zips matching {name}_*.zip under {src}")

    # Refresh the staging dir before fetching: drop this questionnaire's stale zips (a
    # now-removed version) and ALL its previously-extracted `<name>_*` folders (re-extracted
    # downstream) so filter_matching_folders can't pick up obsolete export data. Scope it to
    # the `<name>_` prefix: the dir is survey-level (and, when input_root == work_root, IS the
    # input dir), so we must leave other questionnaires' zips and markers like `_success`
    # untouched. Wanted zips are kept so the size-match check below skips re-downloading them
    # (and skips a self-copy when the source already sits here).
    wanted = {Path(z).name for z in zips}
    prefix = f"{name}_"
    for existing in dest.iterdir():
        if existing.is_file() and existing.name in wanted:
            continue
        if not existing.name.startswith(prefix):
            continue
        shutil.rmtree(existing) if existing.is_dir() else existing.unlink()

    for z in zips:
        target = dest / Path(z).name
        # Skip the fetch when an identical zip is already staged (size match) — keeps
        # re-runs cheap, especially for s3:// input_root.
        if target.exists() and target.stat().st_size == fs.info(z)["size"]:
            logger.info(f"Already staged (size match), skipping {target.name}")
            continue
        logger.info(f"Staging {z} -> {target}")
        fs.get(z, str(target))

    return sorted(wanted)
