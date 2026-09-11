"""talk-desktop — Live Voice duplex nativo para Hermes Desktop.

El backend del plugin (routes de mint) vive en ``dashboard/plugin_api.py``;
este paquete no registra tools ni hooks — solo necesita existir pa que el
plugin loader lo cargue y el dashboard monte sus routes.
"""

from __future__ import annotations

import logging

_log = logging.getLogger("hermes.plugins.talk-desktop")


def register(ctx) -> None:
    """No-op registrado: el plugin es una surface de dashboard + desktop."""
    _log.info("talk-desktop: dashboard API + desktop half (sin tools de agente)")
