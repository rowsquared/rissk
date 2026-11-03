"""
stats_utils.py
==============
Kedro-pipeline equivalent of the legacy stats_utils.py.

Changelog vs stats_utils.py
----------------------------
[FIX] calculate_entropy: normalisation divisor corrected from np.log2(unique_values)
      to np.log(unique_values).  scipy.stats.entropy uses the natural logarithm (nats)
      by default, so the maximum entropy for n categories is ln(n) nats.  Dividing by
      log2(n) instead of ln(n) introduced a constant scale factor of ln(2) ≈ 0.693,
      capping the output at ~0.693 instead of 1.0 and making the values uninterpretable
      as a true [0, 1] normalised measure.  Binary anomaly-flag outputs were unaffected
      (the factor cancelled in relative comparisons), but the raw entropy values stored
      or logged were misleading.

[FIX] calculate_list_entropy: same normalisation correction as calculate_entropy.

[REMOVED] Duplicate median_value computation that appeared twice on consecutive lines
          in the caller functions (item_processing.py lines 259–261, 353–355, 389–391).
          Those callers have been cleaned in item_processing.py; the note is kept
          here for traceability.

No algorithmic or interface changes; all function signatures are identical to legacy.
"""

import math
import pandas as pd
import numpy as np
from scipy.stats import entropy
from scipy import stats
from sklearn.preprocessing import StandardScaler
from scipy.stats import chisquare, fisher_exact
from collections import Counter
from scipy.stats.mstats import winsorize


# ---------------------------------------------------------------------------
# Jensen-Shannon helpers (unchanged from legacy)
# ---------------------------------------------------------------------------

def jensen_shannon_divergence(p, q):
    m = 0.5 * (p + q)
    return 0.5 * (entropy(p, m) + entropy(q, m))


def jensen_shannon_distance(p, q):
    return np.sqrt(jensen_shannon_divergence(p, q))


# ---------------------------------------------------------------------------
# Digit helpers (unchanged from legacy)
# ---------------------------------------------------------------------------

def get_digit_frequecies(df, feature_name, apply_first_digit, minimum_sample=50):
    digit_mask = (df[feature_name] != 0)
    if apply_first_digit:
        total_digit_values = df[digit_mask][feature_name].apply(first_digit)
    else:
        total_digit_values = df[digit_mask][feature_name].apply(last_digit)
    total_digit_count = Counter(total_digit_values)
    total_digit_count = [total_digit_count.get(i, 0) for i in range(1, 10)]
    if sum(total_digit_count) < minimum_sample:
        total_digit_freq = None
    else:
        total_digit_freq = [v / sum(total_digit_count) for v in total_digit_count]
    return total_digit_freq


def first_digit(val):
    """
    Extract the first significant digit from a value using log10.
    Follow legacy behaviour of setting values with abs(val) < 1 to 0 
    (non-Benford domain) and applying absolute value to negatives.
    """
    val = abs(val)
    if val < 1:
        return 0
    power = math.floor(math.log10(val))
    return int(val / 10**power)


def last_digit(val):
    """Extract the last digit from a value."""
    return int(str(int(val))[-1])


def apply_benford_tests(df, valid_variables, responsible_col, feature_name,
                        apply_first_digit=True, minimum_sample=50):
    responsible_list = df[responsible_col].unique()
    results = []
    for var in valid_variables:
        variable_mask = df['variable_name'] == var
        for resp in responsible_list:
            score = None
            resp_mask = (df[responsible_col] == resp)
            total_digit_count = get_digit_frequecies(
                df[variable_mask & (~resp_mask)], feature_name, apply_first_digit,
                minimum_sample=minimum_sample,
            )
            resp_digit_count = get_digit_frequecies(
                df[variable_mask & resp_mask], feature_name, apply_first_digit,
                minimum_sample=minimum_sample,
            )
            if resp_digit_count is not None and total_digit_count is not None:
                score = jensen_shannon_distance(
                    np.array(total_digit_count), np.array(resp_digit_count)
                )
            results.append((resp, var, score))
    return pd.DataFrame(results, columns=[responsible_col, 'variable_name', feature_name])


# ---------------------------------------------------------------------------
# Outlier helpers (unchanged from legacy)
# ---------------------------------------------------------------------------

