import pandas as pd
import numpy as np
import logging
import warnings
from typing import List, Dict, Any, Tuple
from pyod.models.thresholds import FILTER
from pyod.models.ecod import ECOD
from pyod.models.cof import COF
from pyod.models.inne import INNE
from pyod.models.lof import LOF
from scipy.spatial import cKDTree

from rissk.utils.stats_utils import (
    calculate_entropy,
    calculate_list_entropy,
    filter_variables_by_magnitude,
    apply_benford_tests,
)
from rissk.core.detection_algorithms import lat_lon_to_cartesian

logger = logging.getLogger(__name__)

def rename_feature(feature_name: str, starting_string: str = 'f', new_string: str = 's') -> str:
    """Rename feature correctly mapping to score (f__ -> s__)."""
    starting_string = starting_string + '__'
    new_string = new_string + '__'
    if feature_name.startswith(starting_string):
        return feature_name.replace(starting_string, new_string)
    return feature_name

def get_contamination_parameter(
        config_features: dict, 
        feature_name: str, 
        automatic_contamination: bool = False, 
        method: str = 'medfilt', 
        random_state: int = 42
        ):
    """Fetch contamination parameter from Kedro parameters/config features.
    Returns a FILTER object for automatic contamination detection (matching legacy behaviour),
    or a fixed float when a contamination value is explicitly configured.
    """
    f_name = feature_name.replace('f__', '')
    contamination = config_features.get(f_name, {}).get('parameters', {}).get('contamination')
    if contamination is None or contamination == 'auto' or automatic_contamination is True:
        return FILTER(method=method, random_state=random_state)
    else:
        return float(contamination)

def filter_variable_name_by_frequency(
        df: pd.DataFrame, 
        feature_name: str, 
        frequency: int = 100, 
        min_unique_values: int = 3
        ) -> List[str]:
    """Filter variables by frequency and unique values."""
    if feature_name not in df.columns:
        return []
    # Count non-null frequency and unique values for each variable
    valid_data = df[~pd.isnull(df[feature_name])]
    grouped_df = valid_data.groupby('variable_name')[feature_name].agg(['count', 'nunique'])
    valid_variables = grouped_df[(grouped_df['count'] >= frequency) & (grouped_df['nunique'] >= min_unique_values)].index
    # Return a list of unique variable names that meet the criteria
    return valid_variables.tolist()

def filter_columns(
    data: pd.DataFrame,
    index_col: List[str],
    threshold: int = 100,
    min_unique_values: int = 3,
) -> Tuple[List[str], List[str]]:
    """Determine columns to keep/drop based on threshold and minimum unique values.
    Keeps a column only if both the non-null count is >= `threshold` and the
    number of unique (non-null) values is >= `min_unique_values`.
    """
    # Prepare column set excluding index columns
    data_cols = data.drop(columns=index_col, errors='ignore')

    # Count non-null values for each column
    non_null_counts = data_cols.count()

    # Count unique non-null values for each column
    unique_counts = data_cols.nunique(dropna=True)

    # Keep columns that meet both thresholds
    keep_mask = (non_null_counts >= threshold) & (unique_counts >= min_unique_values)
    keep_columns = non_null_counts[keep_mask].index.tolist()
    drop_columns = non_null_counts[~keep_mask].index.tolist()

    return index_col + keep_columns, drop_columns


# --- SCORING FUNCTIONS BEGIN --- 

