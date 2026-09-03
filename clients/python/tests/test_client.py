"""Mirror of clients/node/test/client.test.js.

Both clients speak the same closed contract, so both suites assert the same wire
behaviour. A change that is right in one and missing in the other is a drift bug.
"""

from __future__ import annotations

import json
import time
import unittest
from collections.abc import Mapping
from typing import Any

from aether_browser import (
    ALLOWED_KEYS,
    DEFAULT_BASE_URL,
    AgentBrowser,
    AgentBrowserError,
    Response,
    session,
)

CREATED: dict[str, Any] = {
    "api_version": "v1",
    "status": "created",
    "session_id": "11111111-2222-3333-4444-555555555555",
    "state": "active",
    "max_vision_steps": 25,
    "view_url": "http://127.0.0.1:6080/vnc.html",
    "created_at": "2026-09-02T00:00:00Z",
    "expires_at": "2026-09-02T00:30:00Z",
}

# Not secrets: opaque markers the stub echoes back so the role split stays visible.
OBSERVER = "obs"
CONTROLLER = "ctl"

ENDED: dict[str, Any] = {
    "api_version": "v1",
    "status": "ended",
    "session_id": CREATED["session_id"],
    "ended_at": "2026-09-02T00:05:00Z",
}


def stub(responses: list[dict[str, Any]]) -> tuple[Any, list[dict[str, Any]]]:
    """Record every request and reply with scripted responses."""
    calls: list[dict[str, Any]] = []
    queue = list(responses)

    def transport(
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float | None,
    ) -> Response:
        calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "body": json.loads(body) if body else None,
                "timeout": timeout,
            }
        )
        if not queue:
            raise AssertionError(f"unexpected request to {url}")
        nxt = queue.pop(0)
        if "network_error" in nxt:
            raise OSError(nxt["network_error"])
        return Response(
            nxt.get("status", 200),
            nxt.get("headers", {}),
            json.dumps(nxt.get("body", {})),
        )

    return transport, calls


def client(
    responses: list[dict[str, Any]], **options: Any
) -> tuple[AgentBrowser, list[dict[str, Any]]]:
    transport, calls = stub(responses)
    return AgentBrowser(transport=transport, env={}, **options), calls


class TestRequests(unittest.TestCase):
    def test_defaults_to_loopback_and_sends_no_authorization_without_a_token(self) -> None:
        browser, calls = client([{"body": {"api_version": "v1", "status": "ok"}}])
        self.assertEqual(browser.base_url, DEFAULT_BASE_URL)
        browser.health()
        self.assertEqual(calls[0]["url"], f"{DEFAULT_BASE_URL}/browser/health")
        self.assertEqual(calls[0]["method"], "GET")
        self.assertNotIn("authorization", calls[0]["headers"])

    def test_stamps_api_version_on_every_request_body(self) -> None:
        browser, calls = client([{"body": CREATED}])
        browser.create_session()
        self.assertEqual(calls[0]["body"]["api_version"], "v1")

    def test_omits_max_vision_steps_when_unset(self) -> None:
        browser, calls = client([{"body": CREATED}])
        browser.create_session()
        self.assertEqual(list(calls[0]["body"]), ["api_version"])

    def test_sends_max_vision_steps_when_provided(self) -> None:
        browser, calls = client([{"body": CREATED}])
        browser.create_session(max_vision_steps=5)
        self.assertEqual(calls[0]["body"]["max_vision_steps"], 5)

    def test_routes_reads_to_observer_and_writes_to_controller(self) -> None:
        browser, calls = client(
            [{"body": {"status": "ok"}}, {"body": CREATED}],
            observer_token=OBSERVER,
            controller_token=CONTROLLER,
        )
        browser.health()
        browser.create_session()
        self.assertEqual(calls[0]["headers"]["authorization"], "Bearer obs")
        self.assertEqual(calls[1]["headers"]["authorization"], "Bearer ctl")

    def test_snapshot_is_a_read(self) -> None:
        browser, calls = client(
            [{"body": CREATED}, {"body": {"status": "snapshot"}}],
            observer_token=OBSERVER,
            controller_token=CONTROLLER,
        )
        browser.create_session().snapshot()
        self.assertTrue(calls[1]["url"].endswith("/browser/snapshot"))
        self.assertEqual(calls[1]["headers"]["authorization"], "Bearer obs")

    def test_falls_back_to_the_only_token_supplied(self) -> None:
        browser, calls = client([{"body": {"status": "ok"}}], controller_token=CONTROLLER)
        browser.health()
        self.assertEqual(calls[0]["headers"]["authorization"], "Bearer ctl")

    def test_reads_connection_settings_from_the_environment(self) -> None:
        browser = AgentBrowser(
            transport=lambda *args: Response(200, {}, "{}"),
            env={
                "AGENT_BROWSER_URL": "http://127.0.0.1:9000/",
                "AGENT_BROWSER_CONTROLLER_TOKEN": "from-env",
            },
        )
        self.assertEqual(browser.base_url, "http://127.0.0.1:9000")
        self.assertEqual(browser.controller_token, "from-env")


