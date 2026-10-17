import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from gepa import optimize
from gepa.core.state import GEPAState

from src import gepa_optimize
from src.optimize import adapter as adapter_module
from src.optimize.adapter import (
    AgentOptimizationAdapter,
    CostBudgetStopper,
    ResumableNoImprovementStopper,
)


SEED = {"agent_code": "original"}
TRAIN = [{"prompt": "train one"}, {"prompt": "train two"}]
VAL = [{"prompt": "val one"}, {"prompt": "val two"}]


@pytest.fixture
def adapter_factory(monkeypatch, tmp_path):
    monkeypatch.setattr(adapter_module, "AgentProposer", Mock())
    monkeypatch.setattr(adapter_module, "get_eval_config", lambda task: {"eval_function": Mock()})

    def evaluate_batch(self, candidate, batch, phase_dir, rollout_bucket_key, log_tag):
        results = []
        for index, example in enumerate(batch):
            result = self._cache.get(candidate["agent_code"], example)
            if result is None:
                score = float(candidate["agent_code"] != "original")
                result = {"output": {"score": score}, "score": score, "trajectory": {}}
                self._cache.put(candidate["agent_code"], example, result)
                self._cost_tracker[rollout_bucket_key]["prompt_tokens"] += 100
                self._cost_tracker[rollout_bucket_key]["completion_tokens"] += 10
                self._cost_tracker[rollout_bucket_key]["accumulated_cost"] += 0.1
                path = Path(phase_dir) / f"example{index}"
                path.mkdir(parents=True, exist_ok=True)
                (path / "artifact.txt").write_text(candidate["agent_code"])
            results.append(copy.deepcopy(result))
        self._cache.save()
        self._save_cost_summary()
        return results

    def propose(self, candidate, reflective_dataset, components_to_update):
        self.memory.record_proposal(
            self._gepa_iter, candidate["agent_code"], "improved", ["agent_code"], "diff"
        )
        return {"agent_code": "improved"}

    monkeypatch.setattr(AgentOptimizationAdapter, "_run_candidate_on_batch", evaluate_batch)
    monkeypatch.setattr(AgentOptimizationAdapter, "propose_new_texts", propose)

    def create():
        return AgentOptimizationAdapter(
            task_id="fixture", model_name="fixture", logger=Mock(),
            reflection_lm_name="fixture", run_dir=str(tmp_path), agent_batch_size=1,
        )

    return create


def run_gepa(adapter, max_calls):
    stopper = ResumableNoImprovementStopper(10)
    adapter.prepare_resume(SEED, VAL, stopper)
    return optimize(
        seed_candidate=SEED, trainset=TRAIN, valset=VAL, adapter=adapter,
        max_metric_calls=max_calls, reflection_minibatch_size=1,
        stop_callbacks=[stopper], run_dir=adapter.run_dir, seed=42,
        display_progress_bar=False,
        callbacks=[adapter],
    )


def test_gepa_resumes_counter_candidates_and_artifacts(adapter_factory, tmp_path):
    first = adapter_factory()
    initial = run_gepa(first, max_calls=2)
    assert initial.total_metric_calls == 2
    assert initial.best_candidate == SEED

    # Simulate work written after the last GEPA checkpoint, before interruption.
    old_artifact = tmp_path / "iterations/iter_07/current/sentinel.txt"
    old_artifact.parent.mkdir(parents=True)
    old_artifact.write_text("keep this interrupted attempt")
    initial_history = (tmp_path / "shared/proposal_memory/memory.json").read_bytes()

    resumed = adapter_factory()
    result = run_gepa(resumed, max_calls=6)
    assert result.total_metric_calls == 6  # Includes the original two evaluations.
    assert result.best_candidate == {"agent_code": "improved"}
    assert result.val_aggregate_scores[result.best_idx] == 1.0
    assert old_artifact.read_text() == "keep this interrupted attempt"
    assert (tmp_path / "iterations/iter_08/candidates/current.py").exists()
    history = json.loads((tmp_path / "shared/proposal_memory/memory.json").read_text())
    assert history[0] == json.loads(initial_history)[0]
    assert len([r for r in history if r["status"] == "seed"]) == 1
    improved = next(r for r in history if r["agent_code"] == "improved")
    assert improved["status"] == "accepted_on_subsample"
    assert improved["val_score"] == 1.0
    assert improved["subsample_scores"] == [1.0]
    summary = json.loads((tmp_path / "shared/cost_summary.json").read_text())
    assert summary["total"]["prompt_tokens"] == 600
    assert summary["total"]["completion_tokens"] == 60


