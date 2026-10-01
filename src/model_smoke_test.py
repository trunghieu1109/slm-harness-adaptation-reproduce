import argparse
from collections.abc import Sequence

from .utils import LM_DICT


DEFAULT_MODEL = "gemini-3.1-pro-low"
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
        default=64,
        help="Maximum number of output tokens",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    if args.model not in LM_DICT:
        raise ValueError(
            f"Unknown model alias {args.model!r}. Available aliases: {sorted(LM_DICT)}"
        )

    lm = LM_DICT[args.model]
    if not lm.kwargs.get("api_key"):
        raise ValueError(
            f"API key for {args.model!r} is empty. Fill GEMINI_API_KEY in .env first."
        )

    print(f"Model alias: {args.model}")
    print(f"Model ID: {lm.model}")
    print(f"API base: {lm.kwargs.get('api_base')}")

    response = lm(
        messages=[{"role": "user", "content": args.prompt}],
        max_tokens=args.max_tokens,
    )

    print("Response:")
    print(response[0])


if __name__ == "__main__":
    main()
