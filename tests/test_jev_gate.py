"""El gate de Jev (TypeSafe System One) no puede empeorar el lane de voz.

Todo corre offline: el transporte se inyecta, y el único test que abre un socket
apunta a un puerto local cerrado. Lo que se prueba es el contrato fail-open: sin
key, con timeout, con la red caída o con un schema violado, ``decide()`` devuelve
``decided: False`` y el desktop se queda con su heurística de siempre.

La otra mitad vive en ``test_desktop_plugin_static.py``: que el desktop llame al
gate con timeout y que nunca cargue una credencial.
"""

from __future__ import annotations

import ast
import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard"
if str(DASHBOARD) not in sys.path:
    sys.path.insert(0, str(DASHBOARD))

import jev_gate  # noqa: E402

PLUGIN_API = DASHBOARD / "plugin_api.py"
KEY = "test-key-not-a-real-secret"
ENV_NAMES = (
    "TYPESAFE_API_KEY", "JEV_API_KEY",
    "TYPESAFE_BASE_URL", "JEV_BASE_URL",
    "TYPESAFE_MODEL", "JEV_MODEL",
    "TYPESAFE_TIMEOUT",
)


@pytest.fixture(autouse=True)
def gate_env(monkeypatch):
    """Por defecto el gate está ENCENDIDO: el caso apagado se pide explícito."""
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TYPESAFE_API_KEY", KEY)
    return monkeypatch


# ── helpers ─────────────────────────────────────────────────────────────────


def _response(route="chat_task", *, is_task=0.9, needs_confirm=0.1, confidence=0.8, model="jev-latest"):
    """Una respuesta System One válida, con la ruta elegida como más probable."""
    probs = {key: 0.1 for key in jev_gate.ROUTES}
    probs[route] = 1.0 - 0.1 * (len(jev_gate.ROUTES) - 1)
    return {
        "model": model,
        "answers": {
            "is_task": {"type": "noul", "noul": is_task},
            "route": {"type": "choice", "choice": route, "confidence": confidence, "probabilities": probs},
            "needs_confirm": {"type": "noul", "noul": needs_confirm},
        },
    }


class _Recorder:
    """Transport inyectable: guarda los payloads y devuelve una respuesta fija."""

    def __init__(self, route="chat_task", **kwargs):
        self.route = route
        self.kwargs = kwargs
        self.calls = []

    def __call__(self, payload):
        self.calls.append(payload)
        return _response(self.route, **self.kwargs)


_transport = _Recorder


def _load_unit(name, ns):
    """Extrae una función de plugin_api.py y la ejecuta con sus stubs.

    ``plugin_api`` no se puede importar acá (quiere fastapi y el árbol del host),
    así que el endpoint bajo prueba se levanta por AST y recibe un gate falso.
    """
    tree = ast.parse(PLUGIN_API.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(PLUGIN_API), "exec"), ns)
            return ns[name]
    raise AssertionError(f"{name} not found in {PLUGIN_API}")


class StubGate:
    """Reemplazo de :mod:`jev_gate` para los tests del endpoint."""

    def __init__(self, *, key="", decided=None):
        self._key = key
        self._decided = decided
        self.calls = []

    def env_key(self):
        return self._key

    def decide(self, arguments, language="es", *, transport=None):
        self.calls.append((arguments, language))
        return self._decided


def _endpoint_ns(gate, settings=None):
    """Namespace mínimo para levantar el endpoint del backend por AST.

    Trae el gate falso, el ``settings.json`` falso y la versión real de
    ``_voice_gate_on``, para que el apagado se pruebe de verdad y no con un stub.
    """
    ns = {
        "asyncio": asyncio,
        "json": json,
        "jev_gate": gate,
        "_talk_settings": lambda: dict(settings or {}),
        "_DECIDE_TOOL_NAME": "decide_voice_delegation",
    }
    ns["_voice_gate_on"] = _load_unit("_voice_gate_on", ns)
    return ns


