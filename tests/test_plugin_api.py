"""Regression tests for the audit findings in issue #1 (talk-desktop backend).

The AST-extraction technique used here (load one top-level function out of
`dashboard/plugin_api.py` and exec it in a namespace with stubs) is adapted
from the reproduction script in issue #1, thanks @whyyagswhy.

No network, no codex binary, no OpenAI calls: everything is stubbed.
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
PLUGIN_API = REPO / "dashboard" / "plugin_api.py"
VENDOR = REPO / "dashboard" / "talk_vendor"


def load_unit(path: Path, name: str) -> dict:
    """Extract one top-level function `name` from `path` and exec it with stub globals."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = [n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    assert len(nodes) == 1, f"expected exactly one {name} in {path}"
    node = nodes[0]
    node.decorator_list = []
    unit = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")],
                             level=0), node],
        type_ignores=[],
    )
    ast.fix_missing_locations(unit)
    ns: dict = {"asyncio": asyncio, "time": time, "json": json}
    exec(compile(unit, str(path), "exec"), ns)
    return ns


def test_vendored_bundle_imports_without_hermes_talk(tmp_path):
    """#1: on a clean install (no ~/.hermes/plugins/hermes-talk) talk_auth must come from the bundle.

    `-S` keeps site-packages (and any pip-installed talk_* module) out of the
    child's sys.path; httpx is stubbed because the vendored bundle legitimately
    imports it at module level and `-S` removes the environment that provides it.
    """
    child = tmp_path / "child.py"
    child.write_text(textwrap.dedent(f"""
        import importlib.util, json, sys, types
        stub = types.ModuleType("httpx")
        def _blocked(*a, **k):
            raise AssertionError("httpx must not be called during import")
        stub.post = _blocked
        stub.get = _blocked
        sys.modules["httpx"] = stub
        spec = importlib.util.spec_from_file_location("plugin_api", {str(PLUGIN_API)!r})
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        import talk_auth
        print(json.dumps({{"talk_auth": talk_auth.__file__ or "", "vendor": {str(VENDOR)!r}}}))
    """), encoding="utf-8")
    env = dict(os.environ)
    env["HOME"] = str(tmp_path)  # empty home: hermes-talk is NOT installed
    env["PYTHONPATH"] = ""
    res = subprocess.run([sys.executable, "-S", str(child)],
                         capture_output=True, text=True, timeout=60, env=env)
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout.strip().splitlines()[-1])
    assert Path(payload["talk_auth"]).resolve().is_relative_to(Path(payload["vendor"]).resolve()), payload


def test_create_session_concurrent_mints_do_not_deadlock():
    """#3: two concurrent /session mints must complete; the lock lives inside the worker.

    Pre-fix this deadlocks the event loop: a threading.Lock held across
    `await asyncio.to_thread(...)` lets the second coroutine block the loop
    thread, so the first can never be resumed to release it.
    """
    ns = load_unit(PLUGIN_API, "create_session")
    ns.update(
        _MINT_LOCK=threading.Lock(),
        _resolve_voice=lambda _: "marin",
        _profile_home=lambda _p: Path("/tmp"),
        _bot_display_name=lambda p: "Luna",
        HTTPException=type("HTTPException", (Exception,), {}),
    )

    class TalkConfigStub:
        get_hermes_home = staticmethod(lambda: Path.home())

    ns["talk_config"] = TalkConfigStub

    def mint(*args):
        time.sleep(0.05)
        return SimpleNamespace(to_wire=lambda: {}), SimpleNamespace(source="stub")

    ns["_mint_for"] = mint

    class Request:
        async def json(self):
            return {}

    async def run():
        return await asyncio.wait_for(
            asyncio.gather(ns["create_session"](Request()), ns["create_session"](Request())),
            timeout=3,
        )

    # Pre-fix this is a hard deadlock of the event loop itself (the thread
    # blocked on _MINT_LOCK can never be resumed, so not even wait_for fires).
    # Run the loop on a daemon thread with a join guard: a regression fails the
    # test cleanly instead of hanging the whole suite.
    outcome: dict = {}

    def target():
        try:
            outcome["value"] = asyncio.run(run())
        except BaseException as exc:  # noqa: BLE001
            outcome["error"] = exc

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout=10)
    assert not t.is_alive(), "event loop deadlocked on _MINT_LOCK"
    assert "error" not in outcome, outcome.get("error")
    responses = outcome["value"]
    assert all(r["ok"] for r in responses)


def test_cl_request_survives_buffer_pruning():
    """#5: a response for a late request id must be seen even after the reader pruned the buffer."""
    ns = load_unit(PLUGIN_API, "_cl_request")
    ns["_CL"] = {"seq": 0, "notifs": [{"id": -1} for _ in range(6000)]}

    def send(request):
        ns["_CL"]["notifs"].append({"id": request["id"], "result": "received"})
        del ns["_CL"]["notifs"][:2000]

    ns["_cl_send"] = send
    assert ns["_cl_request"]("probe", {}, timeout=0.2) == "received"


