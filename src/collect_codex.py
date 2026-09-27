"""Collect baseline rollouts by running Codex CLI in disposable task containers."""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

from .codex_runtime import load_codex_model_config, run_codex_sample
from .task_setups import (
    preprocess_example,
    setup_servers,
    teardown_servers,
)


SUPPORTED_TASKS = {
    "machine_operating_s2l",
    "refactorbench",
    "webarena",
    "woocommerce_stock_alert_s2l",
}


def _failed_rollouts(
    results_by_key: dict[tuple[int, int], dict],
) -> list[tuple[tuple[int, int], int]]:
    """Return rollout keys and non-zero Codex process return codes."""
    failures = []
    for key, result in sorted(results_by_key.items()):
        execution = result.get("run_result", {}).get("execution", {})
        return_code = execution.get("return_code", 0)
        if return_code != 0:
            failures.append((key, return_code))
    return failures


def _load_completed_result(workspace: Path) -> dict | None:
    log_dir = workspace.parent / f"{workspace.name}_logs"
    metadata_path = log_dir / "run_meta.json"
    if not metadata_path.exists():
        return None
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata["status"] != "completed":
        return None
    trace_path = log_dir / metadata["trace_file"]
    return json.loads(trace_path.read_text(encoding="utf-8"))


def run_task(config: dict) -> None:
    task_id = config["task_id"]
    if task_id not in SUPPORTED_TASKS:
        raise ValueError(
            f"Codex baseline currently supports {sorted(SUPPORTED_TASKS)}, got {task_id!r}"
        )
    if config["harness"] != "codex_exec":
        raise ValueError("src.collect_codex requires harness: codex_exec")
    if not config["use_docker"]:
        raise ValueError("Codex baseline requires use_docker: true")
    if config.get("agent_file"):
        raise ValueError("Codex baseline does not load OpenHands agent_file implementations")

    data_path = Path(config["data_path"])
    examples = json.loads(data_path.read_text(encoding="utf-8"))
    max_examples = config.get("max_examples")
    if max_examples is not None:
        examples = examples[:max_examples]

    task_dir = Path(config.get("task_dir", f"tasks/{task_id}"))
    prompt_path = task_dir / "prompts" / f"{config['prompt_name']}.md"
    system_prompt = prompt_path.read_text(encoding="utf-8")
    rollout_dir = (
        Path("results")
        / task_id
        / f"{config['model_name']}_{config['prompt_name']}"
        / "rollouts"
        / config["rollout_version"]
    )
    rollout_dir.mkdir(parents=True, exist_ok=True)
    rollout_config = {
        "task_id": task_id,
        "model_name": config["model_name"],
        "prompt_name": config["prompt_name"],
        "rollout_version": config["rollout_version"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "max_examples": max_examples,
        "n_responses": config["n_responses"],
        "harness": "codex_exec",
    }
    (rollout_dir / "rollout_config.json").write_text(
        json.dumps(rollout_config, indent=2),
        encoding="utf-8",
    )

    model_config = load_codex_model_config(config["model_name"])
    results_by_key = {}
    pending = []
    resume = config.get("resume", True)
    for example_id, raw_example in enumerate(examples):
        for rollout_id in range(config["n_responses"]):
            workspace = rollout_dir / f"example{example_id}_rollout{rollout_id}"
            example = dict(raw_example)
            example["example_id"] = example_id
            example["rollout_id"] = rollout_id
            key = (example_id, rollout_id)
            completed_trace = _load_completed_result(workspace) if resume else None
            if completed_trace is not None:
                completed_result = dict(example)
                completed_result["run_result"] = completed_trace
                results_by_key[key] = completed_result
            else:
                pending.append(
                    (
                        key,
                        workspace,
                        example,
                    )
                )

    output_path = rollout_dir / "run.json"

    def write_results() -> None:
        ordered = [results_by_key[key] for key in sorted(results_by_key)]
        output_path.write_text(
            json.dumps(ordered, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(
        f"Codex baseline: {len(results_by_key)} completed, {len(pending)} pending, "
        f"image={config['server_image']}"
    )
    server_args = [{"example": example} for _, _, example in pending]
    servers_started = (
        setup_servers(
            task_id,
            server_args,
            start_servers=config.get("start_servers", False),
            timeout=config.get("server_start_timeout", 300),
            docker_network=config.get("docker_network"),
        )
        if pending
        else {}
    )
    processed_pending = [
        (key, workspace, preprocess_example(task_id, example))
        for key, workspace, example in pending
    ]
    try:
        with ThreadPoolExecutor(max_workers=config["agent_batch_size"]) as executor:
            futures = {
                executor.submit(
                    run_codex_sample,
                    task_id=task_id,
                    example=example,
                    system_prompt=system_prompt,
                    workspace_dir=workspace,
                    server_image=config["server_image"],
                    model_config=model_config,
                    max_time=config["max_time"],
                    docker_network=config.get("docker_network"),
                ): key
                for key, workspace, example in processed_pending
            }
            for future in as_completed(futures):
                key = futures[future]
                results_by_key[key] = future.result()
                write_results()
                print(
                    f"Saved Codex rollout {len(results_by_key)}/"
                    f"{len(examples) * config['n_responses']}"
                )
    finally:
        teardown_servers(task_id, servers_started)

    write_results()
    failures = _failed_rollouts(results_by_key)
    if failures:
        failure_summary = ", ".join(
            f"example{example_id}_rollout{rollout_id}={return_code}"
            for (example_id, rollout_id), return_code in failures
        )
        print(f"Codex collection saved failed rollout results to: {output_path}")
        raise RuntimeError(
            f"Codex collection had {len(failures)} failed rollout(s): "
            f"{failure_summary}"
        )

    print(f"Codex collection complete: {output_path}")


def rerun_rollout(config: dict, example_index: int, rollout_id: int) -> None:
    task_id = config["task_id"]
    examples = json.loads(Path(config["data_path"]).read_text(encoding="utf-8"))
    max_examples = config.get("max_examples")
    if max_examples is not None and example_index >= max_examples:
        raise ValueError(
            f"example_index {example_index} is outside max_examples={max_examples}"
        )
    if rollout_id >= config["n_responses"]:
        raise ValueError(
            f"rollout_id {rollout_id} is outside n_responses={config['n_responses']}"
        )

    example = dict(examples[example_index])
    example["example_id"] = example_index
    example["rollout_id"] = rollout_id
    task_dir = Path(config.get("task_dir", f"tasks/{task_id}"))
    system_prompt = (
        task_dir / "prompts" / f"{config['prompt_name']}.md"
    ).read_text(encoding="utf-8")
    rollout_dir = (
        Path("results")
        / task_id
        / f"{config['model_name']}_{config['prompt_name']}"
        / "rollouts"
        / config["rollout_version"]
    )
    workspace = rollout_dir / f"example{example_index}_rollout{rollout_id}"
    model_config = load_codex_model_config(config["model_name"])
    servers_started = setup_servers(
        task_id,
        [{"example": example}],
        start_servers=config.get("start_servers", False),
        timeout=config.get("server_start_timeout", 300),
        docker_network=config.get("docker_network"),
    )
    try:
        result = run_codex_sample(
            task_id=task_id,
            example=preprocess_example(task_id, example),
            system_prompt=system_prompt,
            workspace_dir=workspace,
            server_image=config["server_image"],
            model_config=model_config,
            max_time=config["max_time"],
            docker_network=config.get("docker_network"),
        )
    finally:
        teardown_servers(task_id, servers_started)

    output_path = rollout_dir / "run.json"
    existing_results = json.loads(output_path.read_text(encoding="utf-8"))
    results_by_key = {
        (item["example_id"], item["rollout_id"]): item
        for item in existing_results
    }
    selected_examples = examples[:max_examples] if max_examples is not None else examples
    for saved_example_index, raw_example in enumerate(selected_examples):
        for saved_rollout_id in range(config["n_responses"]):
            saved_workspace = (
                rollout_dir
                / f"example{saved_example_index}_rollout{saved_rollout_id}"
            )
            saved_log_dir = (
                saved_workspace.parent / f"{saved_workspace.name}_logs"
            )
            metadata_path = saved_log_dir / "run_meta.json"
            if not metadata_path.exists():
                continue
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            trace_path = saved_log_dir / metadata["trace_file"]
            saved_result = dict(raw_example)
            saved_result["example_id"] = saved_example_index
            saved_result["rollout_id"] = saved_rollout_id
            saved_result["run_result"] = json.loads(
                trace_path.read_text(encoding="utf-8")
            )
            results_by_key[(saved_example_index, saved_rollout_id)] = saved_result
    results_by_key[(example_index, rollout_id)] = result
    output_path.write_text(
        json.dumps(
            [results_by_key[key] for key in sorted(results_by_key)],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    execution = result["run_result"]["execution"]
    print(
        f"Saved example{example_index}_rollout{rollout_id} to {output_path}: "
        f"return_code={execution['return_code']} timed_out={execution['timed_out']}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run task samples with the Codex CLI harness",
    )
    parser.add_argument("--config", required=True, help="Prepared baseline YAML config")
    parser.add_argument("--example-index", type=int)
    parser.add_argument("--rollout-id", type=int)
    args = parser.parse_args()

    load_dotenv(override=True)
    with open(args.config, "r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    print("Effective Codex config:")
    print(yaml.safe_dump(config, sort_keys=False).rstrip())
    if (args.example_index is None) != (args.rollout_id is None):
        parser.error("--example-index and --rollout-id must be provided together")
    if args.example_index is not None:
        rerun_rollout(config, args.example_index, args.rollout_id)
    else:
        run_task(config)


if __name__ == "__main__":
    main()
