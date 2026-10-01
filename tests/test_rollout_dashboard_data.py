import yaml

from src.rollout_dashboard_data import (
    discover_runs,
    extract_tool_calls,
    overview_rows,
    preferred_run,
)


def write_run(root, benchmark, model, run_name, records):
    run_dir = root / benchmark / model / "rollouts" / run_name
    run_dir.mkdir(parents=True)
    evaluations = [
        {
            "workspace_dir": str(run_dir / f"example{example}_rollout{rollout}"),
            "score": score,
            "feedback": feedback,
        }
        for example, rollout, score, feedback in records
    ]
    (run_dir / "eval_results.yaml").write_text(yaml.safe_dump(evaluations))
    return run_dir


def test_discovery_and_overview_prefer_full_runs(tmp_path):
    write_run(tmp_path, "bench", "model", "codex_smoke", [(0, 0, 1, "ok")])
    full_codex = write_run(
        tmp_path,
        "bench",
        "model",
        "bench_codex_30x2_stamp",
        [(0, 0, 1, "ok"), (0, 1, 0, "bad")],
    )
    write_run(
        tmp_path,
        "bench",
        "model",
        "bench_openhands_30x2_stamp",
        [(0, 0, 1, "ok"), (0, 1, 1, "ok")],
    )

    runs = discover_runs(tmp_path)

    assert len(runs) == 3
    assert preferred_run(runs, "Codex")["path"] == full_codex
    rows = overview_rows(runs)
    assert rows == [
        {
            "benchmark": "bench",
            "agent": "Codex",
            "run": "bench_codex_30x2_stamp",
            "evaluated": 2,
            "correct": 1,
            "incorrect": 1,
            "accuracy": 0.5,
            "score_mean": 0.5,
        },
        {
            "benchmark": "bench",
            "agent": "OpenHands",
            "run": "bench_openhands_30x2_stamp",
            "evaluated": 2,
            "correct": 2,
            "incorrect": 0,
            "accuracy": 1.0,
            "score_mean": 1.0,
        },
    ]


def test_extract_openhands_tool_calls_pairs_errors_and_observations():
    trace = {
        "events": [
            {
                "kind": "ActionEvent",
                "tool_name": "sheet_update",
                "tool_call_id": "a",
                "action": {"data": {"range": "A2:H3"}},
            },
            {
                "kind": "AgentErrorEvent",
                "tool_name": "sheet_update",
                "tool_call_id": "a",
                "error": "missing range",
            },
            {
                "kind": "ActionEvent",
                "tool_name": "email_send",
                "tool_call_id": "b",
                "action": {"data": {"to": "manager@example.com"}},
            },
            {
                "kind": "ObservationEvent",
                "tool_name": "email_send",
                "tool_call_id": "b",
                "observation": {"content": [{"type": "text", "text": "sent"}]},
            },
        ]
    }

    calls = extract_tool_calls(trace)

    assert [call["tool"] for call in calls] == ["sheet_update", "email_send"]
    assert [call["status"] for call in calls] == ["error", "completed"]
    assert "missing range" in calls[0]["result"]


def test_extract_codex_tool_calls_keeps_completed_items_only():
    item = {
        "id": "item_1",
        "type": "mcp_tool_call",
        "server": "google_sheet",
        "tool": "update_cells",
        "arguments": {"range": "A3:H4"},
        "result": {"content": [{"type": "text", "text": "updated"}]},
        "error": None,
        "status": "completed",
    }
    trace = {
        "events": [
            {"type": "item.started", "item": {**item, "status": "in_progress"}},
            {"type": "item.completed", "item": item},
        ]
    }

    calls = extract_tool_calls(trace)

    assert len(calls) == 1
    assert calls[0]["tool"] == "google_sheet.update_cells"
    assert "A3:H4" in calls[0]["arguments"]
