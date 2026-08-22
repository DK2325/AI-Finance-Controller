"""The seal on data/test/, as a mechanism rather than a discipline.

BUILD.md says the test set is sealed until Phase 7. Until now that was an intention, and
an intention is not a control. This is the same shape as tests/test_import_lint.py: if a
boundary matters enough to state, it matters enough to fail the build over.

Two things are enforced:

1.  **Integrity.** data/test/.sealed records a sha256 for every file in the directory.
    If any of them changes, the seal breaks and this test fails loudly. That catches an
    accidental regeneration, which would silently replace the held-out set with one the
    model may since have been tuned against.

2.  **Access.** No module outside evals/ may name data/test in a string literal. AST
    based, so a comment mentioning it is fine but a path that code could actually open
    is not.

Phase 7 breaks the seal deliberately, once, by deleting the marker in the same commit
that reports the test-set numbers. That makes unsealing an explicit, reviewable event in
the history rather than something that quietly happened.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from astutil import code_strings

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_DIR = REPO_ROOT / "data" / "test"
MARKER = TEST_DIR / ".sealed"

# evals/ may read both sides. Everything else may not touch the held-out set.
PERMITTED_PACKAGE = "evals"
GUARDED_PACKAGES = ("core", "model", "llm", "api", "datagen", "ledgerloop")

SEALED_PATH_TOKENS = ("data/test", "data\\test", "data/test/")


def test_the_seal_marker_exists() -> None:
    assert MARKER.is_file(), (
        "data/test/.sealed is missing. Either the test set was never generated, or the "
        "seal was removed outside Phase 7."
    )


def test_the_marker_says_it_is_still_sealed() -> None:
    marker = json.loads(MARKER.read_text(encoding="utf-8"))
    assert marker["sealed"] is True
    assert marker["unseal_at_phase"] == 7


def test_no_file_in_the_test_set_has_changed() -> None:
    """A regenerated test set is a different test set. Fail loudly if it moved."""
    marker = json.loads(MARKER.read_text(encoding="utf-8"))
    recorded = marker["sha256"]

    on_disk = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(TEST_DIR.glob("*"))
        if path.is_file() and not path.name.startswith(".")
    }

    assert set(on_disk) == set(recorded), (
        f"file set changed: added {sorted(set(on_disk) - set(recorded))}, "
        f"removed {sorted(set(recorded) - set(on_disk))}"
    )

    changed = [name for name, digest in recorded.items() if on_disk[name] != digest]
    assert not changed, (
        f"the sealed test set was modified: {changed}. If this was a deliberate "
        "regeneration, the held-out result is no longer out-of-sample."
    )


@pytest.mark.parametrize("package", GUARDED_PACKAGES)
def test_no_guarded_package_names_the_sealed_directory(package: str) -> None:
    offenders: list[str] = []

    for path in sorted((REPO_ROOT / package).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in code_strings(tree):
            normalised = node.value.replace("\\", "/")
            if any(token.replace("\\", "/") in normalised for token in SEALED_PATH_TOKENS):
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}:{node.lineno} -> {node.value!r}"
                )

    assert not offenders, (
        f"{package}/ references the sealed test set:\n  " + "\n  ".join(offenders)
    )


def test_only_evals_is_permitted_to_read_it() -> None:
    """Documents the exemption, and fails if evals/ is ever removed from the layout."""
    assert (REPO_ROOT / PERMITTED_PACKAGE).is_dir()
    assert PERMITTED_PACKAGE not in GUARDED_PACKAGES


def test_the_seal_check_actually_catches_tampering(tmp_path: Path) -> None:
    """A passing integrity check is only reassuring if it can fail."""
    victim = tmp_path / "sealed.csv"
    victim.write_text("a,b\n1,2\n", encoding="utf-8")
    recorded = hashlib.sha256(victim.read_bytes()).hexdigest()

    victim.write_text("a,b\n1,3\n", encoding="utf-8")
    assert hashlib.sha256(victim.read_bytes()).hexdigest() != recorded, (
        "the integrity check failed to notice a deliberate modification"
    )