def _start_ns(delegation, errors):
    """Load _codexlive_start with stubs; `errors` are raised by successive start attempts."""
    ns = load_unit(PLUGIN_API, "_codexlive_start")
    helper = load_unit(PLUGIN_API, "_rejected_field_name")
    helper["re"] = re
    ns["_rejected_field_name"] = helper["_rejected_field_name"]
    ns["_RT_DROPPABLE"] = _droppable_table()
    ns["_CL"] = {"proc": object(), "notifs": [], "seq": 0, "thread_id": "tid-1"}
    ns["_log"] = type("LogStub", (), {"warning": lambda *a: None})()
    ns["_cl_ensure"] = lambda: None
    ns["_cl_thread_ensure"] = lambda language: "tid-1"
    ns["_codexlive_persona"] = lambda profile, language: "persona"
    ns["_talk_settings"] = lambda: {"delegation": delegation}
    ns["_AGENT_INSTR"] = {"es": "agent"}
    ns["_AGENT_INSTR_SKIP"] = {"es": "skip"}
    sent: list[dict] = []
    pending = list(errors)

    def request(method, params, timeout=25):
        if method == "thread/realtime/start":
            sent.append(dict(params))
            if pending:
                raise RuntimeError(pending.pop(0))
            ns["_CL"]["notifs"].append({"method": "thread/realtime/sdp", "params": {"sdp": "v=0"}})
        return {}

    ns["_cl_request"] = request
    return ns, sent


def _droppable_table():
    tree = ast.parse(PLUGIN_API.read_text(encoding="utf-8"))
    for n in tree.body:
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "_RT_DROPPABLE":
            return ast.literal_eval(n.value)
    raise AssertionError("_RT_DROPPABLE not found")


def test_codexlive_start_compatible_server_drops_nothing():
    ns, sent = _start_ns("client", [])
    result = ns["_codexlive_start"](None, "cove", "v=0")
    assert len(sent) == 1 and sent[0]["clientManagedHandoffs"] is True
    assert result["handoff"] == "client"
    assert "droppedFields" not in result and "handoffDegraded" not in result and "warning" not in result


def test_codexlive_start_drops_only_the_named_optional_field():
    ns, sent = _start_ns("client", ["unknown field `delegationAckFiller`, expected one of `threadId`, `prompt`"])
    result = ns["_codexlive_start"](None, "cove", "v=0")
    assert result["droppedFields"] == ["delegationAckFiller"]
    assert result["warning"]
    assert result["handoff"] == "client"
    assert "handoffDegraded" not in result
    assert len(sent) == 2
    assert all(r["clientManagedHandoffs"] is True for r in sent)
    assert "delegationAckFiller" not in sent[1] and "prompt" in sent[1]


def test_codexlive_start_error_naming_other_field_does_not_drop_scheduled_one():
    # Unrelated unknown-field answers must not remove delegationAckFiller / clientManagedHandoffs.
    cases = (('unknown field "bogusField"', "bogusField"), ("unknown field: otherThing", "otherThing"),
             ("unknown field `x`, expected one of `a`, `b`", "x"))
    for msg, name in cases:
        ns, sent = _start_ns("client", [msg, msg])
        with pytest.raises(RuntimeError) as ei:
            ns["_codexlive_start"](None, "cove", "v=0")
        assert len(sent) == 1, "no retry after an unrecognised field"
        assert f"'{name}'" in str(ei.value)
        assert sent[0]["clientManagedHandoffs"] is True and sent[0]["delegationAckFiller"] is True


def test_codexlive_start_unparseable_unknown_field_raises_without_dropping():
    ns, sent = _start_ns("client", ["unknown field", "unknown field"])
    with pytest.raises(RuntimeError, match="rechaz"):
        ns["_codexlive_start"](None, "cove", "v=0")
    assert len(sent) == 1


def test_codexlive_start_client_mode_fails_closed_on_client_managed_handoffs():
    ns, sent = _start_ns("client", ["unknown field `clientManagedHandoffs`"])
    with pytest.raises(RuntimeError, match="LIVE_HANDOFF_NO_SOPORTADO"):
        ns["_codexlive_start"](None, "cove", "v=0")
    assert all("clientManagedHandoffs" in r and r["clientManagedHandoffs"] is True for r in sent)
    assert len(sent) == 1


