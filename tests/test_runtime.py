# ruff: noqa: ASYNC109

from __future__ import annotations

import asyncio
import base64
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from aether_browser.models import MAX_ACCESSIBILITY_NODES, MAX_READABLE_TEXT_CHARS
from aether_browser.policy import NavigationPolicy, PolicyError
from aether_browser.runtime import (
    InvalidBrowserInteractionError,
    PatchrightBrowserAdapter,
)


@dataclass(slots=True)
class FakeLocator:
    page: FakePage
    selector: str

    @property
    def first(self) -> FakeLocator:
        return self

    async def click(self, *, timeout: int) -> None:
        self.page.calls.append(("selector-click", self.selector, timeout))

    async def fill(self, text: str, *, timeout: int) -> None:
        self.page.calls.append(("selector-fill", self.selector, text, timeout))

    async def inner_text(self, *, timeout: int) -> str:
        self.page.calls.append(("inner-text", timeout))
        return self.page.readable_text

    async def aria_snapshot(self, *, timeout: int) -> str:
        self.page.calls.append(("aria", timeout))
        return self.page.aria_text


@dataclass(slots=True)
class FakeMouse:
    calls: list[tuple[object, ...]]

    async def click(self, x: int, y: int) -> None:
        self.calls.append(("coordinate-click", x, y))

    async def wheel(self, delta_x: int, delta_y: int) -> None:
        self.calls.append(("wheel", delta_x, delta_y))


@dataclass(slots=True)
class FakeKeyboard:
    calls: list[tuple[object, ...]]

    async def insert_text(self, text: str) -> None:
        self.calls.append(("insert-text", text))

    async def press(self, key: str) -> None:
        self.calls.append(("press", key))


@dataclass(slots=True)
class FakePage:
    url: str = "https://example.com/"
    title_text: str = "Example"
    readable_text: str = "Readable"
    aria_text: str = '- heading "Example"\n- textbox "Search": empty'
    calls: list[tuple[object, ...]] = field(default_factory=list)
    closed: bool = False
    mouse: FakeMouse = field(init=False)
    keyboard: FakeKeyboard = field(init=False)
    main_frame: object = field(init=False)

    def __post_init__(self) -> None:
        self.mouse = FakeMouse(self.calls)
        self.keyboard = FakeKeyboard(self.calls)
        self.main_frame = object()

    def is_closed(self) -> bool:
        return self.closed

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    async def title(self) -> str:
        return self.title_text

    async def screenshot(self, **options: object) -> bytes:
        self.calls.append(("screenshot", options))
        return b"png-bytes"

    async def close(self) -> None:
        self.calls.append(("close",))
        self.closed = True


class FakeDownload:
    def __init__(self) -> None:
        self.cancelled = False

    async def cancel(self) -> None:
        self.cancelled = True


class FakeExtraPage:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakeRoute:
    def __init__(self) -> None:
        self.continued = False
        self.aborted = False

    async def continue_(self) -> None:
        self.continued = True

    async def abort(self, _reason: str) -> None:
        self.aborted = True


class FakeRequest:
    def __init__(
        self,
        url: str,
        frame: object,
        *,
        redirected: bool = False,
        navigation: bool = True,
    ) -> None:
        self.url = url
        self.frame = frame
        self.redirected_from = object() if redirected else None
        self.navigation = navigation

    def is_navigation_request(self) -> bool:
        return self.navigation


class FakeWebSocketRoute:
    def __init__(self, url: str) -> None:
        self.url = url
        self.connected = False

    async def connect(self) -> None:
        self.connected = True


class Closable:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class Stoppable:
    def __init__(self) -> None:
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


def launched_adapter(page: FakePage | None = None) -> tuple[PatchrightBrowserAdapter, FakePage]:
    adapter = PatchrightBrowserAdapter(action_timeout_seconds=1.0)
    active_page = page or FakePage()
    adapter._page = active_page
    adapter._ready = True
    return adapter, active_page