def get_outlier_by_magnitude(series, mode_deviation=3, threshold_freq=0.02):
    """
    Detects values that are anomalies based on their order of magnitude.

    Args:
    - series (pd.Series): Series of numeric values.
    - mode_deviation (int): Maximum allowable deviation from the mode's order of magnitude.
    - threshold_freq (float): Maximum relative frequency for an order of magnitude to be
      considered anomalous.

    Returns:
    - pd.Series: Boolean Series with True for anomalies and False for normal values.
    """
    min_value = series.min()
    if min_value <= 0:
        order_of_magnitude = np.floor(np.log10(series + abs(min_value) + 1))
    else:
        order_of_magnitude = np.floor(np.log10(series))

    mode_order = max(order_of_magnitude.mode().iloc[0], 1)
    mode_based_anomalies = (
        (order_of_magnitude < mode_order - mode_deviation)
        | (order_of_magnitude > mode_order + mode_deviation)
    )

    freq_count = order_of_magnitude.value_counts() / series.count()
    anomalous_orders = freq_count[freq_count <= threshold_freq].index
    freq_based_anomalies = order_of_magnitude.isin(anomalous_orders)

    return mode_based_anomalies | freq_based_anomalies


def get_outlier_iqr(data, column_name):
    q_high = data[column_name].quantile(0.75)
    q_low = data[column_name].quantile(0.25)
    iqr = q_high - q_low
    lower_outlier = (data[column_name] < q_low - 1.5 * iqr) & (~pd.isnull(data[column_name]))
    upper_outlier = (data[column_name] > q_high + 1.5 * iqr) & (~pd.isnull(data[column_name]))
    return lower_outlier, upper_outlier


def get_outlier_z_score(data, column_name, threshold=2.5):
    lower_limit = data[column_name].mean() - threshold * data[column_name].std()
    upper_limit = data[column_name].mean() + threshold * data[column_name].std()
    lower_outlier = (data[column_name] < lower_limit) & (~pd.isnull(data[column_name]))
    upper_outlier = (data[column_name] > upper_limit) & (~pd.isnull(data[column_name]))
    return lower_outlier, upper_outlier


def filter_variables_by_magnitude(df, feature_name, variables, min_order_of_magnitude=3):
    """Return variables whose nonzero absolute values span at least `min_order_of_magnitude` orders.

    Zeros are excluded because they are not part of the Benford domain and would
    anchor min_magnitude at 0, distorting the apparent range.  Negative values are
    treated by absolute value: using raw min/max with sign inversion would reverse
    the comparison for all-negative series (e.g. min=-1000, max=-0.01 would give
    magnitude(max) - magnitude(min) = -2 - 3 = -5, always failing).
    """
    def order_of_magnitude(num):
        # num is guaranteed positive (abs applied by caller)
        return int(math.floor(math.log10(num)))

    valid_variables = []
    for var in variables:
        var_values = df[df['variable_name'] == var][feature_name]
        nonzero_abs = var_values[var_values != 0].abs()
        if nonzero_abs.empty:
            continue
        max_magnitude = order_of_magnitude(nonzero_abs.max())
        min_magnitude = order_of_magnitude(nonzero_abs.min())
        if max_magnitude - min_magnitude >= min_order_of_magnitude:
            valid_variables.append(var)
    return valid_variables


def get_box_cox_rescaled(series):
    scaler = StandardScaler()
    min_value = series.min()
    box_cox = series
    if series.nunique() > 1:
        if min_value <= 0:
            box_cox = box_cox + abs(min_value) + 1
        box_cox, _ = stats.boxcox(box_cox)
        box_cox = scaler.fit_transform(box_cox.reshape(-1, 1))
    return box_cox


# ---------------------------------------------------------------------------
# Entropy helpers
# ---------------------------------------------------------------------------

