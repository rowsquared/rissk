import math
import pandas as pd
import numpy as np
import ast
import logging

from rissk.utils.stats_utils import first_digit

logger = logging.getLogger(__name__)

# --- Helper Functions ---

def make_index_col(df: pd.DataFrame) -> pd.DataFrame:
    """Creates a unique index column based on interview_id, variable_name, and roster_level."""
    # fillna('') normalises NaN roster_level (non-roster items) and NaN variable_name
    # to empty string before concatenation, matching legacy make_index_col behaviour.
    # Trailing/leading underscores are stripped so non-roster items don't get a trailing '_'.
    df_temp = df[['interview__id', 'variable_name', 'roster_level']].fillna('').astype(str)
    
    # Concatenate columns
    df['index_col'] = df_temp['interview__id'] + "_" + df_temp['variable_name'] + "_" + df_temp['roster_level']
    
    # Remove trailing and leading underscores
    df['index_col'] = df['index_col'].str.strip('_')
    return df

def get_numeric_mask(df_item: pd.DataFrame, filter_answer_values: bool) -> pd.Series:
    """Returns a boolean mask for valid numeric question rows, matching the legacy numeric_question_mask."""
    sentinel_mask = _is_missing_numeric_sentinel(df_item['value'])
    mask = (
        (df_item["qtype"] == 'NumericQuestion') &
        (~pd.isnull(df_item['value'])) &
        (~sentinel_mask)
    )
    if filter_answer_values:
        answer_mask = _is_answer_value(df_item['value'], df_item['answer_sequence'])
        mask &= ~answer_mask

    return mask


def _is_missing_numeric_sentinel(values: pd.Series) -> pd.Series:
    """Robustly detects the numeric missing-value sentinel across mixed object values."""
    return pd.to_numeric(values, errors='coerce').eq(-999999999)

def _is_answer_value(values: pd.Series, answer_sequence: pd.Series) -> pd.Series:
    """Returns True where the numeric value matches an item in the answer_sequence list.

    answer_sequence is expected to be the string-coerced form produced by
    paradata['answer_sequence'].apply(str), e.g. "[1, 2]", "[0, -99]", "nan".
    """
    def _row_is_answer(value, seq_str):
        if not isinstance(seq_str, str) or seq_str in ('nan', 'None', ''):
            return False
        try:
            items = ast.literal_eval(seq_str)
        except (ValueError, SyntaxError):
            return False
        if not isinstance(items, list):
            return False
        numeric_val = pd.to_numeric(value, errors='coerce')
        if pd.isna(numeric_val):
            return False
        return any(numeric_val == pd.to_numeric(item, errors='coerce') for item in items)

    return pd.Series(
        [_row_is_answer(v, s) for v, s in zip(values, answer_sequence)],
        index=values.index,
        dtype=bool,
    )


def _coerce_numeric_with_warning(df_item: pd.DataFrame, numeric_mask: pd.Series, feature_name: str) -> pd.Series:
    """Coerce numeric values and warn about rows that cannot be parsed."""
    values = df_item.loc[numeric_mask, 'value']
    coerced = pd.to_numeric(values, errors='coerce')

    failed_mask = coerced.isna() & values.notna()
    failed_count = int(failed_mask.sum())
    if failed_count > 0:
        sample_bad_values = values[failed_mask].astype(str).drop_duplicates().head(10).tolist()
        logger.warning(
            "%s: failed to parse %d numeric value(s); coerced to NaN. Sample values: %s",
            feature_name,
            failed_count,
            sample_bad_values,
        )

    return coerced

