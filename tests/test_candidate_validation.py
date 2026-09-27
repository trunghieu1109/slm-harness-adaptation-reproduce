from types import SimpleNamespace
from unittest.mock import Mock

from src.optimize import common


def test_validate_agent_candidate_reuses_remote_workspace(monkeypatch, tmp_path):
    candidate_agent = Mock()
    runtime_agent = Mock()
    remote_workspace = SimpleNamespace(
        host="http://127.0.0.1:30000",
        api_key=None,
        read_timeout=600.0,
        max_connections=None,
    )
    validation_workspace = object()
    conversation = Mock()

    monkeypatch.setattr(
        common,
        "_build_agent_in_subprocess",
        lambda code, llm, task_id, local: (True, "", candidate_agent),
    )
    monkeypatch.setattr(
        common,
        "_remap_agent_to_remote_workspace",
        lambda agent, local, remote, task_id: runtime_agent,
    )
    remote_workspace_factory = Mock(return_value=validation_workspace)
    monkeypatch.setattr(common, "RemoteWorkspace", remote_workspace_factory)
    conversation_factory = Mock(return_value=conversation)
    monkeypatch.setattr(common, "Conversation", conversation_factory)

    local_workspace = tmp_path / "validation"
    success, error = common.validate_agent_candidate(
        "candidate = True",
        Mock(),
        workspace=remote_workspace,
        local_workspace=str(local_workspace),
        remote_workspace="/workspace/proposer/validation/attempt_1",
    )

    assert success is True
    assert error == ""
    remote_workspace_factory.assert_called_once_with(
        host=remote_workspace.host,
        api_key=remote_workspace.api_key,
        working_dir="/workspace/proposer/validation/attempt_1",
        read_timeout=remote_workspace.read_timeout,
        max_connections=remote_workspace.max_connections,
    )
    conversation_factory.assert_called_once_with(
        agent=runtime_agent,
        workspace=validation_workspace,
        visualizer=None,
    )
    conversation.send_message.assert_called_once_with("test")
    conversation.close.assert_called_once_with()


def test_validate_agent_candidate_closes_failed_remote_conversation(
    monkeypatch,
    tmp_path,
):
    candidate_agent = Mock()
    remote_workspace = SimpleNamespace(
        host="http://127.0.0.1:30000",
        api_key=None,
        read_timeout=600.0,
        max_connections=None,
    )
    conversation = Mock()
    conversation.send_message.side_effect = RuntimeError("MCP failed")

    monkeypatch.setattr(
        common,
        "_build_agent_in_subprocess",
        lambda code, llm, task_id, local: (True, "", candidate_agent),
    )
    monkeypatch.setattr(
        common,
        "_remap_agent_to_remote_workspace",
        lambda agent, local, remote, task_id: candidate_agent,
    )
    monkeypatch.setattr(common, "RemoteWorkspace", Mock(return_value=object()))
    monkeypatch.setattr(common, "Conversation", Mock(return_value=conversation))

    success, error = common.validate_agent_candidate(
        "candidate = True",
        Mock(),
        workspace=remote_workspace,
        local_workspace=str(tmp_path / "validation"),
        remote_workspace="/workspace/proposer/validation/attempt_1",
    )

    assert success is False
    assert error == "RuntimeError during conversation init: MCP failed"
    conversation.close.assert_called_once_with()
