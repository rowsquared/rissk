import pandas as pd
from rissk.run import combine_microdata


def _loader(df):
    return lambda: df


def test_unions_per_qnr_and_skips_top_level_union():
    partitions = {
        "": _loader(pd.DataFrame({"qnr": ["OLD"], "value": [99]})),          # stale union -> skip
        "community/": _loader(pd.DataFrame({"qnr": ["community"], "value": [1]})),
        "household/": _loader(pd.DataFrame({"qnr": ["household"], "value": [2]})),
    }
    out = combine_microdata(partitions)
    assert sorted(out["qnr"].unique()) == ["community", "household"]
    assert len(out) == 2
    assert "OLD" not in out["qnr"].values


def test_empty_partitions_returns_empty_frame():
    out = combine_microdata({})
    assert isinstance(out, pd.DataFrame)
    assert out.empty


def test_only_top_level_union_returns_empty_frame():
    out = combine_microdata({"": _loader(pd.DataFrame({"qnr": ["OLD"]}))})
    assert out.empty


def _raising_loader():
    raise OSError("corrupt parquet")


def test_skips_unreadable_partition_and_unions_the_rest():
    partitions = {
        "community/": _loader(pd.DataFrame({"qnr": ["community"], "value": [1]})),
        "corrupt/": _raising_loader,
        "household/": _loader(pd.DataFrame({"qnr": ["household"], "value": [2]})),
    }
    out = combine_microdata(partitions)
    assert sorted(out["qnr"].unique()) == ["community", "household"]
    assert len(out) == 2
