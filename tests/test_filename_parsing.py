"""Survey Solutions export-filename parsing.

Covers the classic ``<name>_<version>_<format>_<status>`` convention plus the
newer SuSo paradata export naming that adds a ``Reduced`` infix and/or a trailing
export timestamp (e.g. ``..._Paradata_Reduced_All_20260611T1347Z``).
"""
from pathlib import Path

import pytest

from rissk.utils.file_process_utils import get_file_parts, parse_filename
from rissk.utils.import_utils import get_survey_info


@pytest.mark.parametrize(
    "filename, expected",
    [
        # ── classic naming (must keep working) ──────────────────────────────
        ("slchbs_grenada_2627_10_Paradata_All",
         ("slchbs_grenada_2627", 10, "Paradata", "All")),
        ("slbhies_listing_6_Paradata_All",
         ("slbhies_listing", 6, "Paradata", "All")),
        ("snb_hies_hh_11_STATA_All",
         ("snb_hies_hh", 11, "Tabular", "All")),
        ("snb_hies_hh_9_Tabular_ApprovedByHQ",
         ("snb_hies_hh", 9, "Tabular", "ApprovedByHQ")),
        # ── NEW: reduced paradata + export timestamp ────────────────────────
        ("slchbs_grenada_2627_10_Paradata_Reduced_All_20260611T1347Z",
         ("slchbs_grenada_2627", 10, "Paradata", "All")),
        # ── trailing timestamp on a tabular export ──────────────────────────
        ("slchbs_grenada_2627_10_STATA_All_20260611T1347Z",
         ("slchbs_grenada_2627", 10, "Tabular", "All")),
    ],
)
def test_get_file_parts(filename, expected):
    assert get_file_parts(filename) == expected


def test_reduced_paradata_registers_under_paradata_key():
    """The reduced-paradata folder must not be silently skipped: it has to land
    under the 'Paradata' key so the ingestion node can find it."""
    paths = [
        Path("/x/slchbs_grenada_2627_10_Paradata_Reduced_All_20260611T1347Z"),
        Path("/x/slchbs_grenada_2627_10_STATA_All"),
    ]
    info = get_survey_info(paths)
    formats = info["slchbs_grenada_2627"]["slchbs_grenada_2627_10"]
    assert set(formats) == {"Paradata", "Tabular"}


@pytest.mark.parametrize("bad", ["not_a_survey_file", "missing_status_5_Paradata"])
def test_invalid_names_raise(bad):
    with pytest.raises(ValueError):
        get_file_parts(bad)
