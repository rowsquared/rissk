import os
from pathlib import Path
import pandas as pd
from typing import Dict
import re
import numpy as np
import unicodedata



def set_qnr_version(df, survey_project, project_version):
    df['qnr'] = survey_project
    df['qnr_version'] = project_version
    return df


def normalize_column_name(s):
    """
    This function converts any string with capital letters to a string all lowercase with a "_" before any previously capital letter.

    Parameters:
    s (str): The string to convert.

    Returns:
    new_s (str): The converted string.
    """
    new_s = ""
    for i, char in enumerate(s):
        if char.isupper():
            # Add underscore only if it's not the first or last character
            if i != 0 and i != len(s) - 1:
                new_s += "_"
            new_s += char.lower()
        else:
            new_s += char
    return new_s


def transform_multi(df, variable_list, transformation_type):
    """
    This function takes a DataFrame and a list of variable names and applies a transformation depending on
    transformation_type to the variables in the DataFrame that start with the given variable names.

    The transformation can be either 'unlinked,' 'linked,' 'list,' or 'gps.'

    Parameters:
    df (DataFrame): The DataFrame to be transformed.
    variable_list (list): The list of variable names to be transformed.
    transformation_type (str): The type of transformation to apply. Must be 'unlinked,' 'linked,' 'list,' or 'gps.'

    Returns:
    DataFrame: The transformed DataFrame.

    Raises:
    ValueError: If transformation_type is not 'unlinked,' 'linked,' 'list,' or 'gps.'
    """
    if transformation_type not in ['unlinked', 'linked', 'list', 'gps']:
        raise ValueError("transformation_type must be either 'unlinked', 'linked', 'list', or 'gps'")

    transformed_df = pd.DataFrame(index=df.index)  # DataFrame for storing transformations

    for var in variable_list:
        if var in df.columns:
            # Drop the target column, should it exist (only text list question on a linked roster)
            df = df.drop(var, axis=1)

        related_cols = [col for col in df.columns if col.startswith(f"{var}__")]

        if related_cols:
            transformation = [[] for _ in range(len(df))] \
                if transformation_type != 'gps' \
                else ['' for _ in range(len(df))]

            for col in related_cols:

                if transformation_type == 'unlinked':
                    suffix = int(col.split('__')[1].replace('n', '-'))
                    mask = df[col] > 0
                    transformation = [x + [suffix] if mask.iloc[i] else x for i, x in enumerate(transformation)]
                elif transformation_type == 'linked':
                    # !NOTE! if you add the (df[col] != -999999999) filter it removes also list that not only
                    # contains -999...
                    mask = (df[col].notna())  # & (df[col] != -999999999)
                    transformation = [x + [df.at[i, col]] if mask.iloc[i] else x for i, x in enumerate(transformation)]
                elif transformation_type == 'list':
                    mask = (df[col] != '##N/A##') & (df[col] != '')
                    transformation = [x + [df.at[i, col]] if mask.iloc[i] else x for i, x in enumerate(transformation)]
                elif transformation_type == 'gps':
                    transformation = [x + (',' if x else '') + (str(df.at[i, col])
                                                                if pd.notna(df.at[i, col])
                                                                   and df.at[i, col] not in ['##N/A##', -999999999]
                                                                else '') for i, x in enumerate(transformation)]

            def remove_unset_value(sub_list):
                # Normalize numeric types (float -> int if integer) inside the list construction
                # This ensures lists like [1.0, 2.0] become [1, 2], and np.float64(nan) -> float('nan')
                def normalize(v):
                    if isinstance(v, (np.floating, np.integer)):
                        if np.isnan(v):
                            return float('nan')
                        return v.item()
                    if isinstance(v, float) and v.is_integer():
                        return int(v)
                    return v

                # Filter explicit Survey Solutions structural missing values
                # We do not filter 'true' NaNs yet, as they represent system-missing/skipped
                sub = [x for x in sub_list if x not in [-999999999, '##N/A##']]
                sub = [normalize(ele) for ele in sub]
                
                # Check for empty list safely, avoiding numpy array ambiguity
                # Do NOT filter out NaNs (system missing/skipped) as per requirements to distinguish them from explicit missing
                clean_sub = []
                for ele in sub:
                    if isinstance(ele, list):
                        if len(ele) > 0:
                            clean_sub.append(ele)
                    else:
                        clean_sub.append(ele)
                
                # If the cleaning results in an empty list, it implies all values were
                # missing, skipped, or invalid. We return '##N/A##' to match legacy behavior.
                if len(clean_sub) == 0:
                    return '##N/A##'

                return clean_sub

            transformation = [remove_unset_value(x)
                              if x else ('##N/A##' if transformation_type == 'list' else float('nan'))
                              for x in transformation] if transformation_type != 'gps' else [
                x if x else '' for x in transformation]
            transformed_df[var] = transformation  # Add the transformation to the transformed DataFrame
            df = df.drop(related_cols, axis=1)  # Drop the original columns

    df = pd.concat([df, transformed_df], axis=1)  # Concatenate the original DataFrame with the transformations

    return df.copy()