def test_interrupted_iteration_replays_with_cached_work(adapter_factory, tmp_path, monkeypatch):
    interrupted = adapter_factory()
    monkeypatch.setattr(interrupted, "propose_new_texts", Mock(side_effect=KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        run_gepa(interrupted, max_calls=6)
    assert GEPAState.load(str(tmp_path)).total_num_evals == 2
    assert interrupted._cost_tracker["rollout_reflection"]["prompt_tokens"] == 100
    artifact = tmp_path / "iterations/iter_01/candidates/current.py"
    original_mtime = artifact.stat().st_mtime_ns

    resumed = adapter_factory()
    result = run_gepa(resumed, max_calls=6)
    assert result.total_metric_calls == 6
    assert result.best_candidate == {"agent_code": "improved"}
    assert artifact.stat().st_mtime_ns == original_mtime
    assert sum(b["prompt_tokens"] for b in resumed._cost_tracker.values()) == 600


def test_smaller_budget_stops_without_new_rollouts(adapter_factory, tmp_path, monkeypatch):
    run_gepa(adapter_factory(), max_calls=2)
    resumed = adapter_factory()
    monkeypatch.setattr(resumed, "_run_candidate_on_batch", Mock(side_effect=AssertionError("new rollout")))
    result = run_gepa(resumed, max_calls=1)
    assert result.total_metric_calls == 2
    assert result.best_candidate == SEED
    assert sum(b["prompt_tokens"] for b in resumed._cost_tracker.values()) == 200


def test_changed_patience_honors_saved_count():
    stopper = ResumableNoImprovementStopper(4)
    stopper.restore(best_score=0.8, iterations_without_improvement=4)
    assert stopper(Mock())
    assert stopper.iterations_without_improvement == 4


def test_finalize_only_recovers_missing_memory_scores_without_evaluations(
    adapter_factory, tmp_path, monkeypatch,
):
    adapter = adapter_factory()
    run_gepa(adapter, max_calls=6)
    # Reproduce the old export failure: GEPA knows the score, memory does not.
    for record in adapter.memory.get_all():
        if record.agent_code == "improved":
            record.val_score = None
            record.subsample_scores = None
    adapter.memory.save()
    data_path = tmp_path / "data.json"
    data_path.write_text(json.dumps(TRAIN + VAL))
    config_path = tmp_path / "shared/config/used_config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        f"seed: 42\ndata_path: {data_path}\ndata_start_index: 0\n"
        "max_examples: 4\ntrain_ratio: 0.5\n"
    )
    (tmp_path / "gepa.log").write_text(
        "[cost] total=$0.2000\n"
        "Iteration 0: Base program full valset score: 0.0 over 2 / 2 examples\n"
        "[cost] total=$0.6000\n"
        "Iteration 1: Best score on valset: 1.0\n"
    )
    checkpoint_before = (tmp_path / "gepa_state.bin").read_bytes()
    monkeypatch.setattr(gepa_optimize, "optimize", Mock(side_effect=AssertionError("optimize called")))
    monkeypatch.setattr(gepa_optimize, "setup_servers", Mock(side_effect=AssertionError("server started")))
    assert gepa_optimize.main(["--finalize-only", "--run_dir", str(tmp_path)]) == 0
    summary = json.loads((tmp_path / "shared/config/optimization_summary.json").read_text())
    assert summary["best_score"] == 1.0
    assert summary["best_val_scores"] == [1.0, 1.0]
    assert summary["best_train_score"] is None
    assert summary["total_metric_calls"] == 6
    assert (tmp_path / "shared/config/best_config.py").read_text() == "improved"
    assert (tmp_path / "gepa_state.bin").read_bytes() == checkpoint_before


def test_export_matches_candidate_identity_when_validation_scores_tie(adapter_factory):
    adapter = adapter_factory()
    adapter.memory.record_seed("original", [1.0], [{}], VAL[:1])
    adapter.memory.record_proposal(3, "original", "improved", ["agent_code"], "diff")
    adapter.memory.record_val_score(
        adapter.memory.get_all()[-1].code_hash, 1.0, [1.0], [{}], VAL[:1]
    )
    assert gepa_optimize._find_best_candidate_record(adapter.memory, "improved").iteration == 3


def test_resume_preserves_patience_cost_references_and_latest_spend(adapter_factory, tmp_path):
    first = adapter_factory()
    run_gepa(first, max_calls=2)
    checkpoint = GEPAState.load(str(tmp_path))
    checkpoint.adapter_state["patience"] = {
        "best_score": 0.75, "iterations_without_improvement": 4,
    }
    checkpoint.save(str(tmp_path))
    first._cost_tracker["proposer"]["prompt_tokens"] = 73
    first._cost_tracker["proposer"]["accumulated_cost"] = 0.4
    first._save_cost_summary()

    resumed = adapter_factory()
    cost_tracker_reference = resumed._cost_tracker
    cost_stopper = CostBudgetStopper(0.5, cost_tracker_reference)
    patience = ResumableNoImprovementStopper(7)
    resumed.prepare_resume(SEED, VAL, patience)
    assert resumed._cost_tracker is cost_tracker_reference
    assert cost_stopper(checkpoint)
    assert patience.best_score == 0.75
    assert patience.iterations_without_improvement == 4
    assert patience.max_iterations_without_improvement == 7  # New threshold still applies.
    assert resumed._cost_tracker["proposer"]["prompt_tokens"] == 73
    assert not patience(checkpoint)
    assert patience.iterations_without_improvement == 4

    memory_path = tmp_path / "shared/proposal_memory/memory.json"
    memory_before = memory_path.read_bytes()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(resumed, "_run_candidate_on_batch", Mock(side_effect=AssertionError("new seed rollout")))
        batch = resumed.evaluate(VAL, SEED)
    assert batch.scores == [0.0, 0.0]
    assert memory_path.read_bytes() == memory_before
    # GEPA invokes this hook again after the initial seed callback.
    resumed.set_adapter_state(checkpoint.adapter_state)
    assert resumed._cost_tracker["proposer"]["prompt_tokens"] == 73
    assert cost_stopper(checkpoint)
    snapshot = resumed.get_adapter_state()
    snapshot["cost_tracker"]["proposer"]["prompt_tokens"] = -1
    assert resumed._cost_tracker["proposer"]["prompt_tokens"] == 73


def test_resume_old_checkpoint_recovers_disk_state(adapter_factory, tmp_path):
    run_gepa(adapter_factory(), max_calls=2)
    checkpoint = GEPAState.load(str(tmp_path))
    checkpoint.adapter_state = {}  # Checkpoints created before the resume fix.
    checkpoint.save(str(tmp_path))
    old_artifact = tmp_path / "iterations/iter_25/current/trace.json"
    old_artifact.parent.mkdir(parents=True)
    old_artifact.write_text("original trace")

    resumed = adapter_factory()
    stopper = ResumableNoImprovementStopper(10)
    resumed.prepare_resume(SEED, VAL, stopper)
    assert resumed._gepa_iter == 25
    assert resumed._cost_tracker["rollout_seed"]["prompt_tokens"] == 200
    assert stopper.iterations_without_improvement == 0
    resumed.evaluate(VAL, SEED)
    resumed.set_adapter_state({})
    resumed.on_evaluation_start({"candidate_idx": 0})
    resumed.evaluate(TRAIN[:1], SEED, capture_traces=True)
    resumed.on_evaluation_end({})
    assert resumed._gepa_iter == 26
    assert old_artifact.read_text() == "original trace"
    assert (tmp_path / "iterations/iter_26/candidates/current.py").exists()
    assert GEPAState.load(str(tmp_path)).total_num_evals == 2


def test_resume_rejects_missing_seed_cache(adapter_factory, tmp_path):
    run_gepa(adapter_factory(), max_calls=2)
    (tmp_path / "shared/eval_cache.json").write_text("{}")
    with pytest.raises(ValueError, match="saved seed validation"):
        adapter_factory().prepare_resume(SEED, VAL, ResumableNoImprovementStopper(10))


@pytest.mark.parametrize("artifact", ["cache", "memory", "cost"])
def test_interrupted_json_save_keeps_previous_file(adapter_factory, tmp_path, monkeypatch, artifact):
    adapter = adapter_factory()
    run_gepa(adapter, max_calls=2)
    actions = {
        "cache": (adapter._cache.save, tmp_path / "shared/eval_cache.json"),
        "memory": (adapter.memory.save, tmp_path / "shared/proposal_memory/memory.json"),
        "cost": (adapter._save_cost_summary, tmp_path / "shared/cost_summary.json"),
    }
    save, path = actions[artifact]
    original = path.read_bytes()

    def interrupt(value, stream, **kwargs):
        stream.write("{partial")
        raise KeyboardInterrupt

    monkeypatch.setattr(json, "dump", interrupt)
    with pytest.raises(KeyboardInterrupt):
        save()
    assert path.read_bytes() == original
    json.loads(path.read_text())