def calculate_gps_score(df_item: pd.DataFrame, parameters: Dict[str, Any]) -> pd.DataFrame:
    """Score GPS coordinates for spatial outliers and coordinate proximity.

    Produces three scores on each row:
    - s__gps_proximity_counts: number of other GPS points within 10 m (accounting for accuracy).
    - s__gps_extreme_outlier: 1 if the point is a 0,0 fix or lies beyond p75 + 3.5*IQR of
      the Cartesian distance distribution from the median survey location.
    - s__gps_outlier: 1/0 from COF (< 10 000 points) or LOF (>= 10 000 points) fit on x/y coords.

    Also sets s__gps (integer flag: 1 = GPS question row, NaN = other) so that the unit-level
    aggregation can count GPS questions per interview regardless of outlier model outcome.
    """
    df = df_item.copy()
    score_cols = ['s__gps_proximity_counts', 's__gps_outlier', 's__gps_extreme_outlier']
    required_columns = ['f__gps_latitude', 'f__gps_longitude', 'f__gps_accuracy']
    # variable_name is included so rows from different GPS questions remain distinct
    # when multiple GPS variables exist for the same (interview, roster, responsible)
    index_col = ['interview__id', 'roster_level', 'responsible', 'variable_name']

    # s__gps: integer flag (1 = GPS question, NaN = other). Set unconditionally so that
    # aggregate_item_to_unit_scores can always sum it to the interview-level GPS question
    # count, matching legacy make_score_unit__gps which read f__gps from df_item directly
    # regardless of whether the GPS outlier model ran successfully.
    if 'f__gps' in df.columns:
        df['s__gps'] = np.where(df['f__gps'].fillna(False).astype(bool), 1, np.nan)

    # If required GPS columns are missing, return original df
    if any(col not in df.columns for col in required_columns + ['variable_name']):
        return df

    gps_mask = (~pd.isnull(df['f__gps_latitude'])) & (~pd.isnull(df['f__gps_longitude']))
    if gps_mask.sum() == 0:
        for col in score_cols:
            df[col] = np.nan
        return df

    # Keep each (interview, variable_name) as a separate GPS point so that
    # questionnaires with multiple GPS variables pool all their points together
    # for the outlier model without collapsing coordinates by mean.
    data = df.loc[gps_mask, index_col + required_columns].copy()

    # Everything that has 0,0 as coordinates is considered an extreme outlier
    # (devices sometimes report 0,0 when a fix failed); mark these explicitly
    # so they can be excluded from median/distance calculations.
    data['s__gps_extreme_outlier'] = (
        (data['f__gps_latitude'] == 0.0) & (data['f__gps_longitude'] == 0.0)
    ).astype(int)

    # Convert lat/lon into 3D Cartesian coordinates on a sphere (units = km).
    # Using Cartesian coords lets KDTree operate in Euclidean space instead of
    # running great-circle calculations for every pair.
    data['x'], data['y'], data['z'] = lat_lon_to_cartesian(data['f__gps_latitude'], data['f__gps_longitude'])
    # Accuracy is expected to accompany a GPS fix (Survey Solutions provides it).
    # We convert `f__gps_accuracy` from metres → kilometres to match `lat_lon_to_cartesian`.
    # `fillna(0)` is intentional here: it is a computational guard — a NaN radius would
    # cause `query_ball_point` to raise or silently return empty results. Zero accuracy
    # means we only use the base 10m radius for that point, which is a safe fallback.
    # This is a parameter value, not a score output, so it is not subject to the NaN policy.
    data['accuracy'] = data['f__gps_accuracy'].fillna(0) / 1e3

    # Build spatial index (KDTree) on 3D cartesian coords to count neighbours.
    # Note: KDTree distances are Euclidean in the same units as x/y/z (km).
    tree = cKDTree(data[['x', 'y', 'z']])
    # Radius (search distance) passed to `query_ball_point` — same units as x/y/z (kilometres)
    # Legacy code converted 10 metres into the same units; keep that behaviour.
    radius = 10 / 1e3
    counts = [
        len(tree.query_ball_point(xyz, r=radius + acc)) - 1
        for xyz, acc in zip(data[['x', 'y', 'z']].values, data['accuracy'])
    ]
    data['s__gps_proximity_counts'] = counts

    # Exclude explicitly-marked extreme outliers (e.g., 0,0 fixes) from
    # median/distance computations so they don't skew the central location.
    mask = data['s__gps_extreme_outlier'] < 1
    data['distance_to_median'] = np.nan
    # Default to NaN ("no evaluation possible"); overwritten below only when the spatial
    # model can actually be fit. Guarantees the column exists for the score merge even when
    # fitting is skipped (single-point case) or the else-branch runs.
    data['s__gps_outlier'] = np.nan
    if mask.sum() > 0:
        median_x = data.loc[mask].drop_duplicates(subset='x')['x'].median()
        median_y = data.loc[mask].drop_duplicates(subset='y')['y'].median()
        median_z = data.loc[mask].drop_duplicates(subset='z')['z'].median()

        data.loc[mask, 'distance_to_median'] = np.sqrt(
            (data.loc[mask, 'x'] - median_x) ** 2
            + (data.loc[mask, 'y'] - median_y) ** 2
            + (data.loc[mask, 'z'] - median_z) ** 2
        )

        # Set a threshold for extreme spatial outliers. Legacy code used a
        # percentile + scaled IQR-like range; keep that heuristic here.
        p75 = data.loc[mask, 'distance_to_median'].quantile(0.75)
        median_dist = data.loc[mask, 'distance_to_median'].median()
        range_75 = p75 - median_dist
        threshold = p75 + 3.5 * range_75
        data.loc[mask, 's__gps_extreme_outlier'] = (
            data.loc[mask, 'distance_to_median'] > threshold
        ).astype(int)

        contamination = get_contamination_parameter(
            parameters.get('features', {}),
            'f__gps',
            automatic_contamination=parameters.get('automatic_contamination', False),
            method='medfilt',
            random_state=42,
        )
        # We use only ['x', 'y'] to match legacy 2D behaviour for the COF/LOF
        # model (a planar approximation). For larger geographic extents consider
        # switching to ['x','y','z'] or a geodesic distance measure.
        coords_columns = ['x', 'y']

        # COF/LOF are neighbour-based (need n_neighbors >= 1, i.e. at least 2 points).
        # With a single valid GPS point pyod computes n_neighbors_ = 0 and raises
        # (ValueError: ... not in the range of [1, 1]) — a real crash seen for a
        # questionnaire with only one GPS interview (e.g. srb_roma_wb6_26). Skip fitting
        # in that case and leave s__gps_outlier as NaN, like the all-extreme-outlier branch.
        n_valid = data.loc[mask].shape[0]
        if n_valid >= 2:
            # USE COF if dataset has less than 10000 samples else use LOF
            if n_valid < 10000:
                model = COF(contamination=contamination)
            else:
                model = LOF(contamination=contamination, n_neighbors=20)
            model.fit(data.loc[mask, coords_columns])
            data.loc[mask, 's__gps_outlier'] = model.predict(data.loc[mask, coords_columns])
        # Extreme outlier rows excluded from model fitting keep NaN for s__gps_outlier —
        # they are already classified as extreme outliers and the spatial model cannot
        # evaluate them; NaN signals that no evaluation was possible for those points.
    else:
        # All GPS points are extreme outliers (e.g. all 0,0). The spatial outlier model
        # cannot run because there are no valid points to fit. s__gps_outlier = NaN
        # for all, since no evaluation was possible.
        data['s__gps_outlier'] = np.nan

    # Merge interview-level scores back to every row in the full long-format df.
    # Rows for interviews that had no GPS answers are left as NaN — they are not
    # scored, matching legacy behaviour where those interviews simply had no entry
    # in the returned pivot output.
    score_data = data[index_col + score_cols]
    df = df.merge(score_data, on=index_col, how='left')

    return df