@pytest.mark.asyncio
async def test_selector_first_and_coordinate_fallback_actions() -> None:
    adapter, page = launched_adapter()

    await adapter.click(selector="#submit")
    await adapter.type_text("alpha", selector="#search")
    await adapter.click(x=20, y=30)
    await adapter.type_text("beta", x=40, y=50)
    await adapter.scroll(10, 200)
    await adapter.press("Enter")

    assert page.calls[0][:2] == ("selector-click", "#submit")
    assert page.calls[1][:3] == ("selector-fill", "#search", "alpha")
    assert ("coordinate-click", 20, 30) in page.calls
    assert ("coordinate-click", 40, 50) in page.calls
    assert ("insert-text", "beta") in page.calls
    assert ("wheel", 10, 200) in page.calls
    assert ("press", "Enter") in page.calls


@pytest.mark.asyncio
async def test_coordinate_fallback_is_bounded_to_viewport() -> None:
    adapter, _page = launched_adapter()

    with pytest.raises(InvalidBrowserInteractionError):
        await adapter.click(x=1280, y=20)
    with pytest.raises(InvalidBrowserInteractionError):
        await adapter.type_text("x", x=10, y=720)


@pytest.mark.asyncio
async def test_snapshot_extracts_bounded_state_without_page_script() -> None:
    aria = "\n".join(f'- button "Action {index}"' for index in range(600))
    page = FakePage(
        title_text="t" * 600,
        readable_text="r" * (MAX_READABLE_TEXT_CHARS + 50),
        aria_text=aria,
    )
    adapter, _page = launched_adapter(page)

    snapshot = await adapter.snapshot()

    assert len(snapshot.page.title) == 512
    assert len(snapshot.page.readable_text) == MAX_READABLE_TEXT_CHARS
    assert len(snapshot.page.accessibility.nodes) == MAX_ACCESSIBILITY_NODES
    assert snapshot.page.accessibility.truncated
    assert base64.b64decode(snapshot.screenshot_base64) == b"png-bytes"
    assert snapshot.viewport.width == 1280
    assert not any(call[0] == "evaluate" for call in page.calls)


@pytest.mark.asyncio
async def test_downloads_are_cancelled_and_extra_pages_are_closed() -> None:
    adapter, _page = launched_adapter()
    download = FakeDownload()
    popup = FakeExtraPage()

    adapter._handle_download_event(download)
    adapter._handle_new_page_event(popup)
    await adapter.drain_boundary_events()

    assert download.cancelled
    assert popup.closed


@pytest.mark.asyncio
async def test_top_level_navigation_guard_aborts_blocked_redirect() -> None:
    class Blocked(Exception):
        pass

    async def guard(url: str) -> None:
        if "blocked" in url:
            raise Blocked

    adapter = PatchrightBrowserAdapter(navigation_guard=guard)
    page = FakePage()
    adapter._page = page
    adapter._ready = True
    route = FakeRoute()
    request = FakeRequest("http://blocked.invalid/", page.main_frame)

    await adapter._route_request(route, request)

    assert route.aborted
    assert not route.continued
    assert isinstance(adapter._blocked_navigation_error, Blocked)


@pytest.mark.asyncio
async def test_extra_page_top_level_navigation_is_aborted_before_policy_escape() -> None:
    calls: list[str] = []

    async def guard(url: str) -> None:
        calls.append(url)

    adapter = PatchrightBrowserAdapter(navigation_guard=guard)
    page = FakePage()
    adapter._page = page
    adapter._ready = True
    route = FakeRoute()
    request = FakeRequest("https://example.com/popup", object())

    await adapter._route_request(route, request)

    assert route.aborted
    assert not route.continued
    assert calls == []


