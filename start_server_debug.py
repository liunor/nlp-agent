"""Start web server with explicit .env loading for debugging."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from server.web.__main__ import run

if __name__ == "__main__":
    print("NLP_AGENT_WEB_ALLOWED_ORIGINS =", os.environ.get("NLP_AGENT_WEB_ALLOWED_ORIGINS"))
    run()
