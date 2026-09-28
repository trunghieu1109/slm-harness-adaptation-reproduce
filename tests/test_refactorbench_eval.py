"""Tests for the RefactorBench evaluator."""

import subprocess
from pathlib import Path

from src.task_evals.refactorbench import run_single_instance_eval


def test_eval_runs_with_uv_managed_python311(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured_command: list[str] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_command.extend(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = run_single_instance_eval(
        workspace_dir=str(tmp_path),
        example={
            "id": "example-id",
            "repo_name": "example-repo",
            "eval_script": "print('ok')",
        },
    )

    assert captured_command[:6] == [
        "uv",
        "run",
        "--no-project",
        "--python",
        "3.11",
        "python",
    ]
    assert captured_command[6] == "eval_script.py"
    assert result["score"] == 1.0