def test_codexlive_start_client_mode_fails_closed_after_optional_drop():
    ns, sent = _start_ns("client", ["unknown field `delegationAckFiller`", "unknown field `clientManagedHandoffs`"])
    with pytest.raises(RuntimeError, match="LIVE_HANDOFF_NO_SOPORTADO"):
        ns["_codexlive_start"](None, "cove", "v=0")
    assert all("clientManagedHandoffs" in r for r in sent) and len(sent) == 2


def test_codexlive_start_server_mode_may_drop_client_managed_handoffs():
    ns, sent = _start_ns("server", ["unknown field `clientManagedHandoffs`"])
    result = ns["_codexlive_start"](None, "cove", "v=0")
    assert result["droppedFields"] == ["clientManagedHandoffs"]
    assert result["handoff"] == "server"
    assert "clientManagedHandoffs" not in sent[1]


def test_codexlive_start_naming_prompt_or_instructions_drops_both_once():
    ns, sent = _start_ns("client", ["unknown field `realtimeStartInstructions`"])
    result = ns["_codexlive_start"](None, "cove", "v=0")
    assert result["droppedFields"] == ["realtimeStartInstructions", "prompt"]
    assert "prompt" not in sent[1] and sent[1]["clientManagedHandoffs"] is True
    # the same field rejected again (already dropped) must not loop
    ns, sent = _start_ns("client", ["unknown field `prompt`", "unknown field `prompt`"])
    with pytest.raises(RuntimeError):
        ns["_codexlive_start"](None, "cove", "v=0")
    assert len(sent) == 2


def test_rejected_field_name_formats():
    ns = load_unit(PLUGIN_API, "_rejected_field_name")
    ns["re"] = re
    f = ns["_rejected_field_name"]
    assert f("unknown field `foo`, expected one of `a`") == "foo"
    assert f('Invalid params: unknown field "foo"') == "foo"
    assert f("unknown field: foo") == "foo"
    assert f("UNKNOWN FIELD foo") == "foo"
    assert f("unknown field") is None
    assert f("something else") is None


def test_interrupt_route_does_not_block_event_loop():
    """#4: /codexlive/interrupt must run _cl_request off the event loop."""
    ns = load_unit(PLUGIN_API, "codexlive_interrupt")
    ns["_CL"] = {"thread_id": "tid-1", "notifs": []}

    def slow_request(*a, **k):
        time.sleep(0.5)
        return {}

    ns["_cl_request"] = slow_request

    class Request:
        async def json(self):
            return {"turnId": "turn-7"}

    async def run():
        ticks = [0]

        async def ticker():
            while True:
                ticks[0] += 1
                await asyncio.sleep(0.01)

        t = asyncio.create_task(ticker())
        await asyncio.sleep(0.05)
        before = ticks[0]
        result = await asyncio.create_task(ns["codexlive_interrupt"](Request()))
        during = ticks[0] - before
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
        return result, during

    result, during = asyncio.run(run())
    assert result == {"ok": True}
    assert during >= 20, f"event loop was blocked during the interrupt ({during} ticks)"


def test_codex_thread_recovery_keeps_session_language():
    """#9: a stale thread recreated mid-call must be recreated in the session language.

    `_cl_thread_ensure()` defaults to `es`, so calling it bare from the recovery path
    re-registered an English session as Spanish: the next English call then saw a
    language mismatch in `_CL` and paid for a brand-new thread (new context, new
    plan work). The recovery must carry the language of the session that hit it.
    """
    ns = load_unit(PLUGIN_API, "_codexlive_start")
    ns["_CL"] = {"proc": object(), "notifs": [], "seq": 0, "thread_id": "tid-1"}
    ns["_log"] = type("LogStub", (), {"warning": lambda *a: None})()
    ns["_cl_ensure"] = lambda: None

    ensured: list[str] = []

    def thread_ensure(language="es"):
        ensured.append(language)
        return "tid-1" if len(ensured) == 1 else "tid-2"

    ns["_cl_thread_ensure"] = thread_ensure
    ns["_codexlive_persona"] = lambda profile, language: "persona"
    ns["_talk_settings"] = lambda: {"delegation": "client"}
    ns["_AGENT_INSTR"] = {"es": "agent", "en": "agent"}
    ns["_AGENT_INSTR_SKIP"] = {"es": "skip", "en": "skip"}

    starts = {"n": 0}

    def request(method, params, timeout=25):
        if method == "thread/realtime/start":
            starts["n"] += 1
            if starts["n"] == 1:
                raise RuntimeError("thread not found")
            ns["_CL"]["notifs"].append({"method": "thread/realtime/sdp", "params": {"sdp": "v=0"}})
        return {}

    ns["_cl_request"] = request

    result = ns["_codexlive_start"](None, "cove", "v=0", "en")
    assert ensured == ["en", "en"], f"recovery lost the session language: {ensured}"
    assert result["threadId"] == "tid-2"
    assert result["answer"] == "v=0"
