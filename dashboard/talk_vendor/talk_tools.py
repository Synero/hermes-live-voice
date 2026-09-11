"""Fallback voice tools (self-contained install).

Native voice tools (the ones upstream runs inside the realtime session) are
not bundled. Tasks still work through "work in the chat" delegation, which
uses the user's own Hermes agent, tools and models.

Part of the bundled fallback derived from TheSmokeDev/hermes-talk (MIT).
"""


class TalkToolError(Exception):
    pass


def default_talk_tools():
    return []


def execute_talk_tool(name, arguments):
    raise TalkToolError(
        f"la tool de voz '{name}' no está disponible en esta instalación; "
        "usa 'Trabajar en el chat' para tareas reales"
    )