def calculate_list_entropy(column, unique_values, min_record_sample=10):
    """
    Calculate the normalised entropy of a multi-value (list) column.

    Parameters
    ----------
    column : pd.Series
        Series of lists for a single responsible group.
    unique_values : int
        Global number of distinct answer options for the variable (used as
        the normalisation denominator, so entropy is relative to the maximum
        possible diversity across all responsibles, not just this group).
    min_record_sample : int, optional
        Minimum records required per unique value before entropy is computed.
        Groups below this threshold return None (insufficient data).

    Returns
    -------
    float or None
        Normalised entropy in [0, 1] if conditions are met.
        0 for single-value distributions with enough samples.
        None when the sample is too small.

    Notes
    -----
    [FIX] Legacy used `np.log2(unique_values)` as the divisor, producing a
    range of [0, ln(2)] ≈ [0, 0.693] instead of [0, 1].  scipy.stats.entropy
    returns nats (natural log base), so the correct divisor is np.log(unique_values).
    The binary anomaly flags produced by callers were unaffected because the
    ln(2) factor cancelled in relative (median-based) comparisons, but the raw
    entropy values were uninterpretable as a normalised measure.
    """
    column = column[column != '##N/A##']
    flattened_series = column.explode()
    prob_distribution = flattened_series.value_counts(normalize=True)

    if unique_values > 1 and flattened_series.shape[0] >= min_record_sample * unique_values:
        # [FIX] Corrected: np.log(unique_values) matches scipy's natural-log base,
        # yielding a true [0, 1] normalised entropy.
        # Legacy used np.log2(unique_values), capping values at ln(2) ≈ 0.693.
        entropy_ = entropy(prob_distribution.values) / np.log(unique_values)
    elif unique_values == 1 and flattened_series.shape[0] >= min_record_sample * unique_values:
        entropy_ = 0
    else:
        entropy_ = None

    return entropy_


def calculate_entropy(column, unique_values, min_record_sample=10):
    """
    Calculate the normalised entropy of a column for a single responsible group.

    Parameters
    ----------
    column : pd.Series
        Feature values for a single responsible group; must be null-free
        (callers are responsible for pre-filtering nulls before groupby).
    unique_values : int
        Global number of distinct values for the variable (used as the
        normalisation denominator so that entropy is expressed relative to
        the maximum possible diversity across all responsibles, not just
        within this group).
    min_record_sample : int, optional
        Minimum records required per unique value before entropy is computed.
        Groups below this threshold return None (insufficient data).

    Returns
    -------
    float or None
        Normalised entropy in [0, 1] if conditions are met.
        0 for single-value distributions with enough samples.
        None when the sample is too small.

    Notes
    -----
    [FIX] Legacy used `np.log2(unique_values)` as the divisor, producing a
    range of [0, ln(2)] ≈ [0, 0.693] instead of [0, 1].  scipy.stats.entropy
    returns nats (natural log base), so the correct divisor is np.log(unique_values).
    The binary anomaly flags produced by callers were unaffected because the
    ln(2) factor cancelled in relative (median-based) comparisons, but the raw
    entropy values were uninterpretable as a normalised measure.

    [NOTE] column.shape[0] must reflect only non-null rows.  Callers must apply
    a null-excluding mask before groupby so that the sample-size threshold
    (min_record_sample * unique_values) is not inflated by null entries.
    """
    prob_distribution = column.value_counts(normalize=True)

    if unique_values > 1 and column.shape[0] >= min_record_sample * unique_values:
        # [FIX] Corrected: np.log(unique_values) matches scipy's natural-log base,
        # yielding a true [0, 1] normalised entropy.
        # Legacy used np.log2(unique_values), capping values at ln(2) ≈ 0.693.
        entropy_ = entropy(prob_distribution.values) / np.log(unique_values)
    elif unique_values == 1 and column.shape[0] >= min_record_sample * unique_values:
        entropy_ = 0
    else:
        entropy_ = None

    return entropy_


# ---------------------------------------------------------------------------
# Winsorize helper (unchanged from legacy)
# ---------------------------------------------------------------------------

def adjustable_winsorize(data, initial_lower=0.05, initial_upper=0.05, step=0.01):
    lower_limit = initial_lower
    upper_limit = initial_upper
    winsorized_data = winsorize(data, limits=[lower_limit, upper_limit])

    while len(np.unique(winsorized_data)) <= 1 and (lower_limit > 0 or upper_limit > 0):
        lower_limit = max(0, lower_limit - step)
        upper_limit = max(0, upper_limit - step)
        winsorized_data = winsorize(data, limits=[lower_limit, upper_limit])

    return winsorized_data