# ── configuración: la key decide si el gate existe ──────────────────────────


def test_no_key_means_no_decision(gate_env):
    gate_env.delenv("TYPESAFE_API_KEY")
    tx = _transport()
    out = jev_gate.decide({"text": "abre el navegador"}, transport=tx)
    assert out["decided"] is False
    assert out["reason"] == "missing_api_key"
    assert tx.calls == [], "el gate no debe gastar una llamada si no hay key"


def test_blank_key_is_off_not_an_error(gate_env):
    gate_env.setenv("TYPESAFE_API_KEY", "   ")
    assert jev_gate.decide({"text": "hola"}, transport=_transport())["reason"] == "missing_api_key"


def test_key_turns_the_gate_on_with_sane_defaults(gate_env):
    assert jev_gate.env_key() == KEY
    assert jev_gate.env_base_url() == jev_gate.DEFAULT_BASE_URL
    assert jev_gate.env_model() == jev_gate.DEFAULT_MODEL
    out = jev_gate.decide({"text": "manda el informe"}, transport=_transport("chat_task"))
    assert out["decided"] is True
    assert out["route"] == "chat_task"


def test_env_overrides_and_the_jev_fallbacks(gate_env):
    gate_env.setenv("TYPESAFE_BASE_URL", "https://gate.test/")
    gate_env.setenv("TYPESAFE_MODEL", "jev-2")
    assert jev_gate.env_base_url() == "https://gate.test"  # sin slash final
    assert jev_gate.env_model() == "jev-2"
    gate_env.delenv("TYPESAFE_BASE_URL")
    gate_env.delenv("TYPESAFE_MODEL")
    gate_env.setenv("JEV_BASE_URL", "https://alias.test")
    gate_env.setenv("JEV_MODEL", "jev-alias")
    assert jev_gate.env_base_url() == "https://alias.test"
    assert jev_gate.env_model() == "jev-alias"
    gate_env.delenv("TYPESAFE_API_KEY")
    gate_env.setenv("JEV_API_KEY", KEY)
    assert jev_gate.env_key() == KEY


@pytest.mark.parametrize("raw,expected", [("99", 0.6), ("abc", 0.6), ("0", 0.6), ("-1", 0.6), ("0.2", 0.2)])
def test_timeout_is_hard_capped(gate_env, raw, expected):
    """El presupuesto de voz es 600 ms aunque el operador configure más."""
    gate_env.setenv("TYPESAFE_TIMEOUT", raw)
    assert jev_gate._env_timeout_s() == pytest.approx(expected)


# ── rutas: la decisión que llega al lane de voz ─────────────────────────────


def test_small_talk_is_answered_by_the_voice():
    out = jev_gate.decide({"text": "jaja sí, qué loco"}, transport=_transport("answer_self", is_task=0.05))
    assert out["decided"] is True
    assert out["route"] == "answer_self"


def test_real_work_routes_to_the_chat():
    out = jev_gate.decide({"text": "manda el informe a Seba"}, transport=_transport("chat_task"))
    assert out["route"] == "chat_task"
    assert out["is_task"] == pytest.approx(0.9)


def test_gui_work_can_ask_for_a_confirmation():
    out = jev_gate.decide({"text": "borra esa carpeta"}, transport=_transport("computer_use", needs_confirm=0.9))
    assert out["route"] == "computer_use"
    assert out["needs_confirm"] == pytest.approx(0.9)


def test_vague_work_routes_to_clarify():
    out = jev_gate.decide({"text": "hazlo de nuevo pero mejor"}, transport=_transport("clarify"))
    assert out["route"] == "clarify"


def test_a_non_task_can_never_come_back_as_chat_work():
    """Si el modelo dice "no es tarea", la ruta efectiva no puede ser trabajar."""
    out = jev_gate.decide({"text": "hola, ¿me escuchas?"}, transport=_transport("chat_task", is_task=0.05))
    assert out["decided"] is True
    assert out["route"] == "answer_self"
    assert out["route_raw"] == "chat_task", "la ruta cruda queda para depurar"