def get_df_time(df_paradata_full: pd.DataFrame) -> pd.DataFrame:
    """Calculates time differences and durations from paradata.

    Mirrors the legacy df_active_paradata filter before computing time deltas:
    - AnswerSet / AnswerRemoved / CommentSet: included only when question_scope == 0
    - InterviewCreated / Resumed / Restarted: no question scope (NaN); included regardless.
    - All other event types (Completed, ApprovalRequested, etc.): excluded.

    """
    # Events that carry a question scope — keep only interviewer-scope (== 0).
    # NaN scope (supervisor-originated or no-question events) is intentionally excluded here.
    question_scope_events = ['AnswerSet', 'AnswerRemoved', 'CommentSet']
    # Events that have no question scope (pause / session events); always include.
    no_scope_events = ['InterviewCreated', 'Resumed', 'Restarted']

    active_mask = (
        (df_paradata_full['event'].isin(no_scope_events)) |
        (df_paradata_full['event'].isin(question_scope_events) & (df_paradata_full['question_scope'] == 0))
    )
    df_time = df_paradata_full[active_mask].copy()

    # calculate time difference in seconds
    df_time['time_difference'] = df_time.groupby('interview__id')['timestamp_local'].diff()
    df_time['time_difference'] = df_time['time_difference'].dt.total_seconds()
    
    # Logic for f__time_changed (negative time diffs < -180s)
    df_time['f__time_changed'] = np.where(df_time['time_difference'] < -180, df_time['time_difference'], np.nan)
    
    # Mask negative time differences for duration calculations
    # Using pd.NA for nullable integers/floats in pandas if column allows, or np.nan
    df_time.loc[df_time['time_difference'] < 0, 'time_difference'] = pd.NA

    # f__answer_duration: total time spent recording answers, i.e. the sum of all
    # time-intervals from active events that conclude with AnswerSet or AnswerRemoved.
    df_time['f__answer_duration'] = df_time.loc[
        df_time['event'].isin(['AnswerSet', 'AnswerRemoved']), 'time_difference']
    # f__comment_duration: total time spent on comments, i.e. the sum of all
    # time-intervals from active events that conclude with CommentSet.
    df_time['f__comment_duration'] = df_time.loc[df_time['event'] == 'CommentSet', 'time_difference']
    df_time['f__pause_duration'] = df_time.loc[df_time['event'].isin(['Resumed', 'Restarted']), 'time_difference']

    # UNIT features helper logic
    active_events = ['AnswerSet', 'AnswerRemoved', 'CommentSet', 'Resumed', 'Restarted']
    
    # Calculate total duration (capped at 30 mins per event)
    condition = (df_time['event'].isin(active_events)) & (df_time['time_difference'] < 30 * 60)
    df_time['f__total_duration'] = df_time.loc[condition, 'time_difference']

    # Starting timestamp per interview: minimum timestamp among AnswerSet events (not the
    # global event minimum). If the device clock is adjusted later in the interview, the
    # overall min of timestamp_local would return a misleadingly early start time. Anchoring
    # to the first AnswerSet avoids this clock-adjustment artefact.
    start_time_map = df_time[df_time['event'] == 'AnswerSet'].groupby('interview__id')['timestamp_local'].min()
    df_time['f__starting_timestamp'] = df_time['interview__id'].map(start_time_map)
    
    min_date = df_time['f__starting_timestamp'].min()
    if pd.notna(min_date):
        df_time['f__days_from_start'] = (df_time['timestamp_local'] - min_date).dt.days.abs()
    else:
        df_time['f__days_from_start'] = np.nan

    return df_time

def get_df_sequence(df_paradata_full: pd.DataFrame) -> pd.DataFrame:
    """Calculates sequence-based features (jumps, previous answers)."""
    # Filter for AnswerSet and get the last entry per index_col (filter is already applied in base item table creation)
    # mask = df_paradata_full['event'] == 'AnswerSet'
    df_last = df_paradata_full.groupby('index_col').last().copy()  

    # The groupby puts index_col in the index.
    # We need to sort by interview_id and order to reconstruct the sequence flow.
    # 'order' column is assumed to exist from ingestion.
    df_last = df_last.sort_values(['interview__id', 'order']).reset_index()

    # f__previous_question, f__previous_answer, f__previous_roster
    # Using shift on the group
    df_last['f__previous_question'] = df_last.groupby('interview__id')['variable_name'].shift()
    df_last['f__previous_answer'] = df_last.groupby('interview__id')['answer'].shift().fillna(pd.NA)
    df_last['f__previous_roster'] = df_last.groupby('interview__id')['roster_level'].shift().fillna(pd.NA)
    
    # f__sequence_jump: the change in the gap between questionnaire order and answer order
    # from one answered question to the next. A non-zero value means the interviewer skipped
    # questions or navigated backwards relative to the questionnaire sequence.
    # Calculate answer sequence (1, 2, 3...) based on actual occurrence order.
    df_last['answer_sequence'] = df_last.groupby('interview__id').cumcount() + 1

    # diff = questionnaire position - answer position; a constant diff means sequential
    # answering, while a change in diff indicates skipping ahead or going backwards.
    df_last['question_sequence'] = pd.to_numeric(df_last['question_sequence'], errors='coerce').fillna(0)
    df_last['diff'] = df_last['question_sequence'] - df_last['answer_sequence']

    # The jump is how much the diff itself changed from one answer to the next.
    df_last['f__sequence_jump'] = df_last.groupby('interview__id')['diff'].diff()

    return df_last

def add_sequence_features(df_item: pd.DataFrame, df_sequence: pd.DataFrame, allowed_features: list) -> pd.DataFrame:
    """Merge sequence-based features from df_sequence onto df_item for enabled features."""
    sequence_features = ['f__previous_question', 'f__previous_answer',
                         'f__previous_roster', 'f__sequence_jump']
    
    # Filter to only allowed features
    selected_features = [f for f in sequence_features if f in allowed_features]
    
    if selected_features:
        # Select columns to merge
        cols_to_use = ['index_col'] + selected_features
        # Ensure columns exist in df_sequence
        cols_to_use = [c for c in cols_to_use if c in df_sequence.columns]
        
        if len(cols_to_use) > 1: # at least index_col + 1 feature
            df_item = df_item.merge(df_sequence[cols_to_use], how='left', on='index_col')
            
    return df_item

