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
        ["generate", "--rows", "10", "--seed", "42", "--out", str(tmp_path)],
    )
    assert result.exit_code == 0


def test_recon_accepts_mock_llm_without_an_api_key(monkeypatch) -> None:
    """No API key exists until Phase 5. --mock-llm must run the full pipeline without one."""
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)

    result = runner.invoke(
        app, ["recon", "--in", "data/demo", "--mock-llm", "--run", "cli-smoke"]
    )
    assert result.exit_code == 0, result.output
    assert "matches" in result.output
    assert "not calibrated probabilities" in result.output, (
        "an uncalibrated run must say so on every invocation"
    )


def test_recon_fails_cleanly_on_a_batch_that_is_not_there(tmp_path) -> None:
    result = runner.invoke(app, ["recon", "--in", str(tmp_path), "--mock-llm"])
    assert result.exit_code != 0


def test_chaos_runs_a_corruption_and_reports_the_comparison() -> None:
    """Failing gracefully IS the pass condition, so the command reports both sides.

    --no-model-interpret because the deterministic keyword path is what has to carry a
    live demonstration; the LLM interpreter is layered on with automatic fallback.
    """
    result = runner.invoke(
        app,
        ["chaos", "--in", "data/demo", "--corruption", "swap the date format",
         "--no-model-interpret"],
    )
    assert result.exit_code == 0, result.output
    assert "date_format_swap" in result.output
    assert "clean" in result.output and "corrupted" in result.output
    assert "GRACEFUL" in result.output


def test_chaos_names_which_path_interpreted_the_request() -> None:
    result = runner.invoke(
        app,
        ["chaos", "--in", "data/demo", "--corruption", "truncate the narrations",
         "--share", "0.2", "--no-model-interpret"],
    )
    assert result.exit_code == 0, result.output
    assert "interpreted by keyword" in result.output


def test_eval_fails_cleanly_on_an_unknown_run() -> None:
    """eval does real work from Phase 2, so a missing run must be an error, not a no-op.

    Exit code 2 with a message naming the available runs, rather than a traceback.
    """
    result = runner.invoke(app, ["eval", "--run", "no-such-run-exists", "--no-readme"])
    assert result.exit_code == 2
    assert "no run" in result.output.lower() or "no run" in str(result.stderr).lower()


def test_the_difficulty_flag_is_gone_rather_than_ignored(tmp_path) -> None:
    """It was accepted and did nothing for five phases.

    A flag that lies about what it does is worse than an absent flag: it invites someone
    to rely on it. Compound-case generation is a datagen feature this submission does not
    need, so the flag is removed rather than wired.
    """
    result = runner.invoke(
        app, ["generate", "--difficulty", "hard", "--out", str(tmp_path)]
    )
    assert result.exit_code != 0, "an unknown flag must be refused, not silently ignored"