# ── el estado que ve el gate: acotado, sin secretos ─────────────────────────


def test_utterance_reaches_the_gate_verbatim():
    tx = _transport()
    jev_gate.decide(
        {
            "text": "pasame el clima de Valdivia",
            "recent_transcript": [{"role": "user", "text": "hola"}, {"role": "bot", "text": "te escucho"}],
            "work_in_chat": True,
        },
        transport=tx,
    )
    state = tx.calls[0]["state"]
    assert state["request"] == "pasame el clima de Valdivia"
    assert state["recent_transcript"] == [{"role": "user", "text": "hola"}, {"role": "bot", "text": "te escucho"}]
    assert state["flags"] == {"work_in_chat": True, "language": "es"}


def test_state_stays_bounded_on_a_rant():
    tx = _transport()
    jev_gate.decide(
        {"text": "palabra " * 800, "recent_transcript": [{"role": "user", "text": "x" * 5000}] * 40},
        transport=tx,
    )
    state = tx.calls[0]["state"]
    assert len(state["request"]) <= jev_gate.STATE_MAX_REQUEST_CHARS
    assert len(state["recent_transcript"]) <= jev_gate.STATE_MAX_TURNS
    assert all(len(turn["text"]) <= jev_gate.STATE_MAX_TURN_CHARS for turn in state["recent_transcript"])
    assert len(json.dumps(tx.calls[0], ensure_ascii=False)) < 4000


def test_state_marks_the_language():
    for language, expected in (("en", "en"), ("es", "es"), ("pt", "es")):
        tx = _transport()
        jev_gate.decide({"text": "hello"}, language, transport=tx)
        assert tx.calls[0]["state"]["flags"]["language"] == expected


def test_junk_arguments_do_not_crash_the_gate():
    for arguments in (None, {}, {"text": None}, {"text": 42}, {"recent_transcript": "no es lista"}):
        out = jev_gate.decide(arguments, transport=_transport())
        assert out["decided"] is True, arguments


# ── fail-open: nada de esto puede romper la voz ─────────────────────────────


@pytest.mark.parametrize(
    "error",
    [jev_gate.TransportError("red"), TimeoutError("tarde"), OSError("socket"), ValueError("raro")],
)
def test_transport_failures_fall_back(error):
    def hostile(_payload):
        raise error

    out = jev_gate.decide({"text": "abre el navegador"}, transport=hostile)
    assert out["decided"] is False
    assert out["reason"].startswith("transport:")


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "texto",
        42,
        [],
        {},
        {"answers": {}},
        {"answers": {"is_task": {"type": "noul", "noul": 0.9}}},
        {
            "answers": {
                "is_task": {"type": "noul", "noul": 0.9},
                "route": {"type": "noul", "noul": 0.9},
                "needs_confirm": {"type": "noul", "noul": 0.1},
            }
        },
    ],
)
def test_garbage_answers_fall_back(raw):
    out = jev_gate.decide({"text": "corre los tests"}, transport=lambda _payload: raw)
    assert out["decided"] is False
    assert out["reason"].startswith("protocol:")


def test_probabilities_must_sum_to_one():
    raw = _response()
    raw["answers"]["route"]["probabilities"] = {key: 0.5 for key in jev_gate.ROUTES}
    assert jev_gate.decide({"text": "x"}, transport=lambda _p: raw)["decided"] is False


def test_probabilities_must_match_the_criteria_exactly():
    raw = _response()
    raw["answers"]["route"]["probabilities"]["web"] = 0.0
    assert jev_gate.decide({"text": "x"}, transport=lambda _p: raw)["decided"] is False


def test_choice_must_be_a_top_option():
    raw = _response("chat_task")
    raw["answers"]["route"]["choice"] = "clarify"
    assert jev_gate.decide({"text": "x"}, transport=lambda _p: raw)["decided"] is False