def calculate_sequence_jump_score(df_item: pd.DataFrame, parameters: Dict[str, Any]) -> pd.DataFrame:
    """Score sequence-jump anomalies per variable using the INNE isolation-based model.

    Only variables with at least 100 records and 3 distinct jump values are scored.
    Rows for variables that don't meet the threshold keep s__sequence_jump = NaN.
    """
    feature_name = 'f__sequence_jump'
    score_name = rename_feature(feature_name)
    df = df_item.copy()

    if feature_name not in df.columns:
        return df
    
    if df[feature_name].dropna().empty:
        df[score_name] = np.nan
        return df

    valid_data = df[~pd.isnull(df[feature_name])].copy()
    valid_variables = filter_variable_name_by_frequency(valid_data, feature_name, frequency=100, min_unique_values=3)
    df[score_name] = np.nan
    contamination = get_contamination_parameter(
        parameters.get('features', {}),
        feature_name,
        automatic_contamination=parameters.get('automatic_contamination', False),
    )

    for var in valid_variables:
        mask = (df['variable_name'] == var) & (~pd.isnull(df[feature_name]))
        if mask.sum() > 0:
            model = INNE(contamination=contamination, random_state=42)
            try:
                model.fit(df.loc[mask, [feature_name]])
                df.loc[mask, score_name] = model.predict(df.loc[mask, [feature_name]])
            except (ValueError, FloatingPointError):
                df.loc[mask, score_name] = 0

    return df


def calculate_first_decimals_score(df_item: pd.DataFrame, parameters: Dict[str, Any]) -> pd.DataFrame:
    """Score anomalies in the first two decimal digits per variable using the COF density model.

    f__first_decimals stores the first TWO decimal digits of each numeric response (e.g. 3.47 → 47).
    """
    # s__first_decimals is the anomaly score derived from f__first_decimals (two decimal digits).
    feature_name = 'f__first_decimals'
    score_name = rename_feature(feature_name)
    df = df_item.copy()

    if feature_name not in df.columns:
        return df
    if df[feature_name].dropna().empty:
        df[score_name] = np.nan
        return df
        
    valid_data = df[~pd.isnull(df[feature_name])].copy()
    # Select only those variables that have at least three distinct values and more than one hundred records
    valid_variables = filter_variable_name_by_frequency(valid_data, feature_name, frequency=100, min_unique_values=3)
    df[score_name] = np.nan
    contamination = get_contamination_parameter(
        parameters.get('features', {}),
        feature_name,
        automatic_contamination=parameters.get('automatic_contamination', False),
        method='medfilt',
        random_state=42,
    )
    
    for var in valid_variables:
        # logger.info(f"Calculating {score_name} for variable: {var}")
        mask = (df['variable_name'] == var) & (~pd.isnull(df[feature_name]))
        if mask.sum() > 0:
            model = COF(contamination=contamination)
            # COF on f__first_decimals produces several expected RuntimeWarnings due to
            # degenerate neighbourhoods (many identical values, e.g. x.00):
            #   - pyod.models.cof: "divide by zero / invalid value encountered in scalar divide"
            #     — zero chaining distance causes a 0/0 in the COF score formula.
            #   - numpy._core._methods: "overflow encountered in multiply/reduce"
            #     — intermediate squared-distance arithmetic overflows before being clipped.
            # COF handles these cases gracefully (producing NaN/inf scores that it then
            # clips or ignores). They are intentional side-effects of applying a
            # distance-based algorithm to heavily-tied data, not coding errors.
            # We suppress all RuntimeWarnings from both modules for the duration of
            # fit/predict to keep the log clean.
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    'ignore',
                    category=RuntimeWarning,
                    module=r'pyod\.models\.cof',
                )
                warnings.filterwarnings(
                    'ignore',
                    category=RuntimeWarning,
                    module=r'numpy\._core\._methods',
                )
                try:
                    model.fit(df.loc[mask, [feature_name]])
                    df.loc[mask, score_name] = model.predict(df.loc[mask, [feature_name]])
                except (ValueError, FloatingPointError):
                    df.loc[mask, score_name] = 0
            
    return df


