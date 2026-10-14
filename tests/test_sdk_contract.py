"""Contract tests with the official OpenAI Python SDK pointed at the gateway
(in-process, fake providers): only the base URL and key change."""
import openai
import pytest
from openai import OpenAI

from app.providers.base import GenerationResult, LLMProvider, TokenUsage
from app.providers.mock import MockProvider
from app.router.model_router import ModelRouter

_TOOL = {"type": "function", "function": {"name": "weather", "parameters": {"type": "object", "properties": {}}}}
_CALL = {"id": "call_1", "type": "function", "function": {"name": "weather", "arguments": "{}"}}


class _Scripted(LLMProvider):
    def __init__(self, name, result):
        self.name, self.result, self.params = name, result, None

    async def generate(self, messages, model, params=None):
        self.params = params
        return self.result


@pytest.fixture
def sdk(client):
    # FastAPI's TestClient is an httpx.Client, so the SDK can use it directly
    return OpenAI(base_url="http://testserver/v1", api_key="unused-when-auth-disabled", http_client=client,
                  max_retries=0)


def _use(gateway, provider, **router_kwargs):
    gateway["router"] = ModelRouter({provider.name: provider}, **router_kwargs)
    return provider


def test_chat_completion(sdk, gateway):
    provider = _use(gateway, _Scripted("p", GenerationResult("hi there", TokenUsage(3, 2, 5), None, "stop")))

    completion = sdk.chat.completions.create(
        model="auto", messages=[{"role": "user", "content": "hello"}], max_completion_tokens=20, seed=7,
    )

    assert completion.choices[0].message.content == "hi there"
    assert completion.choices[0].finish_reason == "stop"
    assert completion.usage.total_tokens == 5
    assert provider.params == {"max_tokens": 20, "seed": 7}


def test_streaming_with_usage(sdk, gateway):
    _use(gateway, _Scripted("p", GenerationResult("streamed", TokenUsage(1, 1, 2), None, "stop")))

    stream = sdk.chat.completions.create(
        model="auto", messages=[{"role": "user", "content": "x"}], stream=True,
        stream_options={"include_usage": True},
    )
    chunks = list(stream)

    assert "".join(c.choices[0].delta.content or "" for c in chunks if c.choices) == "streamed"
    assert chunks[-1].choices == [] and chunks[-1].usage.total_tokens == 2


def test_tool_calls(sdk, gateway):
    _use(gateway, _Scripted("p", GenerationResult("", None, [_CALL], "tool_calls")))

    completion = sdk.chat.completions.create(
        model="auto", messages=[{"role": "user", "content": "weather?"}], tools=[_TOOL],
    )

    call = completion.choices[0].message.tool_calls[0]
    assert (call.id, call.function.name, completion.choices[0].finish_reason) == ("call_1", "weather", "tool_calls")


def test_embeddings_default_base64_is_decoded_by_the_sdk(sdk, gateway):
    _use(gateway, MockProvider("mock"))

    result = sdk.embeddings.create(model="mock", input=["a", "b"])

    assert len(result.data) == 2 and len(result.data[0].embedding) == 8
    assert all(isinstance(v, float) for v in result.data[0].embedding)


def test_models_list(sdk, gateway):
    _use(gateway, MockProvider("mock"), aliases={"auto": ["mock"]})

    assert [m.id for m in sdk.models.list()] == ["auto", "mock"]


def test_unsupported_parameter_is_an_explicit_400(sdk):
    with pytest.raises(openai.BadRequestError) as error:
        sdk.chat.completions.create(model="auto", messages=[{"role": "user", "content": "x"}], logprobs=True)

    assert error.value.code == "unsupported_parameter"
    assert error.value.param == "logprobs"


def test_missing_capability_is_an_explicit_400(sdk, gateway):
    _use(gateway, MockProvider("mock"), provider_capabilities={"mock": frozenset({"chat"})})

    with pytest.raises(openai.BadRequestError) as error:
        sdk.chat.completions.create(model="mock", messages=[{"role": "user", "content": "x"}], tools=[_TOOL])

    assert "no deployment supporting: tools" in error.value.message


def test_bad_key_is_an_authentication_error(client, settings, monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "admin_token", "admin")
    sdk = OpenAI(base_url="http://testserver/v1", api_key="gw_live_wrong", http_client=client, max_retries=0)

    with pytest.raises(openai.AuthenticationError):
        sdk.models.list()