def test_confidence_is_required():
    raw = _response()
    del raw["answers"]["route"]["confidence"]
    assert jev_gate.decide({"text": "x"}, transport=lambda _p: raw)["decided"] is False


@pytest.mark.parametrize("confidence", [0.0, 0.2, 0.49])
def test_low_confidence_falls_back(confidence):
    out = jev_gate.decide({"text": "corre los tests"}, transport=_transport("chat_task", confidence=confidence))
    assert out["decided"] is False
    assert out["reason"] == "low_confidence_or_unknown_route"


def test_an_unknown_route_is_a_protocol_error():
    """Una ruta fuera del set cerrado no puede llegar al desktop."""
    raw = _response()
    raw["answers"]["route"]["choice"] = "web_browse"
    out = jev_gate.decide({"text": "x"}, transport=lambda _p: raw)
    assert out["decided"] is False
    assert out["reason"].startswith("protocol:")


@pytest.mark.parametrize("value", [-0.1, 1.5, float("nan"), "0.9", True, None])
def test_non_probability_nouls_fall_back(value):
    raw = _response()
    raw["answers"]["is_task"]["noul"] = value
    assert jev_gate.decide({"text": "x"}, transport=lambda _p: raw)["decided"] is False


def test_decide_never_raises_on_an_unexpected_exception():
    def hostile(_payload):
        raise RuntimeError("kaboom")

    assert jev_gate.decide({"text": "hola"}, transport=hostile)["decided"] is False


def test_decide_lets_base_exceptions_through():
    """Ctrl-C y SystemExit no son nuestros para tragárnoslos: solo los fallos."""

    def hostile(_payload):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        jev_gate.decide({"text": "hola"}, transport=hostile)


# ── higiene del transporte ──────────────────────────────────────────────────


def test_transport_error_never_carries_the_key(gate_env):
    gate_env.setenv("TYPESAFE_BASE_URL", "http://127.0.0.1:1")
    out = jev_gate.decide({"text": "hola"})
    assert out["decided"] is False
    assert KEY not in json.dumps(out), "la key no puede filtrarse en la razón del fallo"
    with pytest.raises(jev_gate.TransportError) as excinfo:
        jev_gate._http_transport({"state": {}})
    assert KEY not in str(excinfo.value)


# ── el schema de preguntas: set cerrado, bilingüe ───────────────────────────


@pytest.mark.parametrize("language", ["es", "en"])
def test_questions_follow_the_documented_contract(language):
    questions = jev_gate.build_questions(language)
    assert set(questions) == {"is_task", "route", "needs_confirm"}
    for key, question in questions.items():
        assert question["type"] in {"noul", "choice"}, key
        assert question["instructions"].strip(), key
    assert set(questions["is_task"]["criteria"]) == {"true", "false"}
    assert set(questions["needs_confirm"]["criteria"]) == {"true", "false"}
    assert set(questions["route"]["criteria"]) == set(jev_gate.ROUTES)


def test_spanish_and_english_questions_actually_differ():
    assert jev_gate.build_questions("es")["route"]["criteria"] != jev_gate.build_questions("en")["route"]["criteria"]


def test_build_request_carries_state_model_and_questions():
    questions = jev_gate.build_questions("es")
    payload = jev_gate.build_request({"request": "hola"}, questions, model="jev-test")
    assert payload["model"] == "jev-test"
    assert payload["questions"] == questions
    assert payload["state"] == {"request": "hola"}


def test_build_request_rejects_an_incomplete_question_map():
    with pytest.raises(jev_gate.ProtocolError):
        jev_gate.build_request({}, {})
    with pytest.raises(jev_gate.ProtocolError):
        jev_gate.build_request({}, {"route": {"type": "choice", "instructions": "x", "criteria": {}}})


# ── el endpoint del backend: normaliza para el desktop ──────────────────────


