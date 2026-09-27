import io
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

from src.collect_codex import _failed_rollouts
from src.codex_runtime import (
    CodexExecution,
    CodexModelConfig,
    _stream_pipe,
    build_codex_trace,
    get_additive_instructions,
    get_codex_mcp_config,
    load_codex_events,
    render_codex_config,
    write_additive_instructions,
)
from src.task_setups import get_mcp_config, setup_workspace


def test_codex_config_uses_responses_api_and_stdio_mcp() -> None:
    model_config = CodexModelConfig(
        model="Qwen/Qwen3.5-9B",
        provider="benchmark",
        api_base="http://model.example/v1",
        api_key="EMPTY",
        api_key_env="CODEX_MODEL_API_KEY",
        context_window=150000,
        reasoning_effort="none",
    )

    rendered = render_codex_config(
        model_config,
        get_mcp_config("machine_operating_s2l", "/workspace/project"),
    )
    parsed = tomllib.loads(rendered)

    assert parsed["model"] == "Qwen/Qwen3.5-9B"
    assert parsed["model_providers"]["benchmark"]["wire_api"] == "responses"
    assert parsed["mcp_servers"]["google_cloud"]["command"] == "/workspace/.venv/bin/python"
    assert parsed["mcp_servers"]["google_cloud"]["required"] is True
    assert parsed["model_reasoning_effort"] == "none"
    assert parsed["sandbox_mode"] == "danger-full-access"
    assert "features" not in parsed


def test_system_prompt_is_written_as_additive_agents_instructions(tmp_path: Path) -> None:
    instructions_path = write_additive_instructions(tmp_path, "Use the benchmark MCP tools.")

    assert instructions_path.name == "AGENTS.md"
    assert instructions_path.read_text(encoding="utf-8") == (
        "# Benchmark task instructions\n\nUse the benchmark MCP tools.\n"
    )


def test_system_prompt_preserves_repository_agents_instructions(tmp_path: Path) -> None:
    instructions_path = tmp_path / "AGENTS.md"
    instructions_path.write_text("# Repository rules\n\nKeep public APIs stable.\n", encoding="utf-8")

    write_additive_instructions(tmp_path, "Complete the benchmark task.", "refactorbench")

    written = instructions_path.read_text(encoding="utf-8")
    assert written.startswith("# Repository rules\n\nKeep public APIs stable.\n\n")
    assert written.endswith("# Benchmark task instructions\n\nComplete the benchmark task.\n")


def test_webarena_uses_playwright_mcp_and_adds_tool_mapping() -> None:
    mcp_config = get_codex_mcp_config("webarena")
    browser = mcp_config["mcpServers"]["playwright"]

    assert browser["command"] == "playwright-mcp"
    assert "--headless" in browser["args"]
    assert "--isolated" in browser["args"]
    executable_index = browser["args"].index("--executable-path")
    assert browser["args"][executable_index + 1] == "/usr/local/bin/webarena-chromium"
    instructions = get_additive_instructions("webarena", "Original benchmark prompt.")
    assert instructions.startswith("Original benchmark prompt.")
    assert "browser_snapshot" in instructions
    assert "browser_get_state" in instructions


def test_codex_trace_preserves_events_output_and_usage(tmp_path: Path) -> None:
    events = [
        {"type": "thread.started", "thread_id": "thread-123"},
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "Task complete."},
        },
        {
            "type": "turn.completed",
            "usage": {"input_tokens": 10, "output_tokens": 4},
        },
    ]
    execution = CodexExecution(
        attempt_id="attempt-1",
        container_name="codex-test",
        codex_version="codex-cli 0.149.0",
        return_code=0,
        timed_out=False,
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:00:01+00:00",
        duration_seconds=1.0,
        events_path=str(tmp_path / "events.jsonl"),
        stderr_path=str(tmp_path / "stderr.log"),
    )

    trace = build_codex_trace(events, execution)

    assert trace["conversation_id"] == "thread-123"
    assert trace["eval_output"] == "Task complete."
    assert trace["events"] == events
    assert trace["metrics"]["accumulated_token_usage"]["input_tokens"] == 10
    assert trace["error"] is None


def test_codex_trace_clears_eval_output_for_truncated_event_stream(
    tmp_path: Path,
) -> None:
    events = [
        {"type": "thread.started", "thread_id": "thread-123"},
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "Must not be evaluated."},
        },
        {
            "type": "error",
            "error_type": "codex_events_json_decode_error",
            "line_number": 3,
        },
    ]
    execution = CodexExecution(
        attempt_id="attempt-1",
        container_name="codex-test",
        codex_version="codex-cli 0.149.0",
        return_code=137,
        timed_out=True,
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:00:01+00:00",
        duration_seconds=1.0,
        events_path=str(tmp_path / "events.jsonl"),
        stderr_path=str(tmp_path / "stderr.log"),
    )

    trace = build_codex_trace(events, execution)

    assert trace["eval_output"] == ""
    assert events[2] in trace["error"]


