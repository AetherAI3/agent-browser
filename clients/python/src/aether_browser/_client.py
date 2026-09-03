"""The Agent Browser v1 client.

Zero runtime dependencies by design: the transport is :mod:`urllib.request` from the
standard library, and it is injectable so tests never touch a socket.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any, NamedTuple

API_VERSION = "v1"

DEFAULT_BASE_URL = "http://127.0.0.1:8092"

#: Keys the server accepts for ``press``. Anything else is refused server-side.
ALLOWED_KEYS: tuple[str, ...] = (
    "Enter",
    "Escape",
    "Tab",
    "Backspace",
    "Delete",
    "Space",
    "ArrowUp",
    "ArrowDown",
    "ArrowLeft",
    "ArrowRight",
    "Home",
    "End",
    "PageUp",
    "PageDown",
    "Control+A",
    "Control+Z",
    "Control+Shift+Z",
    "Meta+A",
    "Meta+Z",
    "Meta+Shift+Z",
)

DEFAULT_TIMEOUT = 30.0


class Response(NamedTuple):
    """What a transport returns: the raw status, headers, and body text."""

    status: int
    headers: Mapping[str, str]
    text: str


#: ``(method, url, headers, body, timeout) -> Response``. ``timeout`` is ``None`` when
#: the caller disabled it, and ``body`` is ``None`` for a request without one.
Transport = Callable[[str, str, Mapping[str, str], "bytes | None", "float | None"], Response]


class AgentBrowserError(Exception):
    """An error returned by the Agent Browser API, or a transport failure reaching it.

    ``code`` is the server's stable error code when the response carried the documented
    error envelope, and ``None`` for transport-level failures.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        http_status: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.retry_after_seconds = retry_after_seconds

    @property
    def is_capacity_reached(self) -> bool:
        """True when the server refused because a session is already active."""
        return self.code == "SESSION_CAPACITY_REACHED"

    @property
    def is_destination_blocked(self) -> bool:
        """True when the server refused the destination rather than failing to reach it."""
        return self.code in {"DESTINATION_BLOCKED", "INVALID_URL"}


def _compact(values: Mapping[str, Any]) -> dict[str, Any]:
    """Strip unset values so the closed server models never see unknown or null keys."""
    return {key: value for key, value in values.items() if value is not None}


def _normalize_base_url(value: str) -> str:
    """Trim trailing slashes by scanning.

    A regular expression such as ``/\\/+$/`` backtracks polynomially on a long run of
    slashes. The base URL is normally the caller's own config, but a linear scan costs
    nothing and removes the failure mode outright.
    """
    text = str(value)
    end = len(text)
    while end > 0 and text[end - 1] == "/":
        end -= 1
    return text[:end]


