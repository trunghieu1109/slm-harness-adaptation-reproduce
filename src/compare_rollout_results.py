"""Compare evaluation results from two rollout directories."""

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import yaml


ROLLOUT_PATTERN = re.compile(r"example(?P<example>\d+)_rollout(?P<rollout>\d+)")


def parse_rollout_key(workspace_dir: str) -> tuple[int, int]:
    name = Path(workspace_dir).name
    match = ROLLOUT_PATTERN.fullmatch(name)
    if match is None:
        raise ValueError(f"Invalid rollout workspace name: {workspace_dir}")
    return int(match.group("example")), int(match.group("rollout"))


def load_evaluations(rollout_dir: Path) -> dict[tuple[int, int], dict[str, Any]]:
    eval_path = rollout_dir / "eval_results.yaml"
    with eval_path.open(encoding="utf-8") as handle:
        records = yaml.safe_load(handle)

    if not isinstance(records, list):
        raise TypeError(f"Expected a list in {eval_path}")

    evaluations = {}
    for record in records:
        key = parse_rollout_key(record["workspace_dir"])
        if key in evaluations:
            raise ValueError(f"Duplicate evaluation for example{key[0]}_rollout{key[1]}")
        evaluations[key] = record
    return evaluations


def summarize_evaluations(
    evaluations: dict[tuple[int, int], dict[str, Any]],
    pass_score: float,
) -> dict[str, int | float]:
    scores = [float(evaluation["score"]) for evaluation in evaluations.values()]
    correct = sum(score >= pass_score for score in scores)
    total = len(scores)
    return {
        "evaluated": total,
        "correct": correct,
        "incorrect": total - correct,
        "accuracy": correct / total,
        "score_sum": sum(scores),
        "score_mean": sum(scores) / total,
    }


def compare_rollout_results(
    left_dir: Path,
    right_dir: Path,
    pass_score: float = 1.0,
) -> dict[str, Any]:
    left = load_evaluations(left_dir)
    right = load_evaluations(right_dir)
    rows = []

    for example_id, rollout_id in sorted(set(left) | set(right)):
        key = (example_id, rollout_id)
        left_evaluation = left.get(key)
        right_evaluation = right.get(key)
        left_score = (
            float(left_evaluation["score"]) if left_evaluation is not None else None
        )
        right_score = (
            float(right_evaluation["score"]) if right_evaluation is not None else None
        )

        if left_evaluation is None:
            comparison = "missing_left"
        elif right_evaluation is None:
            comparison = "missing_right"
        elif left_score >= pass_score and right_score >= pass_score:
            comparison = "both_correct"
        elif left_score >= pass_score:
            comparison = "left_only_correct"
        elif right_score >= pass_score:
            comparison = "right_only_correct"
        else:
            comparison = "both_incorrect"

        rows.append(
            {
                "example_id": example_id,
                "rollout_id": rollout_id,
                "rollout": f"example{example_id}_rollout{rollout_id}",
                "left_score": left_score,
                "right_score": right_score,
                "comparison": comparison,
                "different": left_score != right_score,
                "left_feedback": (
                    left_evaluation["feedback"] if left_evaluation is not None else None
                ),
                "right_feedback": (
                    right_evaluation["feedback"] if right_evaluation is not None else None
                ),
            }
        )

    counts = Counter(row["comparison"] for row in rows)
    return {
        "left_dir": str(left_dir.resolve()),
        "right_dir": str(right_dir.resolve()),
        "pass_score": pass_score,
        "left_summary": summarize_evaluations(left, pass_score),
        "right_summary": summarize_evaluations(right, pass_score),
        "compared_rollouts": len(rows),
        "different_results": sum(row["different"] for row in rows),
        "same_results": sum(not row["different"] for row in rows),
        "counts": {
            comparison: counts[comparison]
            for comparison in (
                "both_correct",
                "left_only_correct",
                "right_only_correct",
                "both_incorrect",
                "missing_left",
                "missing_right",
            )
        },
        "rows": rows,
    }


def format_score(score: float | None) -> str:
    return "missing" if score is None else f"{score:g}"


