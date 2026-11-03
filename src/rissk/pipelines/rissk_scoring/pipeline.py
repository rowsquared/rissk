"""Rissk scoring pipeline definition."""
from kedro.pipeline import Pipeline, node, pipeline
from .nodes import calculate_item_scores, calculate_unit_scores
from rissk.pipelines.feature_creation.nodes import filter_by_consent

def create_pipeline(**kwargs) -> Pipeline:
    """Create the scoring pipeline.
    
    Returns:
        A pipeline that calculates item and unit risk scores.
    """
    return pipeline([
        node(
            func=filter_by_consent,
            inputs=[
                "item_features",
                "unit_features",
                "removed_answers",
                "paradata_processed",
                "params:questionnaire.filter_var",
            ],
            outputs=[
                "item_features_filtered",
                "unit_features_filtered",
                "removed_answers_filtered",
            ],
            name="filter_consent_node",
        ),
        node(
            func=calculate_item_scores,
            inputs=["item_features_filtered", "parameters"],
            outputs="item_scores",
            name="calculate_item_scores_node",
        ),
        node(
            func=calculate_unit_scores,
            # removed_answers gives calculate_unit_scores access to ALL AnswerRemoved events,
            # including those for items deleted from microdata,
            # matching legacy make_score_unit__answer_removed behaviour.
            inputs=["unit_features_filtered", "item_scores", "parameters", "removed_answers_filtered"],
            outputs=["unit_rissk_scores", "responsible_scores"],
            name="calculate_unit_scores_node",
        ),
    ])
