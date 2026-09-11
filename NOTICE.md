# Notices

## Third-party code

This plugin bundles a fallback copy of parts of **hermes-talk**
(https://github.com/TheSmokeDev/hermes-talk), by TheSmokeDev and contributors,
under the MIT License. The bundled files live in `dashboard/talk_vendor/` and
keep their upstream copyright headers.

The bundled copy exists so the plugin is **self-contained**: a fresh install
does not need the hermes-talk plugin present. When hermes-talk IS installed,
it is preferred at runtime and the bundled copy stays unused.

Upstream hermes-talk is excellent — if you want its full voice experience
(native voice tools like memory search, background-agent delegation with
approvals, run steering, cascade voice modes), install the real plugin and
this one will use it automatically.
