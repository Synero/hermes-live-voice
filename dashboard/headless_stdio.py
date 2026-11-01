"""Opt-in, local-only Codex Live session control for external audio clients.

Launch with ``python -u dashboard/headless_stdio.py`` from an installed
``talk-desktop`` plugin. JSON-RPC-like one-line requests travel over stdin and
responses over stdout. This interface does not carry audio, tokens, or Hermes
agent tool calls; the caller owns its WebRTC transport and task execution.

The child process owns its Codex app-server and may host ONE active voice
session. A different process is required for another concurrent session.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any

MAX_REQUEST_LENGTH = 262_144
SUPPORTED_MODELS = {"gpt-live-1-codex", "gpt-live-1.1"}


class ProtocolError(ValueError):
    """User-correctable contract error, safe to send to the local caller."""


def load_backend():
    """Load the installed Synero backend with headless tool isolation enabled."""
    # Force this even if the caller supplied a conflicting environment value.
    os.environ["TALK_DESKTOP_HEADLESS_CLIENT_ONLY"] = "1"
    path = Path(__file__).with_name("plugin_api.py")
    spec = importlib.util.spec_from_file_location("talk_desktop_headless_backend", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("talk-desktop backend unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HeadlessSessions:
    """Supported v1 control contract around Synero's existing Codex backend."""

    def __init__(self, backend: Any):
        self.backend = backend
        self.active_thread: str | None = None

    def status(self) -> dict[str, Any]:
        settings = self.backend._talk_settings()
        model = str(settings.get("liveModel") or "gpt-live-1-codex").strip()
        client_managed = str(settings.get("delegation") or "client").strip().lower() != "server"
        return {
            "interfaceVersion": 1,
            "ok": model in SUPPORTED_MODELS and client_managed,
            "model": model,
            "handoff": "client" if client_managed else "server",
            "active": bool(self.active_thread),
        }

    def dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "plugin/status":
            return self.status()
        if method == "plugin/realtime/start":
            status = self.status()
            if not status["ok"]:
                raise ProtocolError("Headless sessions require a supported Live model and delegation=client")
            if self.active_thread:
                raise ProtocolError("A voice session is already active in this worker")
            if params.get("model") != status["model"]:
                raise ProtocolError("Requested model must match talk-desktop settings; no downgrade")
            offer = params.get("offer")
            if not isinstance(offer, str) or not (0 < len(offer) <= MAX_REQUEST_LENGTH):
                raise ProtocolError("A valid SDP offer is required")
            profile = params.get("profile")
            if profile is not None:
                if not isinstance(profile, str) or not self.backend._profile_home(profile):
                    raise ProtocolError("Unknown or invalid profile")
            voice = params.get("voice", "cove")
            language = params.get("language", "en")
            if not isinstance(voice, str) or not (1 <= len(voice) <= 40):
                raise ProtocolError("Invalid voice")
            if language not in ("en", "es", "de"):
                raise ProtocolError("Unsupported language")
            result = self.backend._codexlive_start(profile, voice, offer, language)
            if not isinstance(result, dict):
                raise RuntimeError("Codex returned an invalid session")
            tid = result.get("threadId")
            if not isinstance(tid, str) or not tid or not result.get("answer"):
                raise RuntimeError("Codex returned an incomplete session")
            if result.get("handoff") != "client" or result.get("version") != "v3":
                self.backend._codexlive_stop(tid)
                raise RuntimeError("Client-managed V3 handoff was not negotiated")
            self.active_thread = tid
            return result
        if method == "plugin/realtime/interrupt":
            tid = params.get("threadId")
            turn = params.get("turnId")
            if not self.active_thread or tid != self.active_thread:
                raise ProtocolError("No matching active voice session")
            if not isinstance(turn, str) or not (1 <= len(turn) <= 256):
                raise ProtocolError("Missing/invalid turn ID")
            self.backend._cl_request("turn/interrupt", {"threadId": tid, "turnId": turn}, timeout=8)
            return {"ok": True}
        if method == "plugin/realtime/stop":
            tid = params.get("threadId")
            if self.active_thread:
                if tid is not None and tid != self.active_thread:
                    raise ProtocolError("No matching active voice session")
                self.backend._codexlive_stop(self.active_thread)
                self.active_thread = None
            return {"ok": True}
        if method == "plugin/shutdown":
            return self.dispatch("plugin/realtime/stop", {})
        raise ProtocolError("Unsupported headless method")

    def close(self) -> None:
        try:
            self.dispatch("plugin/shutdown", {})
        except Exception:
            pass
        # Only terminate this worker's subprocess. Never modify shared Hermes
        # auth or another running dashboard's Codex process.
        proc = getattr(self.backend, "_CL", {}).get("proc")
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()
                proc.wait(timeout=3)


def serve(backend: Any, input_stream: Any, output_stream: Any) -> None:
    bridge = HeadlessSessions(backend)
    try:
        for line in input_stream:
            request_id = None
            method = None
            try:
                if len(line) > MAX_REQUEST_LENGTH:
                    raise ProtocolError("Request is too large")
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ProtocolError("Expected a JSON object")
                request_id = request.get("id")
                method = request.get("method")
                params = request.get("params")
                if not isinstance(method, str) or not isinstance(params, dict):
                    raise ProtocolError("Expected method and object params")
                result = bridge.dispatch(method, params)
                response = {"jsonrpc": "2.0", "id": request_id, "result": result}
            except Exception as exc:
                # Never emit backend exceptions/credentials over the RPC stream.
                message = str(exc)[:200] if isinstance(exc, ProtocolError) else (
                    f"Backend session error ({type(exc).__name__})"
                )
                response = {"jsonrpc": "2.0", "id": request_id, "error": {"message": message}}
            output_stream.write(json.dumps(response, separators=(",", ":")) + "\n")
            output_stream.flush()
            if method == "plugin/shutdown" and "error" not in response:
                break
    finally:
        bridge.close()


def main() -> None:
    serve(load_backend(), sys.stdin, sys.stdout)


if __name__ == "__main__":
    main()
