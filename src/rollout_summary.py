import argparse
import json
import re
from pathlib import Path
from typing import Any

import yaml


ROLLOUT_NAME_PATTERN = re.compile(r"example(?P<example>\d+)_rollout(?P<rollout>\d+)")
CONTEXT_ERROR_PATTERN = re.compile(
    r"context window|context_length_exceeded|maximum context length|"
    r"too many tokens|prompt is too long",
    re.IGNORECASE,
)
NEAR_CONTEXT_LIMIT_RATIO = 0.9
TOKEN_FIELDS = (
    "prompt_tokens",
    "completion_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)


def rollout_key(value: str) -> tuple[int, int]:
    match = ROLLOUT_NAME_PATTERN.search(value)
    if match is None:
        raise ValueError(f"Cannot determine example/rollout IDs from: {value}")
    return int(match.group("example")), int(match.group("rollout"))


def rollout_label(key: tuple[int, int]) -> str:
    return f"example{key[0]}_rollout{key[1]}"


def load_results(rollout_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    run_path = rollout_dir / "run.json"
    eval_path = rollout_dir / "eval_results.yaml"

    with run_path.open(encoding="utf-8") as handle:
        runs = json.load(handle)
    with eval_path.open(encoding="utf-8") as handle:
        evaluations = yaml.safe_load(handle)

    if not isinstance(runs, list):
        raise TypeError(f"Expected a list in {run_path}")
    if not isinstance(evaluations, list):
        raise TypeError(f"Expected a list in {eval_path}")
    return runs, evaluations


def summarize_tokens(
    runs: list[dict[str, Any]],
) -> tuple[dict[str, int], list[tuple[int, int]]]:
    totals = {field: 0 for field in TOKEN_FIELDS}
    missing_metrics = []

    for run in runs:
        key = (int(run["example_id"]), int(run["rollout_id"]))
        usage = run.get("run_result", {}).get("metrics", {}).get(
            "accumulated_token_usage"
        )
        if usage is None:
            missing_metrics.append(key)
            continue
        for field in TOKEN_FIELDS:
            totals[field] += int(usage.get(field, 0) or 0)

    return totals, missing_metrics


def collect_runtime_errors(
    runs: list[dict[str, Any]],
) -> dict[tuple[int, int], str]:
    errors = {}
    for run in runs:
        error = run.get("run_result", {}).get("error")
        if error:
            key = (int(run["example_id"]), int(run["rollout_id"]))
            errors[key] = str(error).strip()
    return errors


def analyze_context_usage(
    rollout_dir: Path,
    key: tuple[int, int],
    runtime_error: str | None,
) -> dict[str, Any]:
    log_dir = rollout_dir / f"{rollout_label(key)}_logs"
    trace_paths = [
        path
        for path in log_dir.glob("trace_*.json")
        if not path.name.startswith("raw_trace_")
    ]
    if not trace_paths:
        return {"status": "missing_trace"}

    trace_path = max(trace_paths, key=lambda path: path.stat().st_mtime)
    with trace_path.open(encoding="utf-8") as handle:
        trace = json.load(handle)

    token_usages = trace.get("metrics", {}).get("token_usages", [])
    trace_error = trace.get("error")
    combined_error = "\n".join(
        str(error) for error in (runtime_error, trace_error) if error
    )
    context_error = bool(CONTEXT_ERROR_PATTERN.search(combined_error))

    if not token_usages:
        return {
            "status": "context_error" if context_error else "missing_token_usage",
            "trace_path": trace_path,
            "context_error": context_error,
        }

    last_usage = token_usages[-1]
    peak_usage = max(token_usages, key=lambda usage: int(usage["per_turn_token"]))
    context_window = int(last_usage["context_window"])
    last_tokens = int(last_usage["per_turn_token"])
    peak_tokens = int(peak_usage["per_turn_token"])
    last_ratio = last_tokens / context_window
    peak_ratio = peak_tokens / context_window

    if context_error:
        status = "context_error"
    elif peak_tokens >= context_window:
        status = "limit_reached"
    elif peak_ratio >= NEAR_CONTEXT_LIMIT_RATIO:
        status = "near_limit"
    else:
        status = "below_limit"

    return {
        "status": status,
        "trace_path": trace_path,
        "llm_calls": len(token_usages),
        "last_tokens": last_tokens,
        "peak_tokens": peak_tokens,
        "context_window": context_window,
        "last_ratio": last_ratio,
        "peak_ratio": peak_ratio,
        "context_error": context_error,
    }


def print_summary(rollout_dir: Path) -> None:
    runs, evaluations = load_results(rollout_dir)
    eval_by_key = {
        rollout_key(str(evaluation["workspace_dir"])): evaluation
        for evaluation in evaluations
    }
    run_keys = {
        (int(run["example_id"]), int(run["rollout_id"])) for run in runs
    }

    score = sum(float(evaluation["score"]) for evaluation in evaluations)
    maximum_score = len(evaluations)
    score_percent = score / maximum_score * 100 if maximum_score else 0.0
    passed = sum(float(evaluation["score"]) >= 1.0 for evaluation in evaluations)
    failed_evaluations = {
        key: evaluation
        for key, evaluation in eval_by_key.items()
        if float(evaluation["score"]) < 1.0
    }

    token_totals, missing_metrics = summarize_tokens(runs)
    total_tokens = (
        token_totals["prompt_tokens"] + token_totals["completion_tokens"]
    )
    runtime_errors = collect_runtime_errors(runs)
    missing_evaluations = run_keys - set(eval_by_key)
    error_keys = sorted(
        set(failed_evaluations) | set(runtime_errors) | missing_evaluations
    )
    context_usage = {
        key: analyze_context_usage(rollout_dir, key, runtime_errors.get(key))
        for key in error_keys
    }
    context_limit_cases = sum(
        usage["status"] in {"context_error", "limit_reached"}
        for usage in context_usage.values()
    )
    near_context_limit_cases = sum(
        usage["status"] == "near_limit" for usage in context_usage.values()
    )

    print(f"Rollout directory: {rollout_dir.resolve()}")
    print()
    print("SCORES")
    print(f"  Evaluated rollouts : {maximum_score}")
    print(f"  Total score        : {score:g}/{maximum_score}")
    print(f"  Score percentage   : {score_percent:.2f}%")
    print(f"  Passed             : {passed}")
    print(f"  Failed             : {len(failed_evaluations)}")
    print()
    print("TOKENS")
    print(f"  Total tokens       : {total_tokens:,}")
    print(f"  Prompt tokens      : {token_totals['prompt_tokens']:,}")
    print(f"  Completion tokens  : {token_totals['completion_tokens']:,}")
    print(f"  Reasoning tokens   : {token_totals['reasoning_tokens']:,}")
    print(f"  Cache read tokens  : {token_totals['cache_read_tokens']:,}")
    print(f"  Cache write tokens : {token_totals['cache_write_tokens']:,}")
    print(f"  Rollouts recorded  : {len(runs)}")
    if missing_metrics:
        labels = ", ".join(rollout_label(key) for key in sorted(missing_metrics))
        print(f"  Missing metrics    : {labels}")
    print()
    print("ERROR CASES")
    print(f"  Total cases        : {len(error_keys)}")
    print(f"  Evaluation failures: {len(failed_evaluations)}")
    print(f"  Runtime errors     : {len(runtime_errors)}")
    print(f"  Missing evaluations: {len(missing_evaluations)}")
    print(f"  Context limit hits : {context_limit_cases}")
    print(f"  Near context limit : {near_context_limit_cases}")

    if not error_keys:
        print("  None")
        return

    for key in error_keys:
        evaluation = failed_evaluations.get(key)
        runtime_error = runtime_errors.get(key)
        print()
        print(f"  {rollout_label(key)}")
        if evaluation is not None:
            print(f"    Score            : {float(evaluation['score']):g}")
            print(f"    Evaluation error : {str(evaluation['feedback']).strip()}")
        elif key in missing_evaluations:
            print("    Evaluation error : Missing from eval_results.yaml")
        if runtime_error is not None:
            print(f"    Runtime error    : {runtime_error}")

        usage = context_usage[key]
        print(f"    Context status   : {usage['status']}")
        if "last_tokens" in usage:
            print(
                "    Last LLM call     : "
                f"{usage['last_tokens']:,}/{usage['context_window']:,} tokens "
                f"({usage['last_ratio']:.2%})"
            )
            print(
                "    Peak LLM call     : "
                f"{usage['peak_tokens']:,}/{usage['context_window']:,} tokens "
                f"({usage['peak_ratio']:.2%})"
            )
            print(f"    LLM calls         : {usage['llm_calls']}")
        if "trace_path" in usage:
            print(f"    Trace             : {usage['trace_path']}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Summarize scores, token usage, errors, and context limits for a "
            "rollout directory."
        )
    )
    parser.add_argument(
        "rollout_dir",
        type=Path,
        help="Directory containing run.json and eval_results.yaml",
    )
    args = parser.parse_args()
    print_summary(args.rollout_dir)


if __name__ == "__main__":
    main()