def calculate_answer_hour_set_score(df_item: pd.DataFrame, parameters: Dict[str, Any]) -> pd.DataFrame:
    """Score hour-of-day anomalies using ECOD on f__answer_hour_set.

    ECOD is a parameter-free outlier detection algorithm based on empirical CDF functions.
    Hours that ECOD flags but which are the most frequent in the dataset are reverted to 0
    (non-anomalous) because high-frequency hours cannot represent interviewer-level anomalies.
    """
    # Detect time set anomalies using ECOD algorithm.
    # ECOD is a parameter-free, highly interpretable outlier detection algorithm based on empirical CDF functions
    feature_name = 'f__answer_hour_set'
    score_name = rename_feature(feature_name)
    df_out = df_item.copy()

    if feature_name not in df_out.columns:
        return df_out

    df_out[score_name] = np.nan
    df_out[feature_name] = pd.to_numeric(df_out[feature_name], errors='coerce')

    mask = ~pd.isnull(df_out[feature_name])
    df = df_out[mask].copy()

    if df.empty:
        return df_out
    # Sorting the DataFrame based on the 'frequency' answer_hour_set in descending order
    sorted_hours = df[feature_name].value_counts().index
    hour_to_rank = {hour: rank for rank, hour in enumerate(sorted_hours)}
        # Create a frequency column
    df['frequency'] = df[feature_name].map(hour_to_rank)

    # IDENTIFY Outliers by ECOD anomaly detection model
    contamination = get_contamination_parameter(
        parameters.get('features', {}),
        feature_name,
        automatic_contamination=parameters.get('automatic_contamination', False),
    )

    model = ECOD(contamination=contamination)
    try:
        model.fit(df[[feature_name]])
        df[score_name] = model.predict(df[[feature_name]])
    except (ValueError, FloatingPointError):
        df[score_name] = 0

    # Revert high-frequency hours that ECOD incorrectly flagged as anomalies.
    # Guard against the degenerate case where every row is an outlier (no inliers),
    # which would make the unguarded expression return NaN and silently skip the
    # correction. Legacy code has this silent failure; Kedro uses the explicit guard.
    inlier_mask = df[score_name] == 0
    if inlier_mask.any():
        min_inlier_rank = df.loc[inlier_mask, 'frequency'].min()
        df.loc[df['frequency'] <= min_inlier_rank, score_name] = 0
    # If no inliers exist (all rows flagged), scores remain as predicted — NaN was
    # never introduced here since ECOD always returns 0/1, so no further action needed.

    # Assign scores back using index labels — safe regardless of index type or value
    df_out.loc[df.index, score_name] = df[score_name].values
    return df_out


def calculate_answer_changed_score(df_item: pd.DataFrame, parameters: Dict[str, Any]) -> pd.DataFrame:
    """Score answer-change anomalies per variable using ECOD.

    Only variables with at least 100 records and at least 1 distinct change value are scored.
    """
    feature_name = 'f__answer_changed'
    score_name = rename_feature(feature_name)
    df = df_item.copy()

    if feature_name not in df.columns:
        return df
    if df[feature_name].dropna().empty:
        df[score_name] = np.nan
        return df

    valid_data = df[~pd.isnull(df[feature_name])]
    # Select only those variables that have at least 1 distinct values and more than one hundred records
    valid_variables = filter_variable_name_by_frequency(valid_data, feature_name, frequency=100, min_unique_values=1)
    df[score_name] = np.nan
    contamination = get_contamination_parameter(
        parameters.get('features', {}),
        feature_name,
        automatic_contamination=parameters.get('automatic_contamination', False),
        method='medfilt',
        random_state=42,
    )
    
    for var in valid_variables:
        mask = (df['variable_name'] == var) & (~pd.isnull(df[feature_name]))
        if mask.sum() > 0:
            model = ECOD(contamination=contamination)
            try:
                model.fit(df.loc[mask, [feature_name]])
                df.loc[mask, score_name] = model.predict(df.loc[mask, [feature_name]])
            except (ValueError, FloatingPointError):
                # Zero-variance data causes pythresh FILTER to produce NaN sigma
                # which crashes on round(NaN). Treat constant data as all inliers.
                df.loc[mask, score_name] = 0
            
    return df

