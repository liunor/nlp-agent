"""Monitor entrypoint for the isolated real HTTP API test environment."""

from fastapi.responses import JSONResponse

from server.monitor.app import app


def _test_openapi() -> JSONResponse:
    """Expose the monitor schema only inside the test process."""

    return JSONResponse(app.openapi())


# The production monitor deliberately sets ``openapi_url=None``.  Keep that
# security choice intact while making the schema available to the test-only
# inventory client.  Excluding this route from the schema avoids counting it
# as an additional operation.
app.add_api_route(
    "/api/openapi.json",
    _test_openapi,
    methods=["GET"],
    include_in_schema=False,
)
