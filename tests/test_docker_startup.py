from unittest.mock import MagicMock, patch

import pytest

from src.runner import _docker_workspace_context


@pytest.mark.parametrize("message", [
    "Container failed to become healthy in time: last_error=ConnectionRefusedError",
    "Port is already allocated",
])
def test_docker_startup_retries_before_entering_the_agent(message):
    workspace = MagicMock()
    workspace.__enter__.return_value = workspace
    with (
        patch("src.runner.DockerWorkspace",
              side_effect=[RuntimeError(message), workspace]) as create,
        patch("src.runner.time.sleep"),
    ):
        with _docker_workspace_context(server_image="stock:latest") as active:
            assert active is workspace
    assert create.call_count == 2
    workspace.__enter__.assert_called_once()
    workspace.__exit__.assert_called_once()


def test_docker_startup_stops_after_three_failed_attempts():
    error = RuntimeError("Container failed to become healthy in time")
    with (
        patch("src.runner.DockerWorkspace", side_effect=error) as create,
        patch("src.runner.time.sleep"),
    ):
        with pytest.raises(RuntimeError, match="Container failed"):
            with _docker_workspace_context(server_image="stock:latest"):
                pytest.fail("The agent must not start without a ready workspace")
    assert create.call_count == 3


def test_docker_startup_does_not_retry_image_errors_or_agent_execution():
    with patch("src.runner.DockerWorkspace",
               side_effect=RuntimeError("Image missing")) as create:
        with pytest.raises(RuntimeError, match="Image missing"):
            with _docker_workspace_context(server_image="missing:latest"):
                pytest.fail("The agent must not start")
    create.assert_called_once()

    workspace = MagicMock()
    workspace.__enter__.return_value = workspace
    workspace.__exit__.return_value = False
    with patch("src.runner.DockerWorkspace", return_value=workspace) as create:
        with pytest.raises(RuntimeError, match="healthy in time"):
            with _docker_workspace_context(server_image="stock:latest"):
                raise RuntimeError("Container failed to become healthy in time")
    create.assert_called_once()
    workspace.__exit__.assert_called_once()
