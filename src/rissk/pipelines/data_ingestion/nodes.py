from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import logging
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)
from rissk.utils.import_utils import (
    extract_zip,
    filter_matching_folders,
    get_survey_info,
    get_questionnaire,
    get_paradata_raw,
    get_microdata_raw,
    merge_microdata_questionnaire
)
from rissk.core.feature_processing import make_index_col
from rissk.utils.storage import stage_zips


def stage_input_zips_node(
    input_root: str, survey: str, work_root: str, questionnaire: Dict
) -> bool:
    """Stage this questionnaire's export zips into the LOCAL work area.

    Thin wrapper around :func:`rissk.utils.storage.stage_zips`, which holds the
    fsspec fetch + stale-cleanup logic (input_root may be local or ``s3://``; the
    downstream unzip + folder reads are local-filesystem only). Returns a flag so
    ``extract_zip_files_node`` is ordered after it.
    """
    staged = stage_zips(input_root, survey, questionnaire["name"], work_root)
    logger.info(f"Staged {len(staged)} zip(s) for {survey}/{questionnaire['name']}")
    return True


def extract_zip_files_node(
    survey_zip_partitions: Dict[str, Callable[[], Path]],
    zip_password: str,
    input_staged: bool = True,
) -> bool:
    """
    Node that iterates through partitions and triggers extraction.
    ``input_staged`` is an ordering dependency on stage_input_zips_node (ignored here).
    Returns a flag so filter_extracted_survey_paths_node runs after extraction.
    """
    if not survey_zip_partitions:
        logger.warning("No zip partitions found to extract.")
        return True

    failures = []
    for partition_id, loader in survey_zip_partitions.items():
        # 1. LOAD THE PATH (This calls PathDataset._load)
        zip_path = loader()

        # 2. VALIDATE & EXTRACT
        if zip_path.suffix.lower() == ".zip" and zip_path.exists():
            destination = zip_path.with_suffix("")
            logger.info(f"Extracting partition [{partition_id}] from {zip_path}")
            if not extract_zip(zip_path, destination, password=zip_password):
                failures.append(zip_path.name)
        # else:
        #     logger.debug(f"Skipping non-zip partition: {partition_id}")

    # Surface failed extractions loudly. We don't raise (the pipeline degrades
    # gracefully on a single bad export), but without this an entirely-failed
    # extraction — e.g. a wrong zip_password — would look like merely empty input
    # and silently produce empty/garbage scores.
    if failures:
        logger.error(
            f"extract_zip_files_node: {len(failures)} zip(s) failed to extract "
            f"(check zip_password / file corruption): {failures}. Downstream stages "
            "will see partial or empty data for these."
        )
    return True


def filter_extracted_survey_paths_node(
    survey_partitions: Dict[str, Callable[[], Any]],
    questionnaire: Dict,
    extracted_flag: bool = True,
) -> List[Path]:
    """
    Return extracted folder paths matching questionnaire/version patterns
    using partition entries.
    This node does not perform extraction.
    """
    ver_list = questionnaire.get("VERSION", [])
    versions = ", ".join(str(v) for v in ver_list) if ver_list else "all"
    lines = [
        "=" * 55,
        "  DATA INGESTION — Questionnaire to process",
        "=" * 55,
        f"  • {questionnaire['name']}  |  versions: [{versions}]",
        "=" * 55,
    ]
    logger.info("\n" + "\n".join(lines))

    logger.info(f"Collecting matching folders from {len(survey_partitions)} partition entries")
    return filter_matching_folders(survey_partitions, [questionnaire])


