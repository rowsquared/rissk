# py313
#requirements:
#rissk@git+https://github.com/rowsquared/rissk@kedro_pipeline
"""Windmill script f/rissk/run_rissk: run the RISSK pipeline for one survey.

`survey` is the rissk_survey resource (the configuration, passed to rissk as is) and
`storage` the s3 resource (bucket credentials, exported as the AWS environment variables
s3fs reads). rissk itself is installed from GitHub by the one `#requirements:` line above;
the deploy workflow replaces `kedro_pipeline` there with the pushed commit, so the lockfile
pins the exact rissk version that runs. Operator guide: windmill/README.md in that repo.
"""
import os
from typing import TypedDict

from rissk.run import run_survey


class rissk_survey(TypedDict, total=False):
    """Resource type rissk_survey (windmill/rissk_survey.schema.json)."""

    survey: str
    input_root: str
    output_root: str
    questionnaires: list
    zip_password: str
    parameters: dict


class s3(TypedDict, total=False):
    """Windmill's built-in resource type s3 (the fields used here)."""

    accessKey: str
    secretKey: str
    region: str
    endPoint: str


def main(survey: rissk_survey, storage: s3, pipeline: str = "__default__") -> dict:
    endpoint = (storage.get("endPoint") or "").strip()
    aws = {
        "AWS_ACCESS_KEY_ID": storage["accessKey"],
        "AWS_SECRET_ACCESS_KEY": storage["secretKey"],
        "AWS_DEFAULT_REGION": storage.get("region"),
        # s3fs targets AWS unless told otherwise; a non-AWS endpoint (MinIO, Hetzner, R2) is passed on.
        "AWS_ENDPOINT_URL": f"https://{endpoint}" if endpoint and not endpoint.endswith("amazonaws.com") else "",
    }
    os.environ.update({key: value for key, value in aws.items() if value})
    outcomes = run_survey(survey, pipeline=pipeline)
    failed = [label for label, outcome in outcomes.items() if outcome != "OK"]
    if failed:
        raise RuntimeError(f"rissk run failed for: {failed}")
    return {"survey": survey["survey"], "pipeline": pipeline, "outcomes": outcomes}
