"""The isolation boundary, enforced.

datagen/ produces the synthetic data AND the answer key. If core/, model/, llm/ or api/
can see either one, every number in the submission is worthless. BUILD.md requires this
test to exist before any matching code does -- so it is written in Phase 0, when the
packages it guards are still empty.

AST-based rather than grep-based: a comment mentioning truth.csv should not fail the
build, but a real import must, and a string literal naming the answer key must.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Packages that must never see the generator or the answer key.
QUARANTINED = ("core", "model", "llm", "api")

# Only evals/ may read both sides, and only to score predictions after the fact.
FORBIDDEN_MODULE = "datagen"
FORBIDDEN_ARTIFACTS = ("truth.csv", "truth.json")


def _python_files(package: str) -> list[Path]:
    return sorted((REPO_ROOT / package).rglob("*.py"))


def _all_quarantined_files() -> list[Path]:
    return [f for pkg in QUARANTINED for f in _python_files(pkg)]


def test_quarantined_packages_exist() -> None:
    """Guard against the lint silently passing because it is looking at nothing."""
    for package in QUARANTINED:
        assert (REPO_ROOT / package).is_dir(), f"{package}/ is missing"


@pytest.mark.parametrize("package", QUARANTINED)
def test_package_does_not_import_datagen(package: str) -> None:
    offenders: list[str] = []

    for path in _python_files(package):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == FORBIDDEN_MODULE or alias.name.startswith(
                        FORBIDDEN_MODULE + "."
                    ):
                        offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} "
                                         f"import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == FORBIDDEN_MODULE or module.startswith(FORBIDDEN_MODULE + "."):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} "
                                     f"from {module} import ...")

    assert not offenders, (
        f"{package}/ breaches the isolation boundary:\n  " + "\n  ".join(offenders)
    )


def test_no_quarantined_package_references_the_answer_key() -> None:
    """A string literal naming truth.csv is as much a breach as importing datagen."""
    offenders: list[str] = []

    for path in _all_quarantined_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for artifact in FORBIDDEN_ARTIFACTS:
                    if artifact in node.value:
                        offenders.append(
                            f"{path.relative_to(REPO_ROOT)}:{node.lineno} "
                            f"string literal contains {artifact!r}"
                        )

    assert not offenders, (
        "the answer key is referenced outside evals/:\n  " + "\n  ".join(offenders)
    )


def test_dynamic_import_of_datagen_is_also_caught() -> None:
    """importlib.import_module('datagen') would slip past a plain import check."""
    offenders: list[str] = []

    for path in _all_quarantined_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.attr if isinstance(func, ast.Attribute)
                else func.id if isinstance(func, ast.Name)
                else ""
            )
            if name not in {"import_module", "__import__"}:
                continue
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    if arg.value.split(".")[0] == FORBIDDEN_MODULE:
                        offenders.append(
                            f"{path.relative_to(REPO_ROOT)}:{node.lineno} "
                            f"dynamic import of {arg.value!r}"
                        )

    assert not offenders, (
        "datagen is imported dynamically:\n  " + "\n  ".join(offenders)
    )


def test_the_lint_actually_catches_a_breach(tmp_path: Path) -> None:
    """A passing lint is only reassuring if it can fail. Prove it on a synthetic breach."""
    breach = tmp_path / "offender.py"
    breach.write_text("from datagen.generator import make_batch\n", encoding="utf-8")

    tree = ast.parse(breach.read_text(encoding="utf-8"))
    found = any(
        isinstance(node, ast.ImportFrom) and (node.module or "").startswith(FORBIDDEN_MODULE)
        for node in ast.walk(tree)
    )
    assert found, "the import-lint logic failed to flag a deliberate breach"
