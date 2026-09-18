"""Regression coverage for the instructions sent to the voice provider."""
from types import SimpleNamespace

import pytest

from test_plugin_api import PLUGIN_API, load_unit


@pytest.mark.parametrize("profile", [None, "example-bot"])
@pytest.mark.parametrize("allow_chat", [False, True])
def test_minted_identity_does_not_invent_owner_or_host(profile, allow_chat):
    ns = load_unit(PLUGIN_API, "_mint_for")
    captured = {}
    sections = {"PERSONA": "A helpful test bot."}
    auth = SimpleNamespace(token="test-token")
    descriptor = object()

    def mint(**kwargs):
        captured.update(kwargs)
        return descriptor

    ns.update(
        talk_tools=SimpleNamespace(default_talk_tools=lambda: []),
        _send_to_chat_tool=lambda: {"name": "send_to_chat"},
        _bot_identity_sections=lambda _: sections,
        talk_host=SimpleNamespace(host=lambda: SimpleNamespace(identity_sections=lambda: sections)),
        talk_identity=SimpleNamespace(
            build_instructions=lambda sections, **kwargs: "You are Hermes, speaking live. " + sections["PERSONA"]
        ),
        talk_capabilities=SimpleNamespace(instruction_section=lambda: ""),
        _language_directive=lambda language: "",
        _voice_policy=lambda language: "",
        _bot_display_name=lambda _: "Example Bot",
        talk_auth=SimpleNamespace(resolve_auth=lambda: auth),
        talk_config=SimpleNamespace(talk_model=lambda: "test-model"),
        talk_wire=SimpleNamespace(mint_ephemeral_session=mint),
        _voice_gate_on=lambda: False,
    )

    assert ns["_mint_for"](profile, "marin", allow_chat) == (descriptor, auth)
    instructions = captured["instructions"]
    assert "Nacho" not in instructions
    assert "VPS" not in instructions
    assert sections["PERSONA"] in instructions
    if profile:
        assert "You are Example Bot, speaking live" in instructions
        assert "IDENTIDAD: Eres Example Bot." in instructions
    else:
        assert "You are Hermes, speaking live" in instructions
    assert captured["tools"] == ([{"name": "send_to_chat"}] if allow_chat else [])
