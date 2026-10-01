import json

import pytest
import yaml

from src.benchmark_rollout_summary import summarize


def test_partial_artifacts_and_overlapping_timeout_score(tmp_path):
    log_dir = tmp_path / 'example0_rollout0_logs'
    log_dir.mkdir()
    result = {
        'error': 'Run timed out after 600 seconds',
        'metrics': {'accumulated_token_usage': {'prompt_tokens': 100, 'completion_tokens': 20}},
    }
    (log_dir / 'trace_test.json').write_text(json.dumps(result))
    (tmp_path / 'example1_rollout0').mkdir()
    (tmp_path / 'eval_results.yaml').write_text(yaml.safe_dump([
        {'workspace_dir': 'example0_rollout0', 'score': 0.8, 'feedback': 'Partial credit'},
        {'workspace_dir': 'example2_rollout0', 'score': 0, 'feedback': 'Failed'},
    ]))
    report = summarize(tmp_path, pass_score=0.8)
    assert report['rollouts'] == 3
    assert report['score_mean'] == 0.4
    assert report['evaluation_correct'] == 1
    assert report['evaluation_incorrect'] == 1
    assert report['outcomes']['timeout'] == 1
    assert report['outcomes']['other_error'] == 2
    assert report['rollouts_with_tokens'] == 1
    assert report['tokens_mean']['total_tokens'] == 120
    assert report['missing_evaluations'] == ['example1_rollout0']


def test_no_evaluations_or_metrics_are_unknown(tmp_path):
    (tmp_path / 'run.json').write_text(json.dumps([{
        'example_id': 0, 'rollout_id': 0,
        'run_result': {'error': None, 'metrics': {}},
    }]))
    report = summarize(tmp_path)
    assert report['score_mean'] is None
    assert report['tokens_mean']['total_tokens'] is None
    assert report['outcomes']['unevaluated'] == 1


def test_multiple_attempts_are_not_silently_merged(tmp_path):
    logs = tmp_path / 'example0_rollout0_logs'
    logs.mkdir()
    for name in ('trace_a.json', 'trace_b.json'):
        (logs / name).write_text('{}')
    with pytest.raises(ValueError, match='Multiple traces'):
        summarize(tmp_path)


def test_token_means_include_incorrect_answers_but_separate_runtime_errors(tmp_path):
    records = []
    evaluations = []
    for i, (error, score, tokens) in enumerate([
        (None, 1.0, 100),
        (None, 0.0, 300),
        ('Run timed out', 1.0, 600),
        ('Remote conversation got stuck', 0.0, 1000),
    ]):
        records.append({
            'example_id': i, 'rollout_id': 0,
            'run_result': {
                'error': error,
                'metrics': {'accumulated_token_usage': {
                    'prompt_tokens': tokens, 'completion_tokens': tokens // 10,
                }},
            },
        })
        evaluations.append({'workspace_dir': f'example{i}_rollout0', 'score': score, 'feedback': ''})
    (tmp_path / 'run.json').write_text(json.dumps(records))
    (tmp_path / 'eval_results.yaml').write_text(yaml.safe_dump(evaluations))
    report = summarize(tmp_path)
    assert report['tokens_mean'] == {
        'prompt_tokens': 500, 'completion_tokens': 50, 'total_tokens': 550,
    }
    assert report['rollouts_without_runtime_errors'] == 2
    assert report['rollouts_without_runtime_errors_with_tokens'] == 2
    assert report['tokens_mean_without_runtime_errors'] == {
        'prompt_tokens': 200, 'completion_tokens': 20, 'total_tokens': 220,
    }


@pytest.mark.parametrize("usage", [
    {"prompt_tokens": 100, "completion_tokens": 20},
    {"input_tokens": 100, "output_tokens": 20, "cached_input_tokens": 80,
     "cache_write_input_tokens": 10, "reasoning_output_tokens": 5},
])
def test_token_schemas_and_missing_usage(tmp_path, usage):
    records = []
    for i, token_usage in enumerate([usage, {}, None]):
        records.append({
            "example_id": i, "rollout_id": 0,
            "run_result": {
                "error": None if i == 0 else [{"type": "timeout", "max_time_reached": True}],
                "metrics": {"accumulated_token_usage": token_usage},
            },
        })
    (tmp_path / "run.json").write_text(json.dumps(records))
    report = summarize(tmp_path)
    assert report["tokens_total"] == 120
    assert report["rollouts_with_tokens"] == 1
    assert report["missing_tokens"] == ["example1_rollout0", "example2_rollout0"]
    assert report["tokens_mean"] == {
        "prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
    }
    assert report["tokens_mean_without_runtime_errors"] == report["tokens_mean"]
    assert report["outcomes"]["timeout"] == 2


def test_incomplete_token_usage_raises(tmp_path):
    (tmp_path / "run.json").write_text(json.dumps([{
        "example_id": 0, "rollout_id": 0,
        "run_result": {
            "error": None,
            "metrics": {"accumulated_token_usage": {"input_tokens": 100}},
        },
    }]))
    with pytest.raises(KeyError, match="output_tokens"):
        summarize(tmp_path)
