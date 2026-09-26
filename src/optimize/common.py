import copy
import difflib
import hashlib
import os
import yaml
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path, PurePosixPath
from typing import Any, Optional, Sequence

from openhands.sdk import Agent, Conversation, LLM
from openhands.sdk.workspace import RemoteWorkspace

from ..task_setups import get_mcp_config, setup_proposer_workspace


class LiteralBlockDumper(yaml.Dumper):
    """YAML dumper that renders multiline strings as block literals (|)."""

    def ignore_aliases(self, data):
        return True


def _literal_str_representer(dumper: yaml.Dumper, data: str) -> yaml.ScalarNode:
    if "\n" in data:
        return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", data)


LiteralBlockDumper.add_representer(str, _literal_str_representer)


def execute_agent_candidate(
    code: str,
    base_dir: str,
    llm: LLM,
) -> Agent:
    """Execute candidate code and return the constructed Agent."""
    namespace = {}
    exec(code, namespace)
    return namespace["build_agent"](base_dir, llm)


def extract_workspace_scripts(code: str) -> dict[str, str]:
    """Execute candidate code and extract optional workspace scripts."""
    namespace = {}
    exec(code, namespace)
    fn = namespace.get("get_workspace_scripts")
    if fn is None:
        return {}
    return fn()


def _validate_worker(code: str, llm: LLM, task_id: Optional[str]) -> tuple[bool, str]:
    """Subprocess worker for validate_agent_candidate."""
    import tempfile

    from openhands.sdk import Agent as _Agent
    from openhands.sdk import Conversation as _Conversation

    project_root = Path(__file__).resolve().parent.parent.parent
    validate_root = project_root / ".validate"
    validate_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(validate_root)) as tmp_dir:
        if task_id:
            setup_proposer_workspace(task_id, tmp_dir)

        try:
            namespace = {}
            exec(code, namespace)
            agent = namespace["build_agent"](tmp_dir, llm)
            if not isinstance(agent, _Agent):
                return False, f"build_agent returned {type(agent).__name__}, expected Agent"
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

        conversation = None
        try:
            conversation = _Conversation(agent=agent, workspace=tmp_dir)
            conversation.send_message("test")
        except Exception as e:
            return False, f"{type(e).__name__} during conversation init: {e}"
        finally:
            if conversation is not None:
                try:
                    conversation.close()
                except Exception:
                    pass

        return True, ""


def _build_agent_config_worker(
    code: str,
    llm: LLM,
    task_id: Optional[str],
    local_workspace: str,
) -> tuple[bool, str, Optional[dict[str, Any]]]:
    """Build a candidate in an isolated subprocess and serialize its public config."""
    if task_id:
        setup_proposer_workspace(task_id, local_workspace)

    try:
        agent = execute_agent_candidate(code, local_workspace, llm)
        if not isinstance(agent, Agent):
            return (
                False,
                f"build_agent returned {type(agent).__name__}, expected Agent",
                None,
            )
        return True, "", agent.model_dump(mode="python")
    except Exception as e:
        return False, f"{type(e).__name__}: {e}", None


def _build_agent_in_subprocess(
    code: str,
    llm: LLM,
    task_id: Optional[str],
    local_workspace: str,
) -> tuple[bool, str, Optional[Agent]]:
    with ProcessPoolExecutor(max_workers=1, max_tasks_per_child=1) as pool:
        future = pool.submit(
            _build_agent_config_worker,
            code,
            llm,
            task_id,
            local_workspace,
        )
        success, error, agent_config = future.result()

    if not success:
        return False, error, None
    assert agent_config is not None
    return True, "", Agent.model_validate(agent_config)


