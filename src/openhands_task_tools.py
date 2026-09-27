"""OpenHands tool selection for the legacy baseline collector."""

import os

from openhands.sdk import Tool
from openhands.tools.browser_use import BrowserToolSet
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool


def _webarena_browser_tool_params(workspace_dir: str | None) -> dict:
    if not workspace_dir:
        return {}

    browser_root = os.path.join(workspace_dir, ".browser_use")
    return {
        "user_data_dir": os.path.join(browser_root, "profile"),
        "downloads_path": os.path.join(browser_root, "downloads"),
    }


def get_tools(task_id: str, workspace_dir: str | None = None) -> list:
    """Return the OpenHands tools appropriate for a task."""
    if task_id == "webarena":
        return [
            Tool(
                name=BrowserToolSet.name,
                params=_webarena_browser_tool_params(workspace_dir),
            )
        ]
    if task_id == "tau2_airline":
        return []
    return [Tool(name=TerminalTool.name), Tool(name=FileEditorTool.name)]