# NOTE: s__answer_removed is NOT computed at item level.
#
# Legacy `make_score__answer_removed` operated on a frame derived directly from
# df_paradata (via get_feature_item__answer_removed), which includes AnswerRemoved
# events for items that were subsequently deleted from microdata and are therefore
# absent from df_item. In the Kedro pipeline, df_item is built from microdata and
# f__answer_removed is merged back with how='left' — so those deleted items are
# silently dropped, making an item-level s__answer_removed on df_item structurally
# incomplete and potentially misleading.
   

def calculate_answer_removed_score_from_df(
    removed_answers: pd.DataFrame,
    parameters: Dict[str, Any],
) -> pd.Series:
    """Score answer-removal anomalies from the pre-aggregated removed_answers DataFrame.

    Takes the output of feat_answer_removed (columns: interview__id, responsible,
    variable_name, qnr_seq, f__answer_removed) and applies the same ECOD scoring
    logic as calculate_answer_removed_unit_score, without re-filtering paradata.

    Returns a Series indexed by interview__id → mean s__answer_removed score.
    """
    feature_name = 'f__answer_removed'
    score_name = rename_feature(feature_name)

    if removed_answers is None or removed_answers.empty:
        return pd.Series(dtype=float)

    required_cols = ['interview__id', 'variable_name', feature_name]
    if any(c not in removed_answers.columns for c in required_cols):
        logger.warning(
            "calculate_answer_removed_score_from_df: removed_answers is missing one or more "
            "required columns %s; returning empty Series.", required_cols
        )
        return pd.Series(dtype=float)

    df = removed_answers.copy()

    valid_variables = filter_variable_name_by_frequency(df, feature_name, frequency=100, min_unique_values=1)

    df[score_name] = np.nan
    contamination = get_contamination_parameter(
        parameters.get('features', {}),
        feature_name,
        automatic_contamination=parameters.get('automatic_contamination', False),
        method='medfilt',
        random_state=42,
    )

    for var in valid_variables:
        mask = (df['variable_name'] == var) & (~pd.isnull(df[feature_name]))
        if mask.sum() > 0:
            model = ECOD(contamination=contamination)
            try:
                model.fit(df.loc[mask, [feature_name]])
                df.loc[mask, score_name] = model.predict(df.loc[mask, [feature_name]])
            except (ValueError, FloatingPointError):
                df.loc[mask, score_name] = 0

    return df.groupby('interview__id')[score_name].mean()


def calculate_answer_position_score(df_item: pd.DataFrame, parameters: Dict[str, Any]) -> pd.DataFrame:
    """Score answer-position entropy per responsible per variable.

    Flags enumerators whose selected-answer-position distribution differs by more than 50%
    from the median entropy across all enumerators for each variable.
    Only variables with at least 100 records and 3 distinct position values are scored.
    """
    feature_name = 'f__answer_position'
    score_name = rename_feature(feature_name)
    df = df_item.copy()

    if feature_name not in df.columns:
        return df
    if df[feature_name].dropna().empty:
        df[score_name] = np.nan
        return df

    # Score is computed at the responsible level: entropy of answer-position distributions
    # measures whether an enumerator systematically favours certain positions (e.g. always
    # first or always last option). This bias is only detectable across many interviews for
    # the same enumerator, not within a single interview.
    valid_variables = filter_variable_name_by_frequency(
        df[~pd.isnull(df[feature_name])], feature_name, frequency=100, min_unique_values=3)
    df[score_name] = np.nan
    
    for var in valid_variables:
        mask = (df['variable_name'] == var) & (~pd.isnull(df[feature_name]))
        if mask.sum() > 0:
            unique_values = df[mask][feature_name].nunique()
            entropy_df = df[mask].groupby('responsible')[feature_name].apply(
                calculate_entropy, unique_values=unique_values, min_record_sample=10
            ).reset_index()
            entropy_df = entropy_df[~pd.isnull(entropy_df[feature_name])]

            if entropy_df.shape[0] > 0:
                entropy_df.sort_values(feature_name, inplace=True, ascending=False)
                median_value = entropy_df[feature_name].median()
                entropy_df[score_name] = entropy_df[feature_name].apply(
                    lambda x: 1 if x < median_value - 0.5 * median_value else 0)
                
                # Apply map safely
                responsible_map = entropy_df.set_index('responsible')[score_name].to_dict()
                df.loc[mask, score_name] = df.loc[mask, 'responsible'].map(responsible_map)
    return df