class TestErrors(unittest.TestCase):
    def test_maps_the_documented_error_envelope_onto_a_typed_error(self) -> None:
        browser, _ = client(
            [
                {
                    "status": 503,
                    "headers": {"retry-after": "7"},
                    "body": {
                        "api_version": "v1",
                        "status": "error",
                        "error": {
                            "code": "SESSION_CAPACITY_REACHED",
                            "message": "A session is already active.",
                        },
                    },
                }
            ]
        )
        with self.assertRaises(AgentBrowserError) as caught:
            browser.create_session()
        error = caught.exception
        self.assertEqual(error.code, "SESSION_CAPACITY_REACHED")
        self.assertEqual(error.http_status, 503)
        self.assertEqual(error.retry_after_seconds, 7)
        self.assertTrue(error.is_capacity_reached)
        self.assertEqual(str(error), "A session is already active.")

    def test_surfaces_a_transport_failure_without_inventing_an_error_code(self) -> None:
        browser, _ = client([{"network_error": "ECONNREFUSED"}])
        with self.assertRaises(AgentBrowserError) as caught:
            browser.health()
        self.assertIsNone(caught.exception.code)
        self.assertIn("Could not reach Agent Browser", str(caught.exception))

    def test_a_blocked_destination_is_distinguishable_from_an_unreachable_one(self) -> None:
        browser, _ = client(
            [
                {"body": CREATED},
                {
                    "status": 403,
                    "body": {"error": {"code": "DESTINATION_BLOCKED", "message": "no"}},
                },
            ]
        )
        live = browser.create_session()
        with self.assertRaises(AgentBrowserError) as caught:
            live.navigate("https://blocked.example")
        self.assertTrue(caught.exception.is_destination_blocked)
        self.assertFalse(caught.exception.is_capacity_reached)


class TestInteractions(unittest.TestCase):
    def _live(self, extra: list[dict[str, Any]]) -> tuple[Any, list[dict[str, Any]]]:
        browser, calls = client([{"body": CREATED}, *extra])
        return browser.create_session(), calls

    def test_click_sends_only_a_target(self) -> None:
        live, calls = self._live([{"body": {"status": "interacted"}}])
        live.click(selector="#go")
        self.assertTrue(calls[1]["url"].endswith("/browser/interact"))
        self.assertEqual(
            calls[1]["body"],
            {
                "api_version": "v1",
                "session_id": CREATED["session_id"],
                "action": "click",
                "target": {"selector": "#go"},
            },
        )

    def test_type_splits_text_out_of_the_target(self) -> None:
        live, calls = self._live([{"body": {"status": "interacted"}}])
        live.type("  spaced  ", selector="#name")
        self.assertEqual(calls[1]["body"]["target"], {"selector": "#name"})
        self.assertEqual(calls[1]["body"]["text"], "  spaced  ", "text must survive byte for byte")

    def test_scroll_uses_wire_field_names_and_omits_an_empty_target(self) -> None:
        live, calls = self._live([{"body": {"status": "interacted"}}])
        live.scroll(delta_y=-240)
        self.assertEqual(
            calls[1]["body"],
            {
                "api_version": "v1",
                "session_id": CREATED["session_id"],
                "action": "scroll",
                "delta_y": -240,
            },
        )

    def test_press_sends_the_key_alone(self) -> None:
        live, calls = self._live([{"body": {"status": "interacted"}}])
        live.press("Enter")
        self.assertEqual(
            calls[1]["body"],
            {
                "api_version": "v1",
                "session_id": CREATED["session_id"],
                "action": "press",
                "key": "Enter",
            },
        )

    def test_the_allowed_key_list_matches_the_server_enum_exactly(self) -> None:
        self.assertEqual(len(ALLOWED_KEYS), 20)
        for key in ("Enter", "Control+Shift+Z", "Meta+Shift+Z", "PageDown"):
            self.assertIn(key, ALLOWED_KEYS)
        for key in ("Control+C", "Control+V", "F5"):
            self.assertNotIn(key, ALLOWED_KEYS, "clipboard is not allowlisted")

    def test_end_is_reflected_on_the_session_object(self) -> None:
        browser, _ = client([{"body": CREATED}, {"body": ENDED}])
        live = browser.create_session()
        self.assertFalse(live.ended)
        live.end()
        self.assertTrue(live.ended)

    def test_exposes_the_loopback_view_url_the_server_reported(self) -> None:
        browser, _ = client([{"body": CREATED}])
        live = browser.create_session()
        self.assertEqual(live.view_url, CREATED["view_url"])
        self.assertEqual(live.id, CREATED["session_id"])


