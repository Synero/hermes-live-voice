# Model context language

The desktop uses its existing `EN` flag (`navigator.language` starts with `en`;
otherwise Spanish). It sends `language: "en"` or `"es"` on `/session`,
`/codexlive/session`, and `/tool`, including voice previews. The backend selects
text per request. Missing or unrecognized language values use Spanish. Codex
thread reuse includes the language so a different locale starts a new thread.

Localized text covers the chat prefix, spoken acknowledgement instruction,
delegation results and statuses (including errors, timeouts, empty results,
disabled tasks, queued work, and stale work), omitted-code marker, preview
instruction and sample, backend persona and chat instructions, Codex delegation
and skip instructions, language directive, and tool errors/timeouts. Protocol
identifiers, including `skip` and `<realtime_delegation>`, remain unchanged.

`language_directive.txt` ships both defaults. The exact shipped bundle and the
previous single-language defaults select the corresponding built-in alternative.
Missing or blank files also use that alternative. Any other file content is a
custom override and is included verbatim in either mode. Both defaults retain
user-language matching and natural Chilean Spanish.

This change localizes the Spanish model text addressed by PR #4. It does not
translate the bundled generic English preamble, tool schemas, or existing English
voice policy; only that policy's changed spoken example follows the flag.
User requests, dynamic tool output, SOUL/persona source text, and memory are not
translated. Existing voice cleanup and length limits still apply to chat results.
The Luna behavior is unchanged; neither preview sample adds an owner's name.

Tests use production function extraction and Node evaluation with transport and
host stubs. They do not call providers or use a microphone. Existing Codex
capability fallback can omit prompts when an older server rejects those fields;
the existing dropped-field report remains in place.
