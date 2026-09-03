"""Client for the Agent Browser v1 API.

The server exposes one closed JSON API. Every request and response carries
``api_version: "v1"`` and unknown fields are rejected, so this client sends exactly
the documented fields and omits anything left unset.

This is the Python sibling of the ``aether-browser`` npm package: same name, same
surface, same closed contract.

See https://github.com/AetherAI3/agent-browser/blob/main/docs/API.md
"""

from __future__ import annotations

from ._client import (
    ALLOWED_KEYS,
    API_VERSION,
    DEFAULT_BASE_URL,
    AgentBrowser,
    AgentBrowserError,
    Response,
    Session,
    session,
)

__all__ = [
    "ALLOWED_KEYS",
    "API_VERSION",
    "DEFAULT_BASE_URL",
    "AgentBrowser",
    "AgentBrowserError",
    "Response",
    "Session",
    "__version__",
    "session",
]

__version__ = "0.2.0"
