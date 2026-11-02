"""Data loaders for the interactive visualisation notebooks (``notebooks/viz/``).

Reads **only** the pipeline's output files for a chosen questionnaire under
``<data_root>/<questionnaire>/latest/`` — the scored CSV/parquet in ``35_SCORES``
and the feature parquet in ``30_PROCESSED``. It imports nothing from the legacy
``rissk`` package, so the notebooks depend on data on disk, not pipeline code.

Used by ``feature_scores.py``, ``unit_scores.py`` and ``interview_scores.py``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

import numpy as np
import pandas as pd

# src/rissk/viz.py -> parents[2] == the Kedro project root (repo root)
KEDRO_PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = KEDRO_PROJECT / "data"

PathLike = Union[str, Path]


def _data_root(data_root: Optional[PathLike] = None) -> Path:
    return Path(data_root) if data_root else DEFAULT_DATA_ROOT


def list_questionnaires(data_root: Optional[PathLike] = None) -> list[str]:
    """Questionnaire folders that have a scored output, most-recent first.

    A folder qualifies when ``<data_root>/<name>/latest/35_SCORES/unit_rissk_scores.csv``
    exists; folders are ordered by that file's modification time (newest first).
    """
    root = _data_root(data_root)
    if not root.is_dir():
        return []
    found: list[tuple[str, float]] = []
    for child in sorted(root.iterdir()):
        scored = child / "latest" / "35_SCORES" / "unit_rissk_scores.csv"
        if scored.is_file():
            found.append((child.name, scored.stat().st_mtime))
    return [name for name, _ in sorted(found, key=lambda x: x[1], reverse=True)]


def _scored_dir(questionnaire: str, data_root: Optional[PathLike] = None) -> Path:
    return _data_root(data_root) / questionnaire / "latest" / "35_SCORES"


def _processed_dir(questionnaire: str, data_root: Optional[PathLike] = None) -> Path:
    return _data_root(data_root) / questionnaire / "latest" / "30_PROCESSED"


def load_unit_scores(questionnaire: str, data_root: Optional[PathLike] = None) -> pd.DataFrame:
    """Per-interview scores: ``unit_risk_score`` (0–100), ``responsible_score`` and ``s__*``."""
    return pd.read_csv(_scored_dir(questionnaire, data_root) / "unit_rissk_scores.csv")


def load_item_scores(questionnaire: str, data_root: Optional[PathLike] = None) -> pd.DataFrame:
    """Per-item scores (one row per interview × variable × roster level), ``s__*`` columns."""
    return pd.read_parquet(_scored_dir(questionnaire, data_root) / "item_scores.parquet")


def load_responsible_scores(questionnaire: str, data_root: Optional[PathLike] = None) -> pd.DataFrame:
    """Per-interviewer (responsible) aggregated scores."""
    return pd.read_csv(_scored_dir(questionnaire, data_root) / "responsible_scores.csv")


def load_unit_features(questionnaire: str, data_root: Optional[PathLike] = None) -> pd.DataFrame:
    """Per-interview engineered features (``f__*``)."""
    return pd.read_parquet(_processed_dir(questionnaire, data_root) / "unit_features.parquet")


def load_item_features(questionnaire: str, data_root: Optional[PathLike] = None) -> pd.DataFrame:
    """Per-item engineered features."""
    return pd.read_parquet(_processed_dir(questionnaire, data_root) / "item_features.parquet")


def score_columns(df: pd.DataFrame) -> list[str]:
    """The ``s__*`` score columns present in a scores frame."""
    return [c for c in df.columns if c.startswith("s__")]


def feature_columns(df: pd.DataFrame) -> list[str]:
    """The ``f__*`` feature columns present in a features frame."""
    return [c for c in df.columns if c.startswith("f__")]


def histogram_df(series: pd.Series, bins: int = 30) -> pd.DataFrame:
    """Pre-bin a numeric series into ``bin_start/bin_end/count`` rows.

    Pre-aggregating in pandas keeps Altair fed with a tiny frame, so the large
    item-level tables (~170k rows) never hit Altair's default row limit.
    """
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return pd.DataFrame({"bin_start": [], "bin_end": [], "count": []})
    counts, edges = np.histogram(s, bins=bins)
    return pd.DataFrame(
        {"bin_start": edges[:-1], "bin_end": edges[1:], "count": counts.astype(int)}
    )
