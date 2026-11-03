import pandas as pd
import numpy as np
from typing import Dict, Any, Tuple
import logging

from rissk.core.item_processing import (
    calculate_answer_hour_set_score,
    calculate_sequence_jump_score,
    calculate_first_decimals_score,
    calculate_answer_changed_score,
    # calculate_answer_removed_score is intentionally absent: s__answer_removed is
    # computed at unit level from the removed_answers dataset by calculate_answer_removed_score_from_df
    # so that AnswerRemoved events for items deleted from microdata are not missed.
    calculate_answer_removed_score_from_df,
    calculate_answer_position_score,
    calculate_answer_selected_score,
    calculate_answer_duration_score,
    calculate_single_question_score,
    calculate_multi_option_question_score,
    calculate_first_digit_score,
    calculate_gps_score
)
from rissk.core.unit_processing import (
    calculate_global_score,
    aggregate_unit_score_mean,
    aggregate_item_to_unit_scores,
    calculate_unit_level_scores,
    aggregate_item_to_responsible_scores,
    calculate_responsible_score
)

logger = logging.getLogger(__name__)

def calculate_item_scores(df_item: pd.DataFrame, parameters: Dict[str, Any]) -> pd.DataFrame:
    """
    Run item level scoring applying various mathematical models.
    f__answer_removed is already present in df_item from the feature creation pipeline.
    Each scoring function is only executed when its corresponding feature has use: true
    in parameters['features'], matching the feature creation pipeline behaviour.
    """
    questionnaire = parameters.get('questionnaire', {})
    lines = [
        "=" * 55,
        "  RISSK SCORING",
        "=" * 55,
        f"  Questionnaire: {questionnaire.get('name', 'unknown')}",
        "=" * 55,
    ]
    logger.info("\n" + "\n".join(lines))
    logger.info("Calculating Item Scores...")

    if df_item.empty:
        logger.warning("calculate_item_scores: item features DataFrame is empty — no items to score. Returning empty DataFrame.")
        return df_item

    features = parameters.get('features', {})
    df_scored = df_item

    if features.get('answer_hour_set', {}).get('use', False):
        logger.info("Calculating answer_hour_set_score")
        df_scored = calculate_answer_hour_set_score(df_scored, parameters)

    if features.get('sequence_jump', {}).get('use', False):
        logger.info("Calculating sequence_jump_score")
        df_scored = calculate_sequence_jump_score(df_scored, parameters)

    if features.get('first_decimals', {}).get('use', False):
        logger.info("Calculating first_decimals_score")
        df_scored = calculate_first_decimals_score(df_scored, parameters)

    if features.get('answer_changed', {}).get('use', False):
        logger.info("Calculating answer_changed_score")
        df_scored = calculate_answer_changed_score(df_scored, parameters)

    # s__answer_removed is not computed here — see calculate_answer_removed_score_from_df
    # in calculate_unit_scores, which scores from the removed_answers dataset to match legacy coverage.

    if features.get('answer_position', {}).get('use', False):
        logger.info("Calculating answer_position_score")
        df_scored = calculate_answer_position_score(df_scored, parameters)

    if features.get('answer_selected', {}).get('use', False):
        logger.info("Calculating answer_selected_score")
        df_scored = calculate_answer_selected_score(df_scored, parameters)

    if features.get('answer_duration', {}).get('use', False):
        logger.info("Calculating answer_duration_score")
        df_scored = calculate_answer_duration_score(df_scored, parameters)

    if features.get('single_question', {}).get('use', False):
        logger.info("Calculating single_question_score")
        df_scored = calculate_single_question_score(df_scored)

    if features.get('multi_option_question', {}).get('use', False):
        logger.info("Calculating multi_option_question_score")
        df_scored = calculate_multi_option_question_score(df_scored)

    if features.get('first_digit', {}).get('use', False):
        logger.info("Calculating first_digit_score")
        df_scored = calculate_first_digit_score(df_scored)

    if features.get('gps', {}).get('use', False):
        logger.info("Calculating gps_score")
        df_scored = calculate_gps_score(df_scored, parameters)

    # Keep only the columns needed for downstream unit/responsible scoring and output.
    # - responsible: required by aggregate_item_to_responsible_scores (groupby + init)
    # - s__gps is produced by calculate_gps_score (f__gps.astype(int)) when GPS is
    #   enabled and is picked up naturally by the s__ filter below.
    id_cols = ['qnr', 'qnr_version', 'index_col', 'interview__id', 'variable_name', 'roster_level', 'responsible']
    score_cols = [c for c in df_scored.columns if c.startswith('s__')]
    keep_cols = [c for c in id_cols + score_cols if c in df_scored.columns]
    df_scored = df_scored[keep_cols]

    return df_scored