def process_json_structure(children, parent_group_title, counter, question_data):
    """
    This function processes the JSON structure of a questionnaire, collecting information about the questions.

    Parameters:
    children (list): The children nodes in the current JSON structure.
    parent_group_title (str): The title of the parent group for the current child nodes.
    counter (int): A counter to keep track of the sequence of questions.
    question_data (list): A list where data about each question is appended as a dictionary.

    Returns:
    counter (int): The updated counter value after processing all children nodes.

    """
    for child in children:
        if "$type" in child:
            question_data.append({
                "qnr_seq": counter,
                "VariableName": child.get("VariableName"),
                "qtype": child["$type"],
                "QuestionType": child.get("QuestionType"),
                "Answers": child.get("Answers"),
                "Children": child.get("Children"),
                "ConditionExpression": child.get("ConditionExpression"),
                "HideIfDisabled": child.get("HideIfDisabled"),
                "Featured": child.get("Featured"),
                "Instructions": child.get("Instructions"),
                "Properties": child.get("Properties"),
                "PublicKey": child.get("PublicKey"),
                "QuestionScope": child.get("QuestionScope"),
                "QuestionText": child.get("QuestionText"),
                "StataExportCaption": child.get("StataExportCaption"),
                "VariableLabel": child.get("VariableLabel"),
                "IsTimestamp": child.get("IsTimestamp"),
                "ValidationConditions": child.get("ValidationConditions"),
                "YesNoView": child.get("YesNoView"),
                "IsFilteredCombobox": child.get("IsFilteredCombobox"),
                "IsInteger": child.get("IsInteger"),
                "CategoriesId": child.get("CategoriesId"),
                "Title": child.get("Title"),
                "IsRoster": child.get("IsRoster"),
                "LinkedToRosterId": child.get("LinkedToRosterId"),
                "LinkedToQuestionId": child.get("LinkedToQuestionId"),
                "CascadeFromQuestionId": child.get("CascadeFromQuestionId"),
                "parents": parent_group_title
            })
            counter += 1

        if "Children" in child:
            child_group_title = child.get("Title", "")
            counter = process_json_structure(child["Children"], parent_group_title + " > " + child_group_title, counter,
                                             question_data)

    return counter


def get_categories(directory: Path) -> Dict[str, Dict[str, list]]:
    """
    This function retrieves categories from Excel files within a directory.

    Parameters:
    directory (Path): The directory where the category Excel files are stored.

    Returns:
    Dict[str, Dict[str, list]]: A dictionary containing category data. Each key represents a filename, and each value is
    another dictionary containing 'n_answers' and 'answer_sequence' which represents the number of answers and the
    sequence of the answer IDs respectively.
    """
    categories = {}

    # List all Excel files in the directory
    files = directory.glob('*.xlsx')  # Finds .xlsx files
    files = list(files) + list(directory.glob('*.xls'))  # Adds .xls files

    for file in files:
        df = pd.read_excel(file)
        n_answers = df.shape[0]
        answer_sequence = df['id'].tolist()
        categories[file.stem] = {'n_answers': n_answers, 'answer_sequence': answer_sequence}
    
    return categories


