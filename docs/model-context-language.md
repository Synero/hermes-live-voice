# Model context language

The desktop recognizes German (`de`, including regional tags such as `de-DE` and `de-AT`) and Spanish (`es`, including regional tags). Every other or missing locale defaults to English. It sends the normalized `language: "en"`, `"es"`, or `"de"` on `/session`, `/codexlive/session`, and `/tool`, including voice previews. The backend applies the same normalization to prompts, tool responses, and Codex thread reuse; changing languages starts a separate Codex thread.

The desktop German catalog covers visible labels, tooltips, status messages, errors, delegation instructions/results, preview speech and chat fallback speech. Model-facing context is localized separately: persona voice/chat guidance, language directive, Codex delegation and skip instructions, tool errors/timeouts, and spoken acknowledgement. Protocol identifiers such as `skip` and `<realtime_delegation>` remain unchanged.

`language_directive.txt` ships Spanish, English and German defaults. The previous Spanish-only, English-only and two-language shipped bundles are still recognized as shipped defaults and select the matching current built-in alternative. Missing or blank files use the current built-in default. Any other file content is a custom override and is included verbatim in every language mode.

Generic base instructions and tool schemas are unchanged; the voice-delegation policy is fully translated into German and retains its existing English and Spanish behavior. User requests, dynamic tool output, SOUL/persona source text and memory are not translated. Existing voice cleanup and length limits still apply to chat results. The host bot behavior is unchanged, and preview samples use the selected language without adding an owner's name.

Tests evaluate production JavaScript locale/catalog functions and extract Python prompt/tool/thread functions with transport stubs. They do not call providers or use a microphone. Older Codex app-servers may reject optional start prompts; existing capability fallback still handles those dropped fields.
