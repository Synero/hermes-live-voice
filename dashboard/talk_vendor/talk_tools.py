"""Fallback voice tools (self-contained install).

Native voice tools (the ones upstream runs inside the realtime session) are
not bundled. Tasks still work through "work in the chat" delegation, which
uses the user's own Hermes agent, tools and models.

Part of the bundled fallback derived from TheSmokeDev/hermes-talk (MIT).
"""


# Optional capability: the full hermes-talk adapter may still take only two arguments.
SUPPORTS_LANGUAGE = True


class TalkToolError(Exception):
    pass


def default_talk_tools():
    return []


def execute_talk_tool(name, arguments, language="en"):
    value = str(language or "").strip().lower().replace("_", "-")
    if value.startswith("de"):
        raise TalkToolError(
            f"Das Sprach-Tool '{name}' ist in dieser Installation nicht verfügbar; "
            "verwende 'Im Chat arbeiten' für echte Aufgaben"
        )
    if not value.startswith("es"):
        raise TalkToolError(
            f"the voice tool '{name}' is not available in this installation; "
            "use 'Work in the chat' for real tasks"
        )
    raise TalkToolError(
        f"la tool de voz '{name}' no está disponible en esta instalación; "
        "usa 'Trabajar en el chat' para tareas reales"
    )