def load_paradata_node(file_paths: List[Path]) -> pd.DataFrame:
    """
    Loads raw paradata from extracted folders.
    No questionnaire metadata is merged at this stage; column splitting,
    timestamp parsing and version tagging are performed by get_paradata_raw.
    """
    logger.info(f"Processing raw paradata for {len(file_paths)} paths")
    survey_info = get_survey_info(file_paths)

    dfs_paradata = []

    for survey_questionnaire, questionnaires_details in survey_info.items():
        for questionnaires_version, file_paths_detail in questionnaires_details.items():
            paradata_path = file_paths_detail.get('Paradata')

            if not paradata_path:
                logger.warning(
                    f"Skipping paradata load for {survey_questionnaire} v{questionnaires_version}: "
                    "missing Paradata export"
                )
                continue

            try:
                df_paradata = get_paradata_raw(paradata_path)
                dfs_paradata.append(df_paradata)
                if df_paradata.empty:
                    logger.warning(
                        f"Empty data returned for paradata {survey_questionnaire} v{questionnaires_version}: "
                        "file may be empty or corrupt"
                    )
                else:
                    logger.info(f"Loaded raw paradata for {survey_questionnaire} v{questionnaires_version}")
            except Exception as e:
                logger.error(f"Failed to load paradata for {survey_questionnaire} v{questionnaires_version}. Skipping. Error: {str(e)}")
                continue

    if not dfs_paradata:
        return pd.DataFrame()

    combined_df = pd.concat(dfs_paradata)
    combined_df.reset_index(drop=True, inplace=True)
    return combined_df

def process_paradata_node(
    paradata_raw: pd.DataFrame,
    questionnaire: pd.DataFrame,
    parameters: Dict,
) -> pd.DataFrame:
    """
    Merges questionnaire metadata onto raw paradata, processes timestamps and
    interviewing flags, makes the index column, and filters to active interviewer
    events - producing the paradata_processed dataset consumed by feature creation.
    """
    if paradata_raw.empty:
        logger.error(
            "process_paradata_node: paradata_raw is empty — all paradata files were missing "
            "or contained no data rows. Cannot process paradata. Returning empty DataFrame."
        )
        return pd.DataFrame()

    paradata = paradata_raw.copy()

    # 1. Merge questionnaire metadata
    if not questionnaire.empty:
        q_columns = [
            'qnr_seq', 'variable_name', 'qtype', 'question_type',
            'answers', 'question_scope',
            'yes_no_view', 'is_filtered_combobox',
            'is_integer', 'cascade_from_question_id',
            'answer_sequence', 'n_answers', 'question_sequence',
            'qnr', 'qnr_version',
        ]
        q_columns = [c for c in q_columns if c in questionnaire.columns]
        paradata = paradata.merge(
            questionnaire[q_columns],
            how='left',
            left_on=['param', 'qnr', 'qnr_version'],
            right_on=['variable_name', 'qnr', 'qnr_version'],
        )

    # 2. Stringify answer_sequence for parquet serialization (matches legacy behaviour)
    if 'answer_sequence' in paradata.columns:
        paradata['answer_sequence'] = paradata['answer_sequence'].apply(str)

    # 3. Calculate f__answer_hour_set
    paradata['f__answer_hour_set'] = (
        paradata['timestamp_local'].dt.hour +
        paradata['timestamp_local'].dt.round('30min').dt.minute / 60
    )

    # 4. Calculate interviewing flag and filter to first-pass interviewer events.
    # interviewing=True for all events that occurred before any Supervisor/HQ interaction,
    # False for everything after. A rejection or review event resets the interpretive
    # context: answers recorded afterwards belong to a different (post-review) pass and
    # should not be scored as if they were the original interviewing session.
    # The cumsum trick propagates the flag forward so every subsequent row in the same
    # interview automatically receives interviewing=False once the first flagged event fires.
    events_split = ['RejectedBySupervisor', 'OpenedBySupervisor', 'OpenedByHQ', 'RejectedByHQ']
    paradata['flag'] = paradata['event'].isin(events_split)
    paradata['cumulative_flag'] = paradata.groupby('interview__id')['flag'].cumsum()
    paradata['interviewing'] = np.where(paradata['cumulative_flag'] > 0, False, True)
    paradata.drop(['flag', 'cumulative_flag'], axis=1, inplace=True)
    paradata = paradata[(paradata['interviewing'] == True) & (paradata['role'] == 1)].copy()

    # 5. Make index column
    paradata = make_index_col(paradata)

    # 6. Sort
    paradata.sort_values(['interview__id', 'order'], inplace=True)
    paradata.reset_index(drop=True, inplace=True)

    return paradata