def _remap_agent_to_remote_workspace(
    agent: Agent,
    local_workspace: str,
    remote_workspace: str,
    task_id: Optional[str],
) -> Agent:
    """Remap host paths embedded in an Agent to an existing remote workspace."""
    local_workspace = os.path.abspath(local_workspace)
    prompt_name = os.path.basename(agent.system_prompt_filename)
    updates: dict[str, Any] = {
        "system_prompt_filename": str(PurePosixPath(remote_workspace) / prompt_name),
    }

    if task_id:
        local_mcp = get_mcp_config(task_id, local_workspace)
        if local_mcp and agent.mcp_config == local_mcp:
            updates["mcp_config"] = get_mcp_config(task_id, remote_workspace)

    remapped_tools = []
    for tool in agent.tools:
        if tool.params:
            params = {
                key: value.replace(local_workspace, remote_workspace, 1)
                if isinstance(value, str) and value.startswith(local_workspace)
                else value
                for key, value in tool.params.items()
            }
            remapped_tools.append(tool.model_copy(update={"params": params}))
        else:
            remapped_tools.append(tool)
    updates["tools"] = remapped_tools

    return agent.model_copy(update=updates)


def _validate_in_remote_workspace(
    code: str,
    llm: LLM,
    task_id: Optional[str],
    workspace: RemoteWorkspace,
    local_workspace: str,
    remote_workspace: str,
) -> tuple[bool, str]:
    """Validate a candidate using an already-running remote workspace server."""
    local_workspace = os.path.abspath(local_workspace)
    os.makedirs(local_workspace, exist_ok=True)
    if task_id:
        setup_proposer_workspace(task_id, local_workspace)

    success, error, agent = _build_agent_in_subprocess(
        code,
        llm,
        task_id,
        local_workspace,
    )
    if not success:
        return False, error
    assert agent is not None

    try:
        agent = _remap_agent_to_remote_workspace(
            agent,
            local_workspace,
            remote_workspace,
            task_id,
        )
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"

    for root, dirs, files in os.walk(local_workspace):
        os.chmod(root, 0o777)
        for directory in dirs:
            os.chmod(os.path.join(root, directory), 0o777)
        for filename in files:
            os.chmod(os.path.join(root, filename), 0o666)

    validation_workspace = RemoteWorkspace(
        host=workspace.host,
        api_key=workspace.api_key,
        working_dir=remote_workspace,
        read_timeout=workspace.read_timeout,
        max_connections=workspace.max_connections,
    )
    conversation = None
    try:
        conversation = Conversation(
            agent=agent,
            workspace=validation_workspace,
            visualizer=None,
        )
        conversation.send_message("test")
    except Exception as e:
        return False, f"{type(e).__name__} during conversation init: {e}"
    finally:
        if conversation is not None:
            conversation.close()

    return True, ""


def validate_agent_candidate(
    code: str,
    llm: LLM,
    task_id: Optional[str] = None,
    workspace: Optional[RemoteWorkspace] = None,
    local_workspace: Optional[str] = None,
    remote_workspace: Optional[str] = None,
) -> tuple[bool, str]:
    """Validate candidate code by compiling, executing, and building the Agent."""
    try:
        compile(code, "agent.py", "exec")
    except SyntaxError as e:
        return False, f"SyntaxError: {e}"

    if workspace is not None:
        if local_workspace is None or remote_workspace is None:
            raise ValueError(
                "local_workspace and remote_workspace are required when reusing "
                "a remote workspace"
            )
        return _validate_in_remote_workspace(
            code,
            llm,
            task_id,
            workspace,
            local_workspace,
            remote_workspace,
        )

    with ProcessPoolExecutor(max_workers=1, max_tasks_per_child=1) as pool:
        future = pool.submit(_validate_worker, code, llm, task_id)
        return future.result()


