"""Loaders behind the visualisation notebooks (rissk.viz)."""
import numpy as np
import pandas as pd
import pytest

from rissk import viz


def _make_scored(root, name, n=20):
    """Create a minimal <root>/<name>/latest/35_SCORES/unit_rissk_scores.csv."""
    scored = root / name / "latest" / "35_SCORES"
    scored.mkdir(parents=True)
    df = pd.DataFrame(
        {
            "interview__id": [f"iv{i}" for i in range(n)],
            "responsible": ["int_a", "int_b"] * (n // 2),
            "qnr_version": ["10"] * n,
            "unit_risk_score": np.linspace(0, 100, n),
            "s__answer_duration_lower": np.random.rand(n),
        }
    )
    df.to_csv(scored / "unit_rissk_scores.csv", index=False)
    return df


def test_list_questionnaires_only_scored_folders(tmp_path):
    _make_scored(tmp_path, "survey_a")
    (tmp_path / "survey_b" / "latest" / "10_RAW").mkdir(parents=True)  # no scored output
    assert viz.list_questionnaires(tmp_path) == ["survey_a"]


def test_list_questionnaires_newest_first(tmp_path):
    import os, time

    _make_scored(tmp_path, "older")
    _make_scored(tmp_path, "newer")
    # Force a newer mtime on "newer"
    newer = tmp_path / "newer" / "latest" / "35_SCORES" / "unit_rissk_scores.csv"
    os.utime(newer, (time.time() + 100, time.time() + 100))
    assert viz.list_questionnaires(tmp_path) == ["newer", "older"]


def test_list_questionnaires_missing_root(tmp_path):
    assert viz.list_questionnaires(tmp_path / "does_not_exist") == []


def test_load_unit_scores(tmp_path):
    expected = _make_scored(tmp_path, "survey_a")
    got = viz.load_unit_scores("survey_a", tmp_path)
    assert len(got) == len(expected)
    assert "unit_risk_score" in got.columns


def test_score_and_feature_columns():
    df = pd.DataFrame(columns=["interview__id", "s__a", "s__b", "f__x", "other"])
    assert viz.score_columns(df) == ["s__a", "s__b"]
    assert viz.feature_columns(df) == ["f__x"]


def test_histogram_df_counts_sum_to_n():
    s = pd.Series(np.arange(100, dtype=float))
    h = viz.histogram_df(s, bins=10)
    assert list(h.columns) == ["bin_start", "bin_end", "count"]
    assert h["count"].sum() == 100


def test_histogram_df_empty_series():
    h = viz.histogram_df(pd.Series([np.nan, np.nan]), bins=10)
    assert h["count"].sum() == 0
