# RISSK: Kedro Pipeline vs Legacy — Full Changelog

This document details every meaningful change between the legacy Ploomber/Python 3.9 pipeline (`rissk/`) and the new Kedro pipeline (`rissk`). Changes are separated into **architectural changes**, **intentional behavioural changes**, and **bug fixes**.

---

## Table of Contents

1. [Environment & Infrastructure](#1-environment--infrastructure)
2. [Architecture Overview](#2-architecture-overview)
3. [Data Ingestion](#3-data-ingestion)
4. [Feature Creation](#4-feature-creation)
5. [Item Scoring](#5-item-scoring)
6. [Unit Scoring](#6-unit-scoring)
7. [Unscored / Missing Features](#7-unscored--missing-features)
8. [Bug Fixes](#8-bug-fixes)

---

## 1. Environment & Infrastructure

| Aspect | Legacy | Kedro |
|---|---|---|
| Orchestrator | Ploomber (`pipeline.yaml`) | Kedro 1.2.0 |
| Python | 3.9 | 3.13 |
| NumPy | 1.26 | 2.3 |
| Pandas | 2.2 | 2.3 |
| SciPy | 1.13 | 1.17 |
| scikit-learn | 1.6 | 1.8 |
| Architecture | OOP class hierarchy with stateful `self.*` DataFrames | Functional pure-function nodes; data flows via Kedro Data Catalog |
| File I/O | `df.to_csv()`, direct `open()` calls, manual pickle | Kedro Dataset abstractions only |
| Configuration | Hardcoded defaults inside classes | `conf/base/parameters.yml` |
| Type hints | `List`, `Dict` from `typing` | Native `list`, `dict` (Python 3.10+ style) via `from __future__ import annotations` |
| Legacy protection | — | All Kedro rewrites are in new `*_kedro.py` files; legacy files are never modified |

---

## 2. Architecture Overview

### Legacy — Three-Level Class Hierarchy

```
FeatureProcessing          (feature_processing.py)   — data loading, base dataframes
    └── ItemFeatureProcessing  (item_processing.py)  — item-level feature & score calculations
            └── UnitDataProcessing (unit_proccessing.py) — unit/responsible scores + global risk
```

State was maintained on the class instance (`self._df_item`, `self._df_unit`, `self._df_resp`). Methods were discovered dynamically via `dir()` and `getattr()` naming conventions (`make_feature_item__*`, `make_score_unit__*`).

### Kedro — Functional Pipeline Graph

```
data_ingestion → feature_creation → rissk_scoring
```

Each pipeline is a DAG of pure functions. DataFrames and parameters flow explicitly as node inputs/outputs. No shared mutable state. Intermediate datasets are persisted to the Data Catalog.

---

## 3. Data Ingestion

### 3.1 Structural Changes

#### Microdata split into two pipeline steps
- **Legacy:** Microdata is loaded and merged with questionnaire metadata in a single step → `microdata.parquet`.
- **Kedro:** Split into two nodes:
  1. `load_raw_microdata_node` — reads raw files, applies `transform_multi` (requires questionnaire data), melts to long format, sets `qnr`/`qnr_version`, applies `normalize_column_name`. Output: `raw_microdata` (`20_INTERIM/microdata_raw.parquet`).
  2. `merge_microdata_questionnaire_node` — merges `raw_microdata` with questionnaire metadata. Output: `microdata` (`30_PROCESSED/microdata.parquet`).
- **Downstream impact:** All downstream pipelines consume `microdata` from step 2, which is structurally identical to the legacy output. `raw_microdata` is a new intermediate dataset not present in legacy.

#### Unzip as a separate node
- **Legacy:** Unzip logic was inlined inside data processing functions.
- **Kedro:** `extract_zip` is its own dedicated node, called optionally before any data processing.

#### Each output dataframe is independently constructed
- **Legacy:** Outputs could share mutable state or be derived from one another.
- **Kedro:** `questionnaire`, `paradata_processed`, and `microdata` are each produced by a dedicated node with no hidden dependencies.

#### Skip/ignore empty files
- **Legacy:** A missing paradata or questionnaire export for a specific version would raise an exception and abort the run.
- **Kedro:** Each load node checks for the presence of `Paradata` or `Tabular` exports per version (both questionnaire and microdata loading use the `Tabular` key — there is no separate Questionnaire or Microdata export type). Four failure modes are handled:
  - **Missing export folder** — logs `WARNING` and skips that version.
  - **File exists but has no data rows** (header-only, valid file) — `pd.read_csv` / `read_stata` succeeds and returns a 0-row DataFrame; the node detects the empty result and logs a `WARNING` (`"file may be empty or corrupt"`). For paradata, the 0-row DataFrame preserves column schema and contributes no rows to the combined output. For microdata, `get_microdata_raw` skips header-only files entirely (returning a column-less empty DataFrame), so the node always warns for empty microdata files.
  - **File exists but is corrupt or unreadable** — the utility function catches the parse exception and returns an empty DataFrame; `get_paradata_raw` and `read_microdata_file` log `ERROR`, while `read_json_questionnaire` (called by `get_questionnaire`) logs `WARNING`. The node then appends the empty DataFrame and logs a `WARNING`. The empty DataFrame contributes no rows to the final output.
  - **Unrecoverable exception in the node itself** — caught by the node's outer `try/except`, logs `ERROR`, and continues to the next version.
- If **all** versions are empty or missing, the combined output for that stage is an empty DataFrame. Downstream nodes detect this and log `ERROR` or `WARNING` before returning early:
  - `process_paradata_node` — logs `ERROR` if `paradata_raw` is entirely empty and returns `pd.DataFrame()`.
  - `build_removed_answers_node` (`feat_answer_removed`) — logs `WARNING` if `paradata_processed` is empty or missing the `'event'` column and returns `pd.DataFrame()`.
  - `create_base_item_table` — logs `ERROR` if `microdata` is entirely empty and returns `pd.DataFrame()`.
  - `create_base_unit_table` — logs `ERROR` if `paradata_full` is entirely empty and returns `pd.DataFrame()`.
  - `calculate_item_scores` and `calculate_unit_scores` — log `WARNING` if their input feature tables are empty and return empty DataFrames without attempting model fitting.
- The pipeline therefore signals all-empty data clearly through `ERROR`/`WARNING` log messages and completes without a cryptic crash.

#### VERSION `[]` includes all available versions
- **Legacy:** `VERSION` in `env.yaml` required explicit version numbers.
- **Kedro:** An empty list `VERSION: []` now means "include all versions found in the survey folder". Explicit version lists still work as before.
  ```yaml
  questionnaires:
    - name: "slchbs_saintlucia_2025"
      VERSION: []  # all available versions
  ```

#### Questionnaire no longer loaded inside paradata ingestion
- **Legacy:** Paradata loading was coupled with questionnaire metadata (`answer_sequence`, `n_answers`).
- **Kedro:** Questionnaire is loaded independently. `answer_sequence` and `n_answers` are now correctly populated via the linked category data from the questionnaire JSON — in legacy these columns were always empty because categories in Excel files were never matched to the questionnaire (extension mismatch + dashes in JSON names).

#### Processed paradata has no `index` column
- **Legacy:** `paradata_processed` carried an `index` column (redundant row numbering).
- **Kedro:** The `index` column is dropped; `reset_index(drop=True)` is used after concatenation.

#### Unzip limitation
- The `extract_zip` node only works on **direct Survey Solutions downloads**. On macOS, re-zipping a folder introduces an extra directory layer that breaks the hardcoded path resolution. Re-zipped files must be extracted manually.

### 3.2 Intentional Behavioural Changes

#### `active_mask` question_scope — filter moved into feature creation
- **Legacy (OOP):** Filled all NaN `question_scope` values with `''` globally via `fillna('')`, then filtered `question_scope in [0, '']`. This correctly let pause events (`Resumed`, `Restarted`) and interview-level events (`InterviewCreated`) through because they have no question scope (NaN → `''` after fill).
- **Legacy (Ploomber):** The `fillna('')` step was missing. The `question_scope in [0, '']` filter dropped all pause and interview-level events. No pause or interview creation events reached `paradata_active` — a silent data loss.
- **Kedro:** The `paradata_active` dataset was removed entirely. Feature creation functions consume `paradata_processed` directly and apply per-event-type filters inline:
  - `AnswerSet`, `AnswerRemoved`, `CommentSet` → filter `question_scope == 0`
  - `Resumed`, `Restarted`, `InterviewCreated` → no `question_scope` filter (these events have `NaN` scope and are included as-is)
  This correctly includes pause events in unit-level time features while excluding supervisor-scoped question events.

#### `limit_unit` / `filter_by_consent` — now active before scoring
- **Legacy (Ploomber):** The `filter_by_consent` function existed in `feature_processing.py` but was commented out in the Ploomber pipeline (`10_process_paradata`). The `limit_unit` config option had no effect.
- **Kedro:** `filter_by_consent` is an explicit node in the `rissk_scoring` pipeline, running before `calculate_item_scores`. It is configured via `filter_var` in `globals.yml` (per questionnaire). The value must be a single-key dict `{variable_name: answer_value}` — interviews where that variable in paradata does **not** match the answer value are dropped from `item_features`, `unit_features`, and `removed_answers` before scoring. Set to `null` (default) to skip filtering. If `filter_var` is set but matches 0 interviews, the pipeline raises a `ValueError` rather than silently producing empty results.

#### Microdata `value` column normalization (integer floats)
- **Legacy:** `value` column contained mixed formats — e.g., `"1"` and `"1.0"` for the same logical integer value (artefact of Pandas NaN-forced float conversion).
- **Kedro:** Explicitly normalizes: if a float is integer-equivalent (`x.is_integer() == True`), it is converted to `int` before stringification. `"1.0"` → `"1"`. Also applied inside list-strings: `"[1.0, 2.0]"` → `"[1, 2]"`.
- **Downstream impact:** No current code in the pipeline performs exact string matching against numeric values in the `value` column — all comparisons use non-numeric sentinels (`'##N/A##'`, `-999999999`) or type-based checks (`pd.isnull`, `isinstance`). This note is a caution for future extensions: any new code performing exact string matching against numeric values (e.g., `val == "1.0"`) should use type-safe comparisons instead.

### 3.3 Utility Function Changes (`import_utils_kedro.py`)

#### `extract_zip` — complete rewrite
- **Legacy:** Read the entire zip into memory (`BytesIO`) before extracting — inefficient and risky for large datasets.
- **Kedro:** Streams extraction directly from disk. Adds **directory traversal security checks** (prevents zip-slip attacks).

#### `get_survey_info`
- **Legacy:** Relied on implicit list appends and looser dictionary construction.
- **Kedro:** Uses `setdefault` for cleaner dictionary building. Added explicit `try-except` around filename parsing so one malformed file does not crash the whole pipeline.

#### `get_questionnaire` / `read_json_questionnaire`
- **Legacy:** Used `os.path.join` and bare `open()`.
- **Kedro:** Fully converted to `pathlib.Path`. Added explicit UTF-8 encoding on all JSON reads (required for cross-platform Python 3.13 compatibility).

#### `get_paradata` / `read_paradata`
- **Legacy:** `read_paradata` uses `os.path.join` and bare `open()` with no encoding hint; `pd.read_csv` has no `low_memory` flag. Parameter splitting uses an unguarded two-step `str.split` / `str.rsplit` on the `parameters` column — if the column is absent or a row has no `||` delimiter the assignment raises.
- **Kedro:** `read_paradata` converts to `pathlib.Path` and adds `encoding='utf-8'` (matching the questionnaire change) and `low_memory=False` to suppress `DtypeWarning` on mixed-type data. The parameter splitting in `get_paradata_raw` still uses `str.split` / `str.rsplit` but wraps each step with a `.shape[1] == 2` guard and a `.notna().any()` check before the answer split, so malformed or missing parameter values no longer raise.

#### `get_microdata`
- **Legacy:** `drop_list` and `is_valid` are both local nested definitions inside `get_microdata`. `is_valid` accepts **any non-empty list unconditionally** (`isinstance(value, list): return True`) and rejects only empty strings and NaN scalars. Filtering uses `.apply(is_valid)`.
- **Kedro:** `get_microdata` is split into `get_microdata_raw` (transform + filter + stringify) and `merge_microdata_questionnaire` (questionnaire metadata join). Filtering still uses `.apply(is_valid_fast)` — not vectorized. `is_valid_fast` is more defensive: handles `None`, empty lists/tuples/arrays, and uses a try/except around `pd.isna` to avoid `TypeError: boolean value of NA is ambiguous` on Pandas 2.x nullable types. Key behavioural difference: `is_valid_fast` rejects lists where every element is NaN or empty (rather than accepting all non-empty lists), which is what drives the TextListQuestion all-NaN filtering in §3.3 Case A.

#### `transform_multi` — MultyOptionsQuestion (linked) bug fix
- **Legacy:** For linked `MultyOptionsQuestion`, the comparison `sub = [ele if ele != [] else '##N/A##' for ele in sub]` evaluated to `True` for integers and `False` for objects in NumPy < 2.x. The result was that a list like `[[3, -999..., -999, ...], [1, -999..., ...]]` collapsed to `[##N/A##, ##N/A##, ...]` — discarding the one valid answer per row.
- **Kedro:** Fixed to produce `[[3], [1], ...]` — preserving the valid answer and correctly filtering out missing-coded values.

#### `TextListQuestion` disabled-question filtering

Two distinct cases arise from how Survey Solutions exports `TextListQuestion` data. Both are handled differently between legacy and Kedro.

**Case A — Disabled question (questionnaire logic prevented it from being shown):**
- Export: all columns are system missing (NaN). NaN passes the `!= '##N/A##'` mask in `transform_multi`, so NaN entries are collected into the list and `remove_unset_value` preserves them → `[nan, nan, nan, ...]`.
- **Legacy:** `is_valid` accepts any list unconditionally → row kept in `df_item` with `value = [nan, nan, nan, ...]`. `~pd.isnull(value)` is `True` (a list object is not null) → incorrectly counted as **answered** in `f__number_answered`.
- **Kedro:** `is_valid_fast` rejects all-NaN lists → row absent from `df_item` → not counted. Correct behaviour.

**Case B — Unanswered but enabled question (shown to the interviewer, left blank):**
- Export: all columns are `'##N/A##'`. The `!= '##N/A##'` mask blocks all columns → nothing is appended → `x` stays `[]`. The outer dispatch `if x else ...` short-circuits on the falsy `[]` before `remove_unset_value` is ever called.
- **Legacy:** Produces `float('nan')` → dropped by `is_valid` → row absent from `df_item` → **not counted in `f__number_unanswered`**. This is a bug: the question was shown and left blank, so it should be counted as unanswered.
- **Kedro (fixed):** The dispatch now returns `'##N/A##'` for `transformation_type == 'list'` when the list is empty: `'##N/A##' if transformation_type == 'list' else float('nan')`. The scalar `'##N/A##'` survives `is_valid_fast`, the row lands in `df_item`, and is counted in `f__number_unanswered`. See also Bug 18.
- **Practical impact:** No interviews with unanswered-but-enabled `TextListQuestion` items were found in the test data of over 2000 interviews. The fix is correct in principle but is expected to have no real-world effect in the surveys tested.

**Net effect on features (both cases combined):**
- `f__number_answered` is lower in Kedro (Case A: disabled questions no longer counted as answered). No disabled `TextListQuestion` rows were found in the test data of over 2000 interviews, so the effect may be minor in practice.
- `f__number_unanswered` is higher in Kedro (Case B: unanswered-but-enabled questions now correctly counted). No such cases were found in the test data of over 2000 interviews — this fix is expected to have no real-world effect in practice.
- `s__pause_count = f__pause_count / f__number_answered` — smaller denominator in Kedro → higher value for affected interviews (Case A only). No such interviews were found in the test data of over 2000 interviews.
- `s__number_answered` is directly lower in Kedro (Case A, same source as `f__number_answered`). No such cases were found in the test data of over 2000 interviews, so the effect is expected to be minor in practice.
- All other features are unaffected: numeric/position/selection features filter by question type and exclude `TextListQuestion`; time and `f__answer_changed` features operate on paradata events, not `df_item` rows.

#### `MultyOptionsQuestion` ComboBox category matching fix
- **Legacy (`file_process_utils.py`):** `get_categories` indexes the categories dict by `file.name` (full filename, e.g. `"mycat.xlsx"`). `update_df_categories` then looks up `row['CategoriesId']` directly — but the questionnaire JSON stores `CategoriesId` as the stem only (e.g. `"mycat"`). Because `"mycat" != "mycat.xlsx"`, the lookup **always fails** — no ComboBox question ever gets `n_answers` or `answer_sequence` populated.
- **Kedro (`file_process_utils_kedro.py`):** Two fixes:
  1. `get_categories` now keys by `file.stem` (no extension), matching the format of `CategoriesId` in the JSON.
  2. `update_df_categories` strips unicode dash characters (`unicodedata.category(c) == 'Pd'`) from both the `CategoriesId` value and the dictionary keys before comparing, handling cases where the JSON uses a different unicode dash character than the filename.

---

## 4. Feature Creation

### 4.1 Structural Changes

#### OOP to functional architecture
- **Legacy:** Class methods (`make_feature_item__*`, `make_feature_unit__*`) mutated `self._df_item` / `self._df_unit` in-place. Discovery via dynamic `getattr`.
- **Kedro:** Stateless functions in `feature_processing_kedro.py`. Feature dispatch uses an explicit dictionary (`ITEM_FEATURE_MAP`, `UNIT_FEATURE_MAP`) instead of `getattr`. Easier to debug; no hidden side effects.

#### Explicit pipeline nodes
Four dedicated nodes replace the monolithic class initialization:
1. `create_base_item_table_node` — microdata + paradata_processed → base item table
2. `create_base_unit_table_node` — paradata_processed → base unit table
3. `enrich_item_features_node` — base item table + paradata_processed → item features
4. `enrich_unit_features_node` — unit base + item features + paradata_processed → unit features
5. `build_removed_answers_node` — paradata_processed → removed_answers (for `answer_removed` scoring)

#### Active paradata node removed
- **Legacy (Ploomber):** A separate `filter_active_paradata` step produced `paradata_active` as a distinct dataset consumed by all feature nodes.
- **Kedro:** The `paradata_active` intermediate dataset no longer exists. All feature creation nodes receive `paradata_processed` directly and apply the correct per-event-type filters inline (see §3.2).

#### `df_item` carries questionnaire name and version columns
- **Legacy:** `df_item` had no questionnaire identifier.
- **Kedro:** Two extra columns (`qnr`, `qnr_version`) are propagated from microdata into `df_item` for informational purposes (logging, output labeling, joining results across questionnaires). Scoring is always run independently per questionnaire.

### 4.2 Intentional Behavioural Changes

#### `f__first_decimal` renamed to `f__first_decimals`
- The feature always extracted the **first two decimal digits**, but was misnamed `f__first_decimal` (singular) in legacy.
- **Kedro:** Renamed to `f__first_decimals` to accurately reflect what is computed.

#### Numeric sentinel filter for `f__numeric_response`, `f__first_digit`, `f__last_digit`, `f__first_decimals`
- **Legacy:** Questions of type `NumericQuestion` can have predefined special-meaning answers (e.g. `NONE=0`, `NO PHONE NUMBER=9999999`, `Don't know=-99`). These were treated as real numeric responses in all numeric features.
- **Kedro:** A sentinel detection flag is introduced. When enabled (default: `True` for all numeric features), values that appear in the question's `answers` list (predefined option set) are excluded before computing the feature. This prevents sentinel codes from polluting first-digit distributions, decimal patterns, and numeric response statistics.
- **Important note:** The filter flag for `f__numeric_response` and `f__first_digit` must be set to the same value, because `f__first_digit` score calculation uses `f__numeric_response` for the Benford frequency/magnitude filters. `f__first_digit` itself is only used to count unique leading digits.

#### `f__first_digit` — interval `[-1, 1]` mapped to first digit `0`
- Values where `|val| < 1` (e.g. `0.12`) cannot have a meaningful first significant digit under Benford's Law. Both legacy and Kedro assign `first_digit = 0` for this range. This behaviour is preserved explicitly in Kedro.

#### `f__answer_selected` — list parsing via `ast.literal_eval`
- **Legacy:** Uses a bare `isinstance(val, list)` check — returns NaN if the value is not already a Python list object (see §4.3 for the bug this causes).
- **Kedro:** Applies `ast.literal_eval(str(val))` before checking length. This handles both native list objects and string-serialized lists (e.g. `"[1, 2]"`), and also wraps in try/except to return NaN on unparseable values.

#### `f__gps` — minor robustness improvement only
- **Legacy:** Splits value string by `,`, assigns named columns, sets `f__gps = True/False` boolean flag plus `f__gps_latitude`, `f__gps_longitude`, `f__gps_accuracy`.
- **Kedro:** Identical behaviour — `f__gps = True/False` boolean flag is still set. The only change is a defensive `if gps_data.shape[1] >= 3` guard before reading the coordinate columns, preventing an `IndexError` if the CSV split produces fewer columns than expected.

### 4.3 Legacy Bugs Fixed in Kedro — Feature Creation

The following items were **broken in the legacy pipeline** and are **corrected in Kedro**. They are not "remaining differences" — the Kedro values are the correct ones.

#### `f__answer_position` — always returned NaN in legacy
- **Legacy (both OOP and Ploomber):** `get_microdata` unconditionally converts all values to strings (`astype(str)`) before returning. `make_feature_item__answer_position` then checks `row['value'] in row['answer_sequence']` where `value` is a string (e.g. `"3"`) and `answer_sequence` is a list of integers (e.g. `[1, 2, 3, 4]`). The membership check always fails — `f__answer_position` is always NaN in both legacy pipelines.
- **Kedro:** Three changes fix the lookup: (1) `answer_sequence` is parsed back to a Python list via `ast.literal_eval(str(...))`, (2) `value` is converted to numeric via `pd.to_numeric(..., errors='coerce')`, and (3) if all sequence values are integers and the numeric value is integer-equivalent, it is cast to `int` to ensure type-safe membership testing. Results verified manually against a subset of answers across four surveys.

#### `f__answer_selected` — always returned NaN in legacy
- **Legacy (both OOP and Ploomber):** `get_microdata` unconditionally applies `astype(str)` to the `value` column before returning, converting Python list objects (e.g. `[1, 2, 3]` from `transform_multi`) into their string representations (e.g. `"[1, 2, 3]"`). By the time `make_feature_item__answer_selected` runs, `isinstance(value, list)` is always `False` — no parquet round-trip is needed for this to fail. The legacy function never extracted a selection count.
- **Kedro:** `value` is converted back to a list via `ast.literal_eval(str(val))` before checking length. Also adds an explicit `n_answers > 0` guard (replaces the missing `is_linked` flag check) before dividing by `n_answers`.

#### `f__answer_changed` — `yes_list` changes ignored for `MultyOptionsQuestion` with yes/no view
- **Legacy:** For `MultyOptionsQuestion` with `yes_no_view == True`, the yes_list change check was immediately overwritten by the no_list check — two consecutive `.loc` assignments on the same `yesno_mask`. Only removal of `no` answers was ever counted; changes to `yes` answers were silently discarded.
- **Kedro fix:** Both checks are combined with bitwise OR before a single assignment: `yes_changed | no_changed`. A change in either list is now correctly counted. `multi_mask` is also narrowed to `qtype == 'MultyOptionsQuestion'` explicitly (no practical effect in legacy since `TextListQuestion` rows have `NaN` for `yes_no_view` which does not match `== False`, but makes intent clear).
- **Impact:** `f__answer_changed` will be higher in Kedro for interviews with `MultyOptionsQuestion` where `yes` answers were changed independently of `no` answers.

#### `f__number_answered` / `f__number_unanswered` — sentinel comparison against string values
- **Legacy (both OOP and Ploomber):** `get_microdata` stringifies the entire `value` column via `astype(str)` before returning. The sentinel comparisons `value == -999999999` and `value != -999999999` then compare strings to an integer, which is always `False`/`True` respectively. As a result: (1) `f__number_answered` **includes** sentinel-coded unanswered items (they pass the `!= -999999999` guard), (2) `f__number_unanswered` **never** counts them (the `== -999999999` check never matches a string).
- **Kedro:** Uses `_is_missing_numeric_sentinel`: `pd.to_numeric(values, errors='coerce').eq(-999999999)`. This coerces the string `"-999999999"` to the float `-999999999.0` before comparing, which correctly identifies the sentinel regardless of string/numeric type.
- **Downstream impact:** `f__number_unanswered` is higher in Kedro (now correctly counts sentinel-coded unanswered items); `f__number_answered` is lower.

### 4.4 Missing Features (present in legacy, absent in Kedro)

The following features exist in the legacy `ITEM_FEATURE_MAP` equivalent but are **entirely absent** from the Kedro feature map. If enabled in `parameters.yml`, they silently produce no output (the map lookup returns `None` and is skipped without error):

| Feature | Legacy Location |
|---|---|
| `f__comment_length` | `feature_processing.py` |
| `f__comment_set` | `feature_processing.py` |
| `f__answer_removed` (item-level) | `item_processing.py` |

Missing from `UNIT_FEATURE_MAP`:

| Feature | Legacy Location |
|---|---|
| `f__translation_positions` | `feature_processing.py` |

---

## 5. Item Scoring

### 5.1 Structural Changes

#### Scoring is enforced per questionnaire at the pipeline level
- **Legacy:** Scoring ran over whatever data was loaded in a single run. In practice this was typically a single questionnaire, but the code did not enforce this.
- **Kedro:** One questionnaire is configured at a time via `questionnaire.name` in `conf/base/globals.yml`. All catalog paths resolve to that questionnaire's data folder. To run a different questionnaire, the `globals.yml` entry is changed and the pipeline re-run. Per-questionnaire isolation is enforced through configuration, not through automatic multi-questionnaire dispatch.

#### OOP `make_score__*` → pure functions
Each legacy `make_score__*` method is now a standalone function in `item_processing_kedro.py`, accepting `df_item` and parameters, returning a modified `df_item`. No `self` attributes.

#### Score initialization: `0` → `np.nan`
- **Legacy:** Pre-filters rows with non-null feature values; initializes score column to `0` (explicit "no anomaly") for all returned rows.
- **Kedro:** Works on the full `df_item`. Initializes score column to `np.nan`. Only rows belonging to variables that pass the frequency/uniqueness threshold receive `0`/`1` from the model. All other rows remain `NaN`.
- **Semantic change:** `NaN` means "this item was not evaluated" rather than "no anomaly detected". This distinction is preserved at item level but resolved to `0` at unit level via `.fillna(0)` after `groupby().mean()`.

#### `s__answer_removed` — moved from item-level to unit-level
- **Legacy:** `make_score__answer_removed` calls `get_feature_item__answer_removed`, which reads directly from `self.df_paradata` (not `df_item`). It counts `AnswerRemoved` events per `(interview__id, responsible, variable_name, qnr_seq)` — including events for items no longer present in microdata. The result is an item-level DataFrame scored by ECOD per variable.
- **Kedro:** `calculate_answer_removed_unit_score` also reads directly from `paradata_full` (equivalent coverage). The difference is architectural: instead of producing item-level scores that are later aggregated, Kedro computes the unit-level score directly — returning a Series indexed by `interview__id` that is mapped into `df_unit`, bypassing the item table entirely.

### 5.2 Intentional Behavioural Changes

#### `filter_variable_name_by_frequency` — stricter NaN filtering in Kedro
- **Legacy:** Counts unique values as `len(group[feature_name].unique())` and frequency as `len(group) > frequency`. Neither excludes NaN from the count, so NaN counts as a unique value and inflates the frequency.
- **Kedro:** Filters NaN values before computing both `nunique()` and `count()`. Variables with exactly 100 non-NaN entries pass in Kedro (the boundary is inclusive) but fail in legacy (legacy uses `>`, so 100 entries fail and 101 pass). For example, a variable with exactly 100 entries is evaluated in Kedro but not in legacy.

#### `s__first_digit` — frequency and magnitude filter changed
- **Legacy:** Frequency filter uses all non-NaN `f__numeric_response` values (including zeros). A variable with ~100 entries of `1`, `10`, and `1000` would pass.
- **Kedro:** Frequency filter uses `f__first_digit` excluding zeros (values in `[-1, 1]` map to first digit `0` and are excluded from the frequency count). Magnitude filter also excludes `|val| < 1` from the order-of-magnitude evaluation.
- **Impact:** Variables that are mostly zeros or have a narrow magnitude range are excluded in Kedro but may have been included in legacy. The effect of this is that fewer variables get scored. Overall it is likely minimal as the there are further filters in the Benford scoring functions.

#### `s__first_digit` — score mapped to entire `df_item`
- **Legacy:** Scores were mapped back only to the non-NaN entries in `df_item` (filtered subset).
- **Kedro:** Scores are mapped back to the **entire `df_item`**. NaN entries for a variable can receive a score if they belong to a `(responsible, variable_name)` pair that was evaluated. This has no effect on unit/responsible aggregation (NaN items don't contribute to means) but produces different item-level score tables.

#### `f__first_decimals` COF warning suppression
- **Legacy:** No warning suppression.
- **Kedro:** COF on `f__first_decimals` produces expected `RuntimeWarning`s from `pyod.models.cof` (divide-by-zero in chaining distance) and `numpy._core._methods` (overflow in squared-distance arithmetic) when the data contains many identical values (e.g. `x.00`). These are handled gracefully by COF internally. `warnings.filterwarnings('ignore', ...)` is applied to both modules for the duration of `fit`/`predict` to keep the log clean. `INNE` and `IForest` were tested as alternatives but COF produces better results, so COF is retained despite the warnings. Adding jitter to COF was also tested but produced incorrect results — common values such as `0` were incorrectly flagged as outliers because jitter breaks the tied-distance structure that COF relies on to score high-frequency decimal patterns.

#### Responsible score guard — minimum 2 columns with variance
- **Legacy:** Constant columns are removed before fitting PCA (`df_resp.loc[:, df_resp.nunique() != 1]`), but there is no guard against 0 or 1 varying columns remaining. With 0 columns, `StandardScaler.fit_transform` raises. With 1 column, PCA produces scores but they are based on a single component — reconstruction error has no minor eigenvectors to measure against, so all scores are effectively identical.
- **Kedro:** After dropping constant columns, an explicit `if df_pca_input.shape[1] < 2:` guard sets `responsible_score = NaN` and returns early (also applies when no columns remain at all). A second guard in `combine_unit_scores` (`if resp_score_series.notna().any() and resp_score_series.nunique() > 1:`) skips the responsible score multiplication when scores are all-NaN or constant, preserving the IForest-derived unit scores as-is. A warning is logged.
- **Impact:** Surveys where all score columns are constant, or only one varies (e.g. listing surveys with very little interviewer variation), no longer raise or produce misleading responsible scores.

#### `answer_hour_set` high-frequency correction — guarded
- **Legacy:** `df.loc[df[score_name] == 0]['frequency'].min()` — if all rows are flagged as anomalies, this returns `NaN`, so the correction silently never fires.
- **Kedro:** Explicit guard `if inlier_mask.any():` — when all rows are anomalies, the `if` branch is skipped. ECOD predictions are preserved as-is. Silent failure is eliminated.

#### GPS aggregation — per variable → per interview
- **Legacy:** `make_score__gps` calls `get_clean_pivot_table`, which pivots `df_item` on `variable_name`, producing multi-level columns like `f__gps_latitude_gps_q1`, `f__gps_latitude_gps_q2`. `replace_with_feature_name` then renames all of them to `f__gps_latitude` — creating duplicate column names. Any column access (`data['f__gps_latitude']`) returns a DataFrame instead of a Series, and the subsequent `lat_lon_to_cartesian` call crashes. Legacy **only works correctly if there is exactly one GPS variable**.
- **Kedro:** Keeps each `(interview__id, variable_name)` row as a distinct GPS point — no grouping, no mean. All GPS points across all GPS variables are pooled together into the KDTree/COF/LOF model, and scores are written back to their original rows. Multiple GPS variables are handled correctly; each variable's coordinates contribute independently to the outlier model.

#### GPS — `s__gps_extreme_outlier` latitude check overwritten by longitude check
- **Legacy intent:** Flag a point as an extreme outlier only when **both** `latitude == 0` and `longitude == 0` (the comment reads "0,0 as coordinates" — a failed GPS fix).
- **Legacy bug:** Three consecutive assignments: first sets the column to `0`, second sets it to `1` where `latitude == 0`, third sets it to `1` where `longitude == 0` — **overwriting** the latitude result entirely. Only `longitude == 0` is ever actually flagged; a point at `(lat=0, lon=5)` is incorrectly flagged as an outlier, while a point at `(lat=5, lon=0)` is also incorrectly flagged. A point at `(lat=0, lon=0)` is flagged correctly but only by coincidence.
- **Kedro (fixed):** Single vectorised expression: `(latitude == 0) & (longitude == 0)`. A point is flagged only when **both** coordinates are zero — matching the original intent.
- **Impact:** Legacy flags any point with `longitude == 0` (regardless of latitude) and misses points where only `latitude == 0`. Kedro correctly flags only true `(0, 0)` fixes.

#### GPS — accuracy and search radius divided by `1e6` instead of `1e3`
- **Legacy:** `data['accuracy'] = data['f__gps_accuracy'] / 1e6` and `radius = 10 / 1e6`. `lat_lon_to_cartesian` returns coordinates in kilometres (Earth radius = 6371 km). Accuracy (in metres) should be converted to km by dividing by `1e3`. Using `1e6` instead makes both the per-point accuracy term and the base search radius ~1000× too small — effectively zero. `s__gps_proximity_counts` is almost always `0` in legacy regardless of how close the GPS points are.
- **Kedro:** `data['accuracy'] = data['f__gps_accuracy'].fillna(0) / 1e3` and `radius = 10 / 1e3`. Correct metres → km conversion. Neighbours within 10 m + device accuracy are counted properly.
- **Impact:** `s__gps_proximity_counts` will be substantially higher in Kedro for surveys where interviewers collected GPS points at the same or nearby locations.

#### GPS — extreme outlier edge case
- **Legacy:** When all GPS points are extreme outliers, the COF/LOF model may throw an exception or produce unpredictable results.
- **Kedro:** Sets score to `NaN` (evaluation not possible) — handled cleanly.

#### GPS — single valid point (too few to fit) edge case
- **Kedro (before fix):** `calculate_gps_score` fit the COF/LOF spatial-outlier model whenever `mask.sum() > 0` (at least one non-extreme GPS point). COF/LOF are neighbour-based and need **≥ 2** points; with **exactly one** valid GPS point pyod computes `n_neighbors_ = 0` and raises `ValueError: … is set to 0. Not in the range of [1, 1]`, aborting that questionnaire's entire run. Observed on a small questionnaire (`srb_roma_wb6_26`) that had a single GPS interview.
- **Kedro (fixed):** The model is fit only when there are **≥ 2** valid points. With 0 or 1, `s__gps_outlier` is left `NaN` ("no evaluation possible"), matching the all-extreme-outlier branch above. `s__gps_outlier` is now initialised to `NaN` up-front so the score column always exists for the downstream merge even when fitting is skipped.
- **Impact:** Questionnaires with ≥ 2 valid GPS points are unaffected (identical scores). A questionnaire with a single GPS point now completes with `s__gps_outlier = NaN` for those rows instead of crashing. Relevant for multi-questionnaire surveys where one small questionnaire would otherwise fail the whole run.

#### Entropy normalization fix
- **Legacy:** `calculate_entropy` divides by `np.log2(unique_values)`.
- **Kedro:** Divides by `np.log(unique_values)` (natural log). Raw entropy values in Kedro are correctly normalized to [0, 1]; legacy values are in [0, ln(2)] ≈ [0, 0.693]. Since entropy is only used in a relative median comparison (`x < median − 0.5 × median`), the ln(2) factor cancels and no responsibles are flagged differently. Raw entropy values differ.

#### `s__last_digit` — not implemented
- **Legacy:** `make_score__last_digit` was commented out.
- **Kedro:** Not implemented (consistent with legacy).

#### `s__answer_removed` — fallback path undercounts (Kedro)
- **Primary path:** `calculate_answer_removed_unit_score` reads from `paradata_full` — complete coverage including deleted items.
- **Fallback path (when `removed_answers` is `None`):** Falls back to `df_item`-based aggregation using a `how='left'` join on the microdata item table. Items deleted from microdata post-collection are absent from `df_item`, so their `AnswerRemoved` events are silently dropped — systematic undercount.
- **Impact:** Only relevant if `removed_answers` dataset is unavailable. The primary path is equivalent to legacy. In practice this should never trigger.

### 5.3 Scoring Coverage

| Score | Kedro Implementation | Notes |
|---|---|---|
| `s__answer_hour_set` | ✅ Implemented | Guarded high-freq correction |
| `s__sequence_jump` | ✅ Faithful port | — |
| `s__first_decimal` | ✅ Faithful port | — |
| `s__answer_changed` | ✅ Faithful port | Feature bug fixed (see §4.3); scoring logic identical |
| `s__answer_removed` | ✅ Implemented (unit-level only) | Direct from `paradata_full`; no item-level score |
| `s__answer_position` | ✅ Faithful port | — |
| `s__answer_selected_lower/upper` | ✅ Faithful port | Intermediate `s__answer_selected` dropped |
| `s__answer_duration_lower/upper` | ✅ Faithful port | — |
| `s__single_question` | ✅ Faithful port | — |
| `s__multi_option_question` | ✅ Faithful port (bug fixed) | See Bug Fix §8.2 |
| `s__first_digit` | ✅ Faithful port | — |
| `s__gps_proximity_counts`, `s__gps_outlier`, `s__gps_extreme_outlier` | ✅ Implemented (bugs fixed) | See §5.2: accuracy `/1e6` bug, extreme-outlier overwrite bug, aggregation change |
| `s__last_digit` | ❌ Not implemented | Commented out in legacy too |

---

## 6. Unit Scoring

### 6.1 Structural Changes

#### OOP `make_score_unit__*` → pure function nodes
All unit-level score methods have been ported to pure functions in `unit_processing_kedro.py`. Aggregation and global modeling logic (IForest, PCA, windsorization) are now explicit node functions.

#### `windsorize_95_percentile` — non-mutating
- **Legacy:** No `.copy()` call; mutates the input DataFrame.
- **Kedro:** Uses `df_out = df.copy()` — input is never mutated. Adds `is_numeric_dtype` guard before operating.

### 6.2 Intentional Behavioural Changes

#### `s__pause_count` — division by zero
- **Legacy:** No zero-division guard; produces `NaN` or `inf` when `f__number_answered == 0`.
- **Kedro:** `np.where(f__number_answered != 0, ..., 0)` — returns `0` when `f__number_answered == 0`.

#### `s__pause_duration` — division by zero
- **Legacy:** No zero-division guard; produces `NaN`/`inf` when `f__total_elapse == 0`.
- **Kedro:** Returns `0` when `f__total_elapse == 0`.

#### `s__answer_hour_set` — missing `fillna(0)` in legacy
- **Legacy:** Interviews not appearing in the grouped data (no hour-set events) get `NaN`.
- **Kedro:** `fillna(0)` applied uniformly. Interviews with no hour-set events get `0`.

#### Responsible score variance guard
- **Legacy:** Always multiplies by `responsible_score` even when it is constant (all-zero), which causes MinMaxScaler to produce `NaN` for all rows.
- **Kedro:** Guards with `if resp_score_series.nunique() > 1:`. If `responsible_score` is constant, the multiplication is skipped and a warning is logged. IForest-derived scores are preserved.

#### ECOD on `f__total_elapse` — NaN rows
- **Legacy:** Fits ECOD on all rows, including any `NaN` in `f__total_elapse` — may error or produce incorrect scores.
- **Kedro:** Filters to `valid_mask` before fitting ECOD; NaN rows are handled cleanly.

#### `s__time_changed`, `s__total_duration`, `s__days_from_start` — missing feature column guard
- **Legacy:** No existence check — raises `KeyError` if the required feature column is absent from `df_unit` (e.g. a survey type that produces no paradata events for that feature). The exception is caught by the `df_unit_score` property loop with a generic `print` warning; the actual error is discarded.
- **Kedro:** Explicit `if 'f__...' in df.columns:` guard — skips cleanly and emits a warning.

#### `windsorize_95_percentile` assignment (pandas ≥ 2.0 compatibility)
- **Legacy:** `self._df_unit['unit_risk_score'] = windsorize_95_percentile(self.df_unit[['unit_risk_score']].copy())` — assigns a DataFrame to a Series column, which raises `ValueError` in pandas ≥ 2.0.
- **Kedro:** Correctly extracts the Series: `df_unit['unit_risk_score'] = windsorize_95_percentile(df_unit[['unit_risk_score']])['unit_risk_score']`.

#### Responsible score — `_df_resp` granularity preserved
- **Legacy:** `make_responsible_score` replaces the entire `_df_resp` with the grouped result (`self._df_resp = self._df_resp.groupby(...).mean()`), permanently losing per-interview rows.
- **Kedro:** Operates on a copy and merges back, preserving original granularity.

### 6.3 NaN vs 0 Divergence — Frequency-Filtered Scores

The `0` (legacy) vs `NaN` (Kedro) item-score initialization only materially affects scores that use `filter_variable_name_by_frequency`. Items belonging to low-frequency variables receive `0` in legacy and `NaN` in Kedro. At unit level, `groupby().mean()` skips NaN by default, so the unit mean denominator differs: legacy includes those zero-scored items in the mean; Kedro excludes them.

**Scores with frequency filter — unit/responsible means can diverge:**

| Score | Aggregation | Filter threshold | Divergence risk |
|---|---|---|---|
| `s__sequence_jump` | unit mean | 100 records, 3 unique | Medium — jumps are sparse, many variables fail |
| `s__first_decimal` | unit mean | 100 records, 3 unique | Medium — only numeric questions |
| `s__answer_changed` | unit mean | 100 records, 1 unique | Lower — low bar, most variables pass |
| `s__answer_selected_lower/upper` | unit mean | 100 records, 3 unique | Medium |
| `s__answer_duration_lower/upper` | unit mean | 100 records, 3 unique | Medium |
| `s__answer_position` | responsible mean | 100 records, 3 unique | Medium |
| `s__single_question` | responsible mean | 100 records, 3 unique | Medium |
| `s__multi_option_question` | responsible mean | Legacy: 100 records only (no unique filter); Kedro: 100 records + 3 unique | Medium — Kedro additionally excludes variables with < 3 unique answer combos |
| `s__first_digit` | responsible mean | 100 records, 3 unique + 3-magnitude filter | **High** — strictest filter; most numerics excluded |

`s__first_digit` carries the highest risk for **responsible scoring / PCA** because its filter is strictest. `s__sequence_jump` and `s__answer_selected/duration` carry the highest risk for **unit scoring / IForest** because sequence jumps are naturally sparse.

`s__answer_removed` is excluded from this table: in Kedro it is computed directly at unit level from `paradata_full` without going through item-level scoring at all (see §5.1). The NaN/0 item-initialization divergence mechanism does not apply; any difference in values is due to the architectural change, not score initialization.

**Scores without frequency filter — unit means are equivalent:**
`s__answer_hour_set`, `s__time_changed`, `s__total_duration`, `s__days_from_start`, `s__total_elapse_lower/upper`, `s__pause_duration`, `s__pause_count`, `s__number_answered`, `s__number_unanswered`, `s__gps_*`.

---

### 6.4 Summary — Scenarios Producing Different Outputs

| Scenario | Legacy output | Kedro output |
|---|---|---|
| Interview with no `answer_hour_set` events | `s__answer_hour_set = NaN` | `= 0` |
| `f__total_elapse == 0` | `s__pause_duration = NaN/inf` | `= 0` |
| `f__number_answered == 0` | `s__pause_count = NaN/inf` | `= 0` |
| All `responsible_score` constant | `unit_risk_score = NaN` (all rows) | Preserved from IForest |
| NaN rows in `f__total_elapse` | ECOD fit may include NaN | ECOD fit excludes NaN |
| pandas ≥ 2.0 windsorize assignment | `ValueError` | Correct |
| Survey missing feature column (e.g. no GPS) | `KeyError` caught with generic print; error discarded | Clean skip with warning |
| First digit Benford with zeros in `f__numeric_response` | Included in sample | Excluded from sample |
| `s__answer_removed` with `removed_answers = None` | Full coverage from paradata | Systematic undercount (fallback to `df_item`) |
---

## 7. Unscored / Missing Features

The following features are enabled (`use: true`) in `parameters.yml` but are **never consumed by any Kedro scoring function**. They compute silently and their values do not appear in `item_scores` or `unit_risk_score`:

| Feature | Calculation status | Scoring status | Notes |
|---|---|---|---|
| `last_digit` | ✅ Calculated | ❌ Not scored | Legacy scoring functions exist but were never ported to Kedro |
| `answer_share_selected` | ❌ Not calculated | ❌ Not scored | Removed from Kedro `parameters.yml` entirely — no feature function ever existed; orphaned entry superseded by `answer_selected` |
| `comment_length` | ✅ Calculated | ❌ Not scored | Legacy scoring exists but was commented out |
| `comment_set` | ✅ Calculated | ❌ Not scored | Same as above |
| `comment_duration` | ✅ Calculated | ❌ Not scored | No `s__comment_duration` is produced anywhere |
| `pause_list` | ✅ Calculated | ❌ Not scored | Computes a list of pause durations; nothing consumes it for scoring |
| `string_length` | ✅ Calculated | ❌ Not scored | No scoring function; GUI also marks it false by default |

`answer_share_selected` has already been removed from the Kedro `parameters.yml`. The remaining entries (`last_digit`, `comment_length`, `comment_set`, `comment_duration`, `pause_list`, `string_length`) are still present and could be candidates for removal to reduce confusion. They remain for now in case some of the scoring functions will be re-instated in the future.

---

## 8. Bug Fixes

The following bugs exist in the legacy code and are corrected in the Kedro pipeline.

---

### Bug 1 — `f__pause_count` inflated by `'size'` aggregation

**File:** `rissk/feature_processing.py`  
**Severity:** High — massively inflates `f__pause_count`

**Legacy:** Uses `('f__pause_duration', 'size')` in the aggregation, which counts **all rows in the group** regardless of NaN. Since `f__pause_duration` is NaN for non-pause events, this counts every paradata row per interview as a "pause".

**Kedro fix:** Uses `('f__pause_duration', 'count')` which counts only **non-NaN rows** — i.e., actual pause events.

---

### Bug 2 — `make_score__multi_option_question`: silent no-op initialization

**File:** `rissk/item_processing.py`  
**Severity:** Medium — score column silently stays uninitialized

**Legacy:**
```python
df.loc[score_name] = 0   # Bug: row assignment, not column assignment
```
`df.loc[score_name]` on an integer-indexed DataFrame adds a **spurious row** with the score name string as the index label, setting all existing columns to `0`. The score column is never created. The column is then implicitly created by the later `df.loc[mask, score_name] = ...` inside the loop — but only for rows that match a variable with enough records. Rows outside any valid variable get `NaN` instead of the intended `0`.

**Kedro fix:**
```python
df[score_name] = np.nan   # Correct column assignment; consistent with NaN initialization elsewhere
```

---

### Bug 3 — `feat_answer_changed`: yes_list change overwritten by no_list check

**File:** `rissk/feature_processing.py`  
**Severity:** High — `yes_list`-only changes are never flagged

**Legacy:** Applies the `yes_list` change check and then immediately overwrites the same column with the `no_list` check via two separate `.loc` assignments on the same mask. Any interview where only the yes answers changed is never flagged.

**Kedro fix:** Both `yes_list` and `no_list` checks are combined with a bitwise OR before assignment.

---

### Bug 4 — `make_score_unit__pause_count`: dead `pause_mask` variable

**File:** `rissk/unit_proccessing.py`  
**Severity:** Medium — no zero-division guard

**Legacy:**
```python
pause_mask = ~pd.isnull(self._df_unit[feature_name])  # computed but never used
self._df_unit[score_name] = self._df_unit[feature_name] / self._df_unit['f__number_answered']
# → NaN or inf when f__number_answered == 0
```
`pause_mask` is defined but never applied. Division proceeds over all rows including null `f__pause_count`, with no guard against `f__number_answered == 0`.

**Kedro fix:**
```python
np.where(df['f__number_answered'] != 0, df['f__pause_count'] / df['f__number_answered'], 0)
```

---

### Bug 5 — `make_score_unit__total_elapse`: destructive in-place mutation of feature column

**File:** `rissk/unit_proccessing.py`  
**Severity:** High — corrupts `f__total_elapse` for all downstream consumers

**Legacy:**
```python
self._df_unit[feature_name] = round(self._df_unit[feature_name] / 300)  # permanently overwrites
```
This permanently replaces `f__total_elapse` with the scaled value (`f__total_elapse / 300`) before ECOD is fitted. Any downstream consumer that runs after this method — including `make_score_unit__pause_duration`, which computes `f__pause_duration / f__total_elapse` — receives the scaled value. This causes `s__pause_duration` to be ~300× larger than intended.

**Kedro fix:** Uses a temporary `f__total_elapse_scaled` column for the ECOD fitting step and drops it afterwards. `f__total_elapse` is never modified.

---

### Bug 6 — `windsorize_95_percentile`: DataFrame assigned to Series column (pandas ≥ 2.0)

**File:** `rissk/unit_proccessing.py`  
**Severity:** High — crashes on pandas ≥ 2.0

**Legacy:**
```python
self._df_unit['unit_risk_score'] = windsorize_95_percentile(self.df_unit[['unit_risk_score']].copy())
# windsorize returns a DataFrame; assigning a DataFrame to a column raises ValueError in pandas ≥ 2.0
```

**Kedro fix:**
```python
df_unit['unit_risk_score'] = windsorize_95_percentile(df_unit[['unit_risk_score']])['unit_risk_score']
```

---

### Bug 7 — `make_responsible_score`: responsible score multiplication with no variance guard

**File:** `rissk/unit_proccessing.py`  
**Severity:** Medium — can zero out all `unit_risk_score` values

**Legacy:** Calls `make_responsible_score()` and multiplies without checking if `responsible_score` has any variance. When there are too few interviewers, PCA produces a constant responsible score of 0, and multiplying all unit risk scores by 0 → all zeros → MinMaxScaler produces all-NaN.

**Kedro fix:** Guards with `if resp_score_series.nunique() > 1:`. Logs a warning and skips the multiplication when responsible score is constant.

---

### Bug 8 — `transform_multi`: MultyOptionsQuestion (linked) collapses valid answers to `##N/A##`

**File:** `rissk/utils/import_utils.py`  
**Severity:** High — valid responses replaced with missing-value sentinel

**Legacy:** The list comprehension `[ele if ele != [] else '##N/A##' for ele in sub]` compared `ele != []` against integer values. In NumPy < 2.x this comparison returned `True` for integers and `False` for objects, causing all elements to be treated as empty lists. A linked multi-option list `[[3, -999, -999, ...], [1, -999, ...]]` collapsed to `[##N/A##, ##N/A##, ...]`.

**Kedro fix (`import_utils_kedro.py`):** Replaced the comparison with an explicit type check and value filter. Valid integer answers are preserved; missing-coded values (`-999999999`) are stripped. Result: `[[3], [1], ...]`.

---

### Bug 9 — `MultyOptionsQuestion` ComboBox: category files never matched

**File:** `rissk/utils/import_utils.py`  
**Severity:** Medium — ComboBox answer options always unlinked

**Legacy:** Category files for ComboBox questions are Excel files (e.g. `my-categories.xlsx`). `get_categories` keys the categories dict by `file.name` (full filename, e.g. `"my-categories.xlsx"`), but the questionnaire JSON stores `CategoriesId` as the stem only (e.g. `"my-categories"`). Because `"my-categories" != "my-categories.xlsx"`, the lookup in `update_df_categories` **always fails** — no ComboBox question ever gets `n_answers` or `answer_sequence` populated.

**Kedro fix (`file_process_utils_kedro.py`):** Two fixes:
  1. `get_categories` keys by `file.stem` (no extension), matching the format of `CategoriesId` in the JSON.
  2. `update_df_categories` strips unicode dash characters (`unicodedata.category(c) == 'Pd'`) from both the `CategoriesId` value and the dictionary keys before comparing, handling cases where the JSON uses a different unicode dash variant than the filename.

---

### Bug 10 — `make_score__gps`: `s__gps_extreme_outlier` latitude check overwritten by longitude check

**File:** `rissk/unit_proccessing.py`  
**Severity:** Medium — only longitude == 0 is flagged as extreme outlier

**Legacy:** `s__gps_extreme_outlier` is set twice sequentially — first for zero latitude, then for zero longitude. The second assignment overwrites the first. Only points with `longitude == 0` end up flagged; `latitude == 0` points are silently cleared.

**Kedro fix:** Both latitude and longitude zero-checks are combined with AND before assigning `s__gps_extreme_outlier`.

---

### Bug 11 — `make_score__gps`: distance units divide by `1e6` instead of `1e3`

**File:** `rissk/unit_proccessing.py`  
**Severity:** Medium — GPS accuracy and proximity radius are 1000× too small

**Legacy:**
```python
data['accuracy'] = data['f__gps_accuracy'] / 1e6   # Should be / 1e3 (metres → km)
radius = 10 / 1e6                                   # Should be 10 / 1e3 = 0.01 km
```
The comments state the values are in kilometres, but dividing by `1e6` produces microkm. The 10-metre proximity radius becomes 0.00001 km instead of 0.01 km, making the proximity count almost always zero.

**Kedro fix:** Both `accuracy` and `radius` divide by `1e3` (correct km conversion).

---

### Bug 12 — `feat_answer_position`: always NaN in legacy Ploomber pipeline

**File:** `rissk/feature_processing.py` (Ploomber execution)  
**Severity:** High — feature is always NaN in production pipeline

**Legacy:** `get_microdata` calls `.astype(str)` on all values unconditionally. `f__answer_position` checks membership with `value in answer_sequence` where `answer_sequence` is a list of integers. Because `value` is now a string, the check always fails.

**Kedro fix:** `answer_sequence` is parsed back from its string representation to a Python list of integers before the membership check. Results spot-checked manually against four surveys and confirmed correct.

---

### Bug 13 — `feat_answer_selected`: always NaN in legacy Ploomber pipeline

**File:** `rissk/feature_processing.py` (Ploomber execution)  
**Severity:** High — feature is always NaN in production pipeline

**Legacy:** `isinstance(value, list)` is always `False` when values have been serialized to parquet and read back as strings (e.g. `"[1, 2]"`). The function never enters the selection-count branch.

**Kedro fix:** `value` is converted back to a Python list before the `isinstance` check. Also adds `n_answers > 0` guard to handle edge cases where `n_answers` is zero or NaN (replaces the missing `is_linked` flag check).

---

### Bug 14 — `get_clean_pivot_table`: float `0.2` passed as integer threshold

**File:** `rissk/unit_proccessing.py`  
**Severity:** Low — GPS `filter_columns` filter disabled by incorrect type

**Legacy:** `get_clean_pivot_table` calls `filter_columns(threshold=0.2)`, but `filter_columns` expects an integer count threshold (default 100). A threshold of `0.2` means "keep columns with more than 0.2 non-NaN values" which is effectively always true — the filter is a no-op. Whether `0.2` was intended as a proportion or was simply a typo is unclear.

**Kedro fix:** Uses the integer default of `100` (consistent with all other `filter_columns` calls in the codebase). The GPS filter using this threshold is currently disabled in Kedro anyway.

### Bug 15 — `transform_multi`: unanswered `TextListQuestion` silently dropped instead of counted as unanswered

**File:** `rissk/utils/file_process_utils_kedro.py` (Kedro fix only; legacy bug retained)  
**Severity:** Very Low — `f__number_unanswered` understated for interviews with unanswered `TextListQuestion` items. No such cases were found in the test data of over 2000 interviews; this fix is correct in principle but is expected to have no real-world effect in practice.

**Root cause:** When all columns for a `TextListQuestion` row are `'##N/A##'` (the interviewer was shown the question but left it blank), the `transform_multi` column loop appends nothing and `x` remains `[]`. The outer dispatch line:
```python
[remove_unset_value(x) if x else float('nan') for x in transformation]
```
short-circuits on the falsy `[]` and returns `float('nan')` — bypassing `remove_unset_value` entirely. `float('nan')` is then dropped by `is_valid` / `is_valid_fast`, so the row never reaches `df_item` and is never counted by `f__number_unanswered`.

Note: `remove_unset_value` in legacy does contain logic that would return `'##N/A##'` for an empty input list, but it is unreachable because of the `if x` short-circuit.

**Kedro fix:** Changed the dispatch to:
```python
[remove_unset_value(x) if x else ('##N/A##' if transformation_type == 'list' else float('nan')) for x in transformation]
```
For `transformation_type == 'list'`, an empty list now produces the scalar `'##N/A##'` instead of `float('nan')`. This value survives `is_valid_fast`, the row lands in `df_item` with `value = '##N/A##'`, and is correctly counted in `f__number_unanswered`. All other transformation types (`unlinked`, `linked`) continue to produce `float('nan')` for empty lists, as an empty list there means no selection was made — correct behaviour.

---

## 9. GUI / App Changes

The NiceGUI-based application was updated to reflect the scoring changes:

### Features removed entirely
- `answer_share_selected`: No implementation exists at any level (feature or scoring). Removed from `parameters.yml` and GUI.

### Features set to `false` and removed from GUI controls
These features are calculated but not scored. They remain in `parameters.yml` (set to `false`) as placeholders for potential future use, but are hidden from the GUI to avoid confusion:

| Feature | Reason hidden |
|---|---|
| `last_digit` | Legacy scoring was commented out; not ported |
| `comment_length` | Legacy scoring was commented out; not ported |
| `comment_duration` | No scoring function exists |
| `comment_set` | Legacy scoring was commented out; not ported |
| `pause_list` | Computed but not consumed by any score |
| `string_length` | No scoring function; already defaulted to `false` |

### `numeric_response` linked to `first_digit` in GUI
- `f__numeric_response` is required by the `s__first_digit` score calculation (Benford law uses `f__numeric_response` for frequency/magnitude filtering). Their `use` flags must be set to the same value.
- In the GUI, enabling/disabling `numeric_response` also enables/disables `first_digit`. A comment in `parameters.yml` documents this dependency.

---

## 10. Multi-Questionnaire Surveys & `35_SCORES` Rename

Added 2026-07-14. Lets one survey hold several questionnaire **names** (not just versions), each scored independently, plus a combined microdata union — without dynamic pipeline generation.

### Scores output folder renamed `41_SCORES` → `35_SCORES`
- Pure rename of the final-scores folder (previously renamed `40_SCORED` → `41_SCORES`). Applied across `conf/base/catalog.yml`, `src/rissk/viz.py`, docs, and the notebook. No behavioural change.

### Per-`<qnr>` output sub-level (opt-in, backward-compatible)
- The 13 output catalog entries under `20_INTERIM` / `30_PROCESSED` / `35_SCORES` gained a `${runtime_params:qnr_subdir,''}` path segment. With no runtime param the segment resolves to `''`, so **existing single-questionnaire envs keep the exact flat layout** (`<survey>/latest/<stage>/<file>`) — verified byte-for-byte.
- When the driver passes `qnr_subdir="<name>/"`, each questionnaire's outputs land under `<survey>/latest/<stage>/<name>/`.
- `10_RAW` is **not** subfoldered — it stays survey-level/shared; staging already isolates questionnaires by the `<name>_*.zip` glob.
- `parameters.yml` `questionnaire` is now `${runtime_params:questionnaire,${globals:questionnaire,null}}`. Note: Kedro's `OmegaConfigLoader` merges `runtime_params` over `parameters` directly, so a runtime `questionnaire` override wins via that merge (not the resolver); the expression's genuine effect is the `null` fallback that lets a multi-questionnaire env's `globals.yml` omit `questionnaire`.

### Driver: `rissk.run.run_survey`
- New in-process orchestrator. For an env with a `conf/<env>/questionnaires/*.yml` folder it runs the full pipeline once per questionnaire (injecting `questionnaire` + `qnr_subdir` as Kedro **`runtime_params`** — the Kedro-1.x name; `extra_params` was the pre-0.19 spelling), isolating per-questionnaire failures. An env with no such folder (questionnaire in `globals.yml`) runs once, unchanged, with no combine.
- Static DAG preserved — multi-questionnaire handling is re-running the fixed pipeline per questionnaire, never programmatic node generation.

### Combined microdata (union) — a catalog load/save, not a pipeline
- After the per-questionnaire loop the driver calls `combine_survey_microdata(env)`, which loads the `microdata_by_qnr` PartitionedDataset (each `<qnr>/microdata.parquet`) and saves the survey-level `microdata_combined` (`30_PROCESSED/microdata.parquet`) — using the Kedro catalog for I/O, so `s3://` output roots work via `fsspec`.
- The top-level union file surfaces as PartitionedDataset key `''` and is skipped, so re-running combine is idempotent (no row doubling). A corrupt/unreadable per-questionnaire partition is logged and skipped, not fatal.
- Union is **microdata only** and feeds nothing downstream — scoring is unaffected (pooling different questionnaires into one anomaly model would be statistically wrong).

### Validation
- End-to-end on the `pmpmd` env (survey `pmpmd copy`, two questionnaires `pmpmd_community` + `pmpmd_household`, Tabular+Paradata export): per-`<qnr>` outputs + union (2 819 + 268 731 = 271 550 rows, idempotent), non-empty scores (24 + 589 interviews). `grdslchbs_test` still produces the flat layout. The `rissk_readme.ipynb` notebook runs it green (`3/3 run(s) succeeded`).

