"""Health metadata adapter for the unchanged old product in an isolated test DB.

Run only from the frozen baseline checkout. This test-only ASGI entrypoint adds
the environment label missing in that version; it never changes product files.
"""

import json
import os
from argparse import Namespace
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse

from tests.load.t07_classroom_load import BASELINE_PRODUCT_COMMIT, validate_run_kind


def verify_baseline_environment() -> None:
    target = urlsplit(os.environ.get("NLP_AGENT_DATABASE_URL", ""))
    if os.environ.get("NLP_AGENT_DEPLOYMENT_ENV") != "test" or target.scheme != "mysql+aiomysql" or target.hostname not in {"localhost", "127.0.0.1", "::1"} or not target.path.lstrip("/").startswith("nlp_agent_api_http_"):
        raise RuntimeError("baseline entrypoint requires a managed isolated loopback test database")
    validate_run_kind(Namespace(run_kind="baseline", baseline_ref=BASELINE_PRODUCT_COMMIT), Path("baseline.json"))


verify_baseline_environment()
from server.web.app import app  # noqa: E402


@app.middleware("http")
async def baseline_health_metadata(request: Request, call_next):
    response = await call_next(request)
    if request.url.path != "/health/ready":
        return response
    data = json.loads(b"".join([chunk async for chunk in response.body_iterator]))
    data["deployment_environment"] = "test"
    data["baseline_product_commit"] = BASELINE_PRODUCT_COMMIT
    headers = {key: value for key, value in response.headers.items() if key.lower() not in {"content-length", "content-type"}}
    return JSONResponse(data, status_code=response.status_code, headers=headers)
