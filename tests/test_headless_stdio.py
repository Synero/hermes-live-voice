"""Public headless control contract; no auth, network, or Codex binary required."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("talk_desktop_headless_test", ROOT / "dashboard/headless_stdio.py")
assert SPEC is not None and SPEC.loader is not None
headless = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(headless)


def fake_backend(settings=None, *, handoff="client"):
    config = {"liveModel": "gpt-live-1.1", "delegation": "client"}
    config.update(settings or {})
    calls = []

    def start(profile, voice, offer, language):
        calls.append(("start", profile, voice, offer, language))
        return {"answer": "v=0 answer", "threadId": "voice-thread-1", "version": "v3", "handoff": handoff}

    def request(method, params, timeout):
        calls.append((method, params, timeout))

    return SimpleNamespace(
        _talk_settings=lambda: config,
        _profile_home=lambda profile: Path("/fake/profile") if profile == "allowed" else None,
        _codexlive_start=start,
        _codexlive_stop=lambda tid: calls.append(("stop", tid)),
        _cl_request=request,
        _CL={"proc": None},
        calls=calls,
    )


def start(bridge, **overrides):
    params={"model": "gpt-live-1.1", "offer": "v=0 offer", "voice": "sol", "language": "en"}
    params.update(overrides)
    return bridge.dispatch("plugin/realtime/start", params)


def test_headless_status_is_versioned_and_does_not_disclose_settings():
    backend = fake_backend({"privateAccessToken": "never-return"})
    status = headless.HeadlessSessions(backend).dispatch("plugin/status", {})
    assert status == {"interfaceVersion": 1, "ok": True, "model": "gpt-live-1.1", "handoff": "client", "active": False}
    assert "never-return" not in json.dumps(status)


def test_client_managed_start_interrupt_stop_and_profile_scoping():
    backend = fake_backend()
    bridge = headless.HeadlessSessions(backend)
    result = start(bridge, profile="allowed")
    assert result["answer"] == "v=0 answer"
    assert bridge.status()["active"] is True
    assert backend.calls[0] == ("start", "allowed", "sol", "v=0 offer", "en")
    assert bridge.dispatch("plugin/realtime/interrupt", {"threadId": "voice-thread-1", "turnId": "turn-3"}) == {"ok": True}
    assert backend.calls[1] == ("turn/interrupt", {"threadId": "voice-thread-1", "turnId": "turn-3"}, 8)
    assert bridge.dispatch("plugin/realtime/stop", {"threadId": "voice-thread-1"}) == {"ok": True}
    assert bridge.status()["active"] is False
    assert backend.calls[-1] == ("stop", "voice-thread-1")


@pytest.mark.parametrize("settings", [
    {"delegation": "server"}, {"liveModel": "other-unknown-model"},
])
def test_headless_rejects_server_agent_or_unknown_model_without_starting(settings):
    backend = fake_backend(settings)
    bridge = headless.HeadlessSessions(backend)
    assert not bridge.status()["ok"]
    with pytest.raises(headless.ProtocolError):
        start(bridge)
    assert backend.calls == []


def test_headless_preserves_existing_live_1_without_downgrade():
    backend = fake_backend({"liveModel": "gpt-live-1-codex"})
    bridge = headless.HeadlessSessions(backend)
    assert bridge.status()["model"] == "gpt-live-1-codex"
    with pytest.raises(headless.ProtocolError, match="no downgrade"):
        start(bridge)
    assert bridge.dispatch("plugin/realtime/start", {"model": "gpt-live-1-codex", "offer": "v=0 offer"})["version"] == "v3"


@pytest.mark.parametrize("overrides", [
    {"model": "gpt-live-1-codex"}, {"model": "newer-unknown"},
    {"offer": ""}, {"offer": 123}, {"profile": "../other"},
    {"voice": ""}, {"language": "invalid"},
])
def test_untrusted_requests_fail_before_backend_launch(overrides):
    backend = fake_backend()
    with pytest.raises(headless.ProtocolError):
        start(headless.HeadlessSessions(backend), **overrides)
    assert backend.calls == []


def test_headless_session_fenced_to_one_active_thread():
    backend = fake_backend()
    bridge = headless.HeadlessSessions(backend)
    start(bridge)
    with pytest.raises(headless.ProtocolError, match="already active"):
        start(bridge)
    with pytest.raises(headless.ProtocolError):
        bridge.dispatch("plugin/realtime/stop", {"threadId": "other-thread"})
    with pytest.raises(headless.ProtocolError):
        bridge.dispatch("plugin/realtime/interrupt", {"threadId": "other-thread", "turnId": "turn"})
    assert backend.calls == [("start", None, "sol", "v=0 offer", "en")]


def test_headless_rejects_wrong_protocol_result_and_stops_returned_thread():
    backend = fake_backend(handoff="server")
    bridge = headless.HeadlessSessions(backend)
    with pytest.raises(RuntimeError, match="not negotiated"):
        start(bridge)
    assert backend.calls[-1] == ("stop", "voice-thread-1")
    assert bridge.status()["active"] is False


def test_stdio_errors_never_echo_backend_exception_or_secrets():
    backend = fake_backend()
    def fail(*_):
        raise RuntimeError("sensitive-token-value")
    backend._codexlive_start = fail
    out = io.StringIO()
    wire = [
        json.dumps({"id": 1, "method": "plugin/status", "params": {}}),
        json.dumps({"id": 2, "method": "plugin/realtime/start", "params": {"model": "gpt-live-1.1", "offer": "v=0"}}),
        json.dumps({"id": 3, "method": "plugin/shutdown", "params": {}}),
    ]
    headless.serve(backend, [x+"\n" for x in wire], out)
    records = [json.loads(x) for x in out.getvalue().splitlines()]
    assert [x["id"] for x in records] == [1,2,3]
    assert records[0]["result"]["ok"]
    assert records[1]["error"]["message"] == "Backend session error (RuntimeError)"
    assert records[2]["result"] == {"ok": True}
    assert "sensitive-token-value" not in out.getvalue()


def test_app_server_headless_tool_fences_are_opt_in(monkeypatch):
    # AST load allows the source function to run without a Codex binary.
    sys.path.insert(0, str(ROOT / 'tests'))
    from test_plugin_api import load_unit, PLUGIN_API
    ns = load_unit(PLUGIN_API, "_cl_ensure")
    captured = []
    proc = SimpleNamespace(poll=lambda: None)
    ns.update(
        _CL={"proc": None, "notifs": [], "thread_id": None},
        _codex_binary=lambda: "/fake/codex",
        os=os,
        Path=Path,
        subprocess=SimpleNamespace(PIPE=-1, DEVNULL=-2, Popen=lambda argv, **kw: (captured.append(argv) or proc)),
        threading=SimpleNamespace(Thread=lambda **kw: SimpleNamespace(start=lambda: None)),
        _cl_reader=lambda *a: None,
        _cl_request=lambda *a, **k: None,
    )
    monkeypatch.delenv("TALK_DESKTOP_HEADLESS_CLIENT_ONLY", raising=False)
    ns["_cl_ensure"]()
    assert not any("features.shell_tool=false" == x for x in captured[0])
    ns["_CL"]["proc"] = None
    monkeypatch.setenv("TALK_DESKTOP_HEADLESS_CLIENT_ONLY", "1")
    ns["_cl_ensure"]()
    assert "features.shell_tool=false" in captured[1]
    assert "features.browser_use=false" in captured[1]


def test_thread_start_headless_is_ephemeral_and_readonly(monkeypatch):
    sys.path.insert(0, str(ROOT / 'tests'))
    from test_plugin_api import load_unit, PLUGIN_API
    ns = load_unit(PLUGIN_API, "_cl_thread_ensure")
    sent = []
    ns.update(
        _CL={"thread_id": None}, _agent_model=lambda: None, os=os, Path=Path,
        _cl_request=lambda method, body, timeout: (sent.append(body) or {"thread": {"id": "tid-1"}}),
    )
    monkeypatch.setenv("TALK_DESKTOP_HEADLESS_CLIENT_ONLY", "1")
    assert ns["_cl_thread_ensure"]("en") == "tid-1"
    assert sent[0]["ephemeral"] is True
    assert sent[0]["sandbox"] == "read-only"
    assert "Never run tools" in sent[0]["developerInstructions"]


def test_live11_requires_opt_in_and_does_not_silently_downgrade():
    sys.path.insert(0, str(ROOT / 'tests'))
    from test_plugin_api import _start_ns
    ns, sent = _start_ns("client", [])
    ns["_talk_settings"] = lambda: {"delegation": "client", "liveModel": "gpt-live-1.1"}
    assert ns["_codexlive_start"](None, "sol", "v=0")["handoff"] == "client"
    assert sent[0]["model"] == "gpt-live-1.1"
    ns, sent = _start_ns("client", [])
    assert ns["_codexlive_start"](None, "sol", "v=0")["handoff"] == "client"
    assert "model" not in sent[0], "old default must remain unchanged"
    ns, sent = _start_ns("client", [])
    ns["_talk_settings"] = lambda: {"delegation": "client", "liveModel": "gpt-live-1.1"}
    # Unknown model field on old Codex must fail, not retry an older model.
    original = ns["_cl_request"]
    def reject_model(method, params, timeout=25):
        if method == "thread/realtime/start":
            raise RuntimeError("unknown field `model`")
        return original(method, params, timeout)
    ns["_cl_request"] = reject_model
    with pytest.raises(RuntimeError, match="model"):
        ns["_codexlive_start"](None, "sol", "v=0")


def test_invalid_live_model_rejected_before_codex_process_launch():
    from test_plugin_api import _start_ns
    ns, sent = _start_ns("client", [])
    ran = []
    ns["_cl_ensure"] = lambda: ran.append("spawn")
    ns["_talk_settings"] = lambda: {"delegation": "client", "liveModel": "unknown-model"}
    with pytest.raises(ValueError, match="Unsupported Codex subscription Live model"):
        ns["_codexlive_start"](None, "sol", "v=0")
    assert not ran and not sent
