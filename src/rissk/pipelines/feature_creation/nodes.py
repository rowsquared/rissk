"""Nodes for the Feature Creation pipeline."""
import logging
import pandas as pd
from typing import Dict, Any

from rissk.core.feature_processing import (
    create_base_item_table,
    create_base_unit_table,
    enrich_item_features,
    enrich_unit_features,
    feat_answer_removed,
)

logger = logging.getLogger(__name__)

def create_base_item_table_node(
    microdata: pd.DataFrame,
    paradata_full: pd.DataFrame,
    parameters: Dict[str, Any]
) -> pd.DataFrame:
    """Node wrapper for create_base_item_table."""
    questionnaire = parameters.get('questionnaire', {})
    lines = [
        "=" * 55,
        "  FEATURE CREATION — Configuration",
        "=" * 55,
        f"  Questionnaire: {questionnaire.get('name', 'unknown')}",
        "=" * 55,
    ]
    logger.info("\n" + "\n".join(lines))
    return create_base_item_table(microdata, paradata_full, parameters)

def create_base_unit_table_node(
    paradata_full: pd.DataFrame,
    parameters: Dict[str, Any]
) -> pd.DataFrame:
    """Node wrapper for create_base_unit_table."""
    return create_base_unit_table(paradata_full, parameters)

def enrich_item_features_node(
    item_features_base: pd.DataFrame,
    paradata_full: pd.DataFrame,
    parameters: Dict[str, Any]
) -> pd.DataFrame:
    """Node wrapper for enrich_item_features.
    paradata_full: all processed events, role=1, interviewing=True (equivalent to self.df_paradata).
    """
    return enrich_item_features(item_features_base, paradata_full, parameters)

def enrich_unit_features_node(
    unit_features_base: pd.DataFrame,
    item_features: pd.DataFrame,
    paradata_full: pd.DataFrame,
    parameters: Dict[str, Any]
) -> pd.DataFrame:
    """Node wrapper for enrich_unit_features.
    paradata_full: all processed events, role=1, interviewing=True (equivalent to self.df_paradata).
    """
    return enrich_unit_features(unit_features_base, item_features, paradata_full, parameters)

def build_removed_answers_node(
    paradata_full: pd.DataFrame,
    parameters: Dict[str, Any]
) -> pd.DataFrame:
    """Extract and aggregate all AnswerRemoved events as a standalone dataset.

    This node produces the removed_answers parquet which captures AnswerRemoved
    events for items that may no longer exist in microdata (deleted items), matching
    legacy get_feature_item__answer_removed behaviour. The output is consumed by
    the rissk_scoring pipeline to score s__answer_removed at unit level.
    """
    return feat_answer_removed(paradata_full)


def filter_by_consent(
    item_features: pd.DataFrame,
    unit_features: pd.DataFrame,
    removed_answers: pd.DataFrame,
    paradata: pd.DataFrame,
    filter_var,
):
    """Filter feature tables to interviews that match the consent variable.

    ``filter_var`` must be a dict with exactly one key-value pair
    ``{variable_name: answer_value}`` (matching the legacy ``limit_unit`` shape),
    or ``None`` to skip filtering entirely.

    When set, only interviews where ``variable_name == key`` and
    ``str(value) == str(answer_value)`` are retained across all three feature
    tables.  A WARNING is emitted so operators know filtering is active.
    """
    if filter_var is None:
        return item_features, unit_features, removed_answers

    consent_variable = next(iter(filter_var))
    # Careful: paradata answer column is always a string, so cast the
    # configured value to str — matching legacy filter_by_consent behaviour.
    consent_value = str(filter_var[consent_variable])

    logger.warning(
        "filter_by_consent: consent filtering is ACTIVE — "
        "keeping only interviews where '%s' == '%s'",
        consent_variable, consent_value,
    )

    cond1 = paradata["variable_name"] == consent_variable
    cond2 = paradata["answer"] == consent_value
    approved_ids = paradata.loc[cond1 & cond2, "interview__id"].unique()

    if len(approved_ids) == 0:
        total_interviews = unit_features["interview__id"].nunique()
        raise ValueError(
            f"filter_by_consent: filter_var "
            f"{{'{consent_variable}': '{consent_value}'}} matched 0 interviews "
            f"out of {total_interviews}. "
            f"Check that the variable name and answer value are correct. "
            f"Note: paradata answer values are always strings."
        )

    item_filtered = item_features[item_features["interview__id"].isin(approved_ids)].copy()
    unit_filtered = unit_features[unit_features["interview__id"].isin(approved_ids)].copy()

    if removed_answers is not None and not removed_answers.empty:
        removed_filtered = removed_answers[
            removed_answers["interview__id"].isin(approved_ids)
        ].copy()
    else:
        removed_filtered = removed_answers

    logger.info(
        "filter_by_consent: retained %d / %d interviews (%d item rows)",
        len(unit_filtered), len(unit_features), len(item_filtered),
    )

    return item_filtered, unit_filtered, removed_filtered
