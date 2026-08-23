"""Runtime configuration. Credentials come from the environment, never from source."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

DEFAULT_DATABASE_URL = "postgresql+psycopg://ledgerloop:ledgerloop@localhost:5432/ledgerloop"


def database_url() -> str:
    """Resolve the connection string. No hardcoded credentials in code paths."""
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


# NVIDIA NIM, OpenAI-compatible. See notes/decisions.md for why this provider.
DEFAULT_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_NVIDIA_MODEL = "nvidia/nemotron-3-super-120b-a12b"


def nvidia_api_key() -> str | None:
    """The key, or None. Callers must degrade to --mock-llm when this is None.

    Read from the environment only. There is no code path that accepts a key as an
    argument, writes one to disk, or logs one -- the value never leaves this function.
    """
    return os.environ.get("NVIDIA_API_KEY") or None


def nvidia_base_url() -> str:
    return os.environ.get("NVIDIA_BASE_URL") or DEFAULT_NVIDIA_BASE_URL


def nvidia_model() -> str:
    return os.environ.get("NVIDIA_MODEL") or DEFAULT_NVIDIA_MODEL


DEFAULT_LLM_PROVIDER = "nvidia"


def llm_provider_name() -> str:
    """Which provider to build, before the --mock-llm flag gets its say.

    Defaults to the mock when no key is present rather than raising, so every phase
    before this one keeps running on a machine that has never seen a credential. An
    explicit LLM_PROVIDER always wins over the default, including LLM_PROVIDER=mock on a
    machine that does have a key -- being able to force the mock is what makes a NIM
    outage a config change instead of a crisis.
    """
    explicit = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if explicit:
        return explicit
    return DEFAULT_LLM_PROVIDER if nvidia_api_key() else "mock"


def llm_available() -> bool:
    """Whether a real LLM call is possible. Never prints or returns the key itself."""
    return nvidia_api_key() is not None


def key_fingerprint() -> str:
    """A safe description of the configured key, for diagnostics.

    Deliberately returns a length and a short prefix, never the key. Anything that can
    print a secret eventually prints it into a log someone else reads.
    """
    key = nvidia_api_key()
    if key is None:
        return "not set"
    return f"set ({len(key)} chars, starts {key[:6]}...)"
