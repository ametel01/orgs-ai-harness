from __future__ import annotations

import sys
from pathlib import Path

import pytest

from orgs_ai_harness.llm_runner import is_progress_line, run_llm_command_with_progress


def test_llm_runner_writes_log_tail_and_progress(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "llm.log"

    result = run_llm_command_with_progress(
        [sys.executable, "-c", "print('thinking through plan')\nprint('final line')"],
        cwd=tmp_path,
        log_path=log_path,
        label="fixture generation",
    )

    assert result.returncode == 0
    assert result.tail == "thinking through plan\nfinal line"
    assert log_path.read_text(encoding="utf-8") == "thinking through plan\nfinal line\n"
    stderr = capsys.readouterr().err
    assert "fixture generation: started." in stderr
    assert "fixture generation: thinking through plan" in stderr
    assert "fixture generation: completed." in stderr


def test_llm_runner_preserves_failure_tail(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    log_path = tmp_path / "failure.log"
    script = "import sys\nfor index in range(12): print(f'line-{index}')\nsys.exit(7)"

    result = run_llm_command_with_progress(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        log_path=log_path,
        label="fixture failure",
    )

    assert result.returncode == 7
    assert result.tail.splitlines() == [f"line-{index}" for index in range(2, 12)]
    assert log_path.read_text(encoding="utf-8").splitlines() == [f"line-{index}" for index in range(12)]
    assert "fixture failure: failed with exit 7." in capsys.readouterr().err


def test_progress_line_detection_matches_generation_markers() -> None:
    assert is_progress_line("Generated draft pack")
    assert is_progress_line("validation complete")
    assert not is_progress_line("quiet heartbeat")
