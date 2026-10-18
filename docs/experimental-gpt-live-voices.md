# Experimental Codex GPT-Live voices

This branch makes all currently schema-backed new ChatGPT Live voices manually selectable in the Codex Live engine. Each appears under a language category with a consistently capitalized voice name; the per-option “experimental — not verified” warning has been removed from the UI. The language hint describes the voice’s official grouping; it is not a restriction on the language it can speak. Existing stable voices, engine-specific selection, the `cove` fallback, and the configured Codex model remain unchanged.

Live-test status: one user-selected voice has been tested successfully in a real session. This is evidence only for that selected voice and session; the voice identity is intentionally not asserted here. The other schema-backed choices have not all been individually tested end to end. Removing the UI warning does not imply that every choice is live-validated or available on every account/region.

## Included voices and evidence

OpenAI's [ChatGPT Voice help page](https://help.openai.com/en/articles/20001274-chatgpt-voice) lists these additional GPT-Live options:

| Official language grouping | Voice IDs enabled in this branch |
| --- | --- |
| Brazil | Viola (`viola`), Rio (`rio`) |
| French | Aube (`aube`), Rivage (`rivage`) |
| German | Sonna (`sonna`), Miko (`miko`) |
| Hindi | Leher (`leher`), Neer (`neer`) |
| Japanese | Himari (`himari`), Rindō (`rindo`) |
| Korean | Haetsal (`haetsal`), Hanul (`hanul`) |
| Mexican Spanish | Tinta (`tinta`) |
| English | Bubbie (`bubbie`) |

The current [Codex app-server `RealtimeVoice` schema](https://github.com/openai/codex/blob/main/codex-rs/app-server-protocol/schema/typescript/RealtimeVoice.ts) contains all 14 IDs above. The installed Codex CLI 0.163.0 schema generated with `codex app-server generate-ts --experimental` contains the same IDs; `thread/realtime/start` accepts an optional `voice` typed with this enum. The source IDs are passed unchanged; no model slug or realtime protocol setting is altered.

OpenAI lists Arrullo for Mexican Spanish too, but `arrullo` is absent from the inspected Codex `RealtimeVoice` enum. It is therefore intentionally not exposed: there is no verified Codex ID to send, and this branch does not guess a mapping.

Schema acceptance confirms the request IDs, not end-to-end support of each voice by every installed Codex/app-server or GPT-Live rollout. One selected voice was exercised in a real session as noted above, but the remaining choices have not all been individually verified. Availability may also vary by account and region.

## Manual test guidance

1. Use a separate reviewed copy of this branch in the Hermes Desktop environment that runs the Codex app-server; do not replace a stable install just to test it.
2. Open Live Voice settings, keep Codex selected, and choose a voice under its language category.
3. Start a session and compare the sound with the selected voice; speaking the hint language alone does not validate voice identity. Verify the requested voice ID in the realtime request or server logs if identity matters.
4. If Codex rejects an ID or the session fails, record the error and Codex version. The plugin surfaces startup errors rather than retrying with `cove`.
5. Re-select a stable voice to return to the established path. Do not publish or deploy based only on schema acceptance; complete live checks separately first.
