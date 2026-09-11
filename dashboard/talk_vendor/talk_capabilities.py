"""Fallback capabilities block (self-contained install).

Upstream builds a live capability catalog for the session prompt. The bundled
fallback contributes no claims (``None``), which upstream treats as "fail open
to the plain preamble".

Part of the bundled fallback derived from TheSmokeDev/hermes-talk (MIT).
"""


def instruction_section(snapshot=None):
    return None
