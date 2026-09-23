"""The opt-in Jev layer never widens browser authority or hides a failed decision."""

from __future__ import annotations

import json
import unittest
from collections.abc import Mapping, Sequence
from typing import Any

from aether_browser import AgentBrowserError
from aether_browser.jev import (
    HumanTakeover,
    JevChoice,
    JevDecisionError,
    JevReceipt,
    JevWebAgent,
    NavigationOption,
    OpenRouterJev,
    WebHandoff,
)


def page(
    url: str, *, text: str = "Readable result", nodes: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    return {
        "final_url": url,
        "title": "Example",
        "readable_text": text,
        "accessibility": {"nodes": nodes or []},
        "screenshot_base64": "SHOULD_NOT_BE_SENT",
    }


def receipt() -> JevReceipt:
    return JevReceipt(
        provider_request_id="decision-1",
        model="typesafe/jev-1.13-20260917",
        input_tokens=100,
        output_tokens=8,
        cost_usd=0.0001,
    )


class FakeProvider:
    def __init__(self, actions: Sequence[str | Exception]) -> None:
        self.actions = list(actions)
        self.calls: list[dict[str, Any]] = []

    def choose(self, **kwargs: Any) -> JevChoice:
        self.calls.append(kwargs)
        action = self.actions.pop(0)
        if isinstance(action, Exception):
            raise action
        return JevChoice(action=action, receipt=receipt())


class FakeSession:
    view_url = "http://127.0.0.1:6080/vnc.html"

    def __init__(self, pages: Mapping[str, dict[str, Any]]) -> None:
        self.pages = pages
        self.calls: list[str] = []
        self.ended = False

    def navigate(self, url: str) -> dict[str, Any]:
        self.calls.append(url)
        if url not in self.pages:
            raise AgentBrowserError("destination blocked", code="DESTINATION_BLOCKED")
        return self.pages[url]


class JevTransportTests(unittest.TestCase):
    def test_choice_is_bounded_to_caller_urls_and_has_usage_receipt(self) -> None:
        captured: list[dict[str, Any]] = []

        def post(body: bytes, key: str, timeout: float) -> dict[str, Any]:
            self.assertEqual(key, "secret")
            self.assertEqual(timeout, 3.0)
            request = json.loads(body)
            captured.append(request)
            criteria = request["questions"]["next_step"]["criteria"]
            self.assertEqual(set(criteria), {"handoff", "takeover", "navigate_1"})
            return {
                "id": "decision-1",
                "model": "typesafe/jev-1.13-20260917",
                "provider": "TypeSafe",
                "answers": {
                    "next_step": {
                        "type": "choice",
                        "choice": "navigate_1",
                        "probabilities": {"handoff": 0.1, "takeover": 0.0, "navigate_1": 0.9},
                        "confidence": 0.9,
                    },
                },
                "usage": {"input_tokens": 104, "output_tokens": 8, "cost": 0.00001},
            }

        choice = OpenRouterJev("secret", timeout=3.0, post=post).choose(
            goal="Find source",
            page_url="https://example.com",
            title="Example",
            excerpt="Summary selected by the caller",
            options=[NavigationOption("https://example.com/source", "Primary source")],
        )
        self.assertEqual(choice.action, "navigate_1")
        self.assertEqual(choice.receipt.input_tokens, 104)
        self.assertEqual(captured[0]["model"], "typesafe/jev-1.13")
        self.assertNotIn("SHOULD_NOT_BE_SENT", json.dumps(captured[0]))
        self.assertNotIn("secret", json.dumps(captured[0]))

    def test_rejects_nonchoice_model_drift_and_invalid_usage(self) -> None:
        valid = {
            "id": "decision-1",
            "model": "typesafe/jev-1.13-20260917",
            "answers": {
                "next_step": {
                    "type": "choice",
                    "choice": "handoff",
                    "probabilities": {"handoff": 1.0, "takeover": 0.0},
                    "confidence": 1.0,
                }
            },
            "usage": {"input_tokens": 1, "output_tokens": 0, "cost": 0.0},
        }
        for changed in (
            {"model": "typesafe/jev-latest"},
            {"answers": {"next_step": {"type": "noul", "noul": 1.0}}},
            {"usage": {"input_tokens": True, "output_tokens": 0, "cost": 0}},
        ):
            with self.subTest(changed=changed), self.assertRaises(JevDecisionError):
                OpenRouterJev("secret", post=lambda *_: {**valid, **changed}).choose(
                    goal="Find source",
                    page_url="https://example.com",
                    title="Example",
                    excerpt="",
                    options=[],
                )

    def test_rejects_credentials_and_non_http_navigation(self) -> None:
        for url in ("file:///etc/passwd", "https://user:password@example.com", "javascript:x"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                NavigationOption(url, "Bad destination")


class AgentHandoffTests(unittest.TestCase):
    def test_jev_controls_approved_navigation_then_selected_model_receives_evidence(self) -> None:
        provider = FakeProvider(["navigate_1", "handoff"])
        second = "https://example.com/source"
        live = FakeSession({second: page(second, text="Source text")})
        observed: list[WebHandoff] = []
        result = JevWebAgent(
            provider
        ).run(
            live,  # type: ignore[arg-type]
            goal="Find the primary source",
            initial_page=page("https://example.com"),
            options_for=lambda p: (
                [NavigationOption(second, "Primary source")] if p["final_url"] != second else []
            ),
            excerpt_for=lambda p: p["readable_text"][:100],
            selected_model=lambda h: observed.append(h) or "selected model result",
        )
        self.assertEqual(result, "selected model result")
        self.assertEqual(live.calls, [second])
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(observed[0].visited_urls, ("https://example.com", second))
        self.assertEqual(observed[0].readable_text, "Source text")
        self.assertEqual(len(observed[0].jev_receipts), 2)
        self.assertFalse(live.ended)

    def test_sensitive_page_never_goes_to_jev_or_selected_model(self) -> None:
        provider = FakeProvider([])
        live = FakeSession({})
        called: list[WebHandoff] = []
        result = JevWebAgent(provider).run(
            live,  # type: ignore[arg-type]
            goal="Sign in",
            initial_page=page(
                "https://example.com/other",
                nodes=[{"role": "textbox", "name": "One-time code"}],
            ),
            options_for=lambda _: [],
            excerpt_for=lambda _: "private",
            selected_model=lambda h: called.append(h),
        )
        self.assertIsInstance(result, HumanTakeover)
        self.assertEqual(result.view_url, live.view_url)
        self.assertFalse(provider.calls)
        self.assertFalse(called)
        self.assertFalse(live.ended)

    def test_provider_failure_hands_off_without_browser_action(self) -> None:
        provider = FakeProvider([JevDecisionError("unavailable")])
        live = FakeSession({})
        received: list[WebHandoff] = []
        JevWebAgent(provider).run(
            live,  # type: ignore[arg-type]
            goal="Summarize",
            initial_page=page("https://example.com"),
            options_for=lambda _: [NavigationOption("https://example.org", "Alternate")],
            excerpt_for=lambda _: "",
            selected_model=lambda h: received.append(h),
        )
        self.assertEqual(received[0].reason, "jev_unavailable")
        self.assertEqual(received[0].jev_receipts, ())
        self.assertEqual(live.calls, [])

    def test_blocked_navigation_and_loop_hand_off_without_retry(self) -> None:
        for url, reason in (
            ("https://not-allowed.example", "navigation_refused"),
            ("https://example.com", "navigation_loop"),
        ):
            with self.subTest(url=url):
                provider = FakeProvider(["navigate_1"])
                live = FakeSession({})
                received: list[WebHandoff] = []
                JevWebAgent(provider).run(
                    live,  # type: ignore[arg-type]
                    goal="Find something",
                    initial_page=page("https://example.com"),
                    options_for=lambda _: [NavigationOption(url, "Candidate")],
                    excerpt_for=lambda _: "",
                    selected_model=lambda h: received.append(h),
                )
                self.assertEqual(received[0].reason, reason)
                self.assertEqual(len(provider.calls), 1)
                self.assertLessEqual(len(live.calls), 1)

    def test_hop_limit_offers_only_handoff_or_takeover(self) -> None:
        provider = FakeProvider(["navigate_1", "handoff"])
        second = "https://example.com/source"
        live = FakeSession({second: page(second)})
        JevWebAgent(provider, max_navigations=1).run(
            live,  # type: ignore[arg-type]
            goal="Research",
            initial_page=page("https://example.com"),
            options_for=lambda _: [NavigationOption(second, "Source")],
            excerpt_for=lambda _: "",
            selected_model=lambda _: None,
        )
        self.assertEqual(len(provider.calls[1]["options"]), 0)
        self.assertEqual(live.calls, [second])


if __name__ == "__main__":
    unittest.main()