def update_df_categories(row, categories):
    """
    This function updates a DataFrame row with category information if applicable.

    Parameters:
    row (Series): The Questionnaire DataFrame row to be updated.
    categories (dict): A dictionary containing category data, keys are 'CategoriesId'.

    Returns:
    Series: The updated DataFrame row.

    """
    cid = row.get('CategoriesId')
    if pd.isna(cid) or cid in (None, ''):
        return row

    # Normalize ID: strip all unicode dash characters
    cid_str = str(cid)
    cid_clean = ''.join(c for c in cid_str if unicodedata.category(c) != 'Pd')

    # Try direct match with cleaned ID (assuming keys might be cleaned/filenames)
    match = categories.get(cid_clean)

    if match is None:
        # Fallback: Compare against cleaned keys from the dictionary
        # This handles cases where filenames might still have dashes or other formatting
        for key, val in categories.items():
            key_clean = ''.join(c for c in key if unicodedata.category(c) != 'Pd')
            if key_clean == cid_clean:
                match = val
                break

    if match:
        row['n_answers'] = match['n_answers']
        row['answer_sequence'] = match['answer_sequence']
    return row

# Survey Solutions export naming. Filenames follow
#   <questionnaire>_<version>_<format>[_Reduced]_<status>[_<timestamp>]
# The optional "Reduced" infix (newer paradata export option) and the optional
# trailing export timestamp (e.g. 20260611T1347Z) were added by recent SuSo
# versions; anchoring the format and status tokens lets us recognise and discard
# both instead of mis-capturing them as the format/status (which silently dropped
# the paradata export — see tests/test_filename_parsing.py).
EXPORT_FORMATS = ("Tabular", "STATA", "SPSS", "Paradata")
# Longest-first so the alternation never settles on a prefix (e.g. "Approved").
INTERVIEW_STATUSES = (
    "ApprovedByHeadquarters", "ApprovedBySupervisor", "ApprovedByHQ",
    "InterviewerAssigned", "Approved", "All",
)

_EXPORT_FILENAME_RE = re.compile(
    rf"^(?P<questionnaire>.+)_(?P<version>[0-9]+)"
    rf"_(?P<format>{'|'.join(EXPORT_FORMATS)})(?:_Reduced)?"
    rf"_(?P<status>{'|'.join(INTERVIEW_STATUSES)})(?:_.+)?$"
)


def parse_filename(filename: str):
    """
    Parses a Survey Solutions export name into [questionnaire, version, format, status].

    Handles the classic ``<name>_<version>_<format>_<status>`` convention as well
    as the newer paradata exports that insert a ``Reduced`` flag and/or append an
    export timestamp (``..._Paradata_Reduced_All_20260611T1347Z``); the flag and
    timestamp are recognised and discarded.

    Parameters:
    filename (str): The filename (export folder/file name) to parse.

    Returns:
    list: [questionnaire, version, format, status].
    """
    match = _EXPORT_FILENAME_RE.match(filename)
    if not match:
        raise ValueError(f"Filename '{filename}' does not match the expected pattern.")

    return [
        match.group('questionnaire'),
        match.group('version'),
        match.group('format'),
        match.group('status'),
    ]


def get_file_parts(filename):

    questionnaire, version, file_format, interview_status = parse_filename(filename)
    try:
        version = int(version)
    except ValueError:
        raise ValueError(f"ERROR: {filename} Not a valid Survey Solutions export file. Version not found.")

    # Test input file has the correct name
    if file_format not in ["Tabular", "STATA", "SPSS", "Paradata"]:
        raise ValueError(f"ERROR: {filename} Not a valid Survey Solutions export file. Export type not found")

    if interview_status not in ["Approved", "InterviewerAssigned", "ApprovedBySupervisor", "ApprovedByHQ", "All",
                                'ApprovedByHeadquarters']:
        raise ValueError(f"ERROR: {filename} Not a valid Survey Solutions export file. Interview status not found.")

    file_format = file_format if file_format == 'Paradata' else 'Tabular'
    return questionnaire, version, file_format, interview_status
