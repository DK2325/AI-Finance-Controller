"""Runtime configuration. Credentials come from the environment, never from source."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

DEFAULT_DATABASE_URL = "postgresql+psycopg://ledgerloop:ledgerloop@localhost:5432/ledgerloop"


def database_url() -> str:
    """Resolve the connection string. No hardcoded credentials in code paths."""
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def anthropic_api_key() -> str | None:
    """Absent until Phase 5. Callers must degrade to --mock-llm when this is None."""
    return os.environ.get("ANTHROPIC_API_KEY") or None
