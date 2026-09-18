# Live Voice for Hermes Desktop 🎙️

Native **full-duplex voice** for [Hermes](https://github.com/NousResearch/hermes-agent) — talk to your
bots from Hermes Desktop with real-time audio, live transcript, voice tool calls and task delegation.

**Engine:** [GPT-Live-1](https://openai.com/index/introducing-gpt-live-1-in-the-api/) (`gpt-live-1-codex`)
through the local **Codex app-server**, running on a **ChatGPT/Codex subscription** — no API key needed
for the voice lane.

[![CI](https://github.com/Synero/hermes-live-voice/actions/workflows/ci.yml/badge.svg)](https://github.com/Synero/hermes-live-voice/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.2.2-informational)](plugin.yaml)
[![Status: preview](https://img.shields.io/badge/status-preview-orange)](#status--roadmap)

---

## Contents

- [Features](#features)
- [Requirements](#requirements)
- [Install](#install)
- [First run](#first-run)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)
- [How it works](#how-it-works)
- [Status & roadmap](#status--roadmap)
- [Changelog](#changelog)
- [Tests](#tests)
- [Credits](#credits)
- [License](#license)

---

## Features

- 🎧 **Full-duplex, interruptible voice** over WebRTC — audio goes device ↔ model directly (the server only brokers the session).
- 🤖 **Persona-aware** — the voice session adopts the active bot's `SOUL.md` identity and language.
- 📝 **Live transcript** — iMessage-style bubbles (you / bot), streaming deltas, tool activity chips.
- 🛠️ **Tools from voice** — the model can run tasks: by default they are delegated into your **focused Hermes chat**, i.e. your own agent with **your** models and providers. A voice-side delegation policy keeps this clean (only real task requests are delegated, fillers stay conversational) and an internal queue prevents overlapping runs. An opt-in server-agent mode also exists.
- 🎚️ **Audio & call controls** — device pickers, built-in mic test, one-tap **mute** and hang-up right in the composer.
- 📊 **Usage panels** — measured voice minutes (rolling windows) and the Codex plan bucket.

## Requirements

- **Hermes Agent** (backend) and **Hermes Desktop** (app).
- **`codex` CLI ≥ 0.154**, logged in with a ChatGPT plan (`codex login` — device auth).
- Python 3.11+ for the backend half.

## Install

The plugin ships as a **unified package**: a backend half (`dashboard/plugin_api.py`, mounted by the
Hermes dashboard under `/api/plugins/talk-desktop/`) and a desktop half (`desktop/plugin.js`, loaded by
the Hermes Desktop app).

### From the Hermes plugin catalog

```bash
hermes plugins install hermes-live-voice   # curated catalog · tier: community
hermes plugins enable talk-desktop
```

The catalog entry pins a released revision of this repo (updates: `hermes plugins update
hermes-live-voice`). It lands in `$HERMES_HOME/plugins/talk-desktop` — the folder name the desktop
app expects — so the desktop half is picked up on the next app start.

### Same-machine setup (manual)

```bash
# 1) Drop the repo into your Hermes plugins directory:
cp -r hermes-live-voice "$HERMES_HOME/plugins/talk-desktop"     # default: ~/.hermes/plugins/talk-desktop

# 2) Enable the backend plugin (config.yaml → plugins.enabled) and restart the dashboard.
```

The desktop app discovers the desktop half from the plugin folder
(`$HERMES_HOME/plugins/talk-desktop/desktop/plugin.js`) automatically.

### Remote desktop (app on another machine, backend on a server)

1. Install the backend half on the server as above.
2. Copy **only** `desktop/plugin.js` to the desktop machine:
   `$HERMES_HOME/desktop-plugins/talk-desktop/plugin.js` — the folder name must equal the plugin id
   (`talk-desktop`), and `plugin.js` must sit at the folder root.
3. In the app: `Ctrl/Cmd+K` → **“Reload desktop plugins”**.

## First run

1. The mic button appears in the composer (next to the `+` row). Click to start a call.
2. Open the gear ⚙ for settings: **Bot → Voice engine → Voice → Audio**.
3. If this is a fresh machine: sign in to Codex first (`codex login`) or use the in-panel session flow.

## Configuration

`settings.json` is stored next to the installed backend plugin
(`$HERMES_HOME/plugins/talk-desktop/settings.json`). See `settings.example.json`:

| key | values | default | meaning |
|---|---|---|---|
| `codexAgentModel` | a ChatGPT-valid Codex model slug (e.g. `gpt-5.6-sol`) | *(unset)* | Model forced for the delegated **server agent** thread. Needed only when the machine's default Codex model is not ChatGPT-valid (e.g. a custom provider proxy). |
| `delegation` | `client` · `server` | `client` | Who runs voice-delegated tasks. **`client`** = the tasks run in your focused Hermes chat (your config/providers — recommended, nothing is sent to the ChatGPT lane). **`server`** = tasks run in a Codex agent thread on the backend (advanced/opt-in). |
| `jevGate` | `true` · `false` | *(auto)* | Master switch for the **Jev decision gate** (see below). Unset means "on whenever the backend has a key". |

### Decision gate (Jev)

Every delegated utterance is classified before it runs — real work or small talk, your chat or the
graphical desktop, and whether the action deserves a confirmation — by the
[TypeSafe System One](https://typesafe.ai) endpoint instead of by keyword matching.

- **Turn it on:** set `TYPESAFE_API_KEY` in the backend service environment (optional:
  `TYPESAFE_BASE_URL`, `TYPESAFE_MODEL`, `TYPESAFE_TIMEOUT`, in seconds). No key → the gate is off.
  `jevGate: false` in `settings.json` forces it off even with a key present.
- **Fail-open:** it gets a hard per-call budget (default 600 ms) and *any* timeout, error or
  unusable answer means the desktop keeps its local heuristic. A broken gate behaves exactly like
  no gate; it can never block or delay a task beyond that budget.
- **Closed vocabulary:** the gate picks from four named routes (`chat_task`, `computer_use`,
  `answer_self`, `clarify`). It never writes instructions, tool calls or code, and the key stays in
  the backend — the desktop half never sees a credential.

## Troubleshooting

First-run gotchas:

- **No sign-in prompt?** The voice session needs a ChatGPT-plan Codex login. Open the mic
  config (gear while the plugin is visible) → the auth card shows your sign-in state and a
  **Sign in** button: it opens `auth.openai.com/codex/device` with a code to type.
  Nothing to copy by hand from a terminal.
- **`codex` not found in the dashboard logs?** The backend resolves `codex` from
  `~/.local/bin`, `$PATH` and `/usr/local/bin`. If your service runs with a minimal
  `PATH`, add the codex bin directory to the service environment.
- **Persona too generic?** The voice reads the focused bot's `SOUL.md`. No bot focused →
  it uses your Hermes default identity.

## How it works

The desktop renderer negotiates a WebRTC session against the ChatGPT backend; a local broker spawns
`codex app-server` and drives the realtime conversation (protocol `v3`, model `gpt-live-1-codex`).
Voice-delegated tasks surface as `delegation` items; the client (by default) routes them to your
focused Hermes chat and feeds the result back to the voice model to speak.

```text
┌─ Hermes Desktop ────────────────────────────────────────────────────────────┐
│  mic button in the composer  ·  Live Voice panel: transcript, controls, usage  │
└────────────────────────────────────────────────────────────────────────────┘
                │  WebRTC audio — the device talks to the model directly;
                │  the broker only sets the session up.
                v
┌─ ChatGPT realtime backend ──────────────────────────────────────────────────┐
│  GPT-Live-1 (gpt-live-1-codex)  ·  realtime protocol v3                      │
└────────────────────────────────────────────────────────────────────────────┘
                │  spawn + drive over stdio JSON-RPC
                v
┌─ dashboard half — /api/plugins/talk-desktop/ ───────────────────────────────┐
│  broker: codex app-server on your ChatGPT/Codex subscription                 │
└────────────────────────────────────────────────────────────────────────────┘
                │  delegation item (client mode, default)
                v
┌─ your focused Hermes chat ──────────────────────────────────────────────────┐
│  runs the task with YOUR models and providers, then the result               │
│  is fed back and spoken by the voice model                                   │
└────────────────────────────────────────────────────────────────────────────┘
```

Full engineering recipe, protocol tables and gotchas:
**[`docs/live-voice-recipe.md`](docs/live-voice-recipe.md)**.

## Status & roadmap

⚠️ **Preview (0.2.x).** The backend resolves its voice plumbing at import time in this order: the
full [`hermes-talk`](https://github.com/TheSmokeDev/hermes-talk) plugin when it is installed (richer
— native voice tools, run steering, cascade modes), otherwise the MIT-licensed fallback bundled in
`dashboard/talk_vendor/`, so a fresh install with no other plugin mounts correctly (covered by a
regression test that runs with an empty `HOME` and no `hermes-talk` present). Runtime dependencies
are the Hermes dashboard's own (`fastapi`, `httpx`); the desktop half has no build step. The complete
Live Voice implementation and the protocol documentation are included here for reference and
integration work.

## Changelog

- **0.2.2** — identity and language polish, on top of community fixes by [@tillstriegel](https://github.com/tillstriegel) ([#4](https://github.com/Synero/hermes-live-voice/pull/4)): the voice identity prompt no longer hardcodes the owner's name, the model-facing voice context follows the session language (es/en), and — fixed here — the delegated-thread recovery path now keeps that language instead of silently recreating the thread in the fallback while the previous run was in the other language. Also: the transcript's user label is localized ("Tú"/"You") rather than a hardcoded name, and the identity docstring no longer lists private bot names. **Why it matters:** the plugin runs on other people's machines, so no installer should ever see someone else's name in the prompt or in the UI.
- **0.2.1** — audit fixes: the backend now imports its **bundled** `talk_vendor` fallback on fresh installs (previously it needed `hermes-talk` installed); a concurrent `/session` pair no longer deadlocks the event loop; app-server RPC correlation survives reader-buffer pruning; `/codexlive/interrupt` runs off the event loop; `CODEX_HOME` is honored consistently across login/backup/logout; when an older app-server drops `clientManagedHandoffs` the response now carries `droppedFields`/`handoffDegraded`/`warning` and the desktop shows a notice instead of failing silently; the desktop interrupt call sends a plain object body (no double-encoding) and a muted mic can no longer be un-muted by barge-in.
- **0.2.0** — call controls (mute button, hover states) + delegation hardening: voice-side delegation policy, TTS-friendly reply note, filler filter, task queue, and neutralized background thread turns (the Codex plan no longer pays for invisible tool work).
- **0.1.0** — initial public preview.

## Tests

```bash
python -m pytest -q
```

Regression tests for the audit findings in issue #1 (no network, no `codex` binary, no OpenAI
calls — stdlib + pytest only). CI additionally verifies the bundled fallback imports on a
clean environment (empty `HOME`, no site-packages), compiles the backend, syntax-checks the
desktop half and runs the ESM load harness.

## Credits

Voice auth, session minting and identity plumbing are derived from
[TheSmokeDev/hermes-talk](https://github.com/TheSmokeDev/hermes-talk) (MIT) —
bundled in `dashboard/talk_vendor/` with attribution in `NOTICE.md`. The
plugin prefers the full hermes-talk when it's installed (native voice tools,
run steering, cascade modes); the bundled fallback keeps fresh installs
self-contained.

Thanks to **[@whyyagswhy](https://github.com/whyyagswhy)** for the external audit
and the reproduction script behind the 0.2.1 fixes.

## License

MIT — see [LICENSE](LICENSE). Codex and GPT are OpenAI products; this project is **not affiliated
with or endorsed by OpenAI**. The Codex CLI it drives is open source and installed by the user.
