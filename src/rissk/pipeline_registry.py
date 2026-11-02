"""Project pipelines."""

from kedro.pipeline import Pipeline


def register_pipelines() -> dict[str, Pipeline]:
    """Register the project's pipelines."""
    from rissk.pipelines.data_ingestion import create_pipeline as ingestion_pipeline
    from rissk.pipelines.feature_creation import create_pipeline as feature_creation_pipeline
    from rissk.pipelines.rissk_scoring import create_pipeline as scoring_pipeline

    ingestion = ingestion_pipeline()
    feat_creation = feature_creation_pipeline()
    scoring = scoring_pipeline()

    return {
        "__default__": ingestion + feat_creation + scoring,
        "data_ingestion": ingestion,
        "feature_creation": feat_creation,
        "rissk_scoring": scoring,
    }