def add_item_time_features(df_item: pd.DataFrame, df_time: pd.DataFrame, allowed_features: list, item_level_columns: list) -> pd.DataFrame:
    """Aggregate per-event answer/comment durations from df_time to item level and merge onto df_item."""
    time_features = ['f__answer_duration', 'f__comment_duration']
    
    selected_features = [f for f in time_features if f in allowed_features]
    
    if selected_features:
        # Filter out pause events (Resumed/Restarted) which have variable_name=NaN in Kedro
        # (legacy used '' after global fillna(''), but NaN is the correct sentinel here)
        df_time_filtered = df_time[df_time['variable_name'].notna()].copy()
        # AnswerRemoved / CommentSet events have roster_level=None in paradata (no roster context
        # is recorded on removal/comment events), while AnswerSet rows carry ''. Normalise to ''
        # so they land in the same groupby bucket as the corresponding AnswerSet events, matching
        # the legacy behaviour where process_paradata does fillna('') on the whole dataframe.
        df_time_filtered['roster_level'] = df_time_filtered['roster_level'].fillna('')
        
        # Summarize on item level
        # Note: df_time might have multiple events per item (e.g. AnswerRemoved then AnswerSet)
        # We sum the durations.
        agg_dict = {}
        if 'f__answer_duration' in selected_features:
            agg_dict['f__answer_duration'] = 'sum'
        if 'f__comment_duration' in selected_features:
            agg_dict['f__comment_duration'] = 'sum'
            
        if agg_dict:
             # Ensure grouping columns exist
            group_cols = [c for c in item_level_columns + ['index_col'] if c in df_time_filtered.columns]
            
            df_agg = df_time_filtered.groupby(group_cols).agg(agg_dict).reset_index()
            
            # Merge
            df_agg = df_agg[['index_col'] + list(agg_dict.keys())]
            df_item = df_item.merge(df_agg, how='left', on='index_col')
            
    return df_item

def add_pause_features(df_unit: pd.DataFrame, df_time: pd.DataFrame, allowed_features: list) -> pd.DataFrame:
    """Aggregate pause count, total duration, and duration list from df_time to the unit table."""
    pause_features = ['f__pause_count', 'f__pause_duration', 'f__pause_list']
    selected_features = [f for f in pause_features if f in allowed_features]

    if not selected_features:
        return df_unit

    # Legacy-like flow: compute all pause aggregations once, then keep selected columns.
    # Keep the correction vs legacy: count only non-null pauses.
    df_pause = df_time.groupby('interview__id').agg(
        f__pause_count=('f__pause_duration', 'count'),
        f__pause_duration=('f__pause_duration', 'sum'),
        # Keep only real pause durations; all-NaN groups become an empty list.
        f__pause_list=('f__pause_duration', lambda x: [v for v in x.tolist() if pd.notna(v)]),
    ).reset_index()

    df_pause = df_pause[['interview__id'] + selected_features]
    df_unit = df_unit.merge(df_pause, how='left', on='interview__id')
    
    # Fill NaNs for pause features: no pauses -> count=0, duration=0, empty list.
    if 'f__pause_count' in selected_features:
        df_unit['f__pause_count'] = df_unit['f__pause_count'].fillna(0).astype(int)
    if 'f__pause_duration' in selected_features:
        df_unit['f__pause_duration'] = df_unit['f__pause_duration'].fillna(0)
    if 'f__pause_list' in selected_features:
        # Ensure interviews absent in df_time also get an empty list after merge.
        df_unit['f__pause_list'] = df_unit['f__pause_list'].apply(
            lambda x: x if isinstance(x, list) else []
        )

    return df_unit

def add_unit_time_features(df_unit: pd.DataFrame, df_time: pd.DataFrame, allowed_features: list) -> pd.DataFrame:
    """Aggregate interview-level time features (total duration, elapse, days since start, clock shifts) onto df_unit."""
    time_features = ['f__total_duration', 'f__total_elapse', 'f__days_from_start', 'f__time_changed']
    selected_features = [f for f in time_features if f in allowed_features]

    if not selected_features:
        return df_unit

    # Legacy-like flow: compute all unit-time aggregations once, then keep selected columns.
    df_dur = df_time.groupby('interview__id').agg(
        f__total_duration=('f__total_duration', 'sum'),
        f__total_elapse=('timestamp_local', lambda x: (x.max() - x.min()).total_seconds()),
        f__time_changed=('f__time_changed', 'sum'),
        f__days_from_start=('f__days_from_start', 'min'),
    ).reset_index()

    df_dur = df_dur[['interview__id'] + selected_features]
    df_unit = df_unit.merge(df_dur, how='left', on='interview__id')

    return df_unit


# --- Base Table Creation ---

