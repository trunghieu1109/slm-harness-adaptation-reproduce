"""Codex CLI runtime for isolated LOCA benchmark task containers."""

import json
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .task_setups import get_mcp_config, setup_workspace


CODEX_CONTAINER_WORKSPACE = "/workspace/project"
CODEX_CONTAINER_HOME = "/workspace/.codex"
CONSOLE_OUTPUT_LOCK = threading.Lock()
WEBARENA_CODEX_INSTRUCTIONS = """
## Codex browser-tool mapping

The browser is provided by the Playwright MCP server. Use `browser_navigate` to
open a URL and `browser_snapshot` to inspect the current page and obtain element
references. Use the Playwright MCP click, type, select, keyboard, and navigation
tools with those references. When older benchmark instructions mention
`browser_get_state` or `browser_get_content`, use `browser_snapshot` instead.
"""


@dataclass(frozen=True)
class CodexModelConfig:
    model: str
    provider: str
    api_base: str
    api_key: str
    api_key_env: str
    context_window: int
    reasoning_effort: str


@dataclass(frozen=True)
class CodexExecution:
    attempt_id: str
    container_name: str
    codex_version: str
    return_code: int
    timed_out: bool
    started_at: str
    finished_at: str
    duration_seconds: float
    events_path: str
    stderr_path: str


def _resolve_environment_value(value: Any) -> Any:
    if not isinstance(value, str) or not value.startswith("${") or not value.endswith("}"):
        return value
    variable_name = value[2:-1]
    return os.environ[variable_name]