def calculate_answer_selected_score(df_item: pd.DataFrame, parameters: Dict[str, Any]) -> pd.DataFrame:
    """Score the share of selected options in multi-option questions using ECOD.

    Splits the output into s__answer_selected_lower (too few options selected) and
    s__answer_selected_upper (too many selected) based on the inlier range for each variable.
    Only variables with at least 100 records and 3 distinct share values are scored.
    """
    feature_name = 'f__answer_selected'
    score_name = rename_feature(feature_name)
    df = df_item.copy()

    if feature_name not in df.columns:
        return df
    if df[feature_name].dropna().empty:
        df[score_name + '_lower'] = np.nan
        df[score_name + '_upper'] = np.nan
        return df

    valid_variables = filter_variable_name_by_frequency(
        df[~pd.isnull(df[feature_name])], feature_name, frequency=100, min_unique_values=3)
    
    score_name1 = score_name + '_lower'
    score_name2 = score_name + '_upper'
    df[score_name1] = np.nan
    df[score_name2] = np.nan
    df[score_name] = np.nan


    contamination = get_contamination_parameter(
        parameters.get('features', {}),
        feature_name,
        automatic_contamination=parameters.get('automatic_contamination', False),
        method='medfilt',
        random_state=42,
    )

    for var in valid_variables:
        mask = (df['variable_name'] == var) & (~pd.isnull(df[feature_name]))
        if mask.sum() > 0:
            model = ECOD(contamination=contamination)
            try:
                model.fit(df.loc[mask, [feature_name]])
                df.loc[mask, score_name] = model.predict(df.loc[mask, [feature_name]])
            except (ValueError, FloatingPointError):
                df.loc[mask, score_name] = 0
                continue
            non_anomalies = df.loc[mask & (df[score_name] == 0), feature_name]
            
            if not non_anomalies.empty:
                min_good_value = non_anomalies.min()
                max_good_value = non_anomalies.max()
                
                df.loc[mask, score_name1] = 0
                df.loc[mask, score_name2] = 0
                
                df.loc[mask & (df[feature_name] < min_good_value), score_name1] = 1
                df.loc[mask & (df[feature_name] > max_good_value), score_name2] = 1

    df.drop(columns=[score_name], errors='ignore', inplace=True)
    return df


def calculate_answer_duration_score(df_item: pd.DataFrame, parameters: Dict[str, Any]) -> pd.DataFrame:
    """Score answer-duration anomalies per variable using ECOD.

    Splits output into s__answer_duration_lower (unusually fast) and s__answer_duration_upper
    (unusually slow) relative to the inlier range for each variable.
    Only variables with at least 100 records and 3 distinct duration values are scored.
    """
    feature_name = 'f__answer_duration'
    score_name = rename_feature(feature_name)
    df = df_item.copy()

    if feature_name not in df.columns:
        return df
    if df[feature_name].dropna().empty:
        df[score_name + '_lower'] = np.nan
        df[score_name + '_upper'] = np.nan
        return df
    
    # Select only those variables that have at least three distinct values and more than one hundred records
    valid_variables = filter_variable_name_by_frequency(
        df[~pd.isnull(df[feature_name])], feature_name, frequency=100, min_unique_values=3)

    score_name1 = score_name + '_lower'
    score_name2 = score_name + '_upper'
    df[score_name1] = np.nan
    df[score_name2] = np.nan
    df[score_name] = np.nan
    
    contamination = get_contamination_parameter(
        parameters.get('features', {}),
        feature_name,
        automatic_contamination=parameters.get('automatic_contamination', False),
        method='medfilt',
        random_state=42,
    )

    for var in valid_variables:
        mask = (df['variable_name'] == var) & (~pd.isnull(df[feature_name]))
        if mask.sum() > 0:
            model = ECOD(contamination=contamination)
            try:
                model.fit(df.loc[mask, [feature_name]])
                df.loc[mask, score_name] = model.predict(df.loc[mask, [feature_name]])
            except (ValueError, FloatingPointError):
                df.loc[mask, score_name] = 0
                continue

            non_anomalies = df.loc[mask & (df[score_name] == 0), feature_name]
            if not non_anomalies.empty:
                min_good_value = non_anomalies.min()
                max_good_value = non_anomalies.max()
                
                df.loc[mask, score_name1] = 0
                df.loc[mask, score_name2] = 0
                
                df.loc[mask & (df[feature_name] < min_good_value), score_name1] = 1
                df.loc[mask & (df[feature_name] > max_good_value), score_name2] = 1

    df.drop(columns=[score_name], errors='ignore', inplace=True)
    return df