def create_base_item_table(microdata: pd.DataFrame, paradata_full: pd.DataFrame, parameters: dict) -> pd.DataFrame:
    """
    Creates the base item table by merging microdata with paradata information.
    Equivalent to FeatureProcessing.make_df_item.
    """
    logger.info("Creating base item table...")

    if microdata.empty:
        logger.error(
            "create_base_item_table: microdata is empty — all microdata files were missing "
            "or contained no data rows. Cannot build item table. Returning empty DataFrame."
        )
        return pd.DataFrame()

    if paradata_full.empty:
        logger.error(
            "create_base_item_table: paradata_full is empty — no paradata to merge "
            "(e.g. a STATA/Tabular-only export with no Paradata file). Cannot build the item "
            "table. Returning empty DataFrame."
        )
        return pd.DataFrame()

    item_level_columns = ['interview__id', 'variable_name', 'roster_level']
    allowed_features = ['f__' + k for k, v in parameters['features'].items() if v.get('use', False)]

    sequence_features = ['f__previous_question', 'f__previous_answer', 'f__previous_roster', 'f__sequence_jump']
    time_features = ['f__answer_duration', 'f__comment_duration']
    calculate_sequence = any(f in allowed_features for f in sequence_features)
    calculate_time = any(f in allowed_features for f in time_features)

    # 1. Create Index Column on Microdata
    df_item = make_index_col(microdata.copy())
    
    # 2. Select initial columns
    columns = ['value', "qtype", 'is_integer', 'qnr_seq',
               'n_answers', 'answer_sequence', 
               'cascade_from_question_id', 'is_filtered_combobox',
               'index_col', 'qnr', 'qnr_version'] + item_level_columns
    
    # Intersect with available columns to avoid KeyErrors
    df_item = df_item[columns].copy()

    # 3. Prepare Paradata for Merge
    # We want the *last* AnswerSet for each item
    paradata_columns = ['responsible', 'f__answer_hour_set', 'interviewing', 'tz_offset']
    # available_para_cols = [c for c in paradata_columns if c in paradata_full.columns]
    
    # Interviewer-scope AnswerSet events: scope==0 means interviewer, scope==1 means supervisor.
    # Pause events (Resumed/Restarted) have NaN scope; no fillna needed — they are not AnswerSet events.
    interviewer_answer_mask = (
        (paradata_full['event'] == 'AnswerSet') &
        (paradata_full['question_scope'] == 0)
    )

    data_to_merge = (
        paradata_full[interviewer_answer_mask]
        .dropna(subset=['index_col'])             # drop rows without index_col
        # keep the last AnswerSet per item, paradata is already sorted by interview__id and order in the processing node
        .drop_duplicates(subset='index_col', keep='last')
        [['index_col'] + paradata_columns]    # select only necessary columns for merging
    )

    # 4. Merge
    df_item = df_item.merge(data_to_merge[paradata_columns + ['index_col']], how='left', on='index_col')

    # 5. Filter for 'interviewing' == True (Supervisor Logic)
    # Remove items that are not in interviewing
    df_item = df_item[df_item['interviewing'] == True].copy()

    # 6. Add Sequence Features
    if calculate_sequence:
        df_sequence = get_df_sequence(paradata_full[interviewer_answer_mask])
        df_item = add_sequence_features(df_item, df_sequence, allowed_features)

    # 7. Add Time Features
    if calculate_time:
        # Pass full paradata; get_df_time filters by event type internally.
        # This correctly includes pause events (Resumed/Restarted) which have NaN question_scope.
        df_time = get_df_time(paradata_full)
        df_item = add_item_time_features(df_item, df_time, allowed_features, item_level_columns)

    return df_item

def create_base_unit_table(paradata_full: pd.DataFrame, parameters: dict) -> pd.DataFrame:
    """
    Creates the base unit table (one row per interview).
    Equivalent to FeatureProcessing.make_df_unit.
    """
    logger.info("Creating base unit table...")

    if paradata_full.empty:
        logger.error(
            "create_base_unit_table: paradata_full is empty — no paradata to build unit table from. "
            "Returning empty DataFrame."
        )
        return pd.DataFrame()

    allowed_features = ['f__' + k for k, v in parameters['features'].items() if v.get('use', False)]
    
    # 1. Initialize from paradata
    columns = ['interview__id', 'responsible', 'qnr', 'qnr_version']
    
    # Match legacy code and use active paradata to seed the unit table.
    question_scope_events = ['AnswerSet', 'AnswerRemoved', 'CommentSet']
    # Events that have no question scope (pause / session events); always include.
    no_scope_events = ['InterviewCreated', 'Resumed', 'Restarted']

    active_mask = (
        (paradata_full['event'].isin(no_scope_events)) |
        (paradata_full['event'].isin(question_scope_events) & (paradata_full['question_scope'] == 0))
    )

    df_unit = paradata_full[active_mask][columns].copy()
    df_unit.drop_duplicates(inplace=True)

    # Filter valid responsible
    df_unit = df_unit[df_unit['responsible'].notna()]

    pause_features = ['f__pause_count', 'f__pause_duration', 'f__pause_list']
    unit_time_features = ['f__total_duration', 'f__total_elapse', 'f__days_from_start', 'f__time_changed']
    calculate_pause = any(f in allowed_features for f in pause_features)
    calculate_unit_time = any(f in allowed_features for f in unit_time_features)

    if calculate_pause or calculate_unit_time:
        # Pass full paradata so get_df_time correctly includes pause events (Resumed/Restarted)
        # which have NaN question_scope and would be dropped by any scope filter.
        df_time = get_df_time(paradata_full)
        if calculate_pause:
            df_unit = add_pause_features(df_unit, df_time, allowed_features)
        if calculate_unit_time:
            df_unit = add_unit_time_features(df_unit, df_time, allowed_features)
    
    return df_unit


# --- Feature Enrichment Functions (Item) ---

def feat_string_length(df_item, **kwargs):
    # f__string_length, length of string answer, if TextQuestions else empty pd.NA
    feature_name = 'f__string_length'
    mask = df_item["qtype"] == 'TextQuestion'
    df_item[feature_name] = pd.NA
    if mask.any():
        # Use .str.len() directly to preserve NA (astype(str) would convert NaN -> "nan", length 3)
        df_item.loc[mask, feature_name] = df_item.loc[mask, 'value'].str.len().astype('Int64')
    return df_item