def format_eval_feedback(output: dict, score: float) -> str:
    """Build rich evaluation feedback from an eval result dict."""
    parts = [f"Score: {score}"]

    feedback = output.get("feedback")
    if feedback:
        parts.append(feedback)

    if "error" in output and output["error"]:
        parts.append(f"Error: {output['error']}")

    test_output = output.get("test_output")
    if test_output:
        for fr in test_output:
            if fr.get("status") in ("failed", "error", "timeout"):
                file_line = f"[{fr['status'].upper()}] {fr.get('file', '?')}"
                stderr = fr.get("stderr", "").strip()
                stdout = fr.get("stdout", "").strip()
                if stderr:
                    file_line += f"\n{stderr[:500]}"
                elif stdout:
                    file_line += f"\n{stdout[:500]}"
                parts.append(file_line)

    return "\n".join(parts)


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def empty_cost_bucket() -> dict:
    return {
        "accumulated_cost": 0.0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "reasoning_tokens": 0,
    }


def add_to_cost_bucket(bucket: dict, metrics: dict) -> None:
    bucket["accumulated_cost"] += metrics.get("accumulated_cost", 0.0)
    usage = metrics.get("accumulated_token_usage") or {}
    bucket["prompt_tokens"] += usage.get("prompt_tokens", 0)
    bucket["completion_tokens"] += usage.get("completion_tokens", 0)
    bucket["cache_read_tokens"] += usage.get("cache_read_tokens", 0)
    bucket["cache_write_tokens"] += usage.get("cache_write_tokens", 0)
    bucket["reasoning_tokens"] += usage.get("reasoning_tokens", 0)


def extract_dspy_cost(lm, history_len_before: int) -> Optional[dict]:
    """Extract cost incurred by a dspy LM since history_len_before."""
    if lm is None or not hasattr(lm, "history"):
        return None
    new_calls = lm.history[history_len_before:]
    if not new_calls:
        return None
    cost = sum(c.get("cost", 0.0) or 0.0 for c in new_calls)
    prompt_tokens = 0
    completion_tokens = 0
    for c in new_calls:
        usage = c.get("usage") or {}
        prompt_tokens += usage.get("prompt_tokens", 0) or 0
        completion_tokens += usage.get("completion_tokens", 0) or 0
    return {
        "accumulated_cost": cost,
        "accumulated_token_usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "reasoning_tokens": 0,
        },
    }


def build_code_diff(old_text: str, new_text: str, max_lines: int = 120) -> str:
    diff_lines = list(
        difflib.unified_diff(
            old_text.splitlines(),
            new_text.splitlines(),
            fromfile="current.py",
            tofile="proposed.py",
            lineterm="",
        )
    )
    if not diff_lines:
        return "(no code changes)"
    if len(diff_lines) > max_lines:
        diff_lines = diff_lines[:max_lines] + ["... diff truncated ..."]
    return "\n".join(diff_lines)


def summarize_score_changes(
    batch: Sequence[dict[str, Any]],
    before_scores: Sequence[float],
    after_scores: Sequence[float],
    limit: int = 3,
) -> list[dict[str, Any]]:
    changes = []
    for example, before, after in zip(batch, before_scores, after_scores):
        delta = after - before
        if abs(delta) < 1e-9:
            continue
        changes.append(
            {
                "task_input": example.get("prompt", "")[:300],
                "before": before,
                "after": after,
                "delta": delta,
            }
        )

    changes.sort(key=lambda item: (item["delta"], abs(item["delta"])))
    return changes[:limit]


def resolve_markdown_path(markdown_path: str, docs_dir: str) -> str:
    """Resolve a markdown path from absolute, cwd-relative, or docs-relative input."""
    candidate = Path(markdown_path)
    if candidate.is_absolute():
        resolved = candidate
    else:
        cwd_relative = Path.cwd() / candidate
        docs_relative = Path(docs_dir) / candidate
        if cwd_relative.exists():
            resolved = cwd_relative
        else:
            resolved = docs_relative
    resolved = resolved.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Markdown file not found: {markdown_path}")
    return str(resolved)


def clone_example(example: dict) -> dict:
    return copy.deepcopy(example)
