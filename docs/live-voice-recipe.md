# Live Voice — technical recipe (ChatGPT/Codex subscription realtime)

How this plugin runs **full-duplex GPT-Live-1 voice on a ChatGPT/Codex subscription**, with no API
key on the voice lane. Everything below was verified end-to-end against a real offer (SDP), real
audio and real events (September 2026, `codex-cli 0.154`).

## The idea

The open-source [Codex CLI](https://github.com/openai/codex) (≥ 0.154) contains a realtime
conversation mode backed by the user's ChatGPT subscription. A local broker can:

1. spawn `codex app-server` (stdio JSON-RPC),
2. open a **thread** and start a **realtime session** with a WebRTC offer,
3. hand the SDP answer back to the client.

Audio then flows **client ↔ backend directly** (WebRTC); the broker only signals. No credentials
ever leave the host: the app-server uses the local `codex login` (`~/.codex/auth.json`).

> Realtime calls go to `{base}/realtime/calls?intent=quicksilver&architecture=avas`. On boxes whose
> Codex default provider is a custom proxy, force the provider for the app-server process:
> `codex app-server --listen stdio:// --enable realtime_conversation -c model_provider="openai"`.

## Broker flow (server side)

```
initialize {capabilities:{experimentalApi:true}}
thread/start  { cwd, modelProvider: "openai", model? }          # model optional (see gotchas)
thread/realtime/start {
  threadId, transport: { type: "webrtc", sdp: <client offer> },
  outputModality: "audio", version: "v3", voice: "cove",
  prompt: <persona>, realtimeStartInstructions: <agent hints>,
  clientManagedHandoffs: <bool>, delegationAckFiller: true
}
# notifications: thread/realtime/sdp → { sdp: <answer> } ; thread/realtime/started → { realtimeSessionId }
thread/realtime/stop { threadId }                                # hang up
```

The client renderer does: `RTCPeerConnection` + `createDataChannel("oai-events")` + mic track →
`createOffer()` → POST the SDP to the broker → `setRemoteDescription(answer)`.

## Protocol `v3` (FramelessBidi) — essentials

- **Version map**: `v1` → `gpt-realtime-1.5` (header `openai-alpha: quicksilver=v1`); `v3` →
  **`gpt-live-1-codex`** (header `quicksilver=v2`). AVAS endpoints require `quicksilver=v2` → use `v3`.
- **Voices (v1/v3)**: `cove, juniper, maple, spruce, ember, vale, breeze, arbor, sol` (default `cove`).
- **Events (datachannel)**:
  - `session.started` / `session.updated`, `session.usage.updated` → `{usage:{audio_duration_ms}, usage_limit:{status,reset_seconds}}`
  - `input_transcript.added` / `output_transcript.added` — **deltas**; `turn.created` / `turn.delta` /
    `turn.done` — turn bookkeeping. ⚠️ Turns **rotate** and `turn.done` can arrive with **partial
    transcripts** — never shrink or close a bubble from it; a bubble continues while same-role deltas
    keep arriving within ~3.5 s.
  - Audio from the model arrives as a **normal WebRTC media track** (`ontrack`) — do NOT build a
    PCM/`output_audio.delta` playback path.
- **Client → server messages accepted**: `session.update`, `session.context.append`,
  `response.create` *(only with Responses delegation)*, `delegation.context.append`,
  `delegation.function_call_output.create`, `input_audio.pause|resume`,
  `output_audio.playback.play`, `session.feedback`, `session.close`.
  **`conversation.item.create` does NOT exist in v3** (that's the v1 shape).
- **Text-triggered turn** (no mic needed): send
  `{"type":"session.context.append","content":[{"type":"input_text","text":"..."}]}` — the model
  answers with audio.

## Task delegation (tools from voice)

The voice model emits:

```json
{"type":"delegation.created","item":{"id":"item_…","type":"delegation",
 "content":[{"type":"input_text","text":"<the user's request>"}],
 "handoff_id":"handoff_1","target":"client","user_bidi_turn_id":"turn_…"}}
```

The **executor** is chosen when starting the session:

- `clientManagedHandoffs: true` → **the client runs the task** and answers with
  `{"type":"delegation.context.append","delegation_item_id":"item_…","content":[{"type":"input_text","text":"<result>"}]}`
  — the model then speaks/uses it. (This plugin routes the task to the user's focused Hermes chat,
  so it executes with the user's own models/providers.)
- `clientManagedHandoffs: false` → the **core** routes the delegation into the Codex thread agent
  and streams its output back into the session (observable as `delegation.context.appended` events).
- `delegationAckFiller: true` lets the model fill the wait with natural phrases (“let me check…”).

Notes: the `content` text carries the user's request when it came from real audio; with text-only
setups (`session.context.append` instead of speech) it can be empty — fall back to the transcript.
`delegation.function_call_output.create` expects an `item` object; `delegation.context.append` is the
working result channel.

## Gotchas (learned the hard way)

- **`codex` not found under systemd**: services don't include `~/.npm-global/bin` in `PATH`. Resolve
  the binary with candidate paths and spawn the app-server with an augmented `PATH`; keep
  `OPENAI_API_KEY`/`CODEX_API_KEY` **out** of the child env so the subscription auth is used.
- **Delegated turn 400**: if the machine's Codex default model isn't ChatGPT-valid (e.g. a custom
  provider proxy like `nousportal/…`), the thread agent fails with
  `"<model>" is not supported when using Codex with a ChatGPT account`. Force a valid model at
  `thread/start` (`gpt-5.6-sol`, `gpt-5.6-terra`, … — see the Codex model catalog).
- **Thread lifecycle**: on `thread not found` (app-server restarted) → recreate the thread; on
  `already` (stale realtime session on the thread) → `thread/realtime/stop` + retry once.
- **Voice allowance**: desktop/Codex voice runs on a **separate rolling 5-hour allowance** per plan
  (Plus ≈ 15–30 min, Pro 5x ≈ 1–2.5 h, Pro 20x unlimited). The plan-side counter is **not exposed**
  to clients (checked `wham/usage`, `account/rateLimits/read`, session events — `usage_limit` stays
  `null`), so metering is done locally from `audio_duration_ms`.
- **Phantom tool work (plan drain)**: `clientManagedHandoffs: true` only gates whether the thread's
  output streams back — the core **still routes every delegation into the Codex thread**, where the
  agent runs real tool work in the background (file reads, shell, `custom_tool_call`s) on the user's
  plan. Observed: dozens of hidden tool calls per call session and a visible jump in the weekly
  bucket. Fix: in client mode pass `realtimeStartInstructions` telling the thread agent to reply
  `skip` without tools; use real instructions only in server mode (the thread is the executor
  there). Verified: routes still occur, **0 tool calls**.
- **Delegation quality**: with no voice-side policy the model delegates *everything* (fragments,
  fillers, "aló") — each becomes a full agent turn, and users interrupt turns while waiting. Put a
  labelled **delegation policy** in the session instructions (delegate only action / fact requests;
  clarify small ambiguities yourself; one short ack, then wait silently; read results back short)
  and prepend a **per-turn note** when submitting to the chat (speech transcript may contain
  mis-hearings → use the latest intent; reply short and plain for TTS; no markdown/lists).
  Client-side: skip filler delegations and serialize runs (one task at a time).
- **Aux-model trap**: a Hermes profile can bind auxiliary models (compression / approval / title /
  mcp) to `openai-codex` — those quietly consume the same weekly plan bucket on every heavy turn.
  Check before blaming the voice lane.
- **ESM plugin syntax**: validate the desktop `plugin.js` with a module-mode syntax check
  (`node --check file.mjs`) **plus** a stub-import harness — `node --check file.js` can false-OK an
  ES module. See `tools/plugin-load-test.sh`.

## Reference implementation

- `desktop/plugin.js` — renderer half (WebRTC, transcript, delegation, settings UI).
- `dashboard/plugin_api.py` — broker half (app-server lifecycle, sessions, personas, usage).

## License note

This documents interoperating with the open-source Codex CLI and a user's own ChatGPT account.
Not affiliated with or endorsed by OpenAI. Respect the OpenAI terms for your account, never share
accounts, never bypass rate limits.