def feat_numeric_response(df_item, **kwargs):
    # f__numeric_response, response, if NumericQuestions, else empty pd.NA
    feature_name = 'f__numeric_response'
    # Use the same mask as legacy: excludes empty, null, and -999999999
    # filter_answer_values=True would exclude values that match the answer options (legacy did not apply this filter)
    numeric_mask = get_numeric_mask(df_item=df_item, filter_answer_values=True)
    df_item[feature_name] = np.nan
    if numeric_mask.any():
        numeric_values = _coerce_numeric_with_warning(df_item, numeric_mask, feature_name)
        df_item.loc[numeric_mask, feature_name] = numeric_values
    return df_item

def feat_first_digit(df_item, **kwargs):
    # f__first_digit, first digit of the response if numeric question else empty pd.NA
    feature_name = 'f__first_digit'
    # Use the same mask as legacy: excludes empty, null, and -999999999
    # filter_answer_values=True would exclude values that match the answer options (legacy did not apply this filter)
    numeric_mask = get_numeric_mask(df_item=df_item, filter_answer_values=True)
    df_item[feature_name] = pd.NA
    if numeric_mask.any():
        numeric_values = _coerce_numeric_with_warning(df_item, numeric_mask, feature_name)
        vals = numeric_values.apply(first_digit)
        df_item.loc[numeric_mask, feature_name] = pd.array(vals, dtype='Int64')
    return df_item

def feat_last_digit(df_item, **kwargs):
    # f__last_digit, modulus of 10 of the response if numeric question else empty pd.NA
    feature_name = 'f__last_digit'
    # Use the same mask as legacy: excludes empty, null, and -999999999
    # filter_answer_values=True would exclude values that match the answer options (legacy did not apply this filter)
    numeric_mask = get_numeric_mask(df_item=df_item, filter_answer_values=True)
    df_item[feature_name] = pd.NA

    if numeric_mask.any():
        numeric_values = _coerce_numeric_with_warning(df_item, numeric_mask, feature_name)
        # Legacy casts to int64 before extracting the last digit.
        # Use truncation toward zero so decimals behave like integer casting.
        vals = np.trunc(numeric_values).astype('Int64')
        # .where(condition) keeps values where True, sets False to NA
        df_item.loc[numeric_mask, feature_name] = (vals % 10).where(vals >= 1)
        
    return df_item

def feat_first_decimals(df_item, **kwargs):
    # f__first_decimals, first decimals if numeric question else empty pd.NA
    feature_name = 'f__first_decimals'
    # mask: not integer, not empty & not mumeric sentinel
    numeric_mask = get_numeric_mask(df_item=df_item, filter_answer_values=True)
    mask_integer = (df_item['is_integer'] == False) & (~pd.isnull(df_item['value']))
    mask = numeric_mask & mask_integer
    df_item[feature_name] = pd.NA
    
    if mask.any():
        values = pd.to_numeric(df_item.loc[mask, 'value'], errors='coerce')
        # Intentional: capture the first TWO decimal digits (e.g. 3.47 → 47) rather than just
        # the first (e.g. 4). Using two digits gives the COF model a finer-grained signal and
        # materially reduces hash-collisions for values like x.10, x.20 … x.90 that would be
        # indistinguishable if only a single decimal were retained. The feature is therefore
        # named f__first_decimals (plural) to make this design choice visible at a glance.
        res = np.floor(values * 100) % 100
        df_item.loc[mask, feature_name] = res.astype('Int64')

    # Match legacy: ensure the full feature column uses nullable integer dtype.
    df_item[feature_name] = df_item[feature_name].astype('Int64')
        
    return df_item

def feat_answer_position(df_item, **kwargs):
    """Compute f__answer_position: relative position of the selected answer within the answer list.

    Calculated only for SingleQuestion items with more than two options, excluding filtered
    comboboxes and cascade children. Position is idx / (n_answers - 1), producing a value in [0, 1].
    """
    # f__answer_position, relative position of the selected answer
    # only questions with more than two answers
    feature_name = 'f__answer_position'

    # filters
    mask = ((df_item["qtype"] == 'SingleQuestion')
            & (df_item['n_answers'] > 2)
            & (df_item['is_filtered_combobox'] == False)
            & (df_item['cascade_from_question_id'].isna()))         
    df_item[feature_name] = np.nan
    
    if mask.any():
        # logic: index of value in answer_sequence / (n_answers - 1)
        # answer_sequence is a list-like or serialized as string.

        def calc_pos(row):
            try:
                seq = ast.literal_eval(str(row['answer_sequence']))
                if not isinstance(seq, list) or len(seq) == 0:
                    return np.nan

                val = pd.to_numeric(row['value'], errors='coerce')
                if pd.isna(val):
                    return np.nan

                # Align numeric types when seq is integer-coded.
                if all(isinstance(x, (int, np.integer)) for x in seq) and float(val).is_integer():
                    val = int(val)

                if val not in seq:
                    return np.nan

                n = row['n_answers']
                if pd.isna(n) or n <= 1:
                    return np.nan

                idx = seq.index(val)
                return round(idx / (n - 1), 3)
            except Exception:
                return np.nan

        # Apply is slow but robust for list operations in cells
        df_item.loc[mask, feature_name] = df_item.loc[mask].apply(calc_pos, axis=1)
        
    return df_item

