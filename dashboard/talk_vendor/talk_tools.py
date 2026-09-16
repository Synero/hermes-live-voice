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
        f"the voice tool '{name}' is not available in this installation; "
        "use 'Work in the chat' for real tasks"
    )
