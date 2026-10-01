"""Streamlit UI for comparing Codex and OpenHands benchmark rollouts."""

import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.rollout_dashboard_data import (
    discover_runs,
    load_comparison,
    load_trace_detail,
    overview_rows,
    pair_tool_calls,
    paired_overview_rows,
    preferred_run,
)


DEFAULT_RESULTS_ROOT = REPO_ROOT / "results"
OUTCOME_LABELS = {
    "both_correct": "Cả hai đúng",
    "left_only_correct": "Chỉ Codex đúng",
    "right_only_correct": "Chỉ OpenHands đúng",
    "both_incorrect": "Cả hai sai",
    "missing_left": "Thiếu Codex",
    "missing_right": "Thiếu OpenHands",
}
FILTERS = {
    "Khác kết quả": lambda row: row["different"],
    "Tất cả": lambda row: True,
    "Chỉ Codex đúng": lambda row: row["comparison"] == "left_only_correct",
    "Chỉ OpenHands đúng": lambda row: row["comparison"] == "right_only_correct",
    "Cả hai đúng": lambda row: row["comparison"] == "both_correct",
    "Cả hai sai": lambda row: row["comparison"] == "both_incorrect",
    "Có runtime error": lambda row: row["codex_status"] in {"timeout", "other_error"}
    or row["openhands_status"] in {"timeout", "other_error"},
}
STATUS_ICONS = {
    "completed": "✓",
    "error": "✕",
    "timeout": "◷",
    "missing": "—",
    "failed": "✕",
}


@st.cache_data(show_spinner=False)
def cached_discover_runs(results_root: str) -> list[dict[str, Any]]:
    return discover_runs(Path(results_root))


