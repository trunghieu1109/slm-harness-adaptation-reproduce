import yaml

from src.compare_rollout_results import compare_rollout_results, print_table


def write_evaluations(path, records):
    path.mkdir()
    evaluations = [
        {
            "workspace_dir": str(
                path / f"example{example_id}_rollout{rollout_id}"
            ),
            "score": score,
            "feedback": feedback,
        }
        for example_id, rollout_id, score, feedback in records
    ]
    (path / "eval_results.yaml").write_text(
        yaml.safe_dump(evaluations), encoding="utf-8"
    )


def test_compare_rollout_results_classifies_and_sorts_cases(tmp_path):
    left = tmp_path / "left"
    right = tmp_path / "right"
    write_evaluations(
        left,
        [
            (0, 0, 1.0, "both pass"),
            (0, 1, 1.0, "left passes"),
            (1, 0, 0.0, "both fail"),
            (2, 0, 0.5, "different failing score"),
            (10, 0, 0.0, "missing on right"),
        ],
    )
    write_evaluations(
        right,
        [
            (0, 0, 1.0, "both pass"),
            (0, 1, 0.0, "right fails"),
            (1, 0, 0.0, "both fail"),
            (2, 0, 0.0, "different failing score"),
            (3, 0, 1.0, "missing on left"),
        ],
    )

    report = compare_rollout_results(left, right)

    assert report["compared_rollouts"] == 6
    assert report["same_results"] == 2
    assert report["different_results"] == 4
    assert report["left_summary"] == {
        "evaluated": 5,
        "correct": 2,
        "incorrect": 3,
        "accuracy": 0.4,
        "score_sum": 2.5,
        "score_mean": 0.5,
    }
    assert report["right_summary"] == {
        "evaluated": 5,
        "correct": 2,
        "incorrect": 3,
        "accuracy": 0.4,
        "score_sum": 2.0,
        "score_mean": 0.4,
    }
    assert report["counts"] == {
        "both_correct": 1,
        "left_only_correct": 1,
        "right_only_correct": 0,
        "both_incorrect": 2,
        "missing_left": 1,
        "missing_right": 1,
    }
    assert [row["rollout"] for row in report["rows"]] == [
        "example0_rollout0",
        "example0_rollout1",
        "example1_rollout0",
        "example2_rollout0",
        "example3_rollout0",
        "example10_rollout0",
    ]


def test_print_table_defaults_to_different_scores(tmp_path, capsys):
    left = tmp_path / "left"
    right = tmp_path / "right"
    write_evaluations(left, [(0, 0, 1.0, "same"), (1, 0, 1.0, "left")])
    write_evaluations(right, [(0, 0, 1.0, "same"), (1, 0, 0.0, "right")])
    report = compare_rollout_results(left, right)

    print_table(report, "OpenHands", "Codex", "different", True)

    output = capsys.readouterr().out
    assert "example0_rollout0" not in output
    assert "PER-FILE STATISTICS" in output
    correct_line = next(
        line for line in output.splitlines() if line.strip().startswith("correct")
    )
    accuracy_line = next(
        line for line in output.splitlines() if line.strip().startswith("accuracy")
    )
    assert correct_line.split() == ["correct", "2", "1"]
    assert accuracy_line.split() == ["accuracy", "100.00%", "50.00%"]
    assert "PAIRED STATISTICS" in output
    assert "left_only_correct" in output
    assert "OpenHands feedback: left" in output
    assert "Codex feedback: right" in output
