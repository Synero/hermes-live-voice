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
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from types import SimpleNamespace

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


def test_codexlive_start_reports_dropped_capability_fields():
    """#8: dropped capability fields are reported; a dropped clientManagedHandoffs degrades visibly.

    The retry loop drops one group per "unknown field" answer, in fixed order
    (delegationAckFiller, then clientManagedHandoffs), so the stub raises twice
    before the third attempt succeeds.
    """
    ns = load_unit(PLUGIN_API, "_codexlive_start")
    ns["_CL"] = {"proc": object(), "notifs": [], "seq": 0, "thread_id": "tid-1"}
    warnings: list[tuple] = []

    class LogStub:
        def warning(self, *a):
            warnings.append(a)

    ns["_log"] = LogStub()
    ns["_cl_ensure"] = lambda: None
    ns["_cl_thread_ensure"] = lambda language: "tid-1"
    ns["_codexlive_persona"] = lambda profile, language: "persona"
    ns["_talk_settings"] = lambda: {"delegation": "client"}
    ns["_AGENT_INSTR"] = {"es": "agent"}
    ns["_AGENT_INSTR_SKIP"] = {"es": "skip"}

    calls = {"start": 0}

    def request(method, params, timeout=25):
        if method == "thread/realtime/start":
            calls["start"] += 1
            if calls["start"] <= 2:
                raise RuntimeError("unknown field clientManagedHandoffs")
            ns["_CL"]["notifs"].append({"method": "thread/realtime/sdp", "params": {"sdp": "v=0"}})
            return {}
        return {}

    ns["_cl_request"] = request

    result = ns["_codexlive_start"](None, "cove", "v=0")
    assert result["handoffDegraded"] is True
    assert "clientManagedHandoffs" in result["droppedFields"]
    assert result["droppedFields"] == ["delegationAckFiller", "clientManagedHandoffs"]
    assert result["handoff"] == "client"
    assert result["answer"] == "v=0"
    assert result["warning"]
    assert len(warnings) == 1 and "clientManagedHandoffs" in warnings[0][1]


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