@st.cache_data(show_spinner=False)
def cached_overview(results_root: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    runs = discover_runs(Path(results_root))
    return overview_rows(runs), paired_overview_rows(runs)


@st.cache_data(show_spinner=False)
def cached_comparison(codex_dir: str, openhands_dir: str) -> dict[str, Any]:
    return load_comparison(Path(codex_dir), Path(openhands_dir))


@st.cache_data(show_spinner=False)
def cached_trace_detail(run_dir: str, rollout: str) -> dict[str, Any]:
    return load_trace_detail(Path(run_dir), rollout)


def inject_styles() -> None:
    st.markdown(
        """
        <style>
        .block-container { padding-top: 2rem; padding-bottom: 3rem; }
        [data-testid="stMainBlockContainer"] { max-width: 1540px; }
        [data-testid="stMetric"] {
            border: 1px solid rgba(128, 128, 128, 0.22);
            border-radius: 14px;
            padding: 0.8rem 1rem;
            background: rgba(128, 128, 128, 0.035);
        }
        [data-testid="stMetricValue"] { font-size: 1.75rem; }
        .run-path {
            font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
            color: #7b8494;
            font-size: 0.78rem;
            word-break: break-all;
        }
        .pass { color: #16a36a; font-weight: 700; }
        .fail { color: #e2534a; font-weight: 700; }
        [data-testid="stDataFrame"] {
            border: 1px solid rgba(128, 128, 128, 0.18);
            border-radius: 12px;
            overflow: hidden;
        }
        [data-testid="stExpander"] {
            border-color: rgba(128, 128, 128, 0.2);
            border-radius: 12px;
        }
        div[data-testid="stCode"] pre { font-size: 0.82rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_header() -> None:
    st.title("Rollout Explorer")
    st.caption(
        "Đọc, lọc và so sánh kết quả Codex ↔ OpenHands trên bốn benchmark từ các artifact trong results/."
    )


def render_overview(results_root: str) -> None:
    overview, paired = cached_overview(results_root)
    frame = pd.DataFrame(overview)
    paired_frame = pd.DataFrame(paired)

    st.subheader("Tổng quan 4 benchmark")
    columns = st.columns(len(paired_frame))
    for column, benchmark in zip(columns, sorted(frame["benchmark"].unique())):
        subset = frame[frame["benchmark"] == benchmark].set_index("agent")
        codex_accuracy = subset.loc["Codex", "accuracy"]
        openhands_accuracy = subset.loc["OpenHands", "accuracy"]
        with column:
            st.metric(
                benchmark.replace("_", " ").title(),
                f"{codex_accuracy:.1%}",
                f"{codex_accuracy - openhands_accuracy:+.1%} vs OpenHands",
            )

    chart = frame.pivot(index="benchmark", columns="agent", values="accuracy")
    st.bar_chart(chart, height=330, y_label="Accuracy", x_label="Benchmark")

    left, right = st.columns([1.15, 1])
    with left:
        st.markdown("#### Điểm theo run")
        display = frame.copy()
        display["accuracy"] = display["accuracy"].map(lambda value: f"{value:.1%}")
        display["score_mean"] = display["score_mean"].map(lambda value: f"{value:.3f}")
        st.dataframe(
            display.rename(
                columns={
                    "benchmark": "Benchmark",
                    "agent": "Agent",
                    "run": "Run",
                    "evaluated": "Evaluated",
                    "correct": "Correct",
                    "incorrect": "Incorrect",
                    "accuracy": "Accuracy",
                    "score_mean": "Mean score",
                }
            ),
            hide_index=True,
            width="stretch",
        )
    with right:
        st.markdown("#### Kết quả ghép cặp")
        st.dataframe(
            paired_frame.rename(
                columns={
                    "benchmark": "Benchmark",
                    "both_correct": "Both ✓",
                    "codex_only": "Codex only",
                    "openhands_only": "OpenHands only",
                    "both_incorrect": "Both ✗",
                    "different": "Different",
                }
            ),
            hide_index=True,
            width="stretch",
        )


def format_tokens(value: int | float | None) -> str:
    return "—" if value is None else f"{value:,.0f}"


def render_run_metrics(label: str, summary: dict[str, Any]) -> None:
    evaluated = summary["evaluated_rollouts"]
    correct = summary["evaluation_correct"]
    accuracy = correct / evaluated if evaluated else 0.0
    runtime_errors = summary["outcomes"]["timeout"] + summary["outcomes"]["other_error"]
    cols = st.columns(3)
    cols[0].metric(f"{label} accuracy", f"{accuracy:.1%}", f"{correct}/{evaluated}")
    cols[1].metric("Runtime errors", runtime_errors, f"{summary['outcomes']['timeout']} timeout")
    cols[2].metric("Mean tokens", format_tokens(summary["tokens_mean"]["total_tokens"]))


def filter_rows(report: dict[str, Any], selected_filter: str, query: str) -> list[dict[str, Any]]:
    predicate = FILTERS[selected_filter]
    lowered_query = query.lower().strip()
    return [
        row
        for row in report["rows"]
        if predicate(row)
        and (
            not lowered_query
            or lowered_query in row["rollout"].lower()
            or lowered_query in (row["left_feedback"] or "").lower()
            or lowered_query in (row["right_feedback"] or "").lower()
        )
    ]


def comparison_table(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Rollout": row["rollout"],
                "Codex": row["left_score"],
                "OpenHands": row["right_score"],
                "So sánh": OUTCOME_LABELS[row["comparison"]],
                "Codex runtime": row["codex_status"],
                "OpenHands runtime": row["openhands_status"],
                "Codex feedback": row["left_feedback"],
                "OpenHands feedback": row["right_feedback"],
            }
            for row in rows
        ]
    )


def format_trace_error(error: Any) -> str:
    if isinstance(error, (dict, list)):
        return json.dumps(error, ensure_ascii=False, indent=2)
    return str(error)


def status_text(status: str) -> str:
    return f"{STATUS_ICONS.get(status, '•')} {status}"


def render_trace_summary(
    label: str,
    run_dir: Path,
    rollout: str,
    score: float | None,
    feedback: str | None,
    detail: dict[str, Any],
) -> None:
    with st.container(border=True):
        st.markdown(f"### {label}")
        metrics = st.columns(3)
        metrics[0].metric("Score", "missing" if score is None else f"{score:g}")
        metrics[1].metric("Tool calls", len(detail["tool_calls"]))
        metrics[2].metric("Call issues", detail["error_calls"])

        if detail["error"]:
            st.error(format_trace_error(detail["error"]))
        else:
            st.success("Runtime hoàn tất")

        st.markdown("**Evaluator feedback**")
        st.write(feedback or "Không có feedback")
        tool_counts = " · ".join(
            f"`{tool}` × {count}" for tool, count in detail["tool_counts"].items()
        )
        if tool_counts:
            st.caption(tool_counts)
        st.markdown(
            f"<div class='run-path'>{run_dir / rollout}</div>",
            unsafe_allow_html=True,
        )


def call_matches(call: dict[str, Any] | None, query: str) -> bool:
    if call is None:
        return False
    searchable = " ".join(
        str(call[field])
        for field in ("tool", "summary", "thought", "arguments_preview", "result_preview")
    )
    return query in searchable.lower()


def paired_timeline_frame(pairs: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for pair in pairs:
        left = pair["left"]
        right = pair["right"]
        rows.append(
            {
                "Step": pair["step"],
                "Codex status": status_text(left["status"]) if left else "— missing",
                "Codex action": left["tool"] if left else "—",
                "Codex intent": (left["summary"] or left["arguments_preview"]) if left else "",
                "OpenHands status": status_text(right["status"]) if right else "— missing",
                "OpenHands action": right["tool"] if right else "—",
                "OpenHands intent": (right["summary"] or right["arguments_preview"])
                if right
                else "",
                "Tool match": "✓" if pair["same_tool"] else "≠",
            }
        )
    return pd.DataFrame(rows)


def render_arguments(call: dict[str, Any]) -> None:
    arguments = call["arguments"]
    if arguments in (None, "", {}):
        st.caption("Không có arguments")
        return
    if isinstance(arguments, dict) and "query" in arguments:
        st.code(str(arguments["query"]), language="sql", wrap_lines=True)
        remaining = {key: value for key, value in arguments.items() if key != "query"}
        if remaining:
            st.json(remaining, expanded=2)
        return
    if isinstance(arguments, str):
        language = "bash" if call["kind"] == "Terminal" else None
        st.code(arguments, language=language, wrap_lines=True, height=300)
        return
    st.json(arguments, expanded=2)


def render_call_panel(label: str, call: dict[str, Any] | None) -> None:
    with st.container(border=True):
        st.markdown(f"#### {label}")
        if call is None:
            st.warning("Agent này không có step tương ứng.")
            return

        heading = st.columns([3, 1])
        heading[0].markdown(f"**Step {call['step']} · `{call['tool']}`**")
        heading[1].markdown(f"**{status_text(call['status'])}**")
        metadata = call["kind"]
        if call["timestamp"]:
            metadata += f" · {call['timestamp']}"
        st.caption(metadata)

        if call["summary"]:
            st.info(call["summary"])
        if call["thought"]:
            with st.expander("Reasoning trước action", expanded=True):
                st.markdown(call["thought"])

        arguments_tab, result_tab = st.tabs(("Action input", "Tool output"))
        with arguments_tab:
            render_arguments(call)
        with result_tab:
            if call["result_text"]:
                st.code(call["result_text"], wrap_lines=True, height=360)
            else:
                st.caption("Không có output")


def render_paired_trace(
    codex_detail: dict[str, Any],
    openhands_detail: dict[str, Any],
    widget_key: str,
) -> None:
    pairs = pair_tool_calls(codex_detail["tool_calls"], openhands_detail["tool_calls"])
    controls = st.columns([1.5, 1])
    with controls[0]:
        query = st.text_input(
            "Tìm trong tool, reasoning, input hoặc output",
            placeholder="Ví dụ: upload, timeout, SQL…",
            key=f"paired-query-{widget_key}",
        ).lower()
    with controls[1]:
        mode = st.selectbox(
            "Lọc timeline",
            ("Tất cả step", "Khác tool", "Có lỗi hoặc thiếu"),
            key=f"paired-mode-{widget_key}",
        )

    filtered = [
        pair
        for pair in pairs
        if (not query or call_matches(pair["left"], query) or call_matches(pair["right"], query))
        and (mode != "Khác tool" or not pair["same_tool"])
        and (
            mode != "Có lỗi hoặc thiếu"
            or pair["left"] is None
            or pair["right"] is None
            or pair["left"]["status"] != "completed"
            or pair["right"]["status"] != "completed"
        )
    ]
    if not filtered:
        st.info("Không có step phù hợp bộ lọc.")
        return

    st.dataframe(
        paired_timeline_frame(filtered),
        hide_index=True,
        width="stretch",
        height=min(460, 42 + len(filtered) * 36),
        column_config={
            "Step": st.column_config.NumberColumn(width="small"),
            "Codex status": st.column_config.TextColumn(width="small"),
            "Codex action": st.column_config.TextColumn(width="medium"),
            "Codex intent": st.column_config.TextColumn(width="large"),
            "OpenHands status": st.column_config.TextColumn(width="small"),
            "OpenHands action": st.column_config.TextColumn(width="medium"),
            "OpenHands intent": st.column_config.TextColumn(width="large"),
            "Tool match": st.column_config.TextColumn(width="small"),
        },
    )

    selected = st.selectbox(
        "Mở chi tiết step",
        filtered,
        format_func=lambda pair: (
            f"Step {pair['step']} · "
            f"{pair['left']['tool'] if pair['left'] else '—'} ↔ "
            f"{pair['right']['tool'] if pair['right'] else '—'}"
        ),
        key=f"paired-step-{widget_key}",
    )
    columns = st.columns(2)
    with columns[0]:
        render_call_panel("Codex", selected["left"])
    with columns[1]:
        render_call_panel("OpenHands", selected["right"])


def timeline_frame(calls: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Step": call["step"],
                "Status": status_text(call["status"]),
                "Kind": call["kind"],
                "Tool": call["tool"],
                "Intent": call["summary"] or call["thought"],
                "Input": call["arguments_preview"],
                "Output": call["result_preview"],
            }
            for call in calls
        ]
    )


def render_agent_timeline(label: str, detail: dict[str, Any], widget_key: str) -> None:
    calls = detail["tool_calls"]
    controls = st.columns([1.5, 1, 1])
    with controls[0]:
        query = st.text_input(
            "Tìm trong trace",
            key=f"agent-query-{widget_key}",
            placeholder="Tool, command, output…",
        ).lower()
    with controls[1]:
        tool_filter = st.selectbox(
            "Tool",
            ("Tất cả", *detail["tool_counts"]),
            key=f"agent-tool-{widget_key}",
        )
    with controls[2]:
        status_filter = st.selectbox(
            "Status",
            ("Tất cả", "completed", "error", "timeout", "missing"),
            key=f"agent-status-{widget_key}",
        )

    filtered = [
        call
        for call in calls
        if (not query or call_matches(call, query))
        and (tool_filter == "Tất cả" or call["tool"] == tool_filter)
        and (status_filter == "Tất cả" or call["status"] == status_filter)
    ]
    if not filtered:
        st.info("Không có tool call phù hợp bộ lọc.")
        return

    st.dataframe(
        timeline_frame(filtered),
        hide_index=True,
        width="stretch",
        height=min(520, 42 + len(filtered) * 36),
        column_config={
            "Step": st.column_config.NumberColumn(width="small"),
            "Status": st.column_config.TextColumn(width="small"),
            "Kind": st.column_config.TextColumn(width="small"),
            "Tool": st.column_config.TextColumn(width="medium"),
            "Intent": st.column_config.TextColumn(width="large"),
            "Input": st.column_config.TextColumn(width="large"),
            "Output": st.column_config.TextColumn(width="large"),
        },
    )
    selected = st.selectbox(
        "Mở tool call",
        filtered,
        format_func=lambda call: f"Step {call['step']} · {status_text(call['status'])} · {call['tool']}",
        key=f"agent-step-{widget_key}",
    )
    render_call_panel(label, selected)


def render_raw_trace(label: str, detail: dict[str, Any], widget_key: str) -> None:
    st.markdown(f"#### {label}")
    if not detail["markdown"]:
        st.info("Không có trace Markdown.")
        return
    st.download_button(
        "Tải trace Markdown",
        detail["markdown"],
        file_name=detail["markdown_path"].name,
        mime="text/markdown",
        key=f"download-{widget_key}",
    )
    with st.expander("Xem raw trace", expanded=False):
        st.code(
            detail["markdown"],
            language="markdown",
            line_numbers=True,
            height=680,
            wrap_lines=True,
        )


def render_trace_explorer(
    codex: dict[str, Any],
    openhands: dict[str, Any],
    selected: dict[str, Any],
) -> None:
    rollout = selected["rollout"]
    codex_detail = cached_trace_detail(str(codex["path"]), rollout)
    openhands_detail = cached_trace_detail(str(openhands["path"]), rollout)
    summary_columns = st.columns(2)
    with summary_columns[0]:
        render_trace_summary(
            "Codex",
            codex["path"],
            rollout,
            selected["left_score"],
            selected["left_feedback"],
            codex_detail,
        )
    with summary_columns[1]:
        render_trace_summary(
            "OpenHands",
            openhands["path"],
            rollout,
            selected["right_score"],
            selected["right_feedback"],
            openhands_detail,
        )

    compare_tab, codex_tab, openhands_tab, raw_tab = st.tabs(
        ("↔ So sánh từng bước", "Codex timeline", "OpenHands timeline", "Raw artifacts")
    )
    with compare_tab:
        st.caption(
            "Các tool call được đặt cạnh nhau theo thứ tự thực thi; dấu ≠ cho biết hai agent chọn tool khác nhau ở cùng step."
        )
        render_paired_trace(codex_detail, openhands_detail, rollout)
    with codex_tab:
        render_agent_timeline("Codex", codex_detail, f"codex-{rollout}")
    with openhands_tab:
        render_agent_timeline("OpenHands", openhands_detail, f"openhands-{rollout}")
    with raw_tab:
        raw_columns = st.columns(2)
        with raw_columns[0]:
            render_raw_trace("Codex", codex_detail, f"codex-{rollout}")
        with raw_columns[1]:
            render_raw_trace("OpenHands", openhands_detail, f"openhands-{rollout}")


def render_comparison(runs: list[dict[str, Any]]) -> None:
    benchmarks = sorted({run["benchmark"] for run in runs})
    benchmark = st.selectbox("Benchmark", benchmarks)
    benchmark_runs = [run for run in runs if run["benchmark"] == benchmark]
    codex_runs = [run for run in benchmark_runs if run["agent"] == "Codex"]
    openhands_runs = [run for run in benchmark_runs if run["agent"] == "OpenHands"]
    preferred_codex = preferred_run(benchmark_runs, "Codex")
    preferred_openhands = preferred_run(benchmark_runs, "OpenHands")

    run_columns = st.columns(2)
    with run_columns[0]:
        codex = st.selectbox(
            "Codex run",
            codex_runs,
            index=codex_runs.index(preferred_codex),
            format_func=lambda run: f"{run['run_name']} · {run['evaluated']} evals",
        )
    with run_columns[1]:
        openhands = st.selectbox(
            "OpenHands run",
            openhands_runs,
            index=openhands_runs.index(preferred_openhands),
            format_func=lambda run: f"{run['run_name']} · {run['evaluated']} evals",
        )

    with st.spinner("Đang đọc trace metadata…"):
        report = cached_comparison(str(codex["path"]), str(openhands["path"]))

    metric_columns = st.columns(2)
    with metric_columns[0]:
        render_run_metrics("Codex", report["codex_runtime"])
    with metric_columns[1]:
        render_run_metrics("OpenHands", report["openhands_runtime"])

    st.markdown("#### Rollout results")
    filter_columns = st.columns([1, 1.4, 2])
    with filter_columns[0]:
        selected_filter = st.selectbox("Hiển thị", list(FILTERS))
    with filter_columns[1]:
        query = st.text_input("Tìm theo rollout hoặc feedback", placeholder="Ví dụ: timeout, missing email…")
    with filter_columns[2]:
        counts = report["counts"]
        st.caption(
            f"Both ✓ {counts['both_correct']} · Codex only {counts['left_only_correct']} · "
            f"OpenHands only {counts['right_only_correct']} · Both ✗ {counts['both_incorrect']}"
        )

    rows = filter_rows(report, selected_filter, query)
    if not rows:
        st.info("Không có rollout phù hợp bộ lọc.")
        return
    st.dataframe(comparison_table(rows), hide_index=True, width="stretch", height=360)

    selected = st.selectbox(
        "Mở chi tiết rollout",
        rows,
        format_func=lambda row: (
            f"{row['rollout']} · Codex {row['left_score']} / OpenHands {row['right_score']} · "
            f"{OUTCOME_LABELS[row['comparison']]}"
        ),
    )
    render_trace_explorer(codex, openhands, selected)


def main() -> None:
    st.set_page_config(page_title="Rollout Explorer", page_icon="◫", layout="wide")
    inject_styles()
    render_header()

    with st.sidebar:
        st.header("Nguồn dữ liệu")
        results_root = st.text_input("Results directory", str(DEFAULT_RESULTS_ROOT))
        if st.button("Quét lại artifacts", width="stretch"):
            st.cache_data.clear()
        page = st.radio("Trang", ("Tổng quan", "So sánh & đọc trace"))
        st.caption("UI chỉ đọc artifact; không thay đổi kết quả rollout.")

    runs = cached_discover_runs(results_root)
    if not runs:
        st.error(f"Không tìm thấy eval_results.yaml của Codex/OpenHands trong {results_root}")
        return
    if page == "Tổng quan":
        render_overview(results_root)
    else:
        render_comparison(runs)


if __name__ == "__main__":
    main()
