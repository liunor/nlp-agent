"""Isolated load-test startup: real application/model runtime, loopback fake I/O."""

import os
from tests.load.model_configuration import configure_models


configure_models()
if os.environ.get("T07_LOAD_RUN_KIND") == "baseline":
    from tests.load.baseline_entrypoint import app  # noqa: F401,E402
else:
    from server.web.app import app  # noqa: F401,E402
