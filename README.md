# Live Voice for Hermes Desktop 🎙️

Native **full-duplex voice** for [Hermes](https://github.com/NousResearch/hermes-agent) — talk to your
bots from Hermes Desktop with real-time audio, live transcript, voice tool calls and task delegation.

**Engine:** [GPT-Live-1](https://openai.com/index/introducing-gpt-live-1-in-the-api/) (`gpt-live-1-codex`)
through the local **Codex app-server**, running on a **ChatGPT/Codex subscription** — no API key needed
for the voice lane.

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

### Same-machine setup

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

### Configuration — `settings.json`

Stored next to the installed backend plugin (`$HERMES_HOME/plugins/talk-desktop/settings.json`).
See `settings.example.json`:

| key | values | default | meaning |
|---|---|---|---|
| `codexAgentModel` | a ChatGPT-valid Codex model slug (e.g. `gpt-5.6-sol`) | *(unset)* | Model forced for the delegated **server agent** thread. Needed only when the machine's default Codex model is not ChatGPT-valid (e.g. a custom provider proxy). |
| `delegation` | `client` · `server` | `client` | Who runs voice-delegated tasks. **`client`** = the tasks run in your focused Hermes chat (your config/providers — recommended, nothing is sent to the ChatGPT lane). **`server`** = tasks run in a Codex agent thread on the backend (advanced/opt-in). |

## How it works

The desktop renderer negotiates a WebRTC session against the ChatGPT backend; a local broker spawns
`codex app-server` and drives the realtime conversation (protocol `v3`, model `gpt-live-1-codex`).
Voice-delegated tasks surface as `delegation` items; the client (by default) routes them to your
focused Hermes chat and feeds the result back to the voice model to speak.

Full engineering recipe, protocol tables and gotchas:
**[`docs/live-voice-recipe.md`](docs/live-voice-recipe.md)**.

## Status & roadmap

⚠️ **Preview (0.2.x).** The backend currently imports helper modules from the authors' `hermes-talk`
package (an in-house Hermes plugin, being prepared for open source). The complete Live Voice
implementation and the protocol documentation are included here for reference and integration work.
A fully self-contained backend build is planned before a plugin-catalog submission.

## Changelog

- **0.2.0** — call controls (mute button, hover states) + delegation hardening: voice-side delegation policy, TTS-friendly reply note, filler filter, task queue, and neutralized background thread turns (the Codex plan no longer pays for invisible tool work).
- **0.1.0** — initial public preview.

## License

MIT — see [LICENSE](LICENSE). Codex and GPT are OpenAI products; this project is **not affiliated
with or endorsed by OpenAI**. The Codex CLI it drives is open source and installed by the user.
