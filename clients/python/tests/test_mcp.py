"""The MCP server is the same nine tools as the Node twin, over the same transport."""

from __future__ import annotations

import io
import json
import unittest
from typing import Any

from aether_browser import AgentBrowserError
from aether_browser.mcp import TOOLS, Server


class _FakeSession:
    def __init__(self) -> None:
        self.id = "s-1"
        self.view_url = "http://127.0.0.1:6080/vnc.html"
        self.created_at = "now"
        self.expires_at = "later"
        self.max_vision_steps = 25
        self.ended = False
        self.navigate_error: Exception | None = None

    def navigate(self, url: str, **_: Any) -> dict[str, Any]:
        if self.navigate_error is not None:
            raise self.navigate_error
        return {"final_url": url, "title": "T", "readable_text": "hello"}

    def snapshot(self, **_: Any) -> dict[str, Any]:
        return {
            "url": "http://example.test/",
            "title": "T",
            "vision_steps_remaining": 24,
            "readable_text": "hello",
            "screenshot_base64": "AAAA",
            "accessibility": {"nodes": [{"role": "button", "name": "Verify"}], "truncated": False},
        }

    def click(self, **_: Any) -> dict[str, Any]:
        return {"status": "interacted"}

    def type(self, _text: str, **_kw: Any) -> dict[str, Any]:
        return {"status": "interacted"}

    def end(self, **_: Any) -> dict[str, Any]:
        self.ended = True
        return {"status": "ended"}


class _FakeBrowser:
    base_url = "http://127.0.0.1:8092"

    def __init__(self, session: _FakeSession | None = None) -> None:
        self.session = session or _FakeSession()
        self.created = 0

    def create_session(self, **_: Any) -> _FakeSession:
        self.created += 1
        return self.session

    def health(self, **_: Any) -> dict[str, Any]:
        return {
            "status": "ok",
            "version": "0.2.1",
            "browser_ready": True,
            "session_active": False,
            "slots_available": 1,
        }


def _harness(browser: _FakeBrowser) -> tuple[Server, io.StringIO]:
    out = io.StringIO()
    return Server(browser, out=out, log=io.StringIO()), out


def _messages(out: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]


def _text(message: dict[str, Any]) -> str:
    content = message.get("result", {}).get("content", [])
    return "\n".join(part["text"] for part in content if part["type"] == "text")


class ToolSchemas(unittest.TestCase):
    def test_the_toolset_is_nine_closed_schemas(self) -> None:
        self.assertEqual(len(TOOLS), 9)
        for tool in TOOLS:
            with self.subTest(tool=tool["name"]):
                self.assertTrue(tool["name"].startswith("browser_"))
                self.assertGreater(len(tool["description"]), 20)
                self.assertEqual(tool["inputSchema"]["type"], "object")
                self.assertFalse(tool["inputSchema"]["additionalProperties"])

    def test_tool_names_are_unique(self) -> None:
        names = [tool["name"] for tool in TOOLS]
        self.assertEqual(len(set(names)), len(names))


class Protocol(unittest.TestCase):
    def test_initialize_echoes_a_supported_protocol(self) -> None:
        server, out = _harness(_FakeBrowser())
        server.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            }
        )
        result = _messages(out)[0]["result"]
        self.assertEqual(result["protocolVersion"], "2024-11-05")
        self.assertEqual(result["serverInfo"]["name"], "agent-browser")
        self.assertIn("take over", result["instructions"])

    def test_the_advertised_version_tracks_the_package(self) -> None:
        """A release bump must not be able to leave the server advertising a stale version."""
        import aether_browser

        server, out = _harness(_FakeBrowser())
        server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        served = _messages(out)[0]["result"]["serverInfo"]["version"]
        self.assertEqual(served, aether_browser.__version__)

    def test_initialize_falls_back_for_an_unknown_protocol(self) -> None:
        server, out = _harness(_FakeBrowser())
        server.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "1999-01-01"},
            }
        )
        self.assertEqual(_messages(out)[0]["result"]["protocolVersion"], "2025-06-18")

    def test_a_notification_is_not_answered(self) -> None:
        server, out = _harness(_FakeBrowser())
        server.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(_messages(out), [])

    def test_an_unknown_method_is_a_jsonrpc_error(self) -> None:
        server, out = _harness(_FakeBrowser())
        server.dispatch({"jsonrpc": "2.0", "id": 7, "method": "nope/nope"})
        self.assertEqual(_messages(out)[0]["error"]["code"], -32601)

    def test_a_line_that_is_not_json_does_not_kill_the_transport(self) -> None:
        server, out = _harness(_FakeBrowser())
        server.serve(io.StringIO('not json\n{"jsonrpc":"2.0","id":1,"method":"ping"}\n'))
        self.assertEqual(_messages(out)[0]["result"], {})


class Tools(unittest.TestCase):
    def _call(
        self,
        server: Server,
        name: str,
        arguments: dict[str, Any] | None = None,
        request_id: int = 1,
    ) -> None:
        server.dispatch(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }
        )

    def test_open_reuses_the_session_and_always_shows_the_view_url(self) -> None:
        browser = _FakeBrowser()
        server, out = _harness(browser)
        self._call(server, "browser_open", request_id=1)
        self._call(server, "browser_open", request_id=2)
        self.assertEqual(browser.created, 1)
        messages = _messages(out)
        self.assertIn("Session open", _text(messages[0]))
        self.assertIn("Reusing the open session", _text(messages[1]))
        for message in messages:
            self.assertIn("6080/vnc.html", _text(message))

    def test_acting_without_a_session_points_at_browser_open(self) -> None:
        server, out = _harness(_FakeBrowser())
        self._call(server, "browser_click", {"selector": "#go"})
        message = _messages(out)[0]
        self.assertTrue(message["result"]["isError"])
        self.assertIn("browser_open", _text(message))

    def test_a_blocked_destination_reads_as_policy_not_a_bug(self) -> None:
        session = _FakeSession()
        session.navigate_error = AgentBrowserError("blocked", code="DESTINATION_BLOCKED")
        server, out = _harness(_FakeBrowser(session))
        self._call(server, "browser_navigate", {"url": "http://10.0.0.1/"})
        message = _messages(out)[0]
        self.assertTrue(message["result"]["isError"])
        self.assertIn("by design", _text(message))

    def test_read_returns_structure_and_only_attaches_the_image_on_request(self) -> None:
        server, out = _harness(_FakeBrowser())
        self._call(server, "browser_open", request_id=1)
        self._call(server, "browser_read", {}, request_id=2)
        self._call(server, "browser_read", {"include_screenshot": True}, request_id=3)
        plain, with_image = _messages(out)[1], _messages(out)[2]
        self.assertFalse(any(part["type"] == "image" for part in plain["result"]["content"]))
        self.assertIn('button "Verify"', _text(plain))
        image = next(part for part in with_image["result"]["content"] if part["type"] == "image")
        self.assertEqual(image["mimeType"], "image/png")

    def test_close_is_idempotent(self) -> None:
        server, out = _harness(_FakeBrowser())
        self._call(server, "browser_open", request_id=1)
        self._call(server, "browser_close", request_id=2)
        self._call(server, "browser_close", request_id=3)
        self.assertIn("slot is free", _text(_messages(out)[1]))
        self.assertIn("No session was open", _text(_messages(out)[2]))


if __name__ == "__main__":
    unittest.main()