def print_table(
    report: dict[str, Any],
    left_label: str,
    right_label: str,
    show: str,
    include_feedback: bool,
) -> None:
    print(f"Left : {left_label} ({report['left_dir']})")
    print(f"Right: {right_label} ({report['right_dir']})")
    print(f"Pass score: {report['pass_score']:g}")
    print()
    print("PER-FILE STATISTICS")
    statistics = (
        (
            "evaluated",
            str(report["left_summary"]["evaluated"]),
            str(report["right_summary"]["evaluated"]),
        ),
        (
            "correct",
            str(report["left_summary"]["correct"]),
            str(report["right_summary"]["correct"]),
        ),
        (
            "incorrect",
            str(report["left_summary"]["incorrect"]),
            str(report["right_summary"]["incorrect"]),
        ),
        (
            "accuracy",
            f"{report['left_summary']['accuracy']:.2%}",
            f"{report['right_summary']['accuracy']:.2%}",
        ),
        (
            "score sum",
            f"{report['left_summary']['score_sum']:g}",
            f"{report['right_summary']['score_sum']:g}",
        ),
        (
            "score mean",
            f"{report['left_summary']['score_mean']:.4f}",
            f"{report['right_summary']['score_mean']:.4f}",
        ),
    )
    statistic_headers = ("metric", left_label, right_label)
    statistic_widths = [
        max(
            len(statistic_headers[index]),
            *(len(row[index]) for row in statistics),
        )
        for index in range(len(statistic_headers))
    ]
    print(
        "  "
        + "  ".join(
            value.ljust(width)
            for value, width in zip(statistic_headers, statistic_widths)
        )
    )
    print("  " + "  ".join("-" * width for width in statistic_widths))
    for statistic in statistics:
        print(
            "  "
            + "  ".join(
                value.ljust(width)
                for value, width in zip(statistic, statistic_widths)
            )
        )

    print()
    print("PAIRED STATISTICS")
    print(
        f"  compared rollouts : {report['compared_rollouts']}\n"
        f"  same score        : {report['same_results']}\n"
        f"  different score   : {report['different_results']}"
    )
    for comparison, count in report["counts"].items():
        print(f"  {comparison:18} {count}")

    rows = (
        report["rows"]
        if show == "all"
        else [row for row in report["rows"] if row["different"]]
    )
    title = "ALL RESULTS" if show == "all" else "DIFFERENT RESULTS"
    print()
    print(title)
    if not rows:
        print("  None")
        return

    headers = ("example", "rollout", left_label, right_label, "comparison")
    values = [
        (
            str(row["example_id"]),
            str(row["rollout_id"]),
            format_score(row["left_score"]),
            format_score(row["right_score"]),
            row["comparison"],
        )
        for row in rows
    ]
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in values))
        for index in range(len(headers))
    ]
    print("  " + "  ".join(value.ljust(width) for value, width in zip(headers, widths)))
    print("  " + "  ".join("-" * width for width in widths))
    for row, values_row in zip(rows, values):
        print(
            "  "
            + "  ".join(
                value.ljust(width) for value, width in zip(values_row, widths)
            )
        )
        if include_feedback:
            print(f"    {left_label} feedback: {row['left_feedback']}")
            print(f"    {right_label} feedback: {row['right_feedback']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left_dir", type=Path)
    parser.add_argument("right_dir", type=Path)
    parser.add_argument(
        "--pass-score",
        type=float,
        default=1.0,
        help="Minimum score considered correct (default: 1.0)",
    )
    parser.add_argument(
        "--show",
        choices=("different", "all"),
        default="different",
        help="Show only different scores or every paired result (default: different)",
    )
    parser.add_argument(
        "--feedback",
        action="store_true",
        help="Print evaluator feedback for displayed results",
    )
    parser.add_argument("--left-label")
    parser.add_argument("--right-label")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = compare_rollout_results(args.left_dir, args.right_dir, args.pass_score)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return

    print_table(
        report,
        args.left_label or args.left_dir.name,
        args.right_label or args.right_dir.name,
        args.show,
        args.feedback,
    )


if __name__ == "__main__":
    main()