def load_questionnaire_node(file_paths: List[Path]) -> pd.DataFrame:
    """
    Loads questionnaire metadata from extracted folders.
    """
    logger.info(f"Processing questionnaires for {len(file_paths)} paths")
    survey_info = get_survey_info(file_paths)
    
    dfs_questionnaires = []
    
    for survey_questionnaire, questionnaires_details in survey_info.items():
        for questionnaires_version, file_paths in questionnaires_details.items():
            tabular_path = file_paths.get('Tabular')

            if not tabular_path:
                logger.warning(
                    f"Skipping questionnaire load for {survey_questionnaire} v{questionnaires_version}: "
                    "missing Tabular export"
                )
                continue

            try:
                df_questionnaires = get_questionnaire(tabular_path)
                dfs_questionnaires.append(df_questionnaires)
                if df_questionnaires.empty:
                    logger.warning(
                        f"Empty data returned for questionnaire {survey_questionnaire} v{questionnaires_version}: "
                        "file may be empty or corrupt"
                    )
                else:
                    logger.info(f"Loaded questionnaire for {survey_questionnaire} v{questionnaires_version}")
            except Exception as e:
                logger.error(f"Failed to load questionnaire for {survey_questionnaire} v{questionnaires_version}. Skipping. Error: {str(e)}")
                continue

    if not dfs_questionnaires:
        return pd.DataFrame()

    combined_df = pd.concat(dfs_questionnaires)
    combined_df.reset_index(drop=True, inplace=True)
    
    if 'answer_sequence' in combined_df.columns:
        combined_df['answer_sequence'] = combined_df['answer_sequence'].apply(str)
    if 'properties' in combined_df.columns:
        combined_df['properties'] = combined_df['properties'].apply(
            lambda x: str(x) if isinstance(x, dict) else x
        )
        
    return combined_df


def load_raw_microdata_node(file_paths: List[Path], questionnaire: pd.DataFrame) -> pd.DataFrame:
    """
    Loads raw microdata (answers) from extracted folders.
    Applies multi-question transformation using questionnaire metadata but does not
    merge questionnaire columns into the output. Values are normalized and stringified.
    """
    logger.info(f"Processing raw microdata for {len(file_paths)} paths")
    survey_info = get_survey_info(file_paths)

    dfs_microdata = []

    for survey_questionnaire, questionnaires_details in survey_info.items():
        for questionnaires_version, file_paths_detail in questionnaires_details.items():
            tabular_path = file_paths_detail.get('Tabular')

            if not tabular_path:
                logger.warning(
                    f"Skipping raw microdata load for {survey_questionnaire} v{questionnaires_version}: "
                    "missing Tabular export"
                )
                continue

            try:
                df_questionnaires = questionnaire[questionnaire['qnr'] == survey_questionnaire]
                df_microdata = get_microdata_raw(tabular_path, df_questionnaires)
                dfs_microdata.append(df_microdata)
                if df_microdata.empty:
                    logger.warning(
                        f"Empty data returned for microdata {survey_questionnaire} v{questionnaires_version}: "
                        "file may be empty or corrupt"
                    )
                else:
                    logger.info(f"Loaded raw microdata for {survey_questionnaire} v{questionnaires_version}")
            except Exception as e:
                logger.error(f"Failed to load raw microdata for {survey_questionnaire} v{questionnaires_version}. Skipping. Error: {str(e)}")
                continue

    if not dfs_microdata:
        return pd.DataFrame()

    combined_df = pd.concat(dfs_microdata)
    combined_df.reset_index(drop=True, inplace=True)
    return combined_df


def merge_microdata_questionnaire_node(raw_microdata: pd.DataFrame, questionnaire: pd.DataFrame) -> pd.DataFrame:
    """
    Merges raw microdata with questionnaire metadata and normalizes column names.
    """
    logger.info("Merging raw microdata with questionnaire metadata")
    merged = merge_microdata_questionnaire(raw_microdata, questionnaire)
    merged.reset_index(drop=True, inplace=True)
    return merged
