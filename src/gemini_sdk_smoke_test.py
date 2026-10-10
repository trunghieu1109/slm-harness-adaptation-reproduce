"""Smoke-test Gemini text generation and tool round trips through OpenHands SDK."""

import json
import secrets
from collections.abc import Sequence
from time import perf_counter
from typing import Literal, Self

from openhands.sdk import Action, LLM, Message, Observation, TextContent, ToolDefinition
from openhands.sdk.tool import ToolExecutor
from pydantic import Field

from .model_smoke_test import build_parser, check_response, expected_response, get_model
from .utils import build_sdk_llm


class LookupTokenAction(Action):
    label: Literal["gemini"] = Field(description="Token label to look up")


class LookupTokenObservation(Observation):
    pass


class LookupTokenExecutor(ToolExecutor):
    def __init__(self, token: str):
        self.token = token

    def __call__(
        self, action: LookupTokenAction, conversation=None
    ) -> LookupTokenObservation:
        return LookupTokenObservation.from_text(self.token)


class LookupTokenTool(ToolDefinition[LookupTokenAction, LookupTokenObservation]):
    @classmethod
    def create(cls, token: str) -> Sequence[Self]:
        return [
            cls(
                description="Look up a token by label. Return the token exactly to the user.",
                action_type=LookupTokenAction,
                observation_type=LookupTokenObservation,
                executor=LookupTokenExecutor(token),
            )
        ]


def message_text(message: Message) -> str:
    return "".join(c.text for c in message.content if isinstance(c, TextContent))


def run_text_test(llm: LLM, prompt: str, expected: str | None) -> None:
    started = perf_counter()
    response = llm.completion(
        messages=[Message(role="user", content=[TextContent(text=prompt)])]
    )
    text = message_text(response.message)
    print(f"Text response ({perf_counter() - started:.2f}s): {text!r}")
    check_response(text, expected)
    print("PASS: SDK text completion")


def run_tool_test(llm: LLM) -> None:
    # Generate the expected answer locally so the model must read the tool result.
    token = f"GEMINI_TOOL_{secrets.token_hex(8)}"
    tool = LookupTokenTool.create(token)[0]
    messages = [
        Message(
            role="user",
            content=[
                TextContent(
                    text=f"Call {tool.name} once with label gemini, then reply "
                    "with exactly the returned token and nothing else."
                )
            ],
        )
    ]
    started = perf_counter()
    response = llm.completion(messages=messages, tools=[tool], tool_choice="required")
    calls = response.message.tool_calls
    if not calls or len(calls) != 1 or calls[0].name != tool.name:
        raise ValueError(f"FAIL: expected one {tool.name} call, got {calls!r}")

    call = calls[0]
    arguments = json.loads(call.arguments)
    # The SDK adds summary to tool schemas; the agent removes this metadata too.
    arguments.pop("summary", None)
    action = tool.action_from_arguments(arguments)
    observation = tool(action)
    print(f"Tool call: {call.name}({call.arguments})")
    messages.extend(
        [
            response.message,
            Message(
                role="tool",
                tool_call_id=call.id,
                name=call.name,
                content=observation.to_llm_content,
            ),
        ]
    )
    response = llm.completion(messages=messages, tools=[tool], tool_choice="none")
    if response.message.tool_calls:
        raise ValueError("FAIL: expected a final text response, got another tool call")
    text = message_text(response.message)
    print(f"Tool response ({perf_counter() - started:.2f}s): {text!r}")
    check_response(text, token)
    print("PASS: SDK tool round trip")


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    parser.description = (
        "Test Gemini through the same OpenHands SDK LLM used by the harness."
    )
    parser.add_argument(
        "--test",
        choices=["text", "tools", "all"],
        default="all",
        help="Checks to run: text (1 request), tools (2 requests), all (3 requests)",
    )
    args = parser.parse_args(argv)
    if args.max_tokens <= 0 or args.timeout <= 0:
        raise ValueError("--max-tokens and --timeout must be positive")
    llm = build_sdk_llm(get_model(args.model)).model_copy(
        update={
            "max_output_tokens": args.max_tokens,
            "timeout": args.timeout,
            "num_retries": 0,
            "stream": False,
            "caching_prompt": False,
            "log_completions": False,
        }
    )
    print(f"Model alias: {args.model}")
    print(f"Model ID: {llm.model}")
    print(f"Reasoning effort: {llm.reasoning_effort}")

    if args.test in ("text", "all"):
        run_text_test(llm, args.prompt, expected_response(args))
    if args.test in ("tools", "all"):
        run_tool_test(llm)

    usage = llm.metrics.accumulated_token_usage
    print(f"Usage: {usage.model_dump()}")
    print(f"Estimated cost: ${llm.metrics.accumulated_cost:.6f}")


if __name__ == "__main__":
    main()