def feat_answer_removed(paradata_full):
    """Aggregate AnswerRemoved event counts per item into f__answer_removed.

    Returns a standalone DataFrame (not merged into df_item) because AnswerRemoved events
    may reference items that were subsequently deleted from microdata and are therefore absent
    from df_item. Consuming code scores this separately via calculate_answer_removed_score_from_df.
    """
    # f__answer_removed, answers removed (by interviewer, or by system as a result of interviewer action).
    # Matches legacy get_feature_item__answer_removed which uses self.df_paradata, but it appends the 
    # feature to the item table instead of returning a separate dataframe. 
    # (all events, role=1, interviewing=True).
    # The legacy method notes this feature may include items no longer in microdata.
    feature_name = 'f__answer_removed'

    if paradata_full.empty or 'event' not in paradata_full.columns:
        logger.warning(
            "feat_answer_removed: paradata_full is empty or missing the 'event' column — "
            "no AnswerRemoved events to process. Returning empty DataFrame."
        )
        return pd.DataFrame()

    removed_mask = (
        (paradata_full['event'] == 'AnswerRemoved') &
        (paradata_full['role'] == 1) # interviewer role is already filtered in paradata processing node
    )

    df_removed = paradata_full[removed_mask].copy()
    if df_removed.empty:
        return df_removed

    # Align grouping grain with legacy helper exactly.
    # qnr and qnr_version are included so removed_answers carries questionnaire
    # identity for per-questionnaire filtering downstream.
    group_cols = ['interview__id', 'responsible', 'variable_name', 'qnr_seq']
    extra_cols = [c for c in ['qnr', 'qnr_version'] if c in df_removed.columns]
    if any(c not in df_removed.columns for c in group_cols):
        logger.warning(
            "%s: missing one or more legacy group columns (%s); skipping feature.",
            feature_name,
            group_cols,
        )
        return df_removed

    df_agg_removed = df_removed.groupby(group_cols + extra_cols).agg(
        f__answer_removed=('order', 'count')
    ).reset_index()

    # # Keep item table cardinality while assigning legacy-grain counts.
    # df_item = df_item.merge(df_agg_removed[group_cols + [feature_name]], how='left', on=group_cols)
    return df_agg_removed


def feat_answer_changed(df_item, **kwargs):
    """
    Legacy bug fixed: the legacy code applied the yes_list change
    check and immediately overwrote it with the no_list check (two separate .loc assignments
    on the same mask), so yes_list changes were always ignored. This implementation
    combines both checks using a bitwise OR.
    """
    feature_name = 'f__answer_changed'
    paradata_full = kwargs.get('paradata_full')

    if paradata_full is None:
        return df_item

    item_level_columns = ['interview__id', 'variable_name', 'roster_level']
    df_changed = paradata_full[(paradata_full['event'] == 'AnswerSet') & (paradata_full['question_scope'] == 0)].copy()

    df_changed[feature_name] = False
    group_cols = [c for c in item_level_columns + ['index_col'] if c in df_changed.columns]
    has_yes_no = 'yes_no_view' in df_changed.columns

    # --- Case 1: TextListQuestion and MultyOptionsQuestion (without yes_no_view mode) ---
    # Keep flow aligned with legacy while scoping masks to their intended qtypes.
    # TextListQuestion don't have yes_no_view mode.
    list_mask = df_changed["qtype"] == 'TextListQuestion'
    
    multi_mask = (
        (df_changed["qtype"] == 'MultyOptionsQuestion') &
        (df_changed['yes_no_view'] == False)
    ) if has_yes_no else (df_changed["qtype"] == 'MultyOptionsQuestion')

    df_changed['answer_list'] = pd.NA
    df_changed.loc[list_mask, 'answer_list'] = df_changed.loc[list_mask, 'answer'].str.split('|')
    df_changed.loc[multi_mask, 'answer_list'] = df_changed.loc[multi_mask, 'answer'].str.split(r', |\|')

    df_changed['prev_answer_list'] = df_changed.groupby(group_cols)['answer_list'].shift()
    answers_mask = df_changed['prev_answer_list'].notna()
    if answers_mask.any():
        df_changed.loc[answers_mask, feature_name] = df_changed.loc[answers_mask].apply(
            lambda row: not set(row['prev_answer_list']).issubset(set(row['answer_list'])), axis=1
        )

    # --- Case 2: Single-answer questions ---
    df_changed['prev_answer'] = df_changed.groupby(group_cols)['answer'].shift()
    single_answer_mask = (
        (~df_changed["qtype"].isin(['MultyOptionsQuestion', 'TextListQuestion'])) &
        (df_changed['prev_answer'].notna()) &
        (df_changed['answer'] != df_changed['prev_answer'])
    )
    df_changed.loc[single_answer_mask, feature_name] = True

    # --- Case 3: Yes/No view questions ---
    if has_yes_no:
        yesno_mask = (df_changed['yes_no_view'] == True)
        if yesno_mask.any():
            df_filtered = df_changed[yesno_mask].copy()
            df_filtered[['yes_list', 'no_list']] = df_filtered['answer'].str.split('|', expand=True)
            df_filtered['yes_list'] = df_filtered['yes_list'].str.split(', ').apply(
                lambda x: [] if x == [''] or x is None else x)
            df_filtered['no_list'] = df_filtered['no_list'].str.split(', ').apply(
                lambda x: [] if x == [''] or x is None else x)
            yesno_group_cols = [c for c in group_cols if c in df_filtered.columns]
            df_filtered['prev_yes_list'] = df_filtered.groupby(yesno_group_cols)['yes_list'].shift(fill_value=[])
            df_filtered['prev_no_list'] = df_filtered.groupby(yesno_group_cols)['no_list'].shift(fill_value=[])
            # A change occurs if either yes or no selections have been removed
            yes_changed = df_filtered.apply(
                lambda row: not set(row['prev_yes_list']).issubset(set(row['yes_list'])), axis=1)
            no_changed = df_filtered.apply(
                lambda row: not set(row['prev_no_list']).issubset(set(row['no_list'])), axis=1)
            df_changed.loc[yesno_mask, feature_name] = (yes_changed | no_changed).values

    # Sum changes per item and map back
    changes_per_item = df_changed.groupby('index_col')[feature_name].sum()
    df_item[feature_name] = df_item['index_col'].map(changes_per_item).fillna(0)

    return df_item