@pytest.mark.asyncio
async def test_redirects_use_the_separate_bounded_redirect_guard() -> None:
    navigation_calls: list[str] = []
    redirect_calls: list[str] = []

    async def navigation_guard(url: str) -> None:
        navigation_calls.append(url)

    async def redirect_guard(url: str) -> None:
        redirect_calls.append(url)

    adapter = PatchrightBrowserAdapter(
        navigation_guard=navigation_guard,
        redirect_guard=redirect_guard,
    )
    page = FakePage()
    adapter._page = page
    adapter._ready = True
    first_route = FakeRoute()
    redirect_route = FakeRoute()

    await adapter._route_request(
        first_route,
        FakeRequest("https://example.com/start", page.main_frame),
    )
    await adapter._route_request(
        redirect_route,
        FakeRequest("https://example.com/final", page.main_frame, redirected=True),
    )

    assert first_route.continued and redirect_route.continued
    assert navigation_calls == ["https://example.com/start"]
    assert redirect_calls == ["https://example.com/final"]


@pytest.mark.asyncio
async def test_every_http_subresource_is_authorized_before_continue() -> None:
    calls: list[str] = []

    async def resolver(hostname: str) -> list[str]:
        calls.append(hostname)
        return ["93.184.216.34"]

    guard = NavigationPolicy(resolver).new_guard()
    adapter = PatchrightBrowserAdapter(navigation_guard=guard.validate)
    page = FakePage()
    adapter._page = page
    adapter._ready = True

    allowed = FakeRoute()
    await adapter._route_request(
        allowed,
        FakeRequest(
            "https://cdn.example.org/script.js",
            page.main_frame,
            navigation=False,
        ),
    )

    assert allowed.continued
    assert calls == ["cdn.example.org"]
    plan = await guard.connection_plan("cdn.example.org", 443)
    assert tuple(str(address) for address in plan.addresses) == ("93.184.216.34",)


@pytest.mark.asyncio
async def test_blocked_subresource_does_not_replace_primary_navigation_error() -> None:
    async def resolver(_hostname: str) -> list[str]:
        return ["127.0.0.1"]

    guard = NavigationPolicy(resolver).new_guard()
    adapter = PatchrightBrowserAdapter(navigation_guard=guard.validate)
    page = FakePage()
    adapter._page = page
    adapter._ready = True
    route = FakeRoute()

    await adapter._route_request(
        route,
        FakeRequest(
            "https://blocked.example.org/pixel",
            page.main_frame,
            navigation=False,
        ),
    )

    assert route.aborted
    assert adapter._blocked_navigation_error is None


@pytest.mark.asyncio
async def test_websocket_is_authorized_before_explicit_connection() -> None:
    async def resolver(hostname: str) -> list[str]:
        if hostname == "socket.example.org":
            return ["93.184.216.34"]
        return ["127.0.0.1"]

    guard = NavigationPolicy(resolver).new_guard()
    adapter = PatchrightBrowserAdapter(navigation_guard=guard.validate)
    allowed = FakeWebSocketRoute("wss://socket.example.org/events")
    blocked = FakeWebSocketRoute("ws://blocked.example.org/events")

    await adapter._route_web_socket(allowed)
    await adapter._route_web_socket(blocked)

    assert allowed.connected
    assert not blocked.connected
    assert (await guard.connection_plan("socket.example.org", 443)).port == 443
    with pytest.raises(PolicyError):
        await guard.connection_plan("blocked.example.org", 80)


