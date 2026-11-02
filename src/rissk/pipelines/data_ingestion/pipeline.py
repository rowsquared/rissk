from kedro.pipeline import Pipeline, node, pipeline
from .nodes import (
    stage_input_zips_node,
    extract_zip_files_node,
    filter_extracted_survey_paths_node,
    load_paradata_node,
    process_paradata_node,
    load_questionnaire_node,
    load_raw_microdata_node,
    merge_microdata_questionnaire_node
)
# catalog for path
def create_pipeline(**kwargs) -> Pipeline:
    return pipeline([
        node(
            func=stage_input_zips_node,
            inputs=[
                "params:input_root",
                "params:survey",
                "params:work_root",
                "params:questionnaire",
            ],
            outputs="input_staged",
            name="stage_input_zips_node",
            tags=["unzip_files"],
        ),
        node(
            func=extract_zip_files_node,
            inputs=[
                "survey_zip_partitions",
                "params:zip_password",
                "input_staged",
            ],
            outputs="extracted_flag",
            name="extract_zip_files_node",
            tags=["unzip_files"]
        ),
        node(
            func=filter_extracted_survey_paths_node,
            inputs=[
                "extracted_survey_folders",
                "params:questionnaire",
                "extracted_flag",
            ],
            outputs="file_paths",
            name="filter_extracted_survey_paths_node"
        ),
        node(
            func=load_paradata_node,
            inputs="file_paths",
            outputs="paradata_raw",
            name="load_paradata_node"
        ),
        node(
            func=load_questionnaire_node,
            inputs="file_paths",
            outputs="raw_questionnaire",
            name="load_questionnaire_node"
        ),
        node(
            func=process_paradata_node,
            inputs=["paradata_raw", "raw_questionnaire", "parameters"],
            outputs="paradata_processed",
            name="process_paradata_node"
        ),
        node(
            func=load_raw_microdata_node,
            inputs=["file_paths", "raw_questionnaire"],
            outputs="raw_microdata",
            name="load_raw_microdata_node"
        ),
        node(
            func=merge_microdata_questionnaire_node,
            inputs=["raw_microdata", "raw_questionnaire"],
            outputs="microdata",
            name="merge_microdata_questionnaire_node"
        )
    ])
