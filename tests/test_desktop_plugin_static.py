"""Static guards on `desktop/plugin.js` for the audit findings in issue #1.

Cheap source-text checks: no bundler, no browser, no network.
"""
from __future__ import annotations

import re
from pathlib import Path

PLUGIN_JS = Path(__file__).resolve().parents[1] / "desktop" / "plugin.js"


def _src() -> str:
    return PLUGIN_JS.read_text(encoding="utf-8")


def _rest_calls(src: str) -> list[str]:
    """Return the source text of every `ctx.rest(...)` call (balanced parens)."""
    calls = []
    for m in re.finditer(r"ctx\.rest\(", src):
        i = m.end()
        depth = 1
        while i < len(src) and depth:
            if src[i] == "(":
                depth += 1
            elif src[i] == ")":
                depth -= 1
            i += 1
        calls.append(src[m.start():i])
    return calls


def _fn_body(src: str, name: str) -> str:
    m = re.search(rf"^function {name}\(\) \{{$", src, re.M)
    assert m, f"function {name}() not found"
    start = m.start()
    end = src.find("\n}\n", start)
    assert end != -1, f"closing brace of {name}() not found"
    return src[start:end]


def test_no_rest_call_pre_stringifies_its_body():
    """#7: a `body: JSON.stringify(...)` is double-encoded by the desktop transport."""
    calls = _rest_calls(_src())
    assert calls, "no ctx.rest() call found — scanner is broken"
    offenders = [c for c in calls if "JSON.stringify" in c]
    assert not offenders, f"pre-stringified ctx.rest body: {offenders[0][:160]}"


def test_interrupt_call_sends_object_body():
    """#7: /codexlive/interrupt must pass a plain object body (and a timeout)."""
    calls = [c for c in _rest_calls(_src()) if "/codexlive/interrupt" in c]
    assert len(calls) == 1, calls
    call = calls[0]
    assert re.search(r"body:\s*\{\s*turnId:\s*tid\s*\}", call), call
    assert "timeoutMs" in call, call


def test_mute_guards_block_barge_in():
    """#6: a muted mic must not be un-muted by barge-in and must read as level 0."""
    src = _src()
    assert "if (bus.muted) return" in _fn_body(src, "doBargeIn")
    assert "if (bus.muted) { _botSpeakingSince = 0; return }" in _fn_body(src, "maybeBarge")
    assert "const micLevel = bus.muted ? 0 :" in src


def test_transcript_user_label_is_not_a_hardcoded_name():
    """#9: the transcript must not label the user with the maintainer's name.

    This plugin installs on other people's desktops, so a literal name in the UI
    (or anywhere else in the shipped source) leaks the maintainer's identity into
    every install — the user bubble belongs to whoever is talking, not to us.
    """
    src = _src()
    assert "Nacho" not in src, "hardcoded owner name found in desktop/plugin.js"
    assert re.search(r"children: isUser \? tr\('Tú', 'You'\)", src), "user label is not localized/owner-neutral"


def test_voice_gate_call_is_bounded_and_keeps_the_heuristic():
    """The Jev gate is an upgrade, never a dependency.

    The desktop half asks the backend for a verdict under a hard timeout and
    still falls back to `_isJunkDelegation` when there is none, so a gate that
    is unconfigured, slow or wrong degrades to the behaviour that shipped
    before it existed.
    """
    src = _src()
    calls = [c for c in _rest_calls(src) if "decide_voice_delegation" in c]
    assert len(calls) == 1, calls
    assert "timeoutMs" in calls[0], calls[0]
    assert re.search(r"if \(!route && _isJunkDelegation\(req\)\)", src), "heuristic fallback missing"
    assert re.search(r"_gateDecide\(ctx, req\)\s*\n\s*\.then\(", src), "gate call is not fire-and-fallback"
    assert re.search(r"\.catch\(\(\) => _routeDelegation\(ctx, dc, itemId, req, null\)\)", src)


def test_gate_carries_no_credential_in_the_desktop_half():
    """The key lives in the backend; the shipped desktop file never holds one."""
    src = _src()
    for needle in ("TYPESAFE", "JEV_API_KEY", "api_key"):
        assert needle not in src, f"{needle!r} found in desktop/plugin.js"