def load_codex_model_config(
    model_name: str,
    models_path: str | Path = "configs/models.yaml",
) -> CodexModelConfig:
    with open(models_path, "r", encoding="utf-8") as handle:
        models_config = yaml.safe_load(handle)

    model_entry = next(
        entry for entry in models_config["models"] if entry["name"] == model_name
    )
    codex_config = model_entry["codex"]
    return CodexModelConfig(
        model=codex_config["model"],
        provider=codex_config["provider"],
        api_base=_resolve_environment_value(model_entry["api_base"]),
        api_key=_resolve_environment_value(model_entry["api_key"]),
        api_key_env=codex_config["api_key_env"],
        context_window=model_entry["max_input_tokens"],
        reasoning_effort=model_entry["reasoning_effort"],
    )


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_string_list(values: list[str]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def render_codex_config(
    model_config: CodexModelConfig,
    mcp_config: dict[str, Any],
) -> str:
    lines = [
        f"model = {_toml_string(model_config.model)}",
        f"model_provider = {_toml_string(model_config.provider)}",
        f"model_context_window = {model_config.context_window}",
        f"model_reasoning_effort = {_toml_string(model_config.reasoning_effort)}",
        'approval_policy = "never"',
        'sandbox_mode = "danger-full-access"',
        "",
        f"[model_providers.{_toml_string(model_config.provider)}]",
        'name = "Benchmark OpenAI-compatible endpoint"',
        f"base_url = {_toml_string(model_config.api_base)}",
        f"env_key = {_toml_string(model_config.api_key_env)}",
        'wire_api = "responses"',
    ]

    for server_name, server in mcp_config.get("mcpServers", {}).items():
        server_table = f"mcp_servers.{_toml_string(server_name)}"
        lines.extend(
            [
                "",
                f"[{server_table}]",
                f"command = {_toml_string(server['command'])}",
                f"args = {_toml_string_list(server.get('args', []))}",
                "required = true",
                "startup_timeout_sec = 30",
                "tool_timeout_sec = 120",
            ]
        )
        if server.get("env"):
            lines.extend(["", f"[{server_table}.env]"])
            for key, value in server["env"].items():
                lines.append(f"{_toml_string(key)} = {_toml_string(str(value))}")

    return "\n".join(lines) + "\n"


def get_codex_mcp_config(task_id: str) -> dict[str, Any]:
    if task_id == "webarena":
        return {
            "mcpServers": {
                "playwright": {
                    "command": "playwright-mcp",
                    "args": [
                        "--headless",
                        "--isolated",
                        "--no-sandbox",
                        "--output-dir",
                        f"{CODEX_CONTAINER_WORKSPACE}/.playwright-mcp",
                    ],
                }
            }
        }
    return get_mcp_config(task_id, CODEX_CONTAINER_WORKSPACE)


def get_additive_instructions(task_id: str, system_prompt: str) -> str:
    if task_id == "webarena":
        return system_prompt.strip() + "\n\n" + WEBARENA_CODEX_INSTRUCTIONS.strip()
    return system_prompt.strip()


def write_additive_instructions(
    workspace_dir: Path,
    system_prompt: str,
    task_id: str = "",
) -> Path:
    instructions_path = workspace_dir / "AGENTS.md"
    existing_instructions = (
        instructions_path.read_text(encoding="utf-8").rstrip() + "\n\n"
        if instructions_path.exists()
        else ""
    )
    instructions_path.write_text(
        existing_instructions
        + "# Benchmark task instructions\n\n"
        + get_additive_instructions(task_id, system_prompt)
        + "\n",
        encoding="utf-8",
    )
    return instructions_path


def _console_log(prefix: str, message: str) -> None:
    with CONSOLE_OUTPUT_LOCK:
        print(f"{prefix} {message}", flush=True)


def _stream_pipe(pipe: Any, output_path: Path, console_prefix: str) -> None:
    with open(output_path, "w", encoding="utf-8") as output:
        for line in iter(pipe.readline, ""):
            output.write(line)
            output.flush()
            with CONSOLE_OUTPUT_LOCK:
                print(
                    f"{console_prefix} {line}",
                    end="" if line.endswith("\n") else "\n",
                    flush=True,
                )
    pipe.close()


def _run_codex_exec(
    container_name: str,
    prompt: str,
    max_time: float,
    events_path: Path,
    stderr_path: Path,
) -> tuple[int, bool]:
    command = [
        "docker",
        "exec",
        "--interactive",
        container_name,
        "codex",
        "exec",
        "--strict-config",
        "--json",
        "--sandbox",
        "danger-full-access",
        "--skip-git-repo-check",
        "--cd",
        CODEX_CONTAINER_WORKSPACE,
        "-",
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    stdout_thread = threading.Thread(
        target=_stream_pipe,
        args=(process.stdout, events_path, f"[{container_name}][event]"),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_stream_pipe,
        args=(process.stderr, stderr_path, f"[{container_name}][stderr]"),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    process.stdin.write(prompt)
    process.stdin.close()
    _console_log(f"[{container_name}]", "Codex task prompt submitted")
    timed_out = False
    try:
        return_code = process.wait(timeout=max_time)
    except subprocess.TimeoutExpired:
        timed_out = True
        _console_log(
            f"[{container_name}]",
            f"Timeout reached after {max_time} seconds; stopping container",
        )
        subprocess.run(
            ["docker", "kill", container_name],
            check=False,
            capture_output=True,
            text=True,
        )
        process.kill()
        return_code = process.wait()

    stdout_thread.join()
    stderr_thread.join()
    return return_code, timed_out


def load_codex_events(events_path: Path) -> list[dict[str, Any]]:
    events = []
    with open(events_path, "r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                events.append(json.loads(line))
    return events


def _extract_codex_result(events: list[dict[str, Any]]) -> dict[str, Any]:
    thread_id = None
    final_output = ""
    usage = {}
    errors = []

    for event in events:
        if event.get("type") == "thread.started":
            thread_id = event["thread_id"]
        if event.get("type") == "turn.completed":
            usage = event.get("usage", usage)
        if event.get("type") in {"error", "turn.failed"}:
            errors.append(event)
        item = event.get("item")
        if (
            event.get("type") == "item.completed"
            and isinstance(item, dict)
            and item.get("type") == "agent_message"
        ):
            final_output = item.get("text", "")

    return {
        "thread_id": thread_id,
        "final_output": final_output,
        "usage": usage,
        "errors": errors,
    }


def build_codex_trace(
    events: list[dict[str, Any]],
    execution: CodexExecution,
) -> dict[str, Any]:
    extracted = _extract_codex_result(events)
    usage = extracted["usage"]
    metrics = {
        "accumulated_cost": 0.0,
        "accumulated_token_usage": usage,
        "token_usages": [usage] if usage else [],
    }
    errors = list(extracted["errors"])
    if execution.timed_out:
        errors.append({"type": "timeout", "max_time_reached": True})
    elif execution.return_code != 0:
        errors.append(
            {"type": "codex_exec_failed", "return_code": execution.return_code}
        )

    return {
        "trace_schema": "codex_exec_v1",
        "harness": "codex_exec",
        "conversation_id": extracted["thread_id"] or execution.attempt_id,
        "eval_output": extracted["final_output"],
        "events": events,
        "metrics": metrics,
        "metrics_breakdown": {"default": metrics},
        "subagents": {},
        "error": errors or None,
        "execution": {
            "attempt_id": execution.attempt_id,
            "container_name": execution.container_name,
            "codex_version": execution.codex_version,
            "return_code": execution.return_code,
            "timed_out": execution.timed_out,
            "started_at": execution.started_at,
            "finished_at": execution.finished_at,
            "duration_seconds": execution.duration_seconds,
            "events_path": execution.events_path,
            "stderr_path": execution.stderr_path,
        },
    }


def _write_trace_markdown(trace: dict[str, Any], path: Path) -> None:
    parts = [
        "# Codex execution trace",
        "",
        f"- Conversation: `{trace['conversation_id']}`",
        f"- Return code: `{trace['execution']['return_code']}`",
        f"- Timed out: `{trace['execution']['timed_out']}`",
        "",
        "## Events",
        "",
    ]
    for event in trace["events"]:
        parts.extend(["```json", json.dumps(event, ensure_ascii=False, indent=2), "```", ""])
    path.write_text("\n".join(parts), encoding="utf-8")


def run_codex_sample(
    task_id: str,
    example: dict[str, Any],
    system_prompt: str,
    workspace_dir: str | Path,
    server_image: str,
    model_config: CodexModelConfig,
    max_time: float,
    docker_network: str | None = None,
) -> dict[str, Any]:
    workspace = Path(workspace_dir).resolve()
    log_dir = workspace.parent / f"{workspace.name}_logs"
    run_prefix = (
        f"[codex:{task_id}:{example['example_id']}:{example['rollout_id']}]"
    )
    _console_log(
        run_prefix,
        f"Preparing workspace={workspace} image={server_image}",
    )
    setup_workspace(task_id, str(workspace), str(log_dir), example)
    instructions_path = write_additive_instructions(workspace, system_prompt, task_id)
    _console_log(run_prefix, f"Wrote additive instructions to {instructions_path}")

    attempt_id = uuid.uuid4().hex
    container_name = f"codex-{task_id.replace('_', '-')}-{attempt_id[:12]}"
    config_dir = workspace.parent / f"{workspace.name}_codex_home_{attempt_id}"
    config_dir.mkdir(parents=True, exist_ok=False)
    os.chmod(config_dir, 0o777)

    container_mcp_config = get_codex_mcp_config(task_id)
    config_path = config_dir / "config.toml"
    config_path.write_text(
        render_codex_config(model_config, container_mcp_config),
        encoding="utf-8",
    )
    mcp_server_names = list(container_mcp_config.get("mcpServers", {}))
    _console_log(
        run_prefix,
        f"Wrote Codex config for model={model_config.model}; "
        f"MCP servers={mcp_server_names}",
    )
    events_path = log_dir / f"codex_events_{attempt_id}.jsonl"
    stderr_path = log_dir / f"codex_stderr_{attempt_id}.log"

    docker_command = [
        "docker",
        "run",
        "--detach",
        "--name",
        container_name,
        "--workdir",
        CODEX_CONTAINER_WORKSPACE,
        "--env",
        model_config.api_key_env,
        "--volume",
        f"{workspace}:{CODEX_CONTAINER_WORKSPACE}",
        "--volume",
        f"{config_dir}:{CODEX_CONTAINER_HOME}",
    ]
    if docker_network:
        docker_command.extend(["--network", docker_network])
    docker_command.append(server_image)

    docker_env = os.environ.copy()
    docker_env[model_config.api_key_env] = model_config.api_key
    started_at = datetime.now(timezone.utc)
    started_monotonic = time.monotonic()
    subprocess.run(
        docker_command,
        check=True,
        capture_output=True,
        text=True,
        env=docker_env,
    )
    _console_log(run_prefix, f"Started container {container_name}")
    try:
        version_result = subprocess.run(
            ["docker", "exec", container_name, "codex", "--version"],
            check=True,
            capture_output=True,
            text=True,
        )
        _console_log(
            run_prefix,
            f"Running {version_result.stdout.strip()} in {container_name}",
        )
        return_code, timed_out = _run_codex_exec(
            container_name=container_name,
            prompt=example["prompt"],
            max_time=max_time,
            events_path=events_path,
            stderr_path=stderr_path,
        )
    finally:
        subprocess.run(
            ["docker", "rm", "--force", container_name],
            check=False,
            capture_output=True,
            text=True,
        )
        _console_log(run_prefix, f"Removed container {container_name}")

    finished_at = datetime.now(timezone.utc)
    execution = CodexExecution(
        attempt_id=attempt_id,
        container_name=container_name,
        codex_version=version_result.stdout.strip(),
        return_code=return_code,
        timed_out=timed_out,
        started_at=started_at.isoformat(),
        finished_at=finished_at.isoformat(),
        duration_seconds=time.monotonic() - started_monotonic,
        events_path=events_path.name,
        stderr_path=stderr_path.name,
    )
    events = load_codex_events(events_path)
    trace = build_codex_trace(events, execution)
    trace_id = trace["conversation_id"]
    trace_path = log_dir / f"trace_{trace_id}.json"
    raw_trace_path = log_dir / f"raw_trace_{trace_id}.json"
    trace_markdown_path = log_dir / f"trace_{trace_id}.md"
    trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
    raw_trace_path.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_trace_markdown(trace, trace_markdown_path)

    status = "completed" if return_code == 0 and not timed_out else "failed"
    run_metadata = {
        "harness": "codex_exec",
        "status": status,
        "attempt_id": attempt_id,
        "conversation_id": trace_id,
        "trace_file": trace_path.name,
        "raw_trace_file": raw_trace_path.name,
        "events_file": events_path.name,
        "stderr_file": stderr_path.name,
        "codex_home": config_dir.name,
        "codex_version": execution.codex_version,
        "return_code": return_code,
        "timed_out": timed_out,
        "started_at": execution.started_at,
        "finished_at": execution.finished_at,
        "duration_seconds": execution.duration_seconds,
    }
    (log_dir / "run_meta.json").write_text(
        json.dumps(run_metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _console_log(
        run_prefix,
        f"Finished status={status} return_code={return_code} "
        f"duration={execution.duration_seconds:.1f}s trace={trace_path}",
    )

    result = dict(example)
    result["run_result"] = trace
    return result