def calculate_single_question_score(df_item: pd.DataFrame) -> pd.DataFrame:
    """Score single-answer question entropy per responsible per variable.

    Flags enumerators whose selected-answer distribution for a variable differs by more than 50%
    from the median entropy across all enumerators, indicating a potential acquiescence bias.
    Excludes filtered comboboxes and cascade questions. Only variables with at least 100
    records and 3 distinct answer values are scored.
    """
    feature_name = 'f__single_question'
    score_name = rename_feature(feature_name)
    df = df_item.copy()
    columns = ['qtype', 'n_answers', 'is_filtered_combobox', 'cascade_from_question_id']

    # f__single_question is not a separately computed feature column — scoring works
    # directly on 'value' with a qtype mask, matching legacy make_score__single_question.
    if any(col not in df.columns for col in columns):
        return df

    # Score is computed at the responsible level: entropy of the selected-answer distribution
    # reveals whether an enumerator consistently picks the same option across interviews.
    # This acquiescence bias only emerges when comparing many interviews per enumerator.

    # Mask specific for single questions without filter rules bypassing cascades
    single_question_mask = (
        (df["qtype"] == 'SingleQuestion') & 
        (df['n_answers'] > 1) & 
        (df['is_filtered_combobox'] == False) & 
        (pd.isnull(df['cascade_from_question_id']))
    )

    df[score_name] = np.nan
    valid_data = df[single_question_mask].copy()
    if valid_data.empty:
        return df

    variables = filter_variable_name_by_frequency(valid_data, 'value', frequency=100, min_unique_values=3)
    
    for var in variables:
        mask = (df['variable_name'] == var) & single_question_mask
        if mask.sum() > 0:
            unique_values = df.loc[mask, 'value'].nunique()

            entropy_df = df[mask].groupby('responsible')['value'].apply(
                calculate_entropy, unique_values=unique_values
            ).reset_index()
            entropy_df = entropy_df[~pd.isnull(entropy_df['value'])]

            if entropy_df.shape[0] > 0:
                entropy_df.sort_values('value', inplace=True, ascending=False)
                median_value = entropy_df['value'].median()
                entropy_df[score_name] = entropy_df['value'].apply(
                    lambda x: 1 if x < median_value - 0.5 * median_value else 0)
                
                responsible_map = entropy_df.set_index('responsible')[score_name].to_dict()
                df.loc[mask, score_name] = df.loc[mask, 'responsible'].map(responsible_map)
                
    return df


def calculate_multi_option_question_score(df_item: pd.DataFrame) -> pd.DataFrame:
    """Score multi-option question entropy per responsible per variable.

    Flags enumerators whose combination of selected answers for a variable differs by more than
    50% from the median list-entropy across all enumerators, indicating systematic subset selection.
    Only variables with at least 100 records and 3 distinct answer combinations are scored.
    """
    feature_name = 'f__multi_option_question'
    score_name = rename_feature(feature_name)
    df = df_item.copy()

    # f__multi_option_question is not a separately computed feature column — scoring works
    # directly on 'value' with a qtype mask, matching legacy make_score__multi_option_question.
    if 'qtype' not in df.columns:
        return df

    # Score is computed at the responsible level: entropy of the combination of options
    # selected across interviews exposes enumerators who systematically pick the same
    # subset of answers for every respondent.

    multi_question_mask = (df["qtype"] == 'MultyOptionsQuestion')
    valid_data = df[multi_question_mask].copy()

    df[score_name] = np.nan
    if valid_data.empty:
        return df
    
    variables = filter_variable_name_by_frequency(valid_data, 'value', frequency=100, min_unique_values=3)

    for var in variables:
        mask = (df['variable_name'] == var) & multi_question_mask
        if mask.sum() > 0:
            # Need safely explode nested lists in values
            exploded_vals = df.loc[mask, 'value'].explode()
            unique_values = len([v for v in exploded_vals.unique() if v != '##N/A##'])

            entropy_df = df[mask].groupby('responsible')['value'].apply(
                calculate_list_entropy, unique_values=unique_values, min_record_sample=5
            ).reset_index()
   

            entropy_df = entropy_df[~pd.isnull(entropy_df['value'])]

            if entropy_df.shape[0] > 0:
                entropy_df.sort_values('value', inplace=True, ascending=False)
                median_value = entropy_df['value'].median()
                entropy_df[score_name] = entropy_df['value'].apply(
                    lambda x: 1 if x < median_value - 0.5 * median_value else 0)
                
                responsible_map = entropy_df.set_index('responsible')[score_name].to_dict()
                df.loc[mask, score_name] = df.loc[mask, 'responsible'].map(responsible_map)

    return df