def test_shared_workspace_setup_is_writable_without_removing_executable_bits(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    log_dir = tmp_path / "logs"

    setup_workspace(
        "webtest",
        str(workspace),
        str(log_dir),
        {"html_content": "<h1>Test</h1>"},
    )

    assert workspace.stat().st_mode & 0o222 == 0o222
    assert (workspace / "index.html").stat().st_mode & 0o222 == 0o222


def test_codex_stream_is_written_to_file_and_console(
    tmp_path: Path,
    capsys,
) -> None:
    output_path = tmp_path / "events.jsonl"

    _stream_pipe(
        io.StringIO('{"type":"thread.started"}\n'),
        output_path,
        "[codex-test][event]",
    )

    assert output_path.read_text(encoding="utf-8") == '{"type":"thread.started"}\n'
    assert capsys.readouterr().out == (
        '[codex-test][event] {"type":"thread.started"}\n'
    )


def test_codex_event_loader_records_truncated_final_line_after_timeout(
    tmp_path: Path,
) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        '{"type":"thread.started","thread_id":"thread-123"}\n'
        '{"type":"item.completed","item":{"text":"unfinished',
        encoding="utf-8",
    )

    events = load_codex_events(events_path, allow_truncated_final_line=True)

    assert events[0]["thread_id"] == "thread-123"
    assert events[1]["type"] == "error"
    assert events[1]["error_type"] == "codex_events_json_decode_error"
    assert events[1]["line_number"] == 2


def test_codex_event_loader_rejects_invalid_json_without_timeout(
    tmp_path: Path,
) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text('{"type":"thread.started"', encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        load_codex_events(events_path)


def test_codex_event_loader_rejects_invalid_nonfinal_line_after_timeout(
    tmp_path: Path,
) -> None:
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(
        '{"type":"thread.started"\n'
        '{"type":"turn.completed"}\n',
        encoding="utf-8",
    )

    with pytest.raises(json.JSONDecodeError):
        load_codex_events(events_path, allow_truncated_final_line=True)


def test_codex_collector_import_does_not_load_openhands() -> None:
    script = (
        "import json, sys; import src.collect_codex; "
        "print(json.dumps(any(name == 'openhands' or name.startswith('openhands.') "
        "for name in sys.modules)))"
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) is False


def test_codex_collector_reports_nonzero_rollout_return_codes() -> None:
    results = {
        (0, 0): {"run_result": {"execution": {"return_code": 0}}},
        (1, 0): {"run_result": {"execution": {"return_code": 1}}},
        (2, 3): {"run_result": {"execution": {"return_code": 137}}},
    }

    assert _failed_rollouts(results) == [((1, 0), 1), ((2, 3), 137)]


def test_codex_task_images_extend_their_openhands_base_images() -> None:
    refactorbench_dockerfile = Path("tasks/Dockerfile.codex").read_text(
        encoding="utf-8"
    )
    loca_dockerfile = Path("tasks/Dockerfile.codex-loca").read_text(encoding="utf-8")
    webarena_dockerfile = Path("tasks/Dockerfile.codex-webarena").read_text(
        encoding="utf-8"
    )

    for dockerfile in (
        refactorbench_dockerfile,
        loca_dockerfile,
        webarena_dockerfile,
    ):
        assert "FROM ${BASE_IMAGE}" in dockerfile
        assert "@openai/codex@${CODEX_VERSION}" in dockerfile
        assert "uv sync" not in dockerfile
        assert "useradd" not in dockerfile

    assert "ARG BASE_IMAGE=refactorbench:latest" in refactorbench_dockerfile
    assert "ARG BASE_IMAGE=webarena:latest" in webarena_dockerfile
    assert "mcp_convert" not in loca_dockerfile
    assert "@playwright/mcp@${PLAYWRIGHT_MCP_VERSION}" in webarena_dockerfile
    assert "playwright install" not in webarena_dockerfile
    assert "chromium.executable_path" in webarena_dockerfile
    assert "WEBARENA_CHROMIUM_EXECUTABLE" in webarena_dockerfile

    webarena_base_dockerfile = Path("tasks/webarena/Dockerfile").read_text(
        encoding="utf-8"
    )
    assert "chown -R appuser:appuser /workspace/.cache/ms-playwright" in (
        webarena_base_dockerfile
    )


def test_compose_builds_task_specific_bases_and_codex_layers() -> None:
    compose = yaml.safe_load(Path("docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    expected = {
        "refactorbench": (
            "tasks/refactorbench/Dockerfile",
            "refactorbench_codex",
            "refactorbench:latest",
        ),
        "webarena": (
            "tasks/webarena/Dockerfile",
            "webarena_codex",
            "webarena:latest",
        ),
        "machine_operating_s2l": (
            "tasks/machine_operating_s2l/Dockerfile",
            "machine_operating_s2l_codex",
            "machine_operating_s2l:latest",
        ),
        "woocommerce_stock_alert_s2l": (
            "tasks/woocommerce_stock_alert_s2l/Dockerfile",
            "woocommerce_stock_alert_s2l_codex",
            "woocommerce_stock_alert_s2l:latest",
        ),
    }

    for base_service, (dockerfile, codex_service, base_image) in expected.items():
        assert services[base_service]["build"]["dockerfile"] == dockerfile
        assert services[codex_service]["build"]["args"]["BASE_IMAGE"] == base_image

    assert "PLAYWRIGHT_VERSION" not in services["webarena_codex"]["build"]["args"]
