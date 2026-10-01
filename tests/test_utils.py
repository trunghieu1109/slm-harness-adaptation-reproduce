from types import SimpleNamespace

from src.utils import build_sdk_llm


def test_build_sdk_llm_forwards_temperature():
    lm = SimpleNamespace(
        model="openai/test-model",
        kwargs={
            "api_base": "http://model.example/v1",
            "api_key": "test-key",
            "temperature": 0.2,
        },
    )

    sdk_llm = build_sdk_llm(lm)

    assert sdk_llm.temperature == 0.2