def calculate_unit_scores(
        df_unit: pd.DataFrame,
        df_item_scores: pd.DataFrame,
        parameters: Dict[str, Any], removed_answers: pd.DataFrame = None
        ) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Aggregate item scores to unit, extract responsible scores, and calculate global risk.

    This node receives data for a single questionnaire — the pipeline_registry filters
    item_features / unit_features per questionnaire before invoking the scoring pipeline,
    so no internal qnr loop is needed here.

    removed_answers is the pre-aggregated AnswerRemoved dataset produced by build_removed_answers_node.
    It is used to compute s__answer_removed at unit level, matching legacy behaviour where
    items deleted from microdata (absent from df_item) are still counted.
    """
    logger.info("Calculating Unit Scores and Global Risk...")

    if df_unit.empty:
        logger.warning("calculate_unit_scores: unit features DataFrame is empty — no units to score. Returning empty DataFrames.")
        return pd.DataFrame(), pd.DataFrame()

    features = parameters.get('features', {})

    # 1. Aggregate item-level scores up to unit level.
    # s__answer_removed is excluded from this aggregation (see aggregate_item_to_unit_scores);
    # it is handled below using removed_answers to match legacy coverage.
    df_unit_scored = aggregate_item_to_unit_scores(df_unit, df_item_scores)

    # 2a. Score answer_removed at unit level from removed_answers.
    # This replicates legacy make_score_unit__answer_removed which read from df_paradata
    # directly and therefore included AnswerRemoved events for items later deleted from
    # microdata. Falling back to the df_item-based mean when removed_answers is unavailable.
    if features.get('answer_removed', {}).get('use', False):
        if removed_answers is not None and not removed_answers.empty:
            unit_removed = calculate_answer_removed_score_from_df(removed_answers, parameters)
            df_unit_scored['s__answer_removed'] = df_unit_scored['interview__id'].map(unit_removed).fillna(0)
        elif 's__answer_removed' in df_item_scores.columns:
            logger.warning(
                "removed_answers not available; falling back to df_item-based s__answer_removed "
                "aggregation (may undercount removals for deleted items)."
            )
            data = df_item_scores.groupby('interview__id')['s__answer_removed'].mean()
            df_unit_scored['s__answer_removed'] = df_unit_scored['interview__id'].map(data).fillna(0)

    # 2b. Add pure unit-level calculations (row-wise or by interview__id).
    df_unit_scored = calculate_unit_level_scores(df_unit_scored, parameters)

    qnr_name = df_unit_scored['qnr'].iloc[0] if 'qnr' in df_unit_scored.columns and not df_unit_scored.empty else None
    logger.info(f"Scoring questionnaire: {qnr_name!r} ({len(df_unit_scored)} interviews)")

    if df_unit_scored.empty:
        logger.warning(f"No units found for questionnaire '{qnr_name}' — returning empty.")
        return df_unit_scored, pd.DataFrame()

    # 3. Aggregate item scores to responsible level.
    # Seed df_resp from unit_features responsibles (all responsibles with any interview
    # activity), matching legacy which seeds _df_resp from df_active_paradata.
    # Responsibles present in unit_features but absent from item_scores (no scoreable
    # items) will have NaN in all score columns → filled to 0 before PCA, exactly as
    # legacy make_responsible_score does via fillna(0).
    df_resp_init = (
        df_unit_scored[['responsible']]
        .drop_duplicates()
        .loc[lambda d: (d['responsible'] != '') & d['responsible'].notna()]
        .reset_index(drop=True)
        .copy()
    )
    df_resp = aggregate_item_to_responsible_scores(df_resp_init, df_item_scores)

    # 4. PCA-based responsible score.
    # restricted_columns = ALL unit-level s__ columns (matching legacy make_responsible_score
    # which receives restricted_columns=_score_columns, the full set including constant cols).
    # This ensures any responsible-level feature that also appears at unit level (e.g.
    # s__single_question, s__answer_position) is excluded from the resp PCA regardless of
    # whether it has variance — exactly as legacy does.
    score_columns = [c for c in df_unit_scored.columns if c.startswith('s__')]
    df_resp = calculate_responsible_score(df_resp, score_columns)

    # 5. IForest global unit risk score.
    df_unit_final = calculate_global_score(
        df_unit_scores=df_unit_scored,
        df_resp_scores=df_resp,
        score_columns=score_columns,
        combine_resp_score=True,
        restricted_columns=None,
    )

    # 6. Merge responsible-level s__ columns back onto unit output.
    # Legacy save() merges _df_resp (s__single_question, s__multi_option_question,
    # s__answer_position, s__first_digit) back onto _df_unit by responsible.
    resp_s_cols = [c for c in df_resp.columns if c.startswith('s__')]
    if resp_s_cols and 'responsible' in df_resp.columns:
        new_resp_cols = [c for c in resp_s_cols if c not in df_unit_final.columns]
        if new_resp_cols:
            df_unit_final = df_unit_final.merge(
                df_resp[['responsible'] + new_resp_cols],
                on='responsible',
                how='left',
            )
            df_unit_final[new_resp_cols] = df_unit_final[new_resp_cols].fillna(0)

    # Drop feature columns (f__*) from unit output — only scores and identifiers needed.
    feature_cols = [c for c in df_unit_final.columns if c.startswith('f__')]
    df_unit_final = df_unit_final.drop(columns=feature_cols)

    # Always ensure responsible_score is present (may be absent when PCA was skipped).
    if 'responsible_score' not in df_unit_final.columns:
        df_unit_final['responsible_score'] = np.nan

    # Apply column ordering for unit_rissk_scores:
    #   interview__id, qnr, responsible, qnr_version, unit_risk_score, responsible_score,
    #   IForest s__ cols, responsible s__ cols, any remaining cols.
    lead_cols = ['interview__id', 'qnr', 'responsible', 'qnr_version', 'unit_risk_score', 'responsible_score']
    iforest_s = [c for c in score_columns if c in df_unit_final.columns]
    resp_s_ordered_unit = [c for c in resp_s_cols if c in df_unit_final.columns]
    ordered_unit = [c for c in lead_cols if c in df_unit_final.columns]
    ordered_unit += iforest_s
    ordered_unit += [c for c in resp_s_ordered_unit if c not in ordered_unit]
    ordered_unit += [c for c in df_unit_final.columns if c not in ordered_unit]
    df_unit_final = df_unit_final[ordered_unit]

    # Apply column ordering for responsible_scores: responsible, responsible_score, s__ cols.
    # qnr is intentionally excluded from the responsible_scores output.
    if 'responsible_score' not in df_resp.columns:
        df_resp['responsible_score'] = np.nan
    resp_lead = ['responsible', 'responsible_score']
    resp_s_ordered = [c for c in df_resp.columns if c.startswith('s__')]
    ordered_resp = [c for c in resp_lead if c in df_resp.columns]
    ordered_resp += resp_s_ordered
    ordered_resp += [c for c in df_resp.columns if c not in ordered_resp and c != 'qnr']
    df_resp = df_resp[ordered_resp]

    return df_unit_final, df_resp