class TestSessionContext(unittest.TestCase):
    def test_ends_the_session_on_success(self) -> None:
        browser, calls = client(
            [{"body": CREATED}, {"body": {"status": "navigated"}}, {"body": ENDED}]
        )
        with session(browser) as live:
            live.navigate("https://example.com")
        self.assertTrue(calls[-1]["url"].endswith("/browser/session/end"))

    def test_ends_the_session_when_the_body_raises_and_preserves_the_error(self) -> None:
        browser, calls = client([{"body": CREATED}, {"body": ENDED}])
        with self.assertRaises(RuntimeError) as caught, session(browser):
            raise RuntimeError("boom")
        self.assertEqual(str(caught.exception), "boom")
        self.assertTrue(calls[-1]["url"].endswith("/browser/session/end"))

    def test_a_cleanup_failure_never_masks_the_caller_error(self) -> None:
        browser, _ = client([{"body": CREATED}, {"network_error": "ECONNRESET"}])
        with self.assertRaises(RuntimeError) as caught, session(browser):
            raise RuntimeError("original")
        self.assertEqual(str(caught.exception), "original")

    def test_a_session_that_was_never_created_is_never_ended(self) -> None:
        browser, calls = client(
            [
                {
                    "status": 503,
                    "body": {"error": {"code": "SESSION_CAPACITY_REACHED", "message": "busy"}},
                }
            ]
        )
        with self.assertRaises(AgentBrowserError), session(browser):
            raise AssertionError("unreachable")
        self.assertEqual(len(calls), 1, "only the create attempt should have been sent")


class TestConnectionSettings(unittest.TestCase):
    def test_an_explicit_timeout_of_zero_disables_the_timer(self) -> None:
        browser, calls = client([{"body": {"status": "ok"}}], timeout=0)
        browser.health()
        self.assertIsNone(calls[0]["timeout"])

    def test_trims_trailing_slashes_without_regex_backtracking(self) -> None:
        def base(url: str) -> str:
            return AgentBrowser(
                transport=lambda *args: Response(200, {}, "{}"), env={}, base_url=url
            ).base_url

        self.assertEqual(base("http://127.0.0.1:8092"), "http://127.0.0.1:8092")
        self.assertEqual(base("http://127.0.0.1:8092/"), "http://127.0.0.1:8092")
        self.assertEqual(base("http://127.0.0.1:8092///"), "http://127.0.0.1:8092")
        self.assertEqual(base("http://127.0.0.1:8092/base/"), "http://127.0.0.1:8092/base")
        self.assertEqual(base("///"), "")

        started = time.perf_counter()
        self.assertEqual(base("http://x" + "/" * 60_000), "http://x")
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.assertLess(elapsed_ms, 250, f"normalisation should stay linear, took {elapsed_ms}ms")

    def test_refuses_a_non_http_url_on_the_real_transport(self) -> None:
        browser = AgentBrowser(env={}, base_url="file:///etc")
        with self.assertRaises(AgentBrowserError) as caught:
            browser.health()
        self.assertIn("non-HTTP(S)", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
