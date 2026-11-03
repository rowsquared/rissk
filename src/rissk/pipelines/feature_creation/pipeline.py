"""Feature Creation pipeline definition."""
from kedro.pipeline import Pipeline, node, pipeline
from .nodes import (
    create_base_item_table_node,
    create_base_unit_table_node,
    enrich_item_features_node,
    enrich_unit_features_node,
    build_removed_answers_node,
)

def create_pipeline(**kwargs) -> Pipeline:
    """Create the feature creation pipeline.
    
    Returns:
        A pipeline that builds item and unit feature tables, and a removed_answers dataset.
    """
    return pipeline([
        node(
            func=create_base_item_table_node,
            inputs=["microdata", "paradata_processed", "parameters"],
            # Legacy test data:
            # inputs=["legacy_microdata", "legacy_paradata_processed", "parameters"],
            outputs="item_features_base",
            name="create_base_item_table_node",
        ),
        node(
            func=create_base_unit_table_node,
            inputs=["paradata_processed", "parameters"],
            # Legacy test data:
            # inputs=["legacy_paradata_processed", "parameters"],
            outputs="unit_features_base",
            name="create_base_unit_table_node",
        ),
        node(
            func=enrich_item_features_node,
            inputs=["item_features_base", "paradata_processed", "parameters"],
            # Legacy test data:
            # inputs=["item_features_base", "legacy_paradata_processed", "parameters"],
            outputs="item_features",
            name="enrich_item_features_node",
        ),
        node(
            func=enrich_unit_features_node,
            inputs=["unit_features_base", "item_features", "paradata_processed", "parameters"],
            # Legacy test data:
            # inputs=["unit_features_base", "item_features", "legacy_paradata_processed", "parameters"],
            outputs="unit_features",
            name="enrich_unit_features_node",
        ),
        node(
            func=build_removed_answers_node,
            inputs=["paradata_processed", "parameters"],
            # Legacy test data:
            # inputs=["legacy_paradata_processed", "parameters"],
            outputs="removed_answers",
            name="build_removed_answers_node",
        ),
    ])
