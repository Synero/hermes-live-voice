# Local headless Codex Live sessions (interface v1)

Hermes Desktop remains the default UI. External audio clients can optionally
reuse this plugin's **own** Codex app-server login and Live session negotiation,
without loading its private Python helpers or creating another Codex auth stack.
This interface owns **session control only**: it does not transport microphone
PCM, execute Hermes tools, grant approvals, or relay chat messages.

## Requirements

- Install this plugin in the regular Hermes plugins directory and authenticate
  its `openai-codex` provider through Hermes. Never send access tokens to this
  interface. The plugin uses the existing Hermes-signed Codex request flow.
- Configure `settings.json` with `"delegation": "client"` and optionally
  `"liveModel": "gpt-live-1.1"`. Default remains `gpt-live-1-codex`.
- Codex CLI/app-server must support the chosen model and the V3
  `clientManagedHandoffs` field. The plugin **fails closed**, without silently
  reverting to an older model or server-managed tool execution.
- The caller manages its **own WebRTC offer and media tracks**. This is useful
  for desktop, Android, or other native clients, not for browser-published
  unauthenticated endpoints.

## Launch

Run from an installed plugin environment with its Hermes Python dependencies:

```sh
python -u "$HERMES_HOME/plugins/talk-desktop/dashboard/headless_stdio.py"
```

Set `HERMES_HOME` as appropriate for your installation. Run this as a **local
subprocess**, with a private stdin/stdout pipe. There is no listening port or
new HTTP endpoint. Each worker owns an isolated Codex app-server process and
supports **one active Live session** at a time. Spawn a separate worker for
another concurrent session.

## Wire format

Each request is a single JSON line with `jsonrpc: "2.0"`, a caller-provided
`id`, a `method`, and an object `params`. Responses return the same `id` with
`result` or `error`. The accepted methods are:

| Method | Params | Result |
|---|---|---|
| `plugin/status` | `{}` | `{interfaceVersion: 1, ok, model, handoff, active}` |
| `plugin/realtime/start` | `{model, offer, voice?, language?, profile?}` | `{answer, threadId, realtimeSessionId?, version: "v3", handoff: "client", ...}` |
| `plugin/realtime/interrupt` | `{threadId, turnId}` | `{ok: true}` |
| `plugin/realtime/stop` | `{threadId?}` | `{ok: true}` |
| `plugin/shutdown` | `{}` | `{ok: true}`; process exits after cleanup |

`model` **must exactly match** the configured model returned by `plugin/status`;
`offer` is the SDP from the caller's WebRTC stack. Optional `profile` must
refer to a recognized Hermes profile. The session is exclusively
**client-managed**, not a server-agent lane. Do not assume other internal
`dashboard/plugin_api.py` functions are stable external entrypoints.

Example control handshake (dummy SDP and session IDs only):

```json
{"jsonrpc":"2.0","id":1,"method":"plugin/status","params":{}}
{"jsonrpc":"2.0","id":2,"method":"plugin/realtime/start","params":{"model":"gpt-live-1.1","offer":"v=0...","voice":"sol","language":"en"}}
{"jsonrpc":"2.0","id":3,"method":"plugin/realtime/stop","params":{"threadId":"returned-thread-id"}}
{"jsonrpc":"2.0","id":4,"method":"plugin/shutdown","params":{}}
```

## Security and compatibility

- No credentials, cookies, or other secret authorization headers enter the
  stdio protocol. Internal backend errors are summarized to avoid leaking
  sensitive data. Do not log user audio or raw SDP offers.
- Only the caller holds the pipes. **Do not expose** this process over an
  unauthenticated socket or public gateway.
- The headless worker forces the hidden Codex app-server into a tool-disabled
  mode, starts a read-only ephemeral handoff thread with restrictive
  instructions, and requires client-managed V3 handoff. The external host owns
  real tool execution and approval policy.
- Closing the pipe, sending shutdown, or terminating the worker should stop
  its own Live session and app-server. It does not touch shared Hermes login or
  the independent Desktop plugin session.
- Normal Hermes Desktop voice behaviour, the existing API routes and the
  default GPT-Live-1 model remain unchanged. GPT Live 1.1 support is opt-in.

This contract is covered by isolated tests with no network or live credentials.
A client integration should independently test WebRTC reconnection, interruption,
result speech, and cleanup against its actual installed Codex version.