def _urllib_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout: float | None,
) -> Response:
    if not url.startswith(("http://", "https://")):
        raise AgentBrowserError(f"Refusing a non-HTTP(S) Agent Browser URL: {url}")
    # The scheme is checked above, which is what S310 asks for.
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=timeout) as reply:  # noqa: S310
            return Response(reply.status, dict(reply.headers), reply.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        # An HTTP error is a real, documented response: read it rather than raising, so
        # the caller gets the server's error envelope instead of a transport failure.
        return Response(error.code, dict(error.headers or {}), error.read().decode("utf-8"))


class Session:
    """A live session. Obtained from :meth:`AgentBrowser.create_session`.

    Every method is a thin call onto a documented route. The session does not cache page
    state: ``navigate`` and ``snapshot`` each return the server's own bounded view.
    """

    def __init__(self, browser: AgentBrowser, created: Mapping[str, Any]) -> None:
        self.browser = browser
        self.id: str = created["session_id"]
        self.view_url: str | None = created.get("view_url")
        self.created_at: str | None = created.get("created_at")
        self.expires_at: str | None = created.get("expires_at")
        self.max_vision_steps: int | None = created.get("max_vision_steps")
        self.ended = False

    def navigate(self, url: str, *, timeout: float | None = None) -> dict[str, Any]:
        """Navigate to an HTTP(S) URL.

        The server evaluates its egress policy separately from schema validation.
        """
        return self.browser._post(
            "/browser/navigate", {"session_id": self.id, "url": url}, "controller", timeout
        )

    def snapshot(self, *, timeout: float | None = None) -> dict[str, Any]:
        """Capture bounded page state plus a base64 PNG. Consumes exactly one vision step."""
        return self.browser._post("/browser/snapshot", {"session_id": self.id}, "observer", timeout)

    def click(
        self,
        *,
        selector: str | None = None,
        x: int | None = None,
        y: int | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Click a selector or an x/y point. Exactly one of the two is allowed by the server."""
        return self._interact(
            {"action": "click", "target": _compact({"selector": selector, "x": x, "y": y})},
            timeout,
        )

    def type(
        self,
        text: str,
        *,
        selector: str | None = None,
        x: int | None = None,
        y: int | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Type text into a selector or an x/y point.

        Text is preserved byte for byte, including leading and trailing whitespace.
        """
        return self._interact(
            {
                "action": "type",
                "target": _compact({"selector": selector, "x": x, "y": y}),
                "text": text,
            },
            timeout,
        )

    def press(self, key: str, *, timeout: float | None = None) -> dict[str, Any]:
        """Press one of the allowed keys or combinations.

        Clipboard shortcuts are not allowlisted; :data:`ALLOWED_KEYS` is the full set.
        """
        return self._interact({"action": "press", "key": key}, timeout)

    def scroll(
        self,
        *,
        delta_x: int | None = None,
        delta_y: int | None = None,
        selector: str | None = None,
        x: int | None = None,
        y: int | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Scroll by a bounded, nonzero delta."""
        body: dict[str, Any] = _compact(
            {"action": "scroll", "delta_x": delta_x, "delta_y": delta_y}
        )
        target = _compact({"selector": selector, "x": x, "y": y})
        if target:
            body["target"] = target
        return self._interact(body, timeout)

    def end(self, *, timeout: float | None = None) -> dict[str, Any]:
        """End the session.

        Idempotent: a repeated call reports ``already_ended`` rather than resurrecting
        state, so calling this twice is safe.
        """
        result = self.browser._post(
            "/browser/session/end", {"session_id": self.id}, "controller", timeout
        )
        self.ended = True
        return result

    def _interact(self, body: Mapping[str, Any], timeout: float | None) -> dict[str, Any]:
        return self.browser._post(
            "/browser/interact", {"session_id": self.id, **body}, "controller", timeout
        )


class AgentBrowser:
    """A client bound to one Agent Browser server.

    Observer and controller tokens are kept separate so the server's role split is visible
    in your own code: reads may be given only the observer token, while anything that
    creates, navigates, interacts, or ends requires the controller token.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        controller_token: str | None = None,
        observer_token: str | None = None,
        timeout: float | None = DEFAULT_TIMEOUT,
        env: Mapping[str, str] | None = None,
        transport: Transport | None = None,
    ) -> None:
        environment = os.environ if env is None else env
        self.base_url = _normalize_base_url(
            base_url
            if base_url is not None
            else environment.get("AGENT_BROWSER_URL", DEFAULT_BASE_URL)
        )
        self.controller_token = (
            controller_token
            if controller_token is not None
            else environment.get("AGENT_BROWSER_CONTROLLER_TOKEN")
        )
        self.observer_token = (
            observer_token
            if observer_token is not None
            else environment.get("AGENT_BROWSER_OBSERVER_TOKEN")
        )
        self.timeout = timeout
        self._transport: Transport = transport if transport is not None else _urllib_transport

    def health(self, *, timeout: float | None = None) -> dict[str, Any]:
        """Liveness and readiness. Accepts the observer token."""
        return self._request("GET", "/browser/health", None, "observer", timeout)

    def create_session(
        self, *, max_vision_steps: int | None = None, timeout: float | None = None
    ) -> Session:
        """Create the one owned session.

        A second concurrent create is refused with ``SESSION_CAPACITY_REACHED`` rather
        than queued.
        """
        created = self._post(
            "/browser/session/create",
            _compact({"max_vision_steps": max_vision_steps}),
            "controller",
            timeout,
        )
        return Session(self, created)

    def _post(
        self, path: str, body: Mapping[str, Any], role: str, timeout: float | None
    ) -> dict[str, Any]:
        return self._request("POST", path, body, role, timeout)

    def _token_for(self, role: str) -> str | None:
        if role == "observer":
            return self.observer_token or self.controller_token
        return self.controller_token or self.observer_token

    def _request(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None,
        role: str,
        timeout: float | None,
    ) -> dict[str, Any]:
        headers = {"accept": "application/json"}
        token = self._token_for(role)
        if token:
            headers["authorization"] = f"Bearer {token}"

        payload: bytes | None = None
        if body is not None:
            headers["content-type"] = "application/json"
            payload = json.dumps({"api_version": API_VERSION, **body}).encode("utf-8")

        effective_timeout = self.timeout if timeout is None else timeout
        if effective_timeout is not None and effective_timeout <= 0:
            effective_timeout = None

        url = f"{self.base_url}{path}"
        try:
            response = self._transport(method, url, headers, payload, effective_timeout)
        except AgentBrowserError:
            raise
        except Exception as cause:  # every transport failure reads the same to the caller
            raise AgentBrowserError(
                f"Could not reach Agent Browser at {self.base_url}: {cause}"
            ) from cause

        try:
            parsed = json.loads(response.text) if response.text else None
        except ValueError:
            parsed = None

        if response.status >= 400:
            detail = parsed.get("error") if isinstance(parsed, dict) else None
            message = None
            if isinstance(detail, Mapping):
                message = detail.get("message")
            raise AgentBrowserError(
                message or f"Agent Browser returned HTTP {response.status}",
                code=detail.get("code") if isinstance(detail, Mapping) else None,
                http_status=response.status,
                retry_after_seconds=_retry_after(response.headers),
            )

        return parsed if isinstance(parsed, dict) else {}


def _retry_after(headers: Mapping[str, str]) -> float | None:
    raw = None
    for name, value in headers.items():
        if name.lower() == "retry-after":
            raw = value
            break
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


@contextmanager
def session(browser: AgentBrowser, *, max_vision_steps: int | None = None) -> Iterator[Session]:
    """Run a block against a fresh session and always attempt to end it.

    A failure to end never masks the original error, and a session that was never
    created is never ended.

    >>> with session(AgentBrowser()) as live:  # doctest: +SKIP
    ...     live.navigate("https://example.com")
    """
    live = browser.create_session(max_vision_steps=max_vision_steps)
    try:
        yield live
    finally:
        try:
            live.end()
        except Exception:  # noqa: S110 - the caller's outcome must not be replaced
            pass