def feat_answer_selected(df_item, **kwargs):
    # f__answers_selected, number of answers selected in a multi-answer or list question, 
    # divided by n_answers to get share selected (only for unlinked questions).
    feature_name = 'f__answer_selected'
    # Select only MultyOptionsQuestion as legacy does.
    multi_list_mask = df_item["qtype"].isin(['MultyOptionsQuestion'])
    # Include only rows where n_answers can be parsed as a positive number to avoid division issues.
    n_answers_num = pd.to_numeric(df_item.loc[multi_list_mask, 'n_answers'], errors='coerce')
    valid_denominator_mask = n_answers_num > 0
    # Combine masks to ensure we only calculate for valid MultyOptionsQuestion rows with a positive n_answers.
    mask = multi_list_mask & valid_denominator_mask

    df_item[feature_name] = np.nan
    
    # Function to calculate the number of elements in a list or return nan
    def count_elements_or_nan(val):
        try:
            val = ast.literal_eval(str(val))
            return len(val)
        except (ValueError, SyntaxError, TypeError):
            return np.nan
        
    if mask.any():
        df_item.loc[mask, feature_name] = df_item.loc[mask, 'value'].apply(count_elements_or_nan)
        # f__share_selected, share between answers selected and available answers (only for unlinked questions).
        # Linked questions will be implicitly excluded since they have nan n_answers after coercion.
        df_item.loc[mask, feature_name] = (
            df_item.loc[mask, feature_name] / n_answers_num.loc[mask]
        )
        
    return df_item


def feat_comment_length(df_item, **kwargs):
    ## Total character length of all comments left on each item.
    feature_name = 'f__comment_length'
    paradata_full = kwargs.get('paradata_full')

    df_item[feature_name] = pd.NA

    if paradata_full is None:
        return df_item

    comment_mask = (
        (paradata_full['event'] == 'CommentSet') &
        (paradata_full['role'] == 1)
    )
    df_comment = paradata_full[comment_mask].copy()
    if df_comment.empty:
        return df_item

    df_comment[feature_name] = df_comment['answer'].str.len()
    df_agg = df_comment.groupby('index_col').agg(f__comment_length=(feature_name, 'sum'))
    df_item[feature_name] = df_item['index_col'].map(df_agg['f__comment_length'])

    return df_item


def feat_comment_set(df_item, **kwargs):
    ## Count of CommentSet events per item.
    feature_name = 'f__comment_set'
    paradata_full = kwargs.get('paradata_full')

    df_item[feature_name] = pd.NA

    if paradata_full is None:
        return df_item

    comment_mask = (
        (paradata_full['event'] == 'CommentSet') &
        (paradata_full['role'] == 1)
    )
    df_comment = paradata_full[comment_mask].copy()
    if df_comment.empty:
        return df_item

    df_agg = df_comment.groupby('index_col').agg(f__comment_set=('order', 'count'))
    df_item[feature_name] = df_item['index_col'].map(df_agg['f__comment_set'])
    return df_item


def feat_gps(df_item, **kwargs):
    # Sets f__gps boolean flag plus f__gps_latitude, f__gps_longitude, f__gps_accuracy
    feature_name = 'f__gps'
    mask = df_item["qtype"] == 'GpsCoordinateQuestion'
    df_item[feature_name] = False
    if mask.any():
        df_item.loc[mask, feature_name] = True
        # Split value "lat,lon,acc,alt,timestamp_utc"
        gps_data = df_item.loc[mask, 'value'].str.split(',', expand=True)
        if gps_data.shape[1] >= 3:
            df_item.loc[mask, 'f__gps_latitude'] = pd.to_numeric(gps_data[0], errors='coerce')
            df_item.loc[mask, 'f__gps_longitude'] = pd.to_numeric(gps_data[1], errors='coerce')
            df_item.loc[mask, 'f__gps_accuracy'] = pd.to_numeric(gps_data[2], errors='coerce')

    return df_item


