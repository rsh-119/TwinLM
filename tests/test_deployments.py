"""Deployment identities, model groups (retry inside a group before falling
back to another group) and capability gating."""
import pytest

from app.config import get_settings
from app.gateway.config import GatewayConfig
from app.router.model_router import ModelRouter, UnknownModelError, build_router
from tests.conftest import FakeProvider

IMAGE = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}]}]
TEXT = [{"role": "user", "content": "hi"}]


def _router(**behaviors):
    providers = {name: FakeProvider(name, behavior, reply=f"from {name}") for name, behavior in behaviors.items()}
    deployments = {name + "-dep": (name, "m", None) for name in providers}
    return ModelRouter(providers, {"grouped": [["east-dep", "west-dep"], ["other-dep"]]}, deployments=deployments)


async def test_next_deployment_in_group_is_not_a_fallback():
    router = _router(east="unavailable", west="ok", other="ok")

    result = await router.route("grouped", TEXT)

    assert (result.content, result.deployment, result.fallback) == ("from west", "west-dep", False)


async def test_next_group_is_a_fallback():
    router = _router(east="unavailable", west="unavailable", other="ok")

    result = await router.route("grouped", TEXT)

    assert (result.deployment, result.fallback) == ("other-dep", True)


async def test_streaming_reports_deployment_and_group_fallback():
    router = _router(east="unavailable", west="ok", other="ok")

    chunks = [chunk async for chunk in router.route_stream("grouped", TEXT)]

    assert chunks[-1].done and (chunks[-1].deployment, chunks[-1].fallback) == ("west-dep", False)


async def test_capability_gating_skips_and_explains():
    providers = {"text": FakeProvider("text", reply="text"), "eyes": FakeProvider("eyes", reply="eyes")}
    caps = {"text": frozenset({"chat"}), "eyes": frozenset({"chat", "vision"})}
    router = ModelRouter(providers, {"auto": ["text", "eyes"], "blind": ["text"]}, provider_capabilities=caps)

    assert (await router.route("auto", IMAGE)).content == "eyes"
    assert providers["text"].calls == 0
    with pytest.raises(UnknownModelError, match="no deployment supporting: vision"):
        await router.route("blind", IMAGE)
    with pytest.raises(UnknownModelError, match="supporting: embeddings"):
        await router.embed("blind", ["x"])
    assert router.capabilities("auto") == ["chat", "vision"]


def _config(**sections):
    return GatewayConfig.model_validate({
        "providers": {
            "a": {"type": "mock", "default_model": "m"},
            "b": {"type": "mock", "default_model": "m", "capabilities": ["chat"]},
            "off": {"type": "mock", "default_model": "m", "enabled": False},
        },
        **sections,
    })


def test_build_router_with_deployments_and_groups():
    router = build_router(_config(
        deployments={"a-big": {"provider": "a", "model": "big", "capabilities": ["chat", "tools"]},
                     "off-big": {"provider": "off", "model": "big"}},
        models={"smart": {"groups": [["a-big", "off-big", "b/big"], ["a"]]}},
    ), get_settings())

    described = {m["id"]: m for m in router.describe_models()}
    assert [[d["deployment"] for d in g] for g in described["smart"]["groups"]] == [["a-big", "b/big"], ["a/m"]]
    assert described["a-big"]["targets"][0]["capabilities"] == ["chat", "tools"]
    assert "off-big" not in router.model_ids


@pytest.mark.parametrize("sections, message", [
    ({"models": {"x": {"targets": ["typo"]}}}, "neither a deployment nor a configured provider"),
    ({"deployments": {"a": {"provider": "a", "model": "m"}}}, "must differ"),
    ({"deployments": {"d": {"provider": "nope", "model": "m"}}}, "unknown provider"),
])
def test_build_router_rejects_bad_references(sections, message):
    with pytest.raises(ValueError, match=message):
        build_router(_config(**sections), get_settings())


def test_alias_needs_exactly_one_of_targets_or_groups():
    with pytest.raises(ValueError, match="exactly one"):
        _config(models={"x": {"targets": ["a"], "groups": [["a"]]}})
