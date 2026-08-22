"""Guards against a credential reaching the repository.

The submission repo is going public on 5 September. A key committed once is a key that
lives in git history forever, and rotating it afterwards is the only fix -- so this fails
the build rather than relying on anyone remembering.

Same standard as the import lint and the seal: if it matters enough to say, it matters
enough to enforce.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from astutil import code_strings

REPO_ROOT = Path(__file__).resolve().parent.parent

# Packages whose source must never contain a literal credential.
SOURCE_PACKAGES = ("ledgerloop", "core", "model", "llm", "api", "evals", "datagen")

# NVIDIA NIM keys are "nvapi-" followed by a long token. The others are here because a
# project that grows a second provider grows a second way to leak.
KEY_PATTERNS = (
    re.compile(r"nvapi-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{32,}"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
)


def test_dotenv_is_not_tracked_by_git() -> None:
    """.env holds the real key. It must never be in the index."""
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", ".env"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0, ".env IS TRACKED BY GIT - remove it from the index now"


def test_dotenv_is_gitignored() -> None:
    ignored = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in [line.strip() for line in ignored], ".env is not in .gitignore"


def test_the_example_env_carries_no_value() -> None:
    """.env.example is committed, so its keys must be empty."""
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    for line in text.splitlines():
        if "API_KEY" in line and not line.strip().startswith("#"):
            _, _, value = line.partition("=")
            assert value.strip() == "", f"a key has a value in .env.example: {line!r}"


@pytest.mark.parametrize("package", SOURCE_PACKAGES)
def test_no_source_file_contains_a_credential(package: str) -> None:
    import ast

    offenders: list[str] = []
    for path in sorted((REPO_ROOT / package).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in code_strings(tree):
            for pattern in KEY_PATTERNS:
                if pattern.search(node.value):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")

    assert not offenders, f"credential-shaped literal in {package}/: {offenders}"


def test_no_tracked_file_contains_a_credential() -> None:
    """Broader sweep: every tracked text file, not just Python sources."""
    listing = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )

    offenders: list[str] = []
    for name in listing.stdout.splitlines():
        path = REPO_ROOT / name
        if not path.is_file() or path.suffix in {".png", ".jpg", ".pdf", ".pkl"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern in KEY_PATTERNS:
            if pattern.search(text):
                offenders.append(name)
                break

    assert not offenders, f"credential-shaped string in tracked files: {offenders}"


def test_the_key_fingerprint_never_returns_the_key(monkeypatch) -> None:
    """Diagnostics must describe the key, never reveal it.

    Anything that can print a secret eventually prints it into a log someone else reads.
    """
    from ledgerloop.config import key_fingerprint

    # Assembled at runtime rather than written as one literal, so this file does not
    # itself contain a credential-shaped string. The alternative -- excusing this file
    # from the sweep -- would leave the one file most likely to grow a real key as the
    # only file nobody checks.
    secret = "nvapi-" + "F4KE" * 8
    monkeypatch.setenv("NVIDIA_API_KEY", secret)

    fingerprint = key_fingerprint()
    assert secret not in fingerprint
    assert secret[6:] not in fingerprint
    assert "set (" in fingerprint


def test_the_fingerprint_says_so_when_no_key_is_configured(monkeypatch) -> None:
    from ledgerloop.config import key_fingerprint, llm_available

    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    assert key_fingerprint() == "not set"
    assert llm_available() is False
