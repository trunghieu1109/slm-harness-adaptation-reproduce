"""Stock Alert agent with a deterministic half-tool MCP view."""

import os

from openhands.sdk import Agent, Tool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool

SEED_PROMPT = (
    "You are an operations automation agent. Use the available WooCommerce, "
    "Google Sheets, and Email MCP tools to complete the assigned task accurately."
)


def build_agent(base_dir, llm):
    from src.task_setups.woocommerce_stock_alert_s2l import (
        get_mcp_config,
        get_tool_filter_regex,
    )

    prompt_path = os.path.join(base_dir, "system_prompt.md")
    with open(prompt_path, "w", encoding="utf-8") as f:
        f.write(SEED_PROMPT)

    return Agent(
        llm=llm,
        tools=[Tool(name=TerminalTool.name), Tool(name=FileEditorTool.name)],
        system_prompt_filename=prompt_path,
        mcp_config=get_mcp_config(base_dir),
        filter_tools_regex=get_tool_filter_regex(),
    )
