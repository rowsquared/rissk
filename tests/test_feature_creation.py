"""Empty-data guards in feature creation (graceful handling, no opaque crashes)."""
import pandas as pd

from rissk.core.feature_processing import create_base_item_table


def test_create_base_item_table_empty_paradata_returns_empty():
    """A STATA-only export (microdata present, no Paradata) must degrade gracefully
    rather than crash with `KeyError: 'event'`, matching create_base_unit_table."""
    microdata = pd.DataFrame(
        {"interview__id": ["i1"], "variable_name": ["q1"], "roster_level": [""]}
    )
    result = create_base_item_table(microdata, pd.DataFrame(), {"features": {}})
    assert result.empty
