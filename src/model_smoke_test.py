import argparse
from collections.abc import Sequence
from time import perf_counter

from dspy.lm15 import Config, Message, Reasoning, Request

from .utils import LM_DICT, resolve_api_key


DEFAULT_MODEL = "gemini-api"
DEFAULT_PROMPT = "Reply with exactly GEMINI_OK and nothing else."


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Send one smoke-test request through a model in configs/models.yaml."
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Model registry alias")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Prompt sent to the model")
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=2048,
        help="Maximum number of output tokens",
    )
    parser.add_argument(
        "--timeout", type=int, default=60, help="Request timeout in seconds"
    )
    parser.add_argument(
        "--expect",
        help="Require this exact response (defaults to GEMINI_OK for the default prompt)",
    )
    return parser


def get_model(model_name: str):
    if model_name not in LM_DICT:
        raise ValueError(
            f"Unknown model alias {model_name!r}. Available aliases: {sorted(LM_DICT)}"
        )

    lm = LM_DICT[model_name]
    if not lm.kwargs.get("api_key"):
        raise ValueError(
            f"API key for {model_name!r} is empty. Check api_key in configs/models.yaml "
            "and its referenced environment variable in .env."
        )
    return lm


def check_response(text: str, expected: str | None) -> None:
    if not text.strip():
        raise ValueError("FAIL: model returned an empty response")
    if expected is not None and text.strip() != expected:
        raise ValueError(f"FAIL: expected {expected!r}, got {text!r}")


def expected_response(args: argparse.Namespace) -> str | None:
    if args.expect is not None:
        return args.expect
    return "GEMINI_OK" if args.prompt == DEFAULT_PROMPT else None


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.max_tokens <= 0 or args.timeout <= 0:
        raise ValueError("--max-tokens and --timeout must be positive")
    lm = get_model(args.model)
    lm = lm.copy(api_key=resolve_api_key(lm), cache=False, num_retries=0)

    print(f"Model alias: {args.model}")
    print(f"Model ID: {lm.model}")

    started = perf_counter()
    # DSPy 3.5 removed the legacy ``lm(messages=[...])`` call.  Use its
    # normalized lm15 request so the smoke test exercises the same configured
    # endpoint and reasoning setting as the current runtime.
    configured_reasoning = lm.kwargs.get("reasoning_effort")
    # SLM's registry uses LiteLLM's ``none`` spelling; lm15 uses ``off`` for
    # the same lowest reasoning setting.
    lm15_reasoning = "off" if configured_reasoning == "none" else configured_reasoning
    request = Request(
        model=lm.model,
        messages=(Message.user(args.prompt),),
        config=Config(
            max_tokens=args.max_tokens,
            temperature=lm.kwargs.get("temperature"),
            reasoning=Reasoning(effort=lm15_reasoning)
            if lm15_reasoning
            else None,
        ),
    )
    response = lm(request, cache=False, timeout=args.timeout)
    response_text = response.text

    print(f"Elapsed: {perf_counter() - started:.2f}s")
    print("Response:")
    print(response_text)
    print(f"Usage: {response.usage}")
    check_response(response_text, expected_response(args))
    print("PASS: DSPy completion")


if __name__ == "__main__":
    main()
