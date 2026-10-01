"""Data loading and normalization for the rollout comparison dashboard."""

import json
import re
from pathlib import Path
from typing import Any

from src.benchmark_rollout_summary import summarize
from src.compare_rollout_results import compare_rollout_results, load_evaluations


AGENT_LABELS = {
    "codex": "Codex",
    "openhands": "OpenHands",
}
ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def infer_agent(run_name: str) -> str | None:
    lowered = run_name.lower()
    for agent, label in AGENT_LABELS.items():
        if agent in lowered:
            return label
    return None


def discover_runs(results_root: Path) -> list[dict[str, Any]]:
    runs = []
    for benchmark_dir in sorted(path for path in results_root.iterdir() if path.is_dir()):
        for model_dir in sorted(path for path in benchmark_dir.iterdir() if path.is_dir()):
            rollouts_dir = model_dir / "rollouts"
            if not rollouts_dir.is_dir():
                continue
            for run_dir in sorted(path for path in rollouts_dir.iterdir() if path.is_dir()):
                eval_path = run_dir / "eval_results.yaml"
                agent = infer_agent(run_dir.name)
                if agent is None or not eval_path.is_file():
                    continue
                runs.append(
                    {
                        "benchmark": benchmark_dir.name,
                        "model": model_dir.name,
                        "agent": agent,
                        "run_name": run_dir.name,
                        "path": run_dir,
                        "evaluated": len(load_evaluations(run_dir)),
                        "modified": eval_path.stat().st_mtime,
                    }
                )
    return runs


def preferred_run(runs: list[dict[str, Any]], agent: str) -> dict[str, Any]:
    candidates = [run for run in runs if run["agent"] == agent]
    return max(
        candidates,
        key=lambda run: (
            "_30x2_" in run["run_name"],
            run["evaluated"],
            run["modified"],
        ),
    )


def evaluation_summary(run_dir: Path, pass_score: float = 1.0) -> dict[str, Any]:
    evaluations = load_evaluations(run_dir)
    scores = [float(record["score"]) for record in evaluations.values()]
    correct = sum(score >= pass_score for score in scores)
    return {
        "evaluated": len(scores),
        "correct": correct,
        "incorrect": len(scores) - correct,
        "accuracy": correct / len(scores),
        "score_mean": sum(scores) / len(scores),
    }


