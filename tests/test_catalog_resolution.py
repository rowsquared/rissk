"""Guards for the per-<qnr> output paths and backward-compatible defaults."""
from kedro.config import OmegaConfigLoader


def _filepath(dataset, runtime_params):
    # Resolve the real project catalog against the grdslchbs_test env's globals.
    cl = OmegaConfigLoader(
        conf_source="conf",
        base_env="base",
        default_run_env="grdslchbs_test",
        runtime_params=runtime_params,
    )
    return cl["catalog"][dataset]["filepath"]


def test_flat_paths_when_no_qnr_subdir():
    # Existing single-questionnaire envs (and the combine run) pass no qnr_subdir.
    assert _filepath("microdata", {}).endswith("/latest/30_PROCESSED/microdata.parquet")
    assert _filepath("item_scores", {}).endswith("/latest/35_SCORES/item_scores.parquet")
    assert _filepath("item_features_base", {}).endswith("/latest/20_INTERIM/item_features_base.parquet")


def test_nested_paths_when_qnr_subdir_set():
    rp = {"qnr_subdir": "pmpmd_community/"}
    assert _filepath("microdata", rp).endswith("/30_PROCESSED/pmpmd_community/microdata.parquet")
    assert _filepath("unit_rissk_scores", rp).endswith("/35_SCORES/pmpmd_community/unit_rissk_scores.csv")


def test_questionnaire_param_falls_back_to_globals():
    cl = OmegaConfigLoader(conf_source="conf", base_env="base",
                           default_run_env="grdslchbs_test", runtime_params={})
    assert cl["parameters"]["questionnaire"]["name"] == "slchbs_grenada_2627"


def test_questionnaire_param_runtime_override():
    cl = OmegaConfigLoader(conf_source="conf", base_env="base", default_run_env="grdslchbs_test",
                           runtime_params={"questionnaire": {"name": "pmpmd_household", "filter_var": None}})
    assert cl["parameters"]["questionnaire"]["name"] == "pmpmd_household"


# --- Configuration as data: runtime params override the env's globals ----------------
def _path(dataset, runtime_params):
    cl = OmegaConfigLoader(conf_source="conf", base_env="base",
                           default_run_env="grdslchbs_test", runtime_params=runtime_params)
    return cl["catalog"][dataset]["path"]


RP = {"survey": "pmpmd copy", "output_root": "s3://b", "work_root": "/w", "qnr_subdir": "hh/"}


def test_runtime_roots_override_globals():
    assert _filepath("microdata", RP) == "s3://b/pmpmd copy/latest/30_PROCESSED/hh/microdata.parquet"
    assert _filepath("unit_rissk_scores", RP) == "s3://b/pmpmd copy/latest/35_SCORES/hh/unit_rissk_scores.csv"
    assert _filepath("paradata_raw", RP) == "/w/pmpmd copy/latest/20_INTERIM/hh/paradata_raw.parquet"
    assert _path("survey_zip_partitions", RP) == "/w/pmpmd copy/latest/10_RAW"
    assert _path("microdata_by_qnr", RP) == "s3://b/pmpmd copy/latest/30_PROCESSED"
    assert _filepath("microdata_combined", RP) == "s3://b/pmpmd copy/latest/30_PROCESSED/microdata.parquet"


def test_templates_are_dropped_from_the_catalog():
    cl = OmegaConfigLoader(conf_source="conf", base_env="base",
                           default_run_env="grdslchbs_test", runtime_params={})
    assert not [k for k in cl["catalog"] if k.startswith("_")]
