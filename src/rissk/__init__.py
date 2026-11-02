"""RISSK: Automatically identify at-risk interviews."""

import warnings

# Filter out Kedro deprecation warning about pipeline_name
warnings.filterwarnings("ignore", message="`pipeline_name` is deprecated")

__version__ = "0.1.2"