def test_endpoint_reports_the_gate_off_so_the_desktop_keeps_its_heuristic():
    gate = StubGate(key="")
    fn = _load_unit("_decide_voice_delegation", _endpoint_ns(gate))
    out = asyncio.run(fn({"text": "hola"}, "es"))
    assert out["ok"] is True
    assert json.loads(out["output"]) == {"enabled": False, "decision": None}
    assert gate.calls == [], "apagado significa que ni se intenta"


def test_endpoint_normalizes_the_verdict_for_the_desktop():
    gate = StubGate(key=KEY, decided={"decided": True, "route": "computer_use", "needs_confirm": 0.9, "confidence": 0.8})
    fn = _load_unit("_decide_voice_delegation", _endpoint_ns(gate))
    payload = json.loads(asyncio.run(fn({"text": "borra eso"}, "es"))["output"])
    assert payload["enabled"] is True
    assert payload["decision"] == {"route": "computer_use", "needs_confirm": True, "confidence": pytest.approx(0.8)}
    assert gate.calls == [({"text": "borra eso"}, "es")]


def test_endpoint_returns_null_on_an_undecided_verdict():
    gate = StubGate(key=KEY, decided={"decided": False, "reason": "transport:URLError"})
    fn = _load_unit("_decide_voice_delegation", _endpoint_ns(gate))
    assert json.loads(asyncio.run(fn({"text": "x"}, "es"))["output"])["decision"] is None


def test_endpoint_survives_a_gate_that_raises():
    class Exploding(StubGate):
        def decide(self, arguments, language="es", *, transport=None):
            raise RuntimeError("el gate se cayó")

    fn = _load_unit("_decide_voice_delegation", _endpoint_ns(Exploding(key=KEY)))
    payload = json.loads(asyncio.run(fn({"text": "x"}, "es"))["output"])
    assert payload == {"enabled": True, "decision": None}


def test_endpoint_treats_any_other_language_as_spanish():
    gate = StubGate(key=KEY, decided={"decided": False})
    fn = _load_unit("_decide_voice_delegation", _endpoint_ns(gate))
    asyncio.run(fn({"text": "x"}, "pt"))
    assert gate.calls[0][1] == "es"


def test_settings_can_force_the_gate_off_even_with_a_key():
    """`jevGate: false` en settings.json es el apagado explícito del operador."""
    keyed = StubGate(key=KEY)
    assert _load_unit("_voice_gate_on", {"jev_gate": keyed, "_talk_settings": lambda: {}})() is True
    off = _load_unit("_voice_gate_on", {"jev_gate": keyed, "_talk_settings": lambda: {"jevGate": False}})
    assert off() is False
    # Solo el false booleano apaga: cualquier otro valor deja el gate encendido.
    raro = _load_unit("_voice_gate_on", {"jev_gate": keyed, "_talk_settings": lambda: {"jevGate": "false"}})
    assert raro() is True


def test_route_entry_point_is_silent_when_the_gate_is_off():
    gate = StubGate(key="")
    ns = _endpoint_ns(gate)
    fn = _load_unit("_voice_gate_route", ns)
    assert asyncio.run(fn("decide_voice_delegation", {"text": "hola"}, "es")) is None
    assert gate.calls == []


def test_route_entry_point_ignores_every_other_tool_name():
    gate = StubGate(key=KEY)
    ns = _endpoint_ns(gate)
    fn = _load_unit("_voice_gate_route", ns)
    assert asyncio.run(fn("send_to_chat", {"text": "hola"}, "es")) is None
    assert gate.calls == []


def test_route_entry_point_answers_when_the_gate_is_on():
    gate = StubGate(key=KEY, decided={"decided": True, "route": "clarify", "needs_confirm": 0.1, "confidence": 0.7})
    ns = _endpoint_ns(gate)
    ns["_decide_voice_delegation"] = _load_unit("_decide_voice_delegation", ns)
    fn = _load_unit("_voice_gate_route", ns)
    out = asyncio.run(fn("decide_voice_delegation", {"text": "hazlo"}, "es"))
    assert out is not None
    assert json.loads(out["output"])["decision"]["route"] == "clarify"