# Dispatcher
ITEM_FEATURE_MAP = {
    'string_length': feat_string_length,
    'numeric_response': feat_numeric_response,
    'first_digit': feat_first_digit,
    'last_digit': feat_last_digit,
    'first_decimals': feat_first_decimals,
    'answer_position': feat_answer_position,
    'answer_changed': feat_answer_changed,
    'answer_selected': feat_answer_selected,
    # answer_removed is handled as a separate pipeline node outputting removed_answers parquet;
    # it is NOT enriched into df_item here.
    'comment_length': feat_comment_length,
    'comment_set': feat_comment_set,
    'gps': feat_gps,
}


def enrich_item_features(df_item: pd.DataFrame, paradata_full: pd.DataFrame, parameters: dict) -> pd.DataFrame:
    """
    Applies feature engineering logic to the item table.
    paradata_full: all processed events, role=1, interviewing=True (self.df_paradata equivalent).
    """
    logger.info("Enriching item features...")
    allowed_features = parameters.get('features', {})

    for feat_key, feat_cfg in allowed_features.items():
        if feat_cfg.get('use', False):
            func = ITEM_FEATURE_MAP.get(feat_key)
            if func:
                logger.info(f"Calculating item feature: {feat_key}")
                try:
                    df_item = func(df_item, paradata_full=paradata_full)
                except Exception as e:
                    logger.warning(f"Failed to calculate {feat_key}: {e}")

    return df_item


# --- Feature Enrichment Functions (Unit) ---

def feat_unit_number_answered(df_unit, item_features, **kwargs):
    feature_name = 'f__number_answered'
    # Match legacy make_feature_unit__number_answered: exclude null, -999999999, '##N/A##',
    # empty string, and Variable-type questions
    sentinel_mask = _is_missing_numeric_sentinel(item_features['value'])
    mask = (
        (~pd.isnull(item_features['value'])) &
        (~sentinel_mask) &
        (item_features['value'] != '##N/A##') &
        (item_features['qtype'] != 'Variable')
    )
    df_agg = item_features[mask].groupby('interview__id').agg(
        f__number_answered=('value', 'count')
    )
    df_unit[feature_name] = df_unit['interview__id'].map(df_agg['f__number_answered']).fillna(0)
    return df_unit

def feat_unit_number_unanswered(df_unit, item_features, **kwargs):
    feature_name = 'f__number_unanswered'
    # Match legacy make_feature_unit__number_unanswered: -999999999 or '##N/A##', excluding Variable type
    sentinel_mask = _is_missing_numeric_sentinel(item_features['value'])
    mask = (
        (
            sentinel_mask |
            (item_features['value'] == '##N/A##')
        ) &
        (item_features['qtype'] != 'Variable')
    )
    df_agg = item_features[mask].groupby('interview__id').agg(
        f__number_unanswered=('value', 'count')
    )
    df_unit[feature_name] = df_unit['interview__id'].map(df_agg['f__number_unanswered']).fillna(0)
    return df_unit

def feat_unit_translation_positions(df_unit, item_features, **kwargs):
    # Relative positions of TranslationSwitched events within each interview.
    # Returns a list of relative positions per interview.

    feature_name = 'f__translation_positions'
    paradata_full = kwargs.get('paradata_full')
    df_unit[feature_name] = np.nan
    if paradata_full is None:
        return df_unit

    trans_mask = paradata_full['event'].isin(['AnswerSet', 'TranslationSwitched'])
    df_trans = paradata_full.loc[trans_mask, ['interview__id', 'order', 'event', 'param']].copy()
    if df_trans.empty:
        return df_unit

    df_trans = df_trans.sort_values(['interview__id', 'order']).reset_index(drop=True)
    df_trans['seq'] = df_trans.groupby('interview__id').cumcount() + 1

    def relative_translation_positions(group):
        total_rows = len(group)
        translation_positions = group.loc[group['event'] == 'TranslationSwitched', 'seq']
        return [pos / total_rows for pos in translation_positions]

    # include_groups=False: 'interview__id' is not needed inside the function; suppresses FutureWarning
    # in pandas 2.2+ where including the grouping column in the passed group is deprecated.
    result = df_trans.groupby('interview__id').apply(
        relative_translation_positions, include_groups=False
    ).reset_index()
    result.columns = ['interview__id', feature_name]

    df_unit[feature_name] = df_unit['interview__id'].map(
        result.set_index('interview__id')[feature_name]
    )
    return df_unit


UNIT_FEATURE_MAP = {
    'number_answered': feat_unit_number_answered,
    'number_unanswered': feat_unit_number_unanswered,
    'translation_positions': feat_unit_translation_positions,
}

def enrich_unit_features(df_unit: pd.DataFrame, item_features: pd.DataFrame, paradata_full: pd.DataFrame, parameters: dict) -> pd.DataFrame:
    """
    Applies feature engineering logic to the unit table.
    paradata_full: all processed events, role=1, interviewing=True (self.df_paradata equivalent).
    Required for f__translation_positions.
    """
    logger.info("Enriching unit features...")
    allowed_features = parameters.get('features', {})

    for feat_key, feat_cfg in allowed_features.items():
        if feat_cfg.get('use', False):
            func = UNIT_FEATURE_MAP.get(feat_key)
            if func:
                logger.info(f"Calculating unit feature: {feat_key}")
                try:
                    df_unit = func(df_unit, item_features=item_features, paradata_full=paradata_full)
                except Exception as e:
                    logger.warning(f"Failed to calculate {feat_key}: {e}")

    return df_unit
