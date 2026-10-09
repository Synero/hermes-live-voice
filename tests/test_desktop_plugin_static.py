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


def test_backend_not_mounted_error_is_explained():
    """A bare 405/404 from an unmounted plugin route must reach the user as a hint."""
    src = _src()
    assert "const backendHint = " in src
    # both user-facing error sinks go through it
    assert "backendHint(String(e?.message || e))" in src
    assert src.count("backendHint(") >= 2


_SPANISH_CHARS = re.compile(r"[áéíóúñ¿¡ÁÉÍÓÚÑ]")
_SPANISH_WORDS = re.compile(
    r"\b(click|copiar|activar|silenciar|configurar|actualizar|colgar|para|hablar|"
    r"micr[oó]fono|transcripci[oó]n|vivo|uso|voz|salida|el|la|los|las|de|del|con|sin|"
    r"abrir|cerrar|guardar|elegir|probar|escuchar|buscar|enviar|cargando)\b",
    re.I,
)
_UI_ATTR = re.compile(r"(?:\btitle|\bplaceholder|\bariaLabel|'aria-label'|\"aria-label\")\s*:\s*")


def _strip_tr_calls(expr: str) -> str:
    """Remove every balanced `tr(...)` call from an expression."""
    out, i = [], 0
    while i < len(expr):
        m = re.compile(r"(?<![\w$.])tr\(").match(expr, i)
        if m:
            depth, i = 1, m.end()
            while i < len(expr) and depth:
                depth += {"(": 1, ")": -1}.get(expr[i], 0)
                i += 1
            continue
        out.append(expr[i])
        i += 1
    return "".join(out)


def _attr_values(src: str):
    """Yield (line, value-expression) for each title/placeholder/aria-label attribute."""
    for m in _UI_ATTR.finditer(src):
        i, depth, quote = m.end(), 0, None
        while i < len(src):
            c = src[i]
            if quote:
                if c == "\\":
                    i += 1
                elif c == quote:
                    quote = None
            elif c in "'\"`":
                quote = c
            elif c in "([{":
                depth += 1
            elif c in ")]}":
                if depth == 0:
                    break
                depth -= 1
            elif c == "," and depth == 0:
                break
            elif c == "\n" and depth == 0:
                break
            i += 1
        yield src.count("\n", 0, m.start()) + 1, src[m.end():i]


def test_ui_attributes_go_through_tr():
    """#12: title / aria-label / placeholder must not carry bare Spanish literals.

    Every user-visible attribute is either non-Spanish (a brand, a variable) or goes
    through `tr(es, en)`, otherwise English users see Spanish tooltips.
    """
    src = _src()
    values = list(_attr_values(src))
    assert values, "no title/placeholder/aria-label attributes found — the scanner is broken"
    bad = []
    for line, expr in values:
        rest = _strip_tr_calls(expr)
        for lit in re.findall(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|`[^`]*`", rest):
            if _SPANISH_CHARS.search(lit) or _SPANISH_WORDS.search(lit):
                bad.append(f"plugin.js:{line}: {lit}")
    assert not bad, "Spanish literal outside tr():\n" + "\n".join(bad)


def test_scanner_flags_untranslated_literal():
    """The regression scanner itself must catch the bug it guards against."""
    expr = "live ? 'Colgar Live Voice' : tr('Activar micrófono', 'Unmute microphone')"
    rest = _strip_tr_calls(expr)
    assert "Colgar" in rest and "micrófono" not in rest


def test_language_fallback_is_english():
    """#12: with no navigator.language the global catalog must default to English."""
    src = _src()
    m = re.search(r"^const EN = .*$", src, re.M)
    assert m, "const EN not found"
    assert "navigator.language) || 'en')" in m.group(0)
    assert "|| 'es'" not in m.group(0)
