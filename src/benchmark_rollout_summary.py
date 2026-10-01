"""Summarize saved benchmark scores, token usage, and runtime failures."""

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from statistics import mean

import yaml


ROLLOUT_PATTERN = re.compile(r"example(\d+)_rollout(\d+)")
TIMEOUT_PATTERN = re.compile(r"timeout|timed out|time limit exceeded", re.IGNORECASE)


def summarize(rollout_dir: Path, pass_score: float = 1.0) -> dict:
    run_path = rollout_dir / "run.json"
    run_records = json.loads(run_path.read_text()) if run_path.exists() else []
    runs = {
        f"example{r['example_id']}_rollout{r['rollout_id']}": r["run_result"]
        for r in run_records
    }
    eval_path = rollout_dir / "eval_results.yaml"
    evaluations = (
        {Path(e['workspace_dir']).name: e for e in yaml.safe_load(eval_path.read_text())}
        if eval_path.exists() else {}
    )
    names = set(runs) | set(evaluations)
    for path in rollout_dir.iterdir():
        if path.is_dir():
            name = path.name.removesuffix("_logs")
            if ROLLOUT_PATTERN.fullmatch(name):
                names.add(name)
    if not names:
        raise ValueError(f"No rollout artifacts found in {rollout_dir}")
    cases = []
    for name in sorted(names, key=lambda n: tuple(map(int, ROLLOUT_PATTERN.fullmatch(n).groups()))):
        traces = list((rollout_dir / f"{name}_logs").glob("trace_*.json"))
        if len(traces) > 1:
            raise ValueError(f"Multiple traces for {name}; select the intended attempt.")
        # Individual traces survive even when the aggregate run.json is incomplete.
        result = json.loads(traces[0].read_text()) if traces else runs.get(name)
        evaluation = evaluations.get(name)
        score = float(evaluation["score"]) if evaluation is not None else None
        error = result["error"] if result is not None else "Missing trace and run.json entry"
        error_text = error if isinstance(error, str) else json.dumps(error)
        if error and TIMEOUT_PATTERN.search(error_text):
            status = "timeout"
        elif error:
            status = "other_error"
        elif evaluation is not None and evaluation.get("error"):
            status = "other_error"
        elif score is None:
            status = "unevaluated"
        else:
            status = "correct" if score >= pass_score else "incorrect"
        usage = result["metrics"].get("accumulated_token_usage") if result is not None else None
        if usage:
            if "input_tokens" in usage:
                prompt_tokens = usage["input_tokens"]
                completion_tokens = usage["output_tokens"]
            else:
                prompt_tokens = usage["prompt_tokens"]
                completion_tokens = usage["completion_tokens"]
            total_tokens = prompt_tokens + completion_tokens
        else:
            # Codex records an empty usage object when no token usage was reported.
            prompt_tokens = completion_tokens = total_tokens = None
        cases.append({
            "rollout": name,
            "score": score,
            "status": status,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "error": error,
            "feedback": evaluation["feedback"] if evaluation is not None else None,
        })
    evaluated = [c for c in cases if c["score"] is not None]
    recorded = [c for c in cases if c["total_tokens"] is not None]
    without_runtime_errors = [c for c in cases if not c["error"]]
    recorded_without_errors = [c for c in without_runtime_errors if c["total_tokens"] is not None]
    counts = Counter(case["status"] for case in cases)
    return {
        "rollout_dir": str(rollout_dir.resolve()),
        "rollouts": len(cases),
        "run_json_records": len(run_records),
        "evaluated_rollouts": len(evaluated),
        "missing_evaluations": [c["rollout"] for c in cases if c["score"] is None],
        "pass_score": pass_score,
        "score_sum": sum(c["score"] for c in evaluated) if evaluated else None,
        "score_mean": mean(c["score"] for c in evaluated) if evaluated else None,
        "evaluation_correct": sum(c["score"] >= pass_score for c in evaluated),
        "evaluation_incorrect": sum(c["score"] < pass_score for c in evaluated),
        "outcomes": {s: counts[s] for s in ("correct", "incorrect", "timeout", "other_error", "unevaluated")},
        "rollouts_with_tokens": len(recorded),
        "missing_tokens": [c["rollout"] for c in cases if c["total_tokens"] is None],
        "tokens_total": sum(c["total_tokens"] for c in recorded),
        "tokens_mean": {f: mean(c[f] for c in recorded) if recorded else None for f in ("prompt_tokens", "completion_tokens", "total_tokens")},
        "rollouts_without_runtime_errors": len(without_runtime_errors),
        "rollouts_without_runtime_errors_with_tokens": len(recorded_without_errors),
        "tokens_mean_without_runtime_errors": {
            f: mean(c[f] for c in recorded_without_errors) if recorded_without_errors else None
            for f in ("prompt_tokens", "completion_tokens", "total_tokens")
        },
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rollout_dir", type=Path)
    parser.add_argument("--json", action="store_true", help="Include per-rollout details as JSON")
    parser.add_argument("--pass-score", type=float, default=1.0, help="Score threshold for correct (default: 1.0)")
    args = parser.parse_args()
    report = summarize(args.rollout_dir, args.pass_score)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return
    print(f"Rollouts: {report['rollouts']} (run.json: {report['run_json_records']})")
    print(f"Evaluated: {report['evaluated_rollouts']}/{report['rollouts']}; pass threshold: {report['pass_score']:g}")
    if report["score_mean"] is None:
        print("Score: N/A (no saved evaluations; run the benchmark evaluation first)")
    else:
        print(f"Mean score: {report['score_mean']:.6f}; sum: {report['score_sum']:g}")
    print(f"Missing evaluations: {len(report['missing_evaluations'])}")
    print(f"Benchmark correct / incorrect: {report['evaluation_correct']} / {report['evaluation_incorrect']}")
    print("Exclusive outcomes (runtime error takes precedence over benchmark score):")
    for status, count in report["outcomes"].items():
        print(f"  {status}: {count}")
    print(f"Recorded total tokens: {report['tokens_total']:,}")
    print(f"Mean tokens across ALL rollouts (including timeout/errors; data: {report['rollouts_with_tokens']}/{report['rollouts']}):")
    print(f"Missing token data: {report['missing_tokens']}")
    for field, value in report["tokens_mean"].items():
        print(f"  {field}: {value:,.2f}" if value is not None else f"  {field}: N/A")
    print(
        "Mean tokens across rollouts WITHOUT runtime errors (including incorrect answers; "
        f"data: {report['rollouts_without_runtime_errors_with_tokens']}/"
        f"{report['rollouts_without_runtime_errors']}):"
    )
    for field, value in report["tokens_mean_without_runtime_errors"].items():
        print(f"  {field}: {value:,.2f}" if value is not None else f"  {field}: N/A")
    print("Means use rollouts with recorded token data; missing data is not counted as zero.")
    print("Tokens include recorded LLM calls only; timed-out calls may be unrecorded.")


if __name__ == "__main__":
    main()