@pytest.mark.asyncio
async def test_launch_installs_pinned_socks_only_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    operations: list[str] = []
    captured: dict[str, Any] = {}
    monkeypatch.setenv("DISPLAY", ":99")

    class LaunchPage(FakePage):
        def on(self, _name: str, _handler: object) -> None:
            return None

    class LaunchBrowser(Closable):
        def on(self, _name: str, _handler: object) -> None:
            return None

    class LaunchContext(Closable):
        def __init__(self) -> None:
            super().__init__()
            self.pages: list[LaunchPage] = []
            self.browser = LaunchBrowser()

        def set_default_timeout(self, _timeout: int) -> None:
            return None

        def set_default_navigation_timeout(self, _timeout: int) -> None:
            return None

        async def route(self, _pattern: str, _handler: object) -> None:
            operations.append("route")

        async def route_web_socket(self, _pattern: str, _handler: object) -> None:
            operations.append("route_web_socket")

        async def new_page(self) -> LaunchPage:
            operations.append("new_page")
            page = LaunchPage()
            self.pages.append(page)
            return page

        def on(self, _name: str, _handler: object) -> None:
            return None

    context = LaunchContext()

    class Chromium:
        async def launch_persistent_context(
            self,
            _profile: str,
            **kwargs: object,
        ) -> LaunchContext:
            captured.update(kwargs)
            return context

    class Manager:
        chromium = Chromium()

        async def stop(self) -> None:
            return None

    class Starter:
        async def start(self) -> Manager:
            return Manager()

    async_module = types.ModuleType("patchright.async_api")
    async_module.async_playwright = lambda: Starter()  # type: ignore[attr-defined]
    package = types.ModuleType("patchright")
    package.async_api = async_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "patchright", package)
    monkeypatch.setitem(sys.modules, "patchright.async_api", async_module)

    async def resolver(_hostname: str) -> list[str]:
        return ["93.184.216.34"]

    guard = NavigationPolicy(resolver).new_guard()
    adapter = PatchrightBrowserAdapter(navigation_guard=guard.validate)
    await adapter.launch(tmp_path)
    try:
        proxy = captured["proxy"]
        assert isinstance(proxy, dict)
        assert str(proxy["server"]).startswith("socks5://127.0.0.1:")
        assert proxy["bypass"] == ""
        args = set(captured["args"])
        assert "--proxy-bypass-list=<-loopback>" in args
        assert "--host-resolver-rules=MAP * ~NOTFOUND" in args
        assert "--disable-quic" in args
        assert "--disable-http2" in args
        assert any(str(value).startswith("--proxy-server=socks5://") for value in args)
        assert operations.index("route_web_socket") < operations.index("new_page")
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_close_releases_page_context_browser_and_patchright_manager() -> None:
    adapter, page = launched_adapter()
    context = Closable()
    browser = Closable()
    patchright = Stoppable()
    adapter._context = context
    adapter._browser = browser
    adapter._patchright = patchright

    await adapter.close()

    assert page.closed
    assert context.closed
    assert browser.closed
    assert patchright.stopped
    assert not adapter.is_ready


@pytest.mark.asyncio
async def test_close_attempts_every_resource_and_retains_a_failed_handle_for_retry() -> None:
    from aether_browser.runtime import BrowserOperationError

    class FailOnceClosable:
        def __init__(self) -> None:
            self.attempts = 0
            self.closed = False

        async def close(self) -> None:
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("private close detail")
            self.closed = True

    adapter, page = launched_adapter()
    context = FailOnceClosable()
    browser = Closable()
    patchright = Stoppable()
    adapter._context = context
    adapter._browser = browser
    adapter._patchright = patchright

    with pytest.raises(BrowserOperationError, match="Browser cleanup did not complete"):
        await adapter.close()

    assert page.closed
    assert context.attempts == 1
    assert adapter._context is context
    assert browser.closed
    assert patchright.stopped

    await adapter.close()
    assert context.attempts == 2
    assert context.closed
    assert adapter._context is None


@pytest.mark.asyncio
async def test_close_drains_all_resources_before_propagating_repeated_cancellation() -> None:
    class BlockingClosable:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.closed = False

        async def close(self) -> None:
            self.started.set()
            await self.release.wait()
            self.closed = True

    adapter, page = launched_adapter()
    context = BlockingClosable()
    browser = Closable()
    patchright = Stoppable()
    adapter._context = context
    adapter._browser = browser
    adapter._patchright = patchright

    closing = asyncio.create_task(adapter.close())
    await context.started.wait()
    closing.cancel()
    await asyncio.sleep(0)
    closing.cancel()
    await asyncio.sleep(0)

    assert not closing.done()
    assert not context.closed
    context.release.set()

    with pytest.raises(asyncio.CancelledError):
        await closing
    assert page.closed
    assert context.closed
    assert browser.closed
    assert patchright.stopped
    assert adapter._context is None
    assert not adapter.is_ready