def overview_rows(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    benchmarks = sorted({run["benchmark"] for run in runs})
    for benchmark in benchmarks:
        benchmark_runs = [run for run in runs if run["benchmark"] == benchmark]
        for agent in ("Codex", "OpenHands"):
            run = preferred_run(benchmark_runs, agent)
            summary = evaluation_summary(run["path"])
            rows.append(
                {
                    "benchmark": benchmark,
                    "agent": agent,
                    "run": run["run_name"],
                    **summary,
                }
            )
    return rows


def paired_overview_rows(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    benchmarks = sorted({run["benchmark"] for run in runs})
    for benchmark in benchmarks:
        benchmark_runs = [run for run in runs if run["benchmark"] == benchmark]
        codex = preferred_run(benchmark_runs, "Codex")
        openhands = preferred_run(benchmark_runs, "OpenHands")
        report = compare_rollout_results(codex["path"], openhands["path"])
        rows.append(
            {
                "benchmark": benchmark,
                "both_correct": report["counts"]["both_correct"],
                "codex_only": report["counts"]["left_only_correct"],
                "openhands_only": report["counts"]["right_only_correct"],
                "both_incorrect": report["counts"]["both_incorrect"],
                "different": report["different_results"],
            }
        )
    return rows


def load_comparison(codex_dir: Path, openhands_dir: Path) -> dict[str, Any]:
    report = compare_rollout_results(codex_dir, openhands_dir)
    codex_summary = summarize(codex_dir)
    openhands_summary = summarize(openhands_dir)
    codex_cases = {case["rollout"]: case for case in codex_summary["cases"]}
    openhands_cases = {case["rollout"]: case for case in openhands_summary["cases"]}
    for row in report["rows"]:
        row["codex_status"] = codex_cases.get(row["rollout"], {}).get("status", "missing")
        row["openhands_status"] = openhands_cases.get(row["rollout"], {}).get("status", "missing")
    report["codex_runtime"] = codex_summary
    report["openhands_runtime"] = openhands_summary
    return report


def trace_paths(run_dir: Path, rollout: str) -> tuple[Path | None, Path | None]:
    logs_dir = run_dir / f"{rollout}_logs"
    json_paths = sorted(logs_dir.glob("trace_*.json"))
    markdown_paths = sorted(logs_dir.glob("trace_*.md"))
    if len(json_paths) > 1 or len(markdown_paths) > 1:
        raise ValueError(f"Multiple traces found for {rollout} in {logs_dir}")
    json_path = json_paths[0] if json_paths else None
    markdown_path = markdown_paths[0] if markdown_paths else None
    return json_path, markdown_path


def _compact(value: Any, limit: int = 500) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _content_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return ANSI_ESCAPE.sub("", value)
    if isinstance(value, list):
        return "\n".join(filter(None, (_content_text(item) for item in value)))
    if isinstance(value, dict):
        if "content" in value:
            return _content_text(value["content"])
        if "text" in value:
            return str(value["text"])
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


def _tool_kind(tool_name: str) -> str:
    if tool_name == "terminal":
        return "Terminal"
    if tool_name == "file_editor":
        return "File"
    if tool_name in {"finish", "think"}:
        return "Agent"
    return "MCP"


def _openhands_status(outcome: dict[str, Any]) -> str:
    if not outcome:
        return "missing"
    if outcome.get("kind") == "AgentErrorEvent":
        return "error"
    observation = outcome.get("observation") or {}
    if observation.get("is_error"):
        return "error"
    if observation.get("timeout"):
        return "timeout"
    exit_code = observation.get("exit_code")
    if exit_code is not None and exit_code != 0:
        return "error"
    return "completed"


def _openhands_tool_calls(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    outcomes = {
        event.get("tool_call_id"): event
        for event in events
        if event.get("kind") in {"ObservationEvent", "AgentErrorEvent"}
    }
    calls = []
    for event in events:
        if event.get("kind") != "ActionEvent":
            continue
        action = event.get("action") or {}
        tool_name = event.get("tool_name") or action.get("kind", "unknown")
        outcome = outcomes.get(event.get("tool_call_id"), {})
        if "data" in action:
            arguments = action["data"]
        elif "command" in action:
            arguments = action["command"]
        else:
            arguments = {key: value for key, value in action.items() if key != "kind"}
        observation = outcome.get("observation") or {}
        result = outcome.get("error") or observation.get("content") or observation
        thought = _content_text(event.get("thought"))
        calls.append(
            {
                "step": len(calls) + 1,
                "tool": tool_name,
                "kind": _tool_kind(tool_name),
                "summary": event.get("summary") or "",
                "thought": thought,
                "timestamp": event.get("timestamp") or "",
                "arguments": arguments,
                "arguments_preview": _compact(arguments, 180),
                "status": _openhands_status(outcome),
                "result": result,
                "result_text": _content_text(result),
                "result_preview": _compact(_content_text(result), 240),
            }
        )
    return calls


def _codex_tool_calls(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calls = []
    pending_messages = []
    for event in events:
        if event.get("type") != "item.completed":
            continue
        item = event.get("item", {})
        item_type = item.get("type")
        if item_type == "agent_message":
            message = (item.get("text") or "").strip()
            if message:
                pending_messages.append(message)
            continue
        if item_type not in {"mcp_tool_call", "command_execution"}:
            continue
        if item_type == "mcp_tool_call":
            tool_name = f"{item['server']}.{item['tool']}"
            arguments = item.get("arguments")
            result = item.get("result")
        else:
            tool_name = "terminal"
            arguments = item.get("command")
            result = item.get("aggregated_output")
        thought = "\n\n".join(pending_messages)
        pending_messages = []
        status = item.get("status", "completed")
        if (
            item.get("error")
            or status == "failed"
            or (item_type == "command_execution" and item.get("exit_code") not in (None, 0))
        ):
            status = "error"
        calls.append(
            {
                "step": len(calls) + 1,
                "tool": tool_name,
                "kind": _tool_kind(tool_name),
                "summary": _compact(thought, 140),
                "thought": thought,
                "timestamp": "",
                "arguments": arguments,
                "arguments_preview": _compact(arguments, 180),
                "status": status,
                "result": item.get("error") or result,
                "result_text": _content_text(item.get("error") or result),
                "result_preview": _compact(_content_text(item.get("error") or result), 240),
            }
        )
    return calls


def extract_tool_calls(trace: dict[str, Any]) -> list[dict[str, Any]]:
    events = trace["events"]
    if any(event.get("kind") == "ActionEvent" for event in events):
        return _openhands_tool_calls(events)
    return _codex_tool_calls(events)


def pair_tool_calls(
    left_calls: list[dict[str, Any]], right_calls: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    def tool_key(call: dict[str, Any]) -> str:
        return call["tool"].split(".")[-1]

    left_count = len(left_calls)
    right_count = len(right_calls)
    matches = [[0] * (right_count + 1) for _ in range(left_count + 1)]
    for left_index in range(left_count - 1, -1, -1):
        for right_index in range(right_count - 1, -1, -1):
            if tool_key(left_calls[left_index]) == tool_key(right_calls[right_index]):
                matches[left_index][right_index] = 1 + matches[left_index + 1][right_index + 1]
            else:
                matches[left_index][right_index] = max(
                    matches[left_index + 1][right_index],
                    matches[left_index][right_index + 1],
                )

    rows = []
    left_index = 0
    right_index = 0
    while left_index < left_count or right_index < right_count:
        left = left_calls[left_index] if left_index < left_count else None
        right = right_calls[right_index] if right_index < right_count else None
        same_tool = (
            left is not None and right is not None and tool_key(left) == tool_key(right)
        )
        if same_tool:
            left_index += 1
            right_index += 1
        elif right is None or (
            left is not None
            and matches[left_index + 1][right_index] >= matches[left_index][right_index + 1]
        ):
            right = None
            left_index += 1
        else:
            left = None
            right_index += 1
        rows.append(
            {
                "step": len(rows) + 1,
                "left": left,
                "right": right,
                "same_tool": same_tool,
            }
        )
    return rows


def load_trace_detail(run_dir: Path, rollout: str) -> dict[str, Any]:
    json_path, markdown_path = trace_paths(run_dir, rollout)
    trace = json.loads(json_path.read_text(encoding="utf-8")) if json_path else None
    markdown = markdown_path.read_text(encoding="utf-8") if markdown_path else ""
    tool_calls = extract_tool_calls(trace) if trace else []
    return {
        "json_path": json_path,
        "markdown_path": markdown_path,
        "markdown": markdown,
        "error": trace.get("error") if trace else "Trace JSON not found",
        "tool_calls": tool_calls,
        "tool_counts": {
            tool: sum(call["tool"] == tool for call in tool_calls)
            for tool in sorted({call["tool"] for call in tool_calls})
        },
        "error_calls": sum(call["status"] != "completed" for call in tool_calls),
        "execution": trace.get("execution") if trace else None,
        "metrics": trace.get("metrics") if trace else None,
    }
