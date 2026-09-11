"""Fallback host adapter (self-contained install).

Reads the operator identity files directly from the Hermes home. This mirrors
upstream's file-backed diagnostic path; the full hermes-talk host adds live
memory providers, vault pointers and the threat-scanning sanitizer.

Part of the bundled fallback derived from TheSmokeDev/hermes-talk (MIT).
"""

from __future__ import annotations

import re
from pathlib import Path

import talk_config

_IDENTITY_FILES = (
    ("PERSONA", "SOUL.md", None),
    ("USER", "memories/USER.md", "user_char_limit"),
    ("MEMORY", "memories/MEMORY.md", "memory_char_limit"),
    ("WORKING", "memories/WORKING.md", "working_char_limit"),
)

_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _sanitize_identity_entries(body: str, filename: str) -> str:
    """Light sanitization for direct file reads.

    Upstream hermes-talk fails closed and drops the section when its full
    threat scanner cannot run. This fallback applies a conservative cleanup
    (control characters removed, hard length cap) instead — good enough for a
    voice persona read from the operator's own files; install hermes-talk for
    the full memory threat scan.
    """

    return _CTRL_RE.sub("", body)[:20000]


def get_ctx():
    return None


class _HostAdapter:
    """File-backed identity sections for the bundled fallback."""

    def identity_sections(self) -> dict:
        sections: dict = {}
        home = talk_config.get_hermes_home()
        for name, relative, limit_key in _IDENTITY_FILES:
            try:
                body = _sanitize_identity_entries(
                    (home / relative).read_text(encoding="utf-8", errors="replace").strip(),
                    relative.split("/")[-1],
                )
            except OSError:
                continue
            if not body:
                continue
            limit = talk_config.identity_char_limit(limit_key) if limit_key else 0
            sections[name] = body[:limit] if limit else body

        include = talk_config.identity_include()
        if include is None:
            return sections
        return {n: b for n, b in sections.items() if n in include}


_HOST = _HostAdapter()


def host():
    return _HOST
