"""Shared AST helpers for the boundary guards.

The guards look for string literals naming files a package must not touch. A docstring
that *mentions* such a file cannot open it, so docstrings are excluded -- otherwise
documenting a boundary would break the build that enforces it, and the natural fix would
be to delete the explanation.
"""

from __future__ import annotations

import ast


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """ids of the Constant nodes that are docstrings."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                found.add(id(first.value))
    return found


def code_strings(tree: ast.AST) -> list[ast.Constant]:
    """Every string literal that is not a docstring -- i.e. one code could actually use."""
    docstrings = _docstring_nodes(tree)
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]