def calculate_first_digit_score(df_item: pd.DataFrame) -> pd.DataFrame:
    """Score first-digit Benford's Law deviations per responsible per variable.

    Computes the Jensen divergence between an enumerator's first-digit distribution and
    that of all other enumerators. Only numeric variables spanning at least 3 orders of
    magnitude and enumerators with at least 50 records per variable are evaluated.
    Enumerators whose divergence exceeds the median by more than 50% are flagged.
    """
    feature_name = 'f__numeric_response'
    first_digit_feature = 'f__first_digit'
    score_name = 's__first_digit'
    df = df_item.copy()

    if feature_name not in df.columns or first_digit_feature not in df.columns:
        return df
    
    valid_data = df[
        ~pd.isnull(df[feature_name]) & (
        ~pd.isnull(df[first_digit_feature])) & (
        df[first_digit_feature] != 0)
        ].copy()

    if valid_data.empty:
        df[score_name] = np.nan
        return df
    # we need both f__numeric_response and f__first_digit to apply Benford tests, 
    # so filter to rows where both are present 
    df[score_name] = np.nan

    # f__first_digit is already computed by the feature pipeline
    valid_variables = filter_variable_name_by_frequency(
        valid_data, first_digit_feature, frequency=100, min_unique_values=3
    )

    # Additionally, Benford's Law is most applicable to variables that span several orders of magnitude,
    valid_variables = filter_variables_by_magnitude(valid_data, feature_name, valid_variables, min_order_of_magnitude=3)

    # Computes the Jensen divergence for each variable_name and responsible on the first digit distribution.
    # Jensen's divergence returns a value between (0, 1) of how much the first digit distribution
    # of specific responsible is similar to the first digit distribution of all others.
    # Higher the value higher is the difference.
    # The Bendford Jensen divergence is calculated only on those responsible and variable_name
    # who have at least 50 records.
    # Once it is calculated, values that diverge from more than 50% from the median value get marked as "anomalous."

    benford_jensen_df = apply_benford_tests(
        valid_data, valid_variables, 'responsible', feature_name, apply_first_digit=True, minimum_sample=50
    )
        
    if not benford_jensen_df.empty:
        variable_list = benford_jensen_df['variable_name'].unique()
        for var in variable_list:
            bj_mask = (benford_jensen_df['variable_name'] == var) & (~pd.isnull(benford_jensen_df[feature_name]))
            bj_df = benford_jensen_df[bj_mask].copy()
            if bj_df.shape[0] > 0:
                bj_df.sort_values(feature_name, inplace=True, ascending=True)
                median_value = bj_df[feature_name].median()
                bj_df[score_name] = bj_df[feature_name].apply(
                    lambda x: 1 if x > median_value + 0.5 * median_value else 0)
                
                mask = (df['variable_name'] == var)
                responsible_map = bj_df.set_index('responsible')[score_name].to_dict()
                # Responsibles absent from the map (below 50-record Benford threshold)
                # keep NaN — evaluation was not possible for them.
                df.loc[mask, score_name] = df.loc[mask, 'responsible'].map(responsible_map)
                
    return df


# def calculate_last_digit_score(df_item: pd.DataFrame) -> pd.DataFrame:
#     """Score last-digit anomalies per responsible per variable (NOT YET PORTED).
#
#     Legacy `make_score__last_digit` in ItemFeatureProcessing applied Benford-style
#     p-value tests on the last-digit distribution of numeric responses using a
#     pivot-table approach.  The item-level scoring was already commented out in the
#     legacy codebase (see make_score__last_digit in item_processing.py) and the
#     corresponding unit aggregation make_score_unit__last_digit was a no-op stub
#     (pass).  Kept commented until the approach is redesigned for the long-format
#     Kedro pipeline.
#     """
#     feature_name = 'f__numeric_response'
#     score_name = 's__last_digit'
#     df = df_item.copy()
#
#     if feature_name not in df.columns:
#         return df
#
#     valid_data = df[~pd.isnull(df[feature_name])].copy()
#     # Filter by frequency and order of magnitude, matching legacy pivot-table filtering
#     valid_variables = filter_variable_name_by_frequency(
#         valid_data, feature_name, frequency=100, min_unique_values=3
#     )
#     valid_variables = filter_variables_by_magnitude(
#         valid_data, feature_name, valid_variables, min_order_of_magnitude=3
#     )
#
#     df[score_name] = np.nan
#     for var in valid_variables:
#         mask = (df['variable_name'] == var) & (~pd.isnull(df[feature_name]))
#         if mask.sum() == 0:
#             continue
#         # Legacy used apply_benford_tests with p-value threshold of 0.05 to flag
#         # enumerators whose last-digit distribution deviates significantly from uniform.
#         results_df = apply_benford_tests(
#             df[mask], [var], 'responsible', feature_name,
#             apply_first_digit=False, minimum_sample=50,
#         )
#         if results_df is not None and not results_df.empty and 'p-value' in results_df.columns:
#             score_col = var + '_last_digit'
#             results_df[score_col] = results_df['p-value'].apply(lambda x: 1 if x <= 0.05 else 0)
#             responsible_map = results_df.set_index('responsible')[score_col].to_dict()
#             df.loc[mask, score_name] = df.loc[mask, 'responsible'].map(responsible_map)
#
#     return df

