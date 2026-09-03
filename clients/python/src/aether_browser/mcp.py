"""Model Context Protocol server for Agent Browser.

Exposes one Agent Browser session as MCP tools over stdio, so any MCP client can drive
the same headed Chrome session a human is watching over noVNC.

The transport is newline-delimited JSON-RPC 2.0 on stdin/stdout, implemented here on the
standard library alone: this package declares no runtime dependencies and the MCP server
keeps that property. It is the Python twin of ``clients/node/src/mcp.js`` and exposes the
same nine tools under the same names, so a client can swap one for the other.

The server owns the session id so the model never has to carry it, and every response that
can carry the live view URL does -- the point of this project is that a human can see and
take over what the agent is doing.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import IO, Any

from . import __version__
from ._client import ALLOWED_KEYS, DEFAULT_BASE_URL, AgentBrowser, AgentBrowserError, Session

SERVER_NAME = "agent-browser"
SUPPORTED_PROTOCOLS = frozenset({"2024-11-05", "2025-03-26", "2025-06-18"})
FALLBACK_PROTOCOL = "2025-06-18"

INSTRUCTIONS = (
    "Agent Browser drives one headed Chrome session that a human can watch and take over "
    "over noVNC. Call browser_open first and show the returned live view URL to the user. "
    "When you hit something you should not do on your own -- a login, a payment, a 2FA "
    "prompt, anything ambiguous -- stop and ask the user to take over in that view rather "
    "than guessing. The session stays yours; they hand it straight back."
)


def _obj(
    properties: dict[str, Any] | None = None, required: list[str] | None = None
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties or {},
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def _STR(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


def _INT(description: str) -> dict[str, Any]:
    return {"type": "integer", "description": description}


TOOLS: list[dict[str, Any]] = [
    {
        "name": "browser_open",
        "description": (
            "Start the one headed Chrome session and return the live view URL. Give that URL to "
            "the human: they can watch this exact session, and take over in it, while you drive. "
            "Safe to call twice -- it reuses the session that is already open."
        ),
        "inputSchema": _obj(),
    },
    {
        "name": "browser_navigate",
        "description": (
            "Navigate the session to an http(s) URL and return the page title, final URL and "
            "readable text. The server validates the destination and refuses blocked address "
            "classes."
        ),
        "inputSchema": _obj({"url": _STR("Absolute http(s) URL to open.")}, ["url"]),
    },
    {
        "name": "browser_read",
        "description": (
            "Read the current page: title, URL, readable text and a bounded accessibility tree. "
            "Prefer this over a screenshot -- it is structure, not pixels, and it does not spend a "
            "vision step unless you ask for the image."
        ),
        "inputSchema": _obj(
            {
                "include_screenshot": {
                    "type": "boolean",
                    "description": (
                        "Also return the PNG as base64. Costs one vision step from the session "
                        "budget. Default false."
                    ),
                }
            }
        ),
    },
    {
        "name": "browser_click",
        "description": (
            "Click a CSS selector, or an x/y point in the viewport. Provide one, not both."
        ),
        "inputSchema": _obj(
            {
                "selector": _STR("CSS selector to click."),
                "x": _INT("Viewport x coordinate."),
                "y": _INT("Viewport y coordinate."),
            }
        ),
    },
    {
        "name": "browser_type",
        "description": (
            "Type text into a CSS selector, or at an x/y point. Text is sent byte for byte. Do not "
            "use this for a secret a human should enter -- ask them to take over in the live view "
            "instead."
        ),
        "inputSchema": _obj(
            {
                "text": _STR("Text to type."),
                "selector": _STR("CSS selector to type into."),
                "x": _INT("Viewport x coordinate."),
                "y": _INT("Viewport y coordinate."),
            },
            ["text"],
        ),
    },
    {
        "name": "browser_press",
        "description": "Press one allowlisted key or combination. Allowed: "
        + ", ".join(ALLOWED_KEYS)
        + ".",
        "inputSchema": _obj(
            {"key": {"type": "string", "enum": list(ALLOWED_KEYS), "description": "Key to press."}},
            ["key"],
        ),
    },
    {
        "name": "browser_scroll",
        "description": "Scroll the page by a nonzero pixel delta.",
        "inputSchema": _obj(
            {
                "delta_y": _INT("Vertical pixels. Positive scrolls down."),
                "delta_x": _INT("Horizontal pixels. Positive scrolls right."),
            }
        ),
    },
    {
        "name": "browser_status",
        "description": (
            "Report whether the runtime is up, whether a session is open, how many vision steps "
            "remain, and the live view URL to hand to a human."
        ),
        "inputSchema": _obj(),
    },
    {
        "name": "browser_close",
        "description": (
            "End the session and release the browser. Idempotent. Call this when the task is "
            "finished so the single session slot is free."
        ),
        "inputSchema": _obj(),
    },
]


def _trim(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n... ({len(text) - limit} more characters)"


def _describe_target(arguments: dict[str, Any]) -> str:
    if arguments.get("selector"):
        return f"`{arguments['selector']}`"
    if arguments.get("x") is not None and arguments.get("y") is not None:
        return f"({arguments['x']}, {arguments['y']})"
    return "the focused element"


class Server:
    """Dispatches MCP requests onto one :class:`AgentBrowser`."""

    def __init__(
        self,
        browser: AgentBrowser,
        *,
        out: IO[str] | None = None,
        log: IO[str] | None = None,
    ) -> None:
        self.browser = browser
        self.session: Session | None = None
        self.out = out if out is not None else sys.stdout
        self.log = log if log is not None else sys.stderr

    # -- transport ---------------------------------------------------------------

    def note(self, message: str) -> None:
        """Write a human-readable line. stdout is the protocol channel, so this is stderr."""
        self.log.write(f"[agent-browser mcp] {message}\n")
        self.log.flush()

    def send(self, message: dict[str, Any]) -> None:
        self.out.write(json.dumps(message) + "\n")
        self.out.flush()

    def reply(self, request_id: Any, result: dict[str, Any]) -> None:
        if request_id is not None:
            self.send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def fail(self, request_id: Any, code: int, message: str) -> None:
        if request_id is not None:
            self.send(
                {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
            )

    # -- helpers -----------------------------------------------------------------

    @staticmethod
    def text(value: str) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": value}]}

    @staticmethod
    def error_text(value: str) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": value}], "isError": True}

    def view(self) -> str | None:
        if self.session is not None and not self.session.ended:
            return self.session.view_url
        return None

    def with_view(self, body: str) -> str:
        url = self.view()
        if not url:
            return body
        return f"{body}\n\nLive view (a human can watch or take over here): {url}"

    def ensure_session(self) -> Session:
        if self.session is not None and not self.session.ended:
            return self.session
        self.session = self.browser.create_session()
        return self.session

    def require_session(self) -> Session:
        if self.session is None or self.session.ended:
            raise AgentBrowserError(
                "No session is open. Call browser_open first.", code="SESSION_NOT_FOUND"
            )
        return self.session

    # -- tools -------------------------------------------------------------------

    def handle_tool(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        a = arguments or {}
        if name == "browser_open":
            reused = self.session is not None and not self.session.ended
            s = self.ensure_session()
            return self.text(
                f"{'Reusing the open session' if reused else 'Session open'}: {s.id}\n"
                f"Vision steps available: {s.max_vision_steps}\n"
                f"Expires: {s.expires_at}\n\n"
                f"Live view (a human can watch or take over here): {s.view_url}\n"
                "Anything they do in that window happens in this same session."
            )
        if name == "browser_navigate":
            s = self.ensure_session()
            r = s.navigate(str(a["url"]))
            return self.text(
                self.with_view(
                    f"Navigated to {r.get('final_url')}\nTitle: {r.get('title')}\n\n"
                    f"{_trim(r.get('readable_text'), 4000)}"
                )
            )
        if name == "browser_read":
            s = self.require_session()
            r = s.snapshot()
            nodes = (r.get("accessibility") or {}).get("nodes") or []
            listed = "\n".join(
                f"  {n.get('role')}"
                + (f' "{n["name"]}"' if n.get("name") else "")
                + (" [disabled]" if n.get("disabled") else "")
                for n in nodes[:60]
            )
            body = (
                f"URL: {r.get('url')}\nTitle: {r.get('title')}\n"
                f"Vision steps remaining: {r.get('vision_steps_remaining')}\n\n"
                f"Readable text:\n{_trim(r.get('readable_text'), 6000)}\n\n"
                f"Accessibility (first {min(60, len(nodes))} nodes):\n{listed}"
            )
            content: list[dict[str, Any]] = [{"type": "text", "text": self.with_view(body)}]
            if a.get("include_screenshot") is True and r.get("screenshot_base64"):
                content.append(
                    {"type": "image", "data": r["screenshot_base64"], "mimeType": "image/png"}
                )
            return {"content": content}
        if name == "browser_click":
            s = self.require_session()
            s.click(selector=a.get("selector"), x=a.get("x"), y=a.get("y"))
            return self.text(self.with_view(f"Clicked {_describe_target(a)}."))
        if name == "browser_type":
            s = self.require_session()
            text = str(a["text"])
            s.type(text, selector=a.get("selector"), x=a.get("x"), y=a.get("y"))
            return self.text(
                self.with_view(f"Typed {len(text)} characters into {_describe_target(a)}.")
            )
        if name == "browser_press":
            s = self.require_session()
            s.press(str(a["key"]))
            return self.text(self.with_view(f"Pressed {a['key']}."))
        if name == "browser_scroll":
            s = self.require_session()
            delta_x = a.get("delta_x")
            delta_y = a.get("delta_y")
            if delta_x is None and delta_y is None:
                delta_y = 600
            s.scroll(delta_x=delta_x, delta_y=delta_y)
            return self.text(self.with_view("Scrolled."))
        if name == "browser_status":
            h = self.browser.health()
            url = self.view()
            tail = f"\nLive view: {url}" if url else "\nNo session open. Call browser_open."
            return self.text(
                f"Runtime: {h.get('status')} (v{h.get('version')})\n"
                f"Browser ready: {h.get('browser_ready')}\n"
                f"Session active: {h.get('session_active')}\n"
                f"Free session slots: {h.get('slots_available')}\n{tail}"
            )
        if name == "browser_close":
            if self.session is None or self.session.ended:
                return self.text("No session was open.")
            r = self.session.end()
            self.session = None
            return self.text(f"Session {r.get('status')}. The browser slot is free.")
        return self.error_text(f"Unknown tool: {name}")

    # -- dispatch ----------------------------------------------------------------

    def dispatch(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        method = message.get("method")
        params = message.get("params") or {}

        if method == "initialize":
            asked = params.get("protocolVersion")
            self.reply(
                request_id,
                {
                    "protocolVersion": asked if asked in SUPPORTED_PROTOCOLS else FALLBACK_PROTOCOL,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": __version__},
                    "instructions": INSTRUCTIONS,
                },
            )
            return
        if method in ("notifications/initialized", "notifications/cancelled"):
            return
        if method == "ping":
            self.reply(request_id, {})
            return
        if method == "tools/list":
            self.reply(request_id, {"tools": TOOLS})
            return
        if method == "resources/list":
            self.reply(request_id, {"resources": []})
            return
        if method == "prompts/list":
            self.reply(request_id, {"prompts": []})
            return
        if method == "tools/call":
            try:
                self.reply(
                    request_id, self.handle_tool(params.get("name", ""), params.get("arguments"))
                )
            except AgentBrowserError as error:
                hint = ""
                if error.code == "SESSION_CAPACITY_REACHED":
                    hint = (
                        "\n\nThe runtime holds one browser session and something else already "
                        "owns it -- another client, or an earlier run that did not call "
                        "browser_close. This server cannot adopt a session it did not create. "
                        "Free the slot on the runtime, then call browser_open again."
                    )
                elif error.code == "DESTINATION_BLOCKED":
                    hint = (
                        "\n\nThe navigation policy refused that destination. It is not a bug: "
                        "loopback, private and reserved address ranges are blocked by design."
                    )
                self.reply(request_id, self.error_text(f"{error.code or 'ERROR'}: {error}{hint}"))
            except OSError as error:
                self.reply(
                    request_id,
                    self.error_text(
                        f"{error}\n\nNothing is listening on {self.browser.base_url}. Start the "
                        "runtime with `docker compose up --build`, or set AGENT_BROWSER_URL."
                    ),
                )
            except Exception as error:  # noqa: BLE001 - a tool fault must not kill the server
                self.reply(request_id, self.error_text(str(error)))
            return
        self.fail(request_id, -32601, f"Method not found: {method}")

    def serve(self, stream: IO[str] | None = None) -> None:
        """Read newline-delimited JSON-RPC from ``stream`` until it closes."""
        source = stream if stream is not None else sys.stdin
        for line in source:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                self.note("ignored a line that was not JSON")
                continue
            try:
                self.dispatch(message)
            except Exception as error:  # noqa: BLE001 - keep the transport alive
                self.note(f"dispatch failed: {error}")
                self.fail(message.get("id"), -32603, str(error))


def run(base_url: str | None = None, *, factory: Callable[[], AgentBrowser] | None = None) -> int:
    """Serve MCP over stdio. Returns when stdin closes."""
    browser = (
        factory()
        if factory is not None
        else AgentBrowser(base_url=base_url or DEFAULT_BASE_URL, timeout=120.0)
    )
    server = Server(browser)
    server.note(f"serving MCP over stdio against {browser.base_url}")
    server.serve()
    return 0
