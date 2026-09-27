import threading
from pathlib import Path

import yaml

import run as run_cli


def test_codex_baseline_prepare_selects_codex_image_and_rollout(tmp_path: Path) -> None:
    manifest_text = (
        "task_id\tmodel_name\tmax_examples\n"
        "machine_operating_s2l\tqwen3.5-9b\t1\n"
    )
    records = run_cli.parse_baseline_manifest_table(manifest_text)
    batch_dir = tmp_path / "batch"

    prepared = run_cli.build_baseline_prepared_runs(
        manifest_text,
        records,
        batch_dir,
        harness="codex_exec",
    )
    config = yaml.safe_load(
        Path(prepared[0].run_config_path).read_text(encoding="utf-8")
    )

    assert prepared[0].rollout_version == "baseline_codex_exec"
    assert Path(prepared[0].run_dir).parts[-2:] == ("rollouts", "baseline_codex_exec")
    assert config["harness"] == "codex_exec"
    assert config["server_image"] == "machine_operating_s2l_codex:latest"
    assert "codex_server_image" not in config
    assert "agent_file" not in config


def test_existing_baseline_prepare_keeps_openhands_image(tmp_path: Path) -> None:
    manifest_text = (
        "task_id\tmodel_name\tmax_examples\n"
        "woocommerce_stock_alert_s2l\tqwen3.5-9b\t1\n"
    )
    records = run_cli.parse_baseline_manifest_table(manifest_text)
    batch_dir = tmp_path / "batch"

    prepared = run_cli.build_baseline_prepared_runs(
        manifest_text,
        records,
        batch_dir,
        harness="openhands",
    )
    config = yaml.safe_load(
        Path(prepared[0].run_config_path).read_text(encoding="utf-8")
    )

    assert config["server_image"] == "woocommerce_stock_alert_s2l:latest"
    assert config["harness"] == "openhands"
    assert "codex_server_image" not in config


def test_baseline_pipeline_dispatches_codex_collector(
    monkeypatch,
    tmp_path: Path,
) -> None:
    manifest_text = (
        "task_id\tmodel_name\tmax_examples\n"
        "machine_operating_s2l\tqwen3.5-9b\t1\n"
    )
    records = run_cli.parse_baseline_manifest_table(manifest_text)
    prepared = run_cli.build_baseline_prepared_runs(
        manifest_text,
        records,
        tmp_path / "batch",
        harness="codex_exec",
    )
    commands = []

    def record_command(command, *args):
        commands.append(command)
        return 0

    monkeypatch.setattr(run_cli, "run_logged_command", record_command)
    lock = threading.Lock()
    result = run_cli.run_baseline_pipeline(
        prepared[0],
        lock,
        tmp_path / "launcher.log",
        lock,
    )

    assert result["status"] == "completed"
    assert commands[0][4] == "src.collect_codex"
    assert commands[1][4] == "src.evaluate"


def test_codex_prepare_selects_refactorbench_image(tmp_path: Path) -> None:
    manifest_text = (
        "task_id\tmodel_name\tmax_examples\n"
        "refactorbench\tqwen3.5-9b\t1\n"
    )
    records = run_cli.parse_baseline_manifest_table(manifest_text)

    prepared = run_cli.build_baseline_prepared_runs(
        manifest_text,
        records,
        tmp_path / "batch",
        harness="codex_exec",
    )
    config = yaml.safe_load(
        Path(prepared[0].run_config_path).read_text(encoding="utf-8")
    )

    assert config["server_image"] == "refactorbench_codex:latest"


def test_codex_prepare_selects_webarena_image_and_network(tmp_path: Path) -> None:
    manifest_text = (
        "task_id\tmodel_name\tmax_examples\n"
        "webarena\tqwen3.5-9b\t1\n"
    )
    records = run_cli.parse_baseline_manifest_table(manifest_text)

    prepared = run_cli.build_baseline_prepared_runs(
        manifest_text,
        records,
        tmp_path / "batch",
        harness="codex_exec",
    )
    config = yaml.safe_load(
        Path(prepared[0].run_config_path).read_text(encoding="utf-8")
    )

    assert config["server_image"] == "webarena_codex:latest"
    assert config["docker_network"] == "webarena-net"
