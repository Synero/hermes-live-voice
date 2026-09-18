"""Jev (TypeSafe System One) decision gate for voice delegation — talk-desktop.

Replaces the local `_isJunkDelegation` heuristic in `desktop/plugin.js` with a
real closed-set decision, served by the BACKEND so the API key never reaches
the client. The contract is fail-open: a missing key, HTTP error, timeout, or
an answer that violates the System One schema yields ``{"decided": False, ...}``
and the caller falls back to its heuristic exactly as before.

Decision questions (closed set):
  is_task       noul    real work vs small talk/noise
  route         choice  chat_task | answer_self | clarify | computer_use
  needs_confirm noul    destructive/costly enough to confirm before running

Hard rule for the voice lane: the HTTP call is bounded by HARD_TIMEOUT_MS
(600 ms). A wall-clock join enforces that budget even if urllib misbehaves,
so a voice turn can never hang on this gate.

Config comes only from the environment (never hardcoded, never committed):
  TYPESAFE_API_KEY | JEV_API_KEY
  TYPESAFE_BASE_URL | JEV_BASE_URL
  TYPESAFE_MODEL | JEV_MODEL
  TYPESAFE_TIMEOUT  (seconds; hard-capped at HARD_TIMEOUT_MS)
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Mapping

ENDPOINT = "/v1/systemone"
DEFAULT_MODEL = "jev-latest"
DEFAULT_BASE_URL = "https://api.typesafe.ai"

HARD_TIMEOUT_MS = 600
MIN_ROUTE_CONFIDENCE = 0.5
IS_TASK_THRESHOLD = 0.5
STATE_MAX_REQUEST_CHARS = 700
STATE_MAX_TURNS = 4
STATE_MAX_TURN_CHARS = 100

ROUTES = ("chat_task", "answer_self", "clarify", "computer_use")

Transport = Callable[[dict[str, Any]], dict[str, Any]]


class ProtocolError(ValueError):
    """Malformed answer relative to the System One schema."""


class TransportError(RuntimeError):
    """Transport failed. Never treated as a decision."""


# ── environment config (key never hardcoded) ────────────────────────────────


def env_key() -> str:
    for name in ("TYPESAFE_API_KEY", "JEV_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def env_base_url() -> str:
    for name in ("TYPESAFE_BASE_URL", "JEV_BASE_URL"):
        value = os.environ.get(name, "").strip().rstrip("/")
        if value:
            return value
    return DEFAULT_BASE_URL


def env_model() -> str:
    value = os.environ.get("TYPESAFE_MODEL", os.environ.get("JEV_MODEL", DEFAULT_MODEL)).strip()
    return value or DEFAULT_MODEL


def _env_timeout_s() -> float:
    raw = os.environ.get("TYPESAFE_TIMEOUT", "").strip()
    try:
        value = float(raw)
    except ValueError:
        value = HARD_TIMEOUT_MS / 1000.0
    if value <= 0:
        value = HARD_TIMEOUT_MS / 1000.0
    # El presupuesto del gate es 600 ms aunque el operador configure más.
    return min(value, HARD_TIMEOUT_MS / 1000.0)


# ── questions (closed set, bilingual mirror of the plugin's tr(es, en)) ─────


def build_questions(language: str = "es") -> dict[str, dict]:
    en = language == "en"
    return {
        "is_task": {
            "type": "noul",
            "instructions": (
                "Is the user's spoken request REAL WORK for an agent (do, create, review, search, "
                "run, change, take action), rather than small talk, noise, a greeting, or a bare "
                "acknowledgement?"
                if en else
                "¿La petición hablada del usuario es TRABAJO REAL para un agente (hacer, crear, revisar, "
                "buscar, ejecutar, cambiar, actuar), en vez de charla, ruido, saludo o simple acuse de recibo?"
            ),
            "criteria": {
                "true": (
                    "Real work: the user asks the agent to do, find, change or execute something concrete."
                    if en else
                    "Trabajo real: el usuario pide hacer, buscar, cambiar o ejecutar algo concreto."
                ),
                "false": (
                    "Small talk, filler, greeting, acknowledgement or noise; nothing to run."
                    if en else
                    "Charla, muletilla, saludo, acuse de recibo o ruido; no hay nada que ejecutar."
                ),
            },
        },
        "route": {
            "type": "choice",
            "instructions": (
                "Given the request and the recent transcript, choose the single best route for this "
                "voice delegation."
                if en else
                "Dada la petición y el transcript reciente, elige la única mejor ruta para esta "
                "delegación de voz."
            ),
            "criteria": {
                "chat_task": (
                    "Delegate to the user's open chat agent: research, files, facts, creating or "
                    "reviewing something — real work in the agent's lane."
                    if en else
                    "Delegar al agente del chat abierto del usuario: investigar, archivos, hechos, "
                    "crear o revisar algo — trabajo real en el lane del agente."
                ),
                "answer_self": (
                    "The voice answers itself: simple question, small talk, greeting, confirmation, "
                    "or the status of something in progress."
                    if en else
                    "La voz responde sola: pregunta simple, charla, saludo, confirmación o el estado "
                    "de algo en curso."
                ),
                "clarify": (
                    "One short clarifying question is needed before anything can be done."
                    if en else
                    "Hace falta una sola pregunta corta de aclaración antes de poder actuar."
                ),
                "computer_use": (
                    "An action on the graphical interface: clicking, typing into fields, opening, "
                    "navigating or controlling apps or windows on the user's machine."
                    if en else
                    "Una acción sobre la interfaz gráfica: hacer clic, escribir en campos, abrir, "
                    "navegar o controlar aplicaciones o ventanas de la máquina del usuario."
                ),
            },
        },
        "needs_confirm": {
            "type": "noul",
            "instructions": (
                "If the request is a GUI action, is it destructive or costly enough to require "
                "explicit user confirmation before running?"
                if en else
                "Si la petición es una acción sobre la GUI, ¿es lo bastante destructiva o cara como "
                "para requerir confirmación explícita del usuario antes de ejecutarla?"
            ),
            "criteria": {
                "true": (
                    "Destructive or costly: delete, overwrite, send, publish, pay, buy, install, "
                    "uninstall, or anything hard to undo."
                    if en else
                    "Destructiva o cara: borrar, sobrescribir, enviar, publicar, pagar, comprar, "
                    "instalar, desinstalar o algo difícil de deshacer."
                ),
                "false": (
                    "Reversible and cheap: read, inspect, open, navigate, type a draft; nothing "
                    "risky to run."
                    if en else
                    "Reversible y barata: leer, inspeccionar, abrir, navegar, tipear un borrador; "
                    "nada riesgoso de ejecutar."
                ),
            },
        },
    }


# ── request / answer contract (adapted from the proven System One adapter) ──


def build_request(state: Any, questions: Mapping[str, dict], *, model: str | None = None) -> dict[str, Any]:
    if not questions:
        raise ProtocolError("questions map must be non-empty")
    for key, question in questions.items():
        if not isinstance(question, dict) or question.get("type") not in {"noul", "choice"}:
            raise ProtocolError(f"question {key!r} missing documented type")
        if not question.get("instructions"):
            raise ProtocolError(f"question {key!r} missing instructions")
        if question["type"] == "choice":
            criteria = question.get("criteria")
            if not isinstance(criteria, dict) or not criteria:
                raise ProtocolError(f"choice {key!r} needs criteria map")
    return {"state": state, "model": model or env_model(), "questions": dict(questions)}


def _finite_prob(value: Any, *, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError(f"{where}: probability must be a number")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise ProtocolError(f"{where}: nonfinite probability")
    if number < 0.0 or number > 1.0:
        raise ProtocolError(f"{where}: probability out of [0,1]")
    return number


def _require_distribution(probs: Any, expected_keys: set[str], *, where: str) -> dict[str, float]:
    if not isinstance(probs, dict) or not probs:
        raise ProtocolError(f"{where}: probabilities map required")
    if set(probs) != expected_keys:
        raise ProtocolError(f"{where}: probabilities keys must match criteria exactly")
    out = {key: _finite_prob(value, where=f"{where}.{key}") for key, value in probs.items()}
    if abs(sum(out.values()) - 1.0) > 1e-6:
        raise ProtocolError(f"{where}: probabilities must sum to 1")
    return out


def parse_systemone_response(raw: Mapping[str, Any], questions: Mapping[str, dict]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ProtocolError("response must be object")
    answers_raw = raw.get("answers")
    if not isinstance(answers_raw, dict):
        raise ProtocolError("answers map required")
    if set(answers_raw) != set(questions):
        raise ProtocolError("answers ids must match questions exactly")
    parsed: dict[str, dict[str, Any]] = {}
    for qid, question in questions.items():
        answer = answers_raw[qid]
        if not isinstance(answer, dict) or answer.get("type") != question.get("type"):
            raise ProtocolError(f"{qid}: answer type mismatch")
        if question["type"] == "noul":
            parsed[qid] = {"noul": _finite_prob(answer.get("noul"), where=f"{qid}.noul")}
        else:
            criteria = set(question["criteria"])
            dist = _require_distribution(answer.get("probabilities"), criteria, where=qid)
            choice = answer.get("choice")
            if choice not in dist:
                raise ProtocolError(f"{qid}: choice {choice!r} not in probabilities")
            argmax = max(dist.items(), key=lambda kv: (kv[1], kv[0]))[0]
            if choice != argmax and dist[choice] < dist[argmax]:
                raise ProtocolError(f"{qid}: choice is not a highest-probability option")
            confidence = answer.get("confidence")
            if confidence is None:
                raise ProtocolError(f"{qid}: confidence required")
            parsed[qid] = {
                "choice": str(choice),
                "confidence": _finite_prob(confidence, where=f"{qid}.confidence"),
                "probabilities": dist,
            }
    usage = raw.get("usage") or {}
    if not isinstance(usage, dict):
        raise ProtocolError("usage must be object")
    return {"model": str(raw.get("model") or ""), "answers": parsed}


# ── bounded state (no secrets, ~1200 chars cap by construction) ─────────────


def _build_state(arguments: dict, language: str) -> dict[str, Any]:
    request = str(arguments.get("text") or "").strip()[:STATE_MAX_REQUEST_CHARS]
    recent = arguments.get("recent_transcript")
    turns: list[dict[str, str]] = []
    if isinstance(recent, list):
        for item in recent[-STATE_MAX_TURNS:]:
            if not isinstance(item, dict):
                continue
            role = "user" if str(item.get("role") or "") == "user" else "bot"
            text = str(item.get("text") or "").strip()[:STATE_MAX_TURN_CHARS]
            if text:
                turns.append({"role": role, "text": text})
    flags = {
        "work_in_chat": bool(arguments.get("work_in_chat", True)),
        "language": language,
    }
    return {"request": request, "recent_transcript": turns, "flags": flags}


# ── urllib transport with the hard 600 ms wall-clock budget ─────────────────


def _http_transport(payload: dict[str, Any]) -> dict[str, Any]:
    api_key = env_key()
    if not api_key:
        raise TransportError("missing TYPESAFE_API_KEY / JEV_API_KEY")
    request = urllib.request.Request(
        f"{env_base_url()}{ENDPOINT}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    outcome: dict[str, Any] = {}

    def run() -> None:
        try:
            with urllib.request.urlopen(request, timeout=_env_timeout_s()) as resp:
                outcome["body"] = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            outcome["error"] = TransportError(f"http {exc.code}")
        except Exception as exc:  # noqa: BLE001 — fail-open, el caller decide
            outcome["error"] = TransportError(f"{type(exc).__name__}: {exc}")

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(HARD_TIMEOUT_MS / 1000.0 + 0.05)
    if worker.is_alive():
        raise TransportError("hard timeout exceeded")
    error = outcome.get("error")
    if error is not None:
        raise error
    body = outcome.get("body", "")
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise TransportError("invalid JSON response") from exc
    if not isinstance(parsed, dict):
        raise TransportError("response must be a JSON object")
    return parsed


# ── the decision ────────────────────────────────────────────────────────────


def _elapsed_ms(t0: float) -> int:
    return max(0, int((time.monotonic() - t0) * 1000))


def _undecided(reason: str, t0: float) -> dict[str, Any]:
    return {"decided": False, "reason": reason, "latency_ms": _elapsed_ms(t0)}


def decide(
    arguments: Mapping[str, Any] | None,
    language: str = "es",
    *,
    transport: Transport | None = None,
) -> dict[str, Any]:
    """Return a decision dict; ``decided: False`` on ANY failure (fail-open)."""
    t0 = time.monotonic()
    args = arguments if isinstance(arguments, dict) else {}
    lang = "en" if language == "en" else "es"
    if not env_key():
        return _undecided("missing_api_key", t0)
    questions = build_questions(lang)
    try:
        payload = build_request(_build_state(args, lang), questions)
    except ProtocolError as exc:
        return _undecided(f"protocol:{exc}", t0)
    try:
        raw = (transport or _http_transport)(payload)
    except Exception as exc:  # noqa: BLE001 — timeout/red/error → fallback
        return _undecided(f"transport:{type(exc).__name__}", t0)
    try:
        parsed = parse_systemone_response(raw, questions)
    except ProtocolError as exc:
        return _undecided(f"protocol:{exc}", t0)
    answers = parsed["answers"]
    is_task = answers["is_task"]["noul"]
    route_answer = answers["route"]
    route = route_answer["choice"]
    confidence = route_answer["confidence"]
    if route not in ROUTES or confidence < MIN_ROUTE_CONFIDENCE:
        return _undecided("low_confidence_or_unknown_route", t0)
    effective_route = route if is_task >= IS_TASK_THRESHOLD else "answer_self"
    return {
        "decided": True,
        "is_task": round(float(is_task), 4),
        "route": effective_route,
        "route_raw": route,
        "confidence": round(float(confidence), 4),
        "needs_confirm": round(float(answers["needs_confirm"]["noul"]), 4),
        "model": parsed["model"],
        "latency_ms": _elapsed_ms(t0),
    }
