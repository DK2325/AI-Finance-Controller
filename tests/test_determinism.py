"""Determinism against COMMITTED hashes, not against a second fresh generation.

tests/test_generator.py::test_same_seed_is_byte_identical generates two batches and
compares them to each other. That passes on any single machine even when the generator is
platform-dependent -- on Linux both fresh batches would agree with each other while
differing from the CSVs committed to this repo.

The api container is python:3.12-slim, i.e. Linux, so that was a live gap rather than a
theoretical one. data/HASHES.json records what the committed batches actually hash to,
and this test regenerates each one and compares against that.

If this fails, either the generator became platform-dependent or a batch was regenerated
without updating the hashes. Both are worth stopping for: the second silently replaces
the data every metric in the submission was computed against.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

import pytest

from datagen.generator import generate_to

REPO_ROOT = Path(__file__).resolve().parent.parent
HASHES = REPO_ROOT / "data" / "HASHES.json"


def load_manifest() -> dict:
    return json.loads(HASHES.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def batch_names() -> list[str]:
    return sorted(load_manifest()["batches"])


def test_the_hash_manifest_is_committed() -> None:
    assert HASHES.is_file(), "data/HASHES.json is missing - determinism is unverifiable"
    assert load_manifest()["batches"], "no batches recorded"


@pytest.mark.parametrize("name", batch_names())
def test_committed_batch_matches_its_recorded_hash(name: str) -> None:
    """Catches a batch edited or regenerated without refreshing the manifest."""
    spec = load_manifest()["batches"][name]
    batch_dir = REPO_ROOT / name

    for filename, expected in sorted(spec["sha256"].items()):
        actual = sha256(batch_dir / filename)
        assert actual == expected, (
            f"{name}/{filename} does not match its committed hash.\n"
            f"  expected {expected}\n  actual   {actual}"
        )


@pytest.mark.parametrize("name", batch_names())
def test_regenerating_reproduces_the_committed_bytes(name: str) -> None:
    """The real cross-platform check: regenerate from seed, compare to what is committed.

    This is what fails on Linux or inside the container if generation ever stops being
    platform-independent.
    """
    spec = load_manifest()["batches"][name]
    tmp = Path(tempfile.mkdtemp())

    try:
        generate_to(
            out_dir=tmp,
            rows=spec["rows"],
            seed=spec["seed"],
            exclude=tuple(spec["exclude"]),
        )
        mismatched = [
            filename
            for filename, expected in sorted(spec["sha256"].items())
            if sha256(tmp / filename) != expected
        ]
        assert not mismatched, (
            f"regenerating {name} (seed={spec['seed']}) did not reproduce the committed "
            f"bytes for: {mismatched}. The generator may have become platform-dependent."
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_no_committed_data_file_contains_crlf() -> None:
    """CRLF would make the hashes above unreproducible on a Windows checkout."""
    offenders = []
    for name in batch_names():
        for path in sorted((REPO_ROOT / name).glob("*")):
            if path.is_file() and path.suffix in (".csv", ".json"):
                if b"\r\n" in path.read_bytes():
                    offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, f"CRLF found in committed data: {offenders}"
