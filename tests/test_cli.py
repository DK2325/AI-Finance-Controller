"""The CLI is the canonical interface (there is no Makefile). All four commands must exist."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from ledgerloop.cli import app

runner = CliRunner()

COMMANDS = ["generate", "recon", "eval", "chaos"]


def test_help_lists_all_four_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in COMMANDS:
        assert command in result.stdout, f"{command} missing from --help"


@pytest.mark.parametrize("command", COMMANDS)
def test_each_command_has_its_own_help(command: str) -> None:
    result = runner.invoke(app, [command, "--help"])
    assert result.exit_code == 0


def test_generate_accepts_its_documented_flags(tmp_path) -> None:
    result = runner.invoke(
        app,
        ["generate", "--rows", "10", "--seed", "42", "--difficulty", "hard",
         "--out", str(tmp_path)],
    )
    assert result.exit_code == 0


def test_recon_accepts_mock_llm_without_an_api_key(tmp_path) -> None:
    """No API key exists until Phase 5. --mock-llm must work from day one."""
    result = runner.invoke(app, ["recon", "--in", str(tmp_path), "--mock-llm"])
    assert result.exit_code == 0


def test_eval_and_chaos_accept_a_run_id() -> None:
    assert runner.invoke(app, ["eval", "--run", "abc123"]).exit_code == 0
    assert runner.invoke(
        app, ["chaos", "--run", "abc123", "--corruption", "unseen_narration"]
    ).exit_code == 0


def test_difficulty_rejects_an_unknown_value(tmp_path) -> None:
    result = runner.invoke(
        app, ["generate", "--difficulty", "impossible", "--out", str(tmp_path)]
    )
    assert result.exit_code != 0
