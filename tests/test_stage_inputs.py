"""stage_input_zips_node: fetch + survey→questionnaire filter into local staging.

S3_BUCKET-style sources are exercised with a local ``file://`` path — fsspec treats
``file://`` and ``s3://`` identically, so this is the real code path without a bucket.
"""
import pytest

from rissk.pipelines.data_ingestion.nodes import stage_input_zips_node


def _make_raw(root, *files):
    raw = root / "surv" / "latest" / "10_RAW"
    raw.mkdir(parents=True)
    for fn in files:
        (raw / fn).write_text("zip")
    return raw


def test_stage_filters_by_questionnaire(tmp_path):
    src = tmp_path / "data"
    _make_raw(src, "qx_10_STATA_All.zip", "qx_11_Paradata_All.zip", "other_10_STATA_All.zip", "_success")
    work = tmp_path / "work"

    flag = stage_input_zips_node(str(src), "surv", str(work), {"name": "qx"})

    assert flag is True
    staged = sorted(p.name for p in (work / "surv" / "latest" / "10_RAW").glob("*.zip"))
    assert staged == ["qx_10_STATA_All.zip", "qx_11_Paradata_All.zip"]


def test_stage_s3_style_via_file_url(tmp_path):
    src = tmp_path / "bucket"
    _make_raw(src, "qx_10_STATA_All.zip")
    work = tmp_path / "work"

    stage_input_zips_node(f"file://{src}", "surv", str(work), {"name": "qx"})

    assert (work / "surv" / "latest" / "10_RAW" / "qx_10_STATA_All.zip").exists()


def test_stage_no_match_is_ok(tmp_path):
    src = tmp_path / "data"
    _make_raw(src, "other_10_STATA_All.zip")
    work = tmp_path / "work"

    flag = stage_input_zips_node(str(src), "surv", str(work), {"name": "qx"})

    assert flag is True
    assert list((work / "surv" / "latest" / "10_RAW").glob("*.zip")) == []


def test_stage_removes_stale_zips_and_extracted_folders(tmp_path):
    """A previous run's zip (now removed from source) and any extracted folder must
    be cleared so filter_matching_folders cannot pick up obsolete data."""
    src = tmp_path / "data"
    _make_raw(src, "qx_10_STATA_All.zip")  # source now only has v10
    work = tmp_path / "work"
    dest = work / "surv" / "latest" / "10_RAW"
    dest.mkdir(parents=True)
    (dest / "qx_99_STATA_All.zip").write_text("stale")        # stale zip
    (dest / "qx_99_STATA_All").mkdir()                         # stale extracted folder
    (dest / "qx_99_STATA_All" / "data.tab").write_text("old")

    stage_input_zips_node(str(src), "surv", str(work), {"name": "qx"})

    assert sorted(p.name for p in dest.iterdir()) == ["qx_10_STATA_All.zip"]


def test_stage_skips_refetch_when_size_matches(tmp_path):
    """An already-staged zip of the same size is kept, not re-fetched (cheap re-runs)."""
    src = tmp_path / "data"
    raw = _make_raw(src)
    (raw / "qx_10_STATA_All.zip").write_text("ABCD")  # 4 bytes
    work = tmp_path / "work"
    dest = work / "surv" / "latest" / "10_RAW"
    dest.mkdir(parents=True)
    (dest / "qx_10_STATA_All.zip").write_text("WXYZ")  # same size, different bytes

    stage_input_zips_node(str(src), "surv", str(work), {"name": "qx"})

    # Skipped → keeps the pre-existing bytes rather than overwriting from source.
    assert (dest / "qx_10_STATA_All.zip").read_text() == "WXYZ"


def test_stage_cleanup_is_scoped_to_questionnaire(tmp_path):
    """Stale ``<name>_*`` entries are cleared, but another questionnaire's zips and
    markers (e.g. ``_success``) in the shared survey-level dir are left untouched."""
    src = tmp_path / "data"
    _make_raw(src, "qx_10_STATA_All.zip")
    work = tmp_path / "work"
    dest = work / "surv" / "latest" / "10_RAW"
    dest.mkdir(parents=True)
    (dest / "qx_99_STATA_All.zip").write_text("stale")    # stale qx zip   -> removed
    (dest / "qx_99_STATA_All").mkdir()                     # stale qx folder -> removed
    (dest / "other_5_STATA_All.zip").write_text("keep")   # another questionnaire -> kept
    (dest / "_success").write_text("")                    # marker -> kept

    stage_input_zips_node(str(src), "surv", str(work), {"name": "qx"})

    assert sorted(p.name for p in dest.iterdir()) == [
        "_success", "other_5_STATA_All.zip", "qx_10_STATA_All.zip",
    ]


def test_stage_in_place_when_input_is_work_root(tmp_path):
    """When input_root == work_root the staging dest IS the input dir; the questionnaire's
    input zips and a foreign ``_success`` must survive (no self-destruct, no self-copy)."""
    root = tmp_path / "data"
    raw = _make_raw(root, "qx_10_STATA_All.zip", "qx_11_STATA_All.zip", "_success")

    flag = stage_input_zips_node(str(root), "surv", str(root), {"name": "qx"})

    assert flag is True
    # `raw` IS <work_root>/surv/latest/10_RAW — input zips + marker still present, intact.
    assert sorted(p.name for p in raw.iterdir()) == [
        "_success", "qx_10_STATA_All.zip", "qx_11_STATA_All.zip",
    ]
    assert (raw / "qx_10_STATA_All.zip").read_text() == "zip"  # not truncated by a self-copy


@pytest.mark.parametrize("survey,name", [("", "qx"), ("surv", ""), ("", "")])
def test_stage_requires_survey_and_name(tmp_path, survey, name):
    with pytest.raises(ValueError):
        stage_input_zips_node(str(tmp_path), survey, str(tmp_path / "work"), {"name": name})
