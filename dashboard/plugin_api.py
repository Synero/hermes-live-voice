"""talk-desktop — backend half: mintea sesiones Realtime para el desktop nativo.

Reusa el lane Codex-OAuth del plugin hermes-talk (talk_* modules) y agrega lo
que el browser tab no tiene: identity por BOT (SOUL.md del profile del VPS)
y gestión de la sesión Codex OAuth (estado, login device-code, logout).

Routes (mounted at /api/plugins/talk-desktop/):
  GET  /status              → auth codex ok? profile list? modelo/voz configuradas?
  POST /session             → {profile?, voice?} → descriptor efímero (to_wire)
  GET  /codex/status        → estado de la sesión Codex (sin secretos) + login en curso
  POST /codex/login/start   → inicia `codex login --device-auth` (URL + código para el usuario)
  POST /codex/login/cancel  → cancela un login pendiente
  POST /codex/logout        → cierra la sesión (respaldo del auth.json; afecta voz + Codex CLI del VPS)
  GET  /codex/usage         → uso/quota del plan ChatGPT (rate_limit del endpoint wham/usage)
  GET  /voice/usage         → uso local acumulado de Live Voice (últimos 7 días)
  POST /voice/usage/report  → {durationMs, audioMs} → acumula la sesión al día (lo llama el renderer al colgar)
  POST /tool                → ejecuta una tool de voz (talk_tools) y devuelve texto para hablar
  POST /codexlive/session   → {profile?, voice?, offer(SDP)} → negocia gpt-live-1-codex (v3) vía codex app-server
  POST /codexlive/stop      → {threadId} → cierra la sesión realtime de Codex Live

El secret efímero va SOLO al renderer del desktop; el OAuth nunca sale del VPS.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_HERMES_TALK_ROOT = Path.home() / ".hermes" / "plugins" / "hermes-talk"
for _p in (str(_PLUGIN_ROOT), str(_HERMES_TALK_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import talk_auth  # noqa: E402
import talk_capabilities  # noqa: E402
import talk_config  # noqa: E402
import talk_host  # noqa: E402
import talk_identity  # noqa: E402
import talk_tools  # noqa: E402
import talk_wire  # noqa: E402

try:
    from fastapi import APIRouter, HTTPException, Request
except ImportError:  # pragma: no cover
    APIRouter = None
    HTTPException = None
    Request = None

router = APIRouter() if APIRouter else None
_log = logging.getLogger("hermes.plugins.talk-desktop")

_PROFILES_ROOT = Path.home() / ".hermes" / "profiles"
_MINT_LOCK = threading.Lock()


def _list_bots() -> list[str]:
    if not _PROFILES_ROOT.is_dir():
        return []
    return sorted(
        e.name for e in _PROFILES_ROOT.iterdir()
        if e.is_dir() and (e / "SOUL.md").is_file()
    )


def _profile_home(profile: str) -> Path | None:
    # Nombre de perfil simple (sin traversal). Los perfiles pueden ser symlinks
    # (p.ej. ticketin → ~/.hermes-ticketin), así que NO se exige que el path
    # resuelto quede bajo profiles/ — solo que el nombre sea seguro y exista.
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", profile or ""):
        return None
    p = _PROFILES_ROOT / profile
    if not p.is_dir():
        return None
    return p.resolve()


def _bot_display_name(profile: str) -> str:
    """Nombre del bot desde el encabezado del SOUL.md; fallback al slug.

    Formatos vistos: "# SOUL.md — Ticketin 🎟️", "# Pavi — CEO de Pavilion",
    "# Pix — CEO de RetroPixel", "# Bestia", "# RONDA DevOps".
    """
    home = _profile_home(profile)
    if home is not None:
        try:
            first = ((home / "SOUL.md").read_text(encoding="utf-8", errors="replace").splitlines() or [""])[0].strip()
        except OSError:
            first = ""
        first = re.sub(r"[^\w\sÁÉÍÓÚÜÑáéíóúüñ—·-]", " ", first.lstrip("#").strip())
        parts = [p.strip() for p in first.split("—") if p.strip()]
        name = ""
        for part in parts:
            if part.lower().replace(" ", "").startswith("soul.md") or part.lower().startswith("soul"):
                continue
            name = part
            break
        if not name and parts:
            name = parts[-1]
        name = name.split("·")[0].strip()
        if name:
            return name[:40]
    return profile.capitalize()


def _bot_identity_sections(profile: str) -> dict[str, str]:
    """Identity del bot: SOUL.md del profile, con la misma sanitización que hermes-talk."""
    home = _profile_home(profile)
    if home is None:
        return {}
    sections: dict[str, str] = {}
    soul = home / "SOUL.md"
    if soul.is_file():
        try:
            body = soul.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            body = ""
        if body:
            try:
                body = talk_host._sanitize_identity_entries(body, "SOUL.md")
            except Exception:  # noqa: BLE001
                body = ""
            if body:
                sections["PERSONA"] = body[: talk_config.identity_char_limit("persona") or 8000]
    return sections


_DIRECTIVE_PATH = _PLUGIN_ROOT / "language_directive.txt"
_LANGUAGE_DIRECTIVE_DEFAULT = (
    "\n\nIDIOMA Y VOZ: Habla en el idioma en que te habla el usuario — si te habla en español, "
    "responde en español natural de Chile; si te habla en inglés, en inglés. Nunca mezcles idiomas "
    "ni respondas en inglés cuando te hablan en español. Tu voz debe sonar como la de un hablante "
    "nativo del idioma que estés usando."
)


def _language_directive() -> str:
    """Directiva de idioma editable en runtime (archivo sin restart) con default embebido."""
    try:
        if _DIRECTIVE_PATH.is_file():
            text = _DIRECTIVE_PATH.read_text(encoding="utf-8").strip()
            if text:
                return "\n\n" + text
    except OSError:
        pass
    return _LANGUAGE_DIRECTIVE_DEFAULT


def _resolve_voice(requested: str | None) -> str:
    """Voice pedida o la configurada (TALK_VOICE / config)."""
    if requested:
        v = str(requested).strip().lower()
        if v in talk_config.OPENAI_REALTIME_VOICES:
            return v
        raise HTTPException(status_code=400, detail=f"voice '{v}' no disponible")
    try:
        return talk_config.voice() or "marin"
    except Exception:  # noqa: BLE001
        return "marin"


_SEND_TO_CHAT_DESC = (
    "Send a request to the user's currently open chat session so the REAL agent handles it "
    "with its full context, memory and tools. The request appears in that chat as a message "
    "from the user and the agent's reply streams there; you receive the reply text back and "
    "read or summarize it aloud. Use this whenever the user asks for actual work, actions, or "
    "information from their conversations, files or services \u2014 anything beyond a quick "
    "conversational answer. Do NOT use it for small talk or things you can answer instantly."
)


def _send_to_chat_tool() -> dict:
    return {
        "type": "function",
        "name": "send_to_chat",
        "description": _SEND_TO_CHAT_DESC,
        "parameters": {
            "type": "object",
            "properties": {
                "request": {
                    "type": "string",
                    "description": "The full request to hand to the agent, phrased naturally in the user's own language.",
                },
            },
            "required": ["request"],
        },
    }


def _mint_for(profile: str | None, voice: str, allow_chat: bool = True):
    """Mint con identity del bot (o del host si no se pide profile)."""
    tools = talk_tools.default_talk_tools()
    if allow_chat:
        tools = tools + [_send_to_chat_tool()]
    if profile:
        sections = _bot_identity_sections(profile)
    else:
        sections = talk_host.host().identity_sections()
    instructions = talk_identity.build_instructions(
        sections,
        tools=tools,
        lane="dashboard",
        capabilities=talk_capabilities.instruction_section(),
    )
    instructions = instructions + _language_directive()
    if allow_chat:
        instructions += (
            "\n\nVOZ + CHAT: el usuario tiene un chat abierto en la aplicaci\u00f3n. Para conversaci\u00f3n "
            "r\u00e1pida, preguntas simples o charla, responde T\u00da directo. Cuando pida TRABAJO REAL "
            "(hacer, crear, revisar, buscar en sus cosas, actuar sobre algo), llama a send_to_chat con la "
            "petici\u00f3n completa y natural en su idioma: el sistema la env\u00eda a su chat abierto, el "
            "agente real responde all\u00ed, y luego recibir\u00e1s el resultado \u2014 l\u00e9elo o "
            "res\u00famelo en voz alta con naturalidad, m\u00e1ximo 2-3 frases. Si no hay chat abierto, "
            "la herramienta te lo dir\u00e1."
        )
    instructions += _VOICE_POLICY
    if profile:
        name = _bot_display_name(profile)
        if "You are Hermes, speaking live" in instructions:
            instructions = instructions.replace("You are Hermes, speaking live", f"You are {name}, speaking live", 1)
        instructions += (
            f"\n\nIDENTIDAD: Eres {name} (agente de Nacho en el VPS). Si te preguntan quién eres, "
            f"responde como {name} con su rol — nunca digas que eres Hermes, un modelo, ni un asistente genérico."
        )
    auth = talk_auth.resolve_auth()
    descriptor = talk_wire.mint_ephemeral_session(
        auth_token=auth.token,
        model=talk_config.talk_model(),
        voice=voice,
        instructions=instructions,
        tools=tools,
        text_output=False,
    )
    return descriptor, auth


# ── Codex OAuth: sesión de voz (device-code login / logout / status) ─────────

_LOGIN_URL = "https://auth.openai.com/codex/device"
_URL_RE = re.compile(r"https://auth\.openai\.com/codex/device")
_CODE_RE = re.compile(r"\b([A-Z0-9]{4}-[A-Z0-9]{5})\b")
_LOGIN_TIMEOUT_S = 16 * 60

_LOGIN: dict = {
    "status": "idle",  # idle | pending | done | error
    "url": None,
    "code": None,
    "message": None,
    "started_at": None,
    "proc": None,
    "output": "",
}
_LOGIN_LOCK = threading.Lock()


def _codex_auth_path() -> Path:
    configured = os.environ.get("CODEX_HOME", "").strip()
    base = Path(configured) if configured else Path.home() / ".codex"
    return base / "auth.json"


def _codex_binary() -> str | None:
    candidates = (str(Path.home() / ".local" / "bin" / "codex"), shutil.which("codex"), "/usr/local/bin/codex")
    for cand in candidates:
        if cand and os.access(cand, os.X_OK):
            return cand
    return None


def _terminate(proc) -> None:
    try:
        proc.terminate()
        try:
            proc.wait(timeout=4)
        except Exception:  # noqa: BLE001
            proc.kill()
    except Exception:  # noqa: BLE001
        pass


def _login_reader(proc) -> None:
    try:
        for line in iter(proc.stdout.readline, ""):
            _LOGIN["output"] = (_LOGIN["output"] + line)[-4000:]
            if not _LOGIN["url"]:
                m = _URL_RE.search(line)
                if m:
                    _LOGIN["url"] = m.group(0)
            if not _LOGIN["code"]:
                m = _CODE_RE.search(line)
                if m:
                    _LOGIN["code"] = m.group(1)
    except Exception:  # noqa: BLE001
        pass


def _login_check() -> None:
    """Fold the device-login process state into _LOGIN (call before reporting)."""
    proc = _LOGIN.get("proc")
    if _LOGIN.get("status") != "pending":
        return
    if proc is not None and proc.poll() is None:
        started = _LOGIN.get("started_at") or 0
        if time.time() - started > _LOGIN_TIMEOUT_S:
            _terminate(proc)
            _LOGIN.update(status="error", message="timeout: el código expiró sin aprobarse", proc=None)
        return
    # process finished (or vanished)
    try:
        diag = talk_auth.auth_diagnostic()
        state = diag.get("codex_oauth")
    except Exception:  # noqa: BLE001
        state = None
    if state in {"valid", "expired"}:
        _LOGIN.update(status="done", message="Sesión iniciada", proc=None, url=None, code=None,
                      output="")
    else:
        tail = " / ".join((_LOGIN.get("output") or "").strip().splitlines()[-3:])
        _LOGIN.update(status="error", message=("No se pudo iniciar sesión" + (f": {tail}" if tail else "")),
                      proc=None)


def _start_codex_login() -> dict:
    with _LOGIN_LOCK:
        _login_check()
        proc = _LOGIN.get("proc")
        if proc is not None and proc.poll() is None:
            _terminate(proc)
        binary = _codex_binary()
        if not binary:
            return {"ok": False, "message": "codex CLI no encontrado en el servidor"}
        # Respaldo de la sesión actual: iniciar un login nuevo puede reemplazar/limpiar
        # ~/.codex/auth.json antes de completarse (verificado 2026-09-11).
        auth_path = _codex_auth_path()
        if auth_path.exists():
            try:
                ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
                shutil.copy2(auth_path, auth_path.with_name(f"auth.json.bak-pre-login-{ts}"))
            except OSError:
                pass
        env = os.environ.copy()
        env.pop("CODEX_HOME", None)  # el login escribe el ~/.codex estándar
        proc = subprocess.Popen(
            [binary, "login", "--device-auth"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
            cwd=str(Path.home()),
        )
        _LOGIN.update(status="pending", url=None, code=None, message=None,
                      started_at=time.time(), proc=proc, output="")
        threading.Thread(target=_login_reader, args=(proc,), daemon=True).start()
    # el reader tarda unos ms en capturar URL/código — esperar un poco
    deadline = time.time() + 8
    while time.time() < deadline and not (_LOGIN.get("url") and _LOGIN.get("code")):
        time.sleep(0.2)
    return {"ok": True, "url": _LOGIN.get("url") or _LOGIN_URL, "code": _LOGIN.get("code"),
            "status": _LOGIN["status"]}


if router is not None:

    @router.get("/status")
    async def status() -> dict:
        try:
            auth = talk_auth.resolve_auth()
            auth_source = auth.source
        except Exception as exc:  # noqa: BLE001
            auth_source = f"unavailable: {exc}"
        return {
            "ok": True,
            "auth": auth_source,
            "model": talk_config.talk_model(),
            "voices": list(talk_config.OPENAI_REALTIME_VOICES),
            "bots": _list_bots(),
        }

    @router.post("/session")
    async def create_session(request: Request) -> dict:
        body = {}
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        profile = str(body.get("profile") or "").strip() or None
        allow_chat = bool(body.get("allowChat", True))
        if profile and _profile_home(profile) is None:
            raise HTTPException(status_code=400, detail=f"perfil '{profile}' no existe")
        voice = _resolve_voice(body.get("voice"))
        # Serializar mints con identity override: el patch de get_hermes_home es
        # global al proceso mientras dura _mint_for; un mint a la vez.
        with _MINT_LOCK:
            if profile:
                orig = talk_config.get_hermes_home
                talk_config.get_hermes_home = lambda: _profile_home(profile)
                try:
                    descriptor, auth = await asyncio.to_thread(_mint_for, profile, voice, allow_chat)
                finally:
                    talk_config.get_hermes_home = orig
            else:
                descriptor, auth = await asyncio.to_thread(_mint_for, None, voice, allow_chat)
        return {
            "ok": True,
            "profile": profile,
            "botName": _bot_display_name(profile) if profile else "Luna",
            **descriptor.to_wire(),
            "authSource": auth.source,
            "voiceMode": "native",
        }

    @router.get("/codex/status")
    async def codex_status() -> dict:
        _login_check()
        try:
            st = talk_auth.auth_status()
            diag = talk_auth.auth_diagnostic()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                    "login": {k: _LOGIN.get(k) for k in ("status", "url", "code", "message")}}
        return {
            "ok": True,
            "configured": st.get("configured"),
            "lane": st.get("source"),
            "detail": st.get("detail"),
            "codexOauth": diag.get("codex_oauth"),
            "refreshRequired": diag.get("refresh_required"),
            "preference": diag.get("preference"),
            "login": {k: _LOGIN.get(k) for k in ("status", "url", "code", "message")},
        }

    @router.get("/codex/usage")
    async def codex_usage() -> dict:
        def _do() -> dict:
            try:
                auth = talk_auth.resolve_auth()
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc)[:160]}
            try:
                import httpx
                account_id = ""
                try:
                    data = json.loads(_codex_auth_path().read_text(encoding="utf-8"))
                    account_id = str((data.get("tokens") or {}).get("account_id") or "")
                except Exception:  # noqa: BLE001
                    pass
                headers = {
                    "Authorization": f"Bearer {auth.token}",
                    "Accept": "application/json",
                    "User-Agent": "codex-cli",
                }
                if account_id:
                    headers["ChatGPT-Account-Id"] = account_id
                payload = None
                last_status = None
                for url in (
                    "https://chatgpt.com/backend-api/wham/usage",
                    "https://api.openai.com/api/codex/usage",
                ):
                    with httpx.Client(timeout=12.0) as client:
                        resp = client.get(url, headers=headers)
                    last_status = resp.status_code
                    if resp.status_code == 200:
                        payload = resp.json() or {}
                        break
                if payload is None:
                    return {"ok": False, "error": f"usage http {last_status}"}
                rl = payload.get("rate_limit") or {}
                windows: dict = {}
                for key, fallback in (("primary_window", "5h"), ("secondary_window", "semanal")):
                    w = rl.get(key) or {}
                    if not (isinstance(w, dict) and w):
                        continue
                    mins = w.get("window_minutes")
                    secs = w.get("limit_window_seconds")
                    if not mins and secs:
                        try:
                            mins = int(secs) // 60
                        except Exception:  # noqa: BLE001
                            mins = None
                    if mins:
                        mins = int(mins)
                        if mins >= 6 * 1440:
                            label = "semanal"
                        else:
                            label = f"{max(1, round(mins / 60))}h"
                    else:
                        label = fallback
                    windows[label] = {
                        "usedPercent": w.get("used_percent"),
                        "windowMinutes": mins,
                        "resetsAt": w.get("reset_at") or w.get("resets_at"),
                    }
                return {
                    "ok": True,
                    "plan": payload.get("plan_type") or payload.get("plan") or None,
                    "windows": windows,
                }
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:160]}

        return await asyncio.to_thread(_do)

    # ── uso de Live Voice (metrado local por sesión) ──────────────────────────
    _VU_FILE = _PLUGIN_ROOT / "data" / "voice-usage.json"
    _VU_LOCK = threading.Lock()

    def _vu_load() -> dict:
        try:
            data = json.loads(_VU_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("sessions"), list):
                return data
        except Exception:  # noqa: BLE001
            pass
        return {"sessions": []}

    def _vu_save(data: dict) -> None:
        try:
            _VU_FILE.parent.mkdir(parents=True, exist_ok=True)
            _VU_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    @router.post("/voice/usage/report")
    async def voice_usage_report(request: Request) -> dict:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        dur = max(0, int((body or {}).get("durationMs") or 0))
        aud = max(0, int((body or {}).get("audioMs") or 0))
        if dur <= 0:
            return {"ok": False, "error": "durationMs requerido"}
        now_ms = int(time.time() * 1000)
        with _VU_LOCK:
            data = _vu_load()
            sess = data.setdefault("sessions", [])
            sess.append({"t": now_ms, "ms": dur, "audio_ms": aud})
            keep = [s for s in sess if isinstance(s, dict) and int(s.get("t") or 0) > now_ms - 8 * 24 * 3600 * 1000]
            data["sessions"] = keep[-2000:]
            _vu_save(data)
        return {"ok": True}

    @router.get("/voice/usage")
    async def voice_usage() -> dict:
        with _VU_LOCK:
            data = _vu_load()
        import datetime as _dt
        now_ms = int(time.time() * 1000)
        midnight = _dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today_ms = int(midnight.timestamp() * 1000)
        sess = [s for s in (data.get("sessions") or []) if isinstance(s, dict)]

        def agg(since_ms: int) -> dict:
            ms = aud = n = 0
            for s in sess:
                if int(s.get("t") or 0) >= since_ms:
                    ms += int(s.get("ms") or 0)
                    aud += int(s.get("audio_ms") or 0)
                    n += 1
            return {"minutes": ms / 60000.0, "audioMinutes": aud / 60000.0, "sessions": n}

        return {
            "ok": True,
            "rolling5h": agg(now_ms - 5 * 3600 * 1000),
            "rolling24h": agg(now_ms - 24 * 3600 * 1000),
            "week": agg(now_ms - 7 * 24 * 3600 * 1000),
            "today": agg(today_ms),
        }

    @router.post("/tool")
    async def run_tool(request: Request) -> dict:
        body = {}
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        name = str(body.get("name") or "").strip()
        arguments = body.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        if not name:
            raise HTTPException(status_code=400, detail="name requerido")
        try:
            output = await asyncio.wait_for(
                asyncio.to_thread(talk_tools.execute_talk_tool, name, arguments),
                timeout=110,
            )
        except asyncio.TimeoutError:
            output = (
                f"La herramienta {name} sigue corriendo; no espero más aquí. "
                "Pídeme revisar el resultado en un momento."
            )
        except talk_tools.TalkToolError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "output": output}

    @router.post("/codex/login/start")
    async def codex_login_start() -> dict:
        return await asyncio.to_thread(_start_codex_login)

    @router.post("/codex/login/cancel")
    async def codex_login_cancel() -> dict:
        with _LOGIN_LOCK:
            proc = _LOGIN.get("proc")
            if proc is not None and proc.poll() is None:
                _terminate(proc)
            _LOGIN.update(status="idle", proc=None, url=None, code=None, message=None, output="")

        def _restore_if_needed() -> None:
            # Un login cancelado puede dejar ~/.codex/auth.json limpio; restaurar el
            # respaldo pre-login más reciente para no perder la sesión previa.
            path = _codex_auth_path()
            if path.exists():
                return
            backups = sorted(path.parent.glob("auth.json.bak-pre-login-*"))
            if backups:
                try:
                    shutil.copy2(backups[-1], path)
                except OSError:
                    pass

        await asyncio.to_thread(_restore_if_needed)
        return {"ok": True}

    @router.post("/codex/logout")
    async def codex_logout() -> dict:
        def _do() -> dict:
            path = _codex_auth_path()
            if not path.exists():
                return {"ok": True, "message": "No había sesión que cerrar"}
            ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            bak = path.with_name(f"auth.json.bak-voice-logout-{ts}")
            try:
                shutil.copy2(path, bak)
                path.unlink()
            except OSError as exc:
                return {"ok": False, "message": f"No se pudo cerrar la sesión: {exc}"}
            return {"ok": True, "message": f"Sesión cerrada (respaldo: {bak.name})"}

        return await asyncio.to_thread(_do)


# ── Codex Live-1 (gpt-live-1-codex, v3/frameless) vía codex app-server ───────
#
# El app-server de Codex (>=0.154) negocia una sesión WebRTC contra el backend
# de ChatGPT usando la suscripción (model_provider=openai). El cliente manda su
# SDP offer; acá se devuelve el answer. El audio va directo cliente<->OpenAI.

_CL: dict = {"proc": None, "notifs": [], "seq": 0, "thread_id": None}
_CL_LOCK = threading.Lock()


def _cl_send(obj: dict) -> None:
    proc = _CL["proc"]
    if proc is None or proc.poll() is not None:
        raise RuntimeError("codex app-server no disponible")
    proc.stdin.write(json.dumps(obj) + "\n")
    proc.stdin.flush()


def _cl_reader(proc) -> None:
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                _CL["notifs"].append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
            if len(_CL["notifs"]) > 6000:
                del _CL["notifs"][:2000]
    except Exception:  # noqa: BLE001
        pass


def _cl_request(method: str, params: dict, timeout: float = 30.0):
    _CL["seq"] += 1
    rid = _CL["seq"]
    start = len(_CL["notifs"])
    _cl_send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
    t0 = time.time()
    while time.time() - t0 < timeout:
        for m in list(_CL["notifs"])[start:]:
            if m.get("id") == rid:
                if "error" in m:
                    raise RuntimeError(str(m["error"].get("message") or m["error"])[:400])
                return m.get("result")
        time.sleep(0.1)
    raise TimeoutError(f"codex app-server: {method} sin respuesta")


def _codex_binary() -> str | None:
    """Resuelve el binario de codex aunque el PATH del servicio no traiga ~/.npm-global/bin."""
    try:
        found = shutil.which("codex")
    except Exception:  # noqa: BLE001
        found = None
    if found:
        return found
    for cand in (Path.home() / ".npm-global" / "bin" / "codex",
                 Path("/usr/local/bin/codex"),
                 Path.home() / ".local" / "bin" / "codex",
                 Path("/usr/bin/codex")):
        if cand.exists():
            return str(cand)
    try:
        out = subprocess.run(["bash", "-lc", "command -v codex"], capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip().splitlines()[0]
    except Exception:  # noqa: BLE001
        pass
    return None


def _cl_ensure() -> None:
    proc = _CL["proc"]
    if proc is not None and proc.poll() is None:
        return
    binary = _codex_binary()
    if not binary:
        raise RuntimeError("codex no encontrado (PATH del servicio sin ~/.npm-global/bin)")
    env = os.environ.copy()
    env["PATH"] = (str(Path.home() / ".npm-global" / "bin") + ":" + env.get("PATH", "")).strip(":")
    # clave: la lane de voz usa la SUSCRIPCIÓN (auth.json), no una API key de entorno
    env.pop("OPENAI_API_KEY", None)
    env.pop("CODEX_API_KEY", None)
    proc = subprocess.Popen(
        [binary, "app-server", "--listen", "stdio://",
         "--enable", "realtime_conversation",
         "-c", "model_provider=openai",
         "-c", "suppress_unstable_features_warning=true"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, bufsize=1, env=env,
    )
    _CL["proc"] = proc
    _CL["notifs"] = []
    _CL["thread_id"] = None
    threading.Thread(target=_cl_reader, args=(proc,), daemon=True).start()
    _cl_request("initialize", {"clientInfo": {"name": "hermes-talk-desktop", "version": "1.0"},
                               "capabilities": {"experimentalApi": True}}, timeout=25)


def _codexlive_persona(profile: str | None) -> str:
    if profile:
        sections = _bot_identity_sections(profile)
        name = _bot_display_name(profile)
    else:
        sections = talk_host.host().identity_sections()
        name = "Luna"
    base = talk_identity.build_instructions(sections, tools=[], lane="dashboard")
    if profile and "You are Hermes, speaking live" in base:
        base = base.replace("You are Hermes, speaking live", f"You are {name}, speaking live", 1)
    base += _language_directive()
    base += (
        f"\n\nVOZ: hablas con el usuario en una sesión de voz en vivo. Respuestas breves, naturales y al grano."
        f" IDENTIDAD: eres {name}; si te preguntan quién eres, responde como {name} con su rol — nunca como un asistente genérico."
    )
    base += _VOICE_POLICY
    return base


_AGENT_INSTR = (
    "Estás conectado a una sesión de voz en vivo con el usuario. Cuando la sesión de voz te delegue "
    "una petición, actúala con tus herramientas y responde conciso: tu texto se leerá en voz alta."
)

# Modo cliente (default): el CLIENTE ejecuta lo que la voz pide (chat de Hermes del usuario).
# El core igual rutea cada delegación a este thread como fallback fantasma — si el agente
# hiciera trabajo aquí (tool calls, gpt-5.6-sol), quemaría el plan ChatGPT y duplicaría todo.
# Esta instrucción lo neutraliza: responder 'skip' sin usar herramientas.
_AGENT_INSTR_SKIP = (
    "Estás conectado a una sesión de voz en vivo, pero es el CLIENTE (la app) quien ejecuta las "
    "peticiones de esa sesión. Si recibes un mensaje <realtime_delegation>, NO ejecutes nada, NO uses "
    "herramientas y NO leas archivos: responde únicamente la palabra: skip"
)

# Política de delegación para el MODELO DE VOZ (el que decide qué pasa al backend).
# Sin esto delega cualquier cosa (saludos, fragmentos, "aló") y satura el chat con turnos largos.
_VOICE_POLICY = (
    "\n\nVOICE DELEGATION POLICY (live call):\n"
    "- Delegate to the chat/backend ONLY when the user asks you to DO something (check, review, find, run, make, fix, send, remember) or when answering needs real facts/actions from the backend.\n"
    "- Do NOT delegate greetings, small talk, acknowledgements, filler, thinking out loud, fragments, or repeats of something already answered (e.g. 'dale', 'ok', 'ya', 'bueno', '¿aló?', '¿me escuchas?'). Answer those yourself, briefly, or stay quiet.\n"
    "- If the request is unclear, ask ONE short clarifying question yourself instead of delegating.\n"
    "- While a task is running: say ONE brief line ('dale, lo reviso') and WAIT silently; do not guess results, do not delegate again in the meantime; if the user speaks, tell them you are still on it.\n"
    "- When the result arrives, read the key facts back in one or two short sentences."
)


def _talk_settings() -> dict:
    try:
        p = _PLUGIN_ROOT / "settings.json"
        if p.is_file():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:  # noqa: BLE001
        pass
    return {}


def _agent_model() -> str | None:
    """Modelo del agente delegado (el thread de Codex que ejecuta tareas de voz).
    En cajas con un modelo default no-ChatGPT (p.ej. un proxy) hay que forzar un
    modelo oficial; si no, el turno del agente falla con 400."""
    v = os.environ.get("TALK_CODEX_AGENT_MODEL") or str(_talk_settings().get("codexAgentModel") or "")
    v = v.strip()
    return v or None


def _cl_thread_ensure() -> str:
    want = _agent_model()
    tid = _CL.get("thread_id")
    if tid and _CL.get("thread_model") == want:
        return tid
    body = {"cwd": str(Path.home()), "modelProvider": "openai"}
    if want:
        body["model"] = want
    th = _cl_request("thread/start", body, timeout=30)
    tid = ((th or {}).get("thread") or {}).get("id")
    if not tid:
        raise RuntimeError("codex: no se pudo crear el thread")
    _CL["thread_id"] = tid
    _CL["thread_model"] = want
    return tid


def _codexlive_start(profile: str | None, voice: str, offer: str) -> dict:
    _cl_ensure()
    tid = _cl_thread_ensure()
    persona = _codexlive_persona(profile)
    client_managed = str(_talk_settings().get("delegation") or "client").strip().lower() != "server"
    params = {
        "threadId": tid,
        "transport": {"type": "webrtc", "sdp": offer},
        "outputModality": "audio",
        "version": "v3",
        "voice": voice or "cove",
        "prompt": persona,
        "realtimeStartInstructions": _AGENT_INSTR if not client_managed else _AGENT_INSTR_SKIP,
        # Gestión de tareas delegadas por voz: POR DEFECTO el CLIENTE (el desktop) las
        # ejecuta en el chat de Hermes del usuario — usa SU configuración/modelos/providers.
        # Solo si settings.json lleva {"delegation": "server"} se deja que el core las
        # mande al agente Codex del thread (lane ChatGPT). ackFiller = frases de espera.
        "clientManagedHandoffs": client_managed,
        "delegationAckFiller": True,
    }
    drop_groups = (("delegationAckFiller",), ("clientManagedHandoffs",), ("realtimeStartInstructions", "prompt"))
    start = len(_CL["notifs"])
    gi = 0
    while True:
        try:
            _cl_request("thread/realtime/start", params, timeout=25)
            break
        except RuntimeError as exc:
            low = str(exc).lower()
            if "unknown field" in low and gi < len(drop_groups):
                for f in drop_groups[gi]:
                    params.pop(f, None)
                gi += 1
                start = len(_CL["notifs"])
                continue
            if "thread" in low and ("not found" in low or "no such" in low or "not loaded" in low or "missing" in low):
                # thread obsoleto: recrear y reintentar una vez
                _CL["thread_id"] = None
                _CL["thread_model"] = None
                tid2 = _cl_thread_ensure()
                params["threadId"] = tid2
                start = len(_CL["notifs"])
                _cl_request("thread/realtime/start", params, timeout=25)
                break
            if "already" in low:
                # sesión realtime colgada en el thread: cerrarla y reintentar
                try:
                    _cl_request("thread/realtime/stop", {"threadId": params.get("threadId")}, timeout=10)
                except Exception:  # noqa: BLE001
                    pass
                start = len(_CL["notifs"])
                _cl_request("thread/realtime/start", params, timeout=25)
                break
            raise
    answer = None
    rsid = None
    t0 = time.time()
    while time.time() - t0 < 45:
        for m in list(_CL["notifs"])[start:]:
            meth = m.get("method") or ""
            if meth == "thread/realtime/sdp":
                answer = (m.get("params") or {}).get("sdp") or answer
            elif meth == "thread/realtime/started":
                rsid = (m.get("params") or {}).get("realtimeSessionId") or rsid
            elif meth == "thread/realtime/error":
                raise RuntimeError("codex live: " + str((m.get("params") or {}).get("message"))[:300])
        if answer:
            break
        time.sleep(0.15)
    if not answer:
        raise TimeoutError("codex live: sin SDP de respuesta")
    return {"answer": answer, "realtimeSessionId": rsid, "threadId": params.get("threadId") or tid, "version": "v3", "engine": "codex",
            "handoff": "client" if client_managed else "server"}


def _codexlive_stop(thread_id: str | None) -> None:
    if not thread_id:
        return
    try:
        _cl_request("thread/realtime/stop", {"threadId": thread_id}, timeout=10)
    except Exception:  # noqa: BLE001
        pass


if router is not None:
    @router.post("/codexlive/session")
    async def codexlive_session(request: Request) -> dict:
        body = {}
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        profile = str(body.get("profile") or "").strip() or None
        if profile and _profile_home(profile) is None:
            raise HTTPException(status_code=400, detail=f"perfil '{profile}' no existe")
        voice = str(body.get("voice") or "cove")
        offer = str(body.get("offer") or "")
        if not offer:
            raise HTTPException(status_code=400, detail="falta el SDP offer")

        def _do():
            with _CL_LOCK:
                return _codexlive_start(profile, voice, offer)

        try:
            result = await asyncio.to_thread(_do)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)[:400]) from exc
        return {
            "ok": True,
            "engine": "codex",
            "profile": profile,
            "botName": _bot_display_name(profile) if profile else "Luna",
            **result,
        }

    @router.post("/codexlive/stop")
    async def codexlive_stop_route(request: Request) -> dict:
        body = {}
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        tid = str(body.get("threadId") or _CL.get("thread_id") or "")
        await asyncio.to_thread(_codexlive_stop, tid)
        return {"ok": True}

