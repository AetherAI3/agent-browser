"""Async browser boundary for the Agent Browser runtime.

The public runtime deliberately exposes a small protocol instead of Patchright
objects.  That keeps browser internals, profiles, downloads, script execution,
and process controls outside the HTTP API and makes lifecycle behavior
deterministically testable with in-memory adapters.
"""

from __future__ import annotations

import asyncio
import base64
import inspect
import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn, Protocol, cast
from urllib.parse import urlsplit

from agent_browser.egress import PinnedSocks5Proxy
from agent_browser.models import (
    MAX_ACCESSIBILITY_NODES,
    MAX_READABLE_TEXT_CHARS,
    MAX_SCREENSHOT_BASE64_CHARS,
    AccessibilityNode,
    AccessibilitySnapshot,
    ErrorCode,
    Viewport,
)

if TYPE_CHECKING:
    from agent_browser.policy import ConnectionPlan

DEFAULT_VIEWPORT_WIDTH = 1280
DEFAULT_VIEWPORT_HEIGHT = 720
DEFAULT_ACTION_TIMEOUT_SECONDS = 15.0
DEFAULT_NAVIGATION_TIMEOUT_SECONDS = 30.0
FAILED_NAVIGATION_SETTLE_TIMEOUT_SECONDS = 2.0
DEFAULT_LAUNCH_TIMEOUT_SECONDS = 30.0
DEFAULT_CLEANUP_TIMEOUT_SECONDS = 10.0
LOGGER = logging.getLogger(__name__)

NavigationGuard = Callable[[str], Awaitable[object] | object]


def _browser_process_environment(
    profile_directory: Path,
) -> dict[str, str | float | bool]:
    """Build a minimal browser environment whose writable state dies with the profile."""

    profile_directory.chmod(0o700)
    cache_directory = profile_directory / ".cache"
    config_directory = profile_directory / ".config"
    data_parent = profile_directory / ".local"
    data_directory = data_parent / "share"
    runtime_directory = profile_directory / ".runtime"
    temporary_directory = profile_directory / ".tmp"
    for directory in (
        cache_directory,
        config_directory,
        data_parent,
        data_directory,
        runtime_directory,
        temporary_directory,
    ):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)

    locale = os.environ.get("LC_ALL") or os.environ.get("LANG") or "C.UTF-8"
    environment: dict[str, str | float | bool] = {
        "HOME": str(profile_directory),
        "LANG": locale,
        "LC_ALL": locale,
        "PATH": os.environ.get("PATH", os.defpath),
        "TEMP": str(temporary_directory),
        "TMP": str(temporary_directory),
        "TMPDIR": str(temporary_directory),
        "XDG_CACHE_HOME": str(cache_directory),
        "XDG_CONFIG_HOME": str(config_directory),
        "XDG_DATA_HOME": str(data_directory),
        "XDG_RUNTIME_DIR": str(runtime_directory),
    }
    for name in ("DISPLAY", "SYSTEMROOT", "WINDIR"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    if os.name == "nt":
        environment["USERPROFILE"] = str(profile_directory)
    return environment


def _bounded_error_detail(error: BaseException) -> str:
    detail = " ".join(str(error).split())
    return detail[:2_000] or "no detail"


class PinnedNetworkGuard(Protocol):
    """Per-session authority used by both interception and the dial proxy."""

    async def authorize_request(self, url: str) -> object: ...

    async def authorize_websocket(self, url: str) -> object: ...

    async def connection_plan(self, hostname: str, port: int) -> ConnectionPlan: ...


class BrowserRuntimeError(RuntimeError):
    """Base class for bounded failures at the browser boundary."""


class BrowserLaunchError(BrowserRuntimeError):
    """Chrome or Patchright could not be started."""


class BrowserNotReadyError(BrowserRuntimeError):
    """The browser process or primary page is no longer usable."""


class BrowserOperationError(BrowserRuntimeError):
    """A bounded browser operation failed."""


class BrowserDestinationBlockedError(BrowserRuntimeError):
    """The egress proxy refused an endpoint without an authorized pin."""

    code = ErrorCode.DESTINATION_BLOCKED


class InvalidBrowserInteractionError(BrowserRuntimeError):
    """An interaction cannot be applied to the current viewport."""


@dataclass(frozen=True, slots=True)
class BrowserPageState:
    """Bounded, structured state extracted from the primary page."""

    url: str
    title: str
    readable_text: str
    accessibility: AccessibilitySnapshot


@dataclass(frozen=True, slots=True)
class BrowserSnapshot:
    """A structured page state plus one bounded PNG screenshot."""

    page: BrowserPageState
    screenshot_base64: str
    viewport: Viewport


class BrowserAdapter(Protocol):
    """The complete browser capability surface owned by a session."""

    @property
    def is_ready(self) -> bool:
        """Return whether the owned browser and primary page are usable."""

    async def launch(self, profile_directory: Path) -> None:
        """Launch one headed browser using the supplied temporary profile."""

    async def navigate(self, url: str) -> BrowserPageState:
        """Navigate the primary page and return bounded structured state."""

    async def snapshot(self) -> BrowserSnapshot:
        """Capture bounded structured state and a PNG screenshot."""

    async def click(
        self,
        *,
        selector: str | None = None,
        x: int | None = None,
        y: int | None = None,
    ) -> None:
        """Click a selector, or bounded coordinates when no selector exists."""

    async def type_text(
        self,
        text: str,
        *,
        selector: str | None = None,
        x: int | None = None,
        y: int | None = None,
    ) -> None:
        """Type into a selector, or at bounded coordinates as a fallback."""

    async def scroll(self, delta_x: int, delta_y: int) -> None:
        """Scroll the primary page by bounded deltas."""

    async def press(self, key: str) -> None:
        """Press a key already accepted by the closed request model."""

    async def close(self) -> None:
        """Close every browser resource owned by this adapter."""


_ARIA_LINE = re.compile(
    r"^\s*-\s*(?P<role>[A-Za-z][\w-]*)"
    r'(?:\s+"(?P<name>(?:[^"\\]|\\.)*)")?'
    r"(?:\s*:\s*(?P<value>.*))?\s*$"
)


async def _call_guard(guard: NavigationGuard | None, url: str) -> None:
    if guard is None:
        return
    result = guard(url)
    if inspect.isawaitable(result):
        await result


def _pinned_owner(guard: NavigationGuard | None) -> PinnedNetworkGuard | None:
    owner = getattr(guard, "__self__", None)
    if owner is None:
        return None
    required = ("authorize_request", "authorize_websocket", "connection_plan")
    if not all(callable(getattr(owner, name, None)) for name in required):
        return None
    return cast(PinnedNetworkGuard, owner)


async def _drain_owned_task(
    task: asyncio.Future[Any],
    cancellation: asyncio.CancelledError | None = None,
) -> asyncio.CancelledError | None:
    """Wait for an owned child without allowing repeated caller cancellation to cancel it."""

    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            cancellation = cancellation or error
        except Exception:
            # The synchronous result inspection by the owner preserves the
            # child's typed failure after the child has finished.
            break
    return cancellation


def _bounded(value: object, limit: int) -> str:
    return str(value or "")[:limit]


def _parse_aria_snapshot(raw: str) -> AccessibilitySnapshot:
    """Flatten Patchright's bounded ARIA YAML without executing page script."""

    nodes: list[AccessibilityNode] = []
    lines = raw.splitlines()
    for line in lines[: MAX_ACCESSIBILITY_NODES * 4]:
        match = _ARIA_LINE.match(line)
        if match is None:
            continue
        role = _bounded(match.group("role"), 128)
        if not role:
            continue
        name = _bounded(match.group("name"), 1024)
        value = _bounded(match.group("value"), 4096)
        lowered = line.casefold()
        nodes.append(
            AccessibilityNode(
                role=role,
                name=name,
                value=value,
                focused="[focused" in lowered,
                disabled="[disabled" in lowered,
            )
        )
        if len(nodes) == MAX_ACCESSIBILITY_NODES:
            break
    return AccessibilitySnapshot(
        nodes=nodes,
        truncated=len(nodes) == MAX_ACCESSIBILITY_NODES and len(lines) > MAX_ACCESSIBILITY_NODES,
    )


class PatchrightBrowserAdapter:
    """Real headed-Chrome adapter attached to the process's existing display."""

    def __init__(
        self,
        *,
        navigation_guard: NavigationGuard | None = None,
        redirect_guard: NavigationGuard | None = None,
        chrome_channel: str = "chrome",
        viewport_width: int = DEFAULT_VIEWPORT_WIDTH,
        viewport_height: int = DEFAULT_VIEWPORT_HEIGHT,
        action_timeout_seconds: float = DEFAULT_ACTION_TIMEOUT_SECONDS,
        navigation_timeout_seconds: float = DEFAULT_NAVIGATION_TIMEOUT_SECONDS,
        launch_timeout_seconds: float = DEFAULT_LAUNCH_TIMEOUT_SECONDS,
        cleanup_timeout_seconds: float = DEFAULT_CLEANUP_TIMEOUT_SECONDS,
    ) -> None:
        if viewport_width < 1 or viewport_width > 4096:
            raise ValueError("viewport_width is outside the supported range")
        if viewport_height < 1 or viewport_height > 4096:
            raise ValueError("viewport_height is outside the supported range")
        for timeout in (
            action_timeout_seconds,
            navigation_timeout_seconds,
            launch_timeout_seconds,
            cleanup_timeout_seconds,
        ):
            if timeout <= 0:
                raise ValueError("browser timeouts must be positive")

        self._navigation_guard = navigation_guard
        self._redirect_guard = redirect_guard or navigation_guard
        self._network_guard = _pinned_owner(navigation_guard)
        self._chrome_channel = chrome_channel
        self._viewport = Viewport(width=viewport_width, height=viewport_height)
        self._action_timeout = action_timeout_seconds
        self._navigation_timeout = navigation_timeout_seconds
        self._launch_timeout = launch_timeout_seconds
        self._cleanup_timeout = cleanup_timeout_seconds

        self._patchright: Any | None = None
        self._proxy: PinnedSocks5Proxy | None = None
        self._browser: Any | None = None
        self._context: Any | None = None
        self._page: Any | None = None
        self._event_tasks: set[asyncio.Task[None]] = set()
        self._failed_navigation_commit = asyncio.Event()
        self._blocked_navigation_error: BaseException | None = None
        self._ready = False
        self._crashed = False
        self._closing = False
        self._cleanup_failures = 0

    @property
    def is_ready(self) -> bool:
        if not self._ready or self._crashed or self._closing or self._page is None:
            return False
        try:
            return not bool(self._page.is_closed())
        except Exception:
            return False

    async def launch(self, profile_directory: Path) -> None:
        if self._patchright is not None or self._context is not None or self._proxy is not None:
            raise BrowserLaunchError("Browser launch was refused.")
        if os.name != "nt" and not os.environ.get("DISPLAY"):
            raise BrowserLaunchError("A headed browser display is unavailable.")
        guard = self._network_guard
        if guard is None:
            raise BrowserLaunchError("A pinned browser egress boundary is required.")
        browser_environment = _browser_process_environment(profile_directory)

        try:
            from patchright.async_api import async_playwright

            async with asyncio.timeout(self._launch_timeout):
                self._proxy = PinnedSocks5Proxy(guard.connection_plan)
                await self._proxy.start()
                proxy_url = self._proxy.server_url
                proxy_host = self._proxy.host
                self._patchright = await async_playwright().start()
                self._context = await self._patchright.chromium.launch_persistent_context(
                    str(profile_directory),
                    channel=self._chrome_channel,
                    headless=False,
                    accept_downloads=False,
                    viewport={
                        "width": self._viewport.width,
                        "height": self._viewport.height,
                    },
                    device_scale_factor=self._viewport.device_scale_factor,
                    env=browser_environment,
                    service_workers="block",
                    timeout=int(self._launch_timeout * 1000),
                    proxy={"server": proxy_url, "bypass": ""},
                    args=[
                        "--disable-background-networking",
                        "--disable-component-update",
                        "--disable-default-apps",
                        "--disable-features="
                        "AsyncDns,DnsOverHttps,DnsOverHttpsUpgrade,"
                        "DownloadBubble,DownloadBubbleV2,UseDnsHttpsSvcbAlpn",
                        "--disable-http2",
                        "--disable-quic",
                        "--disable-sync",
                        "--dns-prefetch-disable",
                        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                        # Keep destination DNS disabled without blocking Chrome's
                        # connection to its already-bound loopback SOCKS proxy.
                        f"--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE {proxy_host}",
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--proxy-bypass-list=<-loopback>",
                        f"--proxy-server={proxy_url}",
                    ],
                )
                self._context.set_default_timeout(int(self._action_timeout * 1000))
                self._context.set_default_navigation_timeout(int(self._navigation_timeout * 1000))
                await self._context.route("**/*", self._route_request)
                route_web_socket = getattr(self._context, "route_web_socket", None)
                if not callable(route_web_socket):
                    raise BrowserLaunchError("WebSocket routing is unavailable.")
                await route_web_socket("**/*", self._route_web_socket)
                pages = list(self._context.pages)
                self._page = pages[0] if pages else await self._context.new_page()
                self._browser = self._context.browser
                self._install_page_boundaries(self._page)
                self._context.on("page", self._handle_new_page_event)
                if self._browser is not None:
                    self._browser.on("disconnected", self._handle_browser_disconnect)
                self._ready = True
        except BaseException as error:
            if not isinstance(error, asyncio.CancelledError):
                LOGGER.error(
                    "browser launch failed (%s): %s",
                    type(error).__name__,
                    _bounded_error_detail(error),
                )
            await self.close()
            if isinstance(error, asyncio.CancelledError):
                raise
            raise BrowserLaunchError("The browser could not be started.") from None

    async def navigate(self, url: str) -> BrowserPageState:
        page = self._require_page()
        proxy = self._proxy
        planner_refusal_generation = proxy.planner_refusal_generation if proxy is not None else None
        self._failed_navigation_commit.clear()
        self._blocked_navigation_error = None
        try:
            async with asyncio.timeout(self._navigation_timeout):
                await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=int(self._navigation_timeout * 1000),
                )
                return await self._extract_page_state(page)
        except BaseException as error:
            blocked = self._blocked_navigation_error
            self._blocked_navigation_error = None
            if blocked is not None:
                await self._settle_failed_navigation(page)
                raise blocked from None
            if isinstance(error, asyncio.CancelledError):
                raise
            if isinstance(error, BrowserRuntimeError):
                raise
            if not self.is_ready:
                raise BrowserNotReadyError("The browser is not ready.") from None
            if (
                proxy is not None
                and planner_refusal_generation is not None
                and proxy.planner_refusal_generation != planner_refusal_generation
            ):
                await self._settle_failed_navigation(page)
                raise BrowserDestinationBlockedError(
                    "The navigation destination was blocked."
                ) from None
            raise BrowserOperationError("Navigation failed.") from None

    async def snapshot(self) -> BrowserSnapshot:
        page = self._require_page()
        try:
            async with asyncio.timeout(self._action_timeout):
                state = await self._extract_page_state(page)
                screenshot = await page.screenshot(
                    type="png",
                    full_page=False,
                    animations="disabled",
                    caret="hide",
                    timeout=int(self._action_timeout * 1000),
                )
                encoded = base64.b64encode(bytes(screenshot)).decode("ascii")
                if len(encoded) > MAX_SCREENSHOT_BASE64_CHARS:
                    raise BrowserOperationError("The browser snapshot exceeded its size limit.")
                return BrowserSnapshot(
                    page=state,
                    screenshot_base64=encoded,
                    viewport=self._viewport,
                )
        except BaseException as error:
            self._raise_operation_error(error, "Snapshot failed.")

    async def click(
        self,
        *,
        selector: str | None = None,
        x: int | None = None,
        y: int | None = None,
    ) -> None:
        page = self._require_page()
        try:
            async with asyncio.timeout(self._action_timeout):
                if selector is not None:
                    await page.locator(selector).first.click(
                        timeout=int(self._action_timeout * 1000)
                    )
                else:
                    click_x, click_y = self._coordinates(x, y)
                    await page.mouse.click(click_x, click_y)
                # A click can synchronously create a popup or start a download.
                # Do not report the interaction as complete until those denial
                # hooks have settled and the owned page is foreground again.
                await asyncio.sleep(0)
                await self.drain_boundary_events()
                if not self.is_ready:
                    raise BrowserNotReadyError("The browser is not ready.")
                await page.bring_to_front()
        except BaseException as error:
            self._raise_operation_error(error, "Click failed.")

    async def type_text(
        self,
        text: str,
        *,
        selector: str | None = None,
        x: int | None = None,
        y: int | None = None,
    ) -> None:
        page = self._require_page()
        try:
            async with asyncio.timeout(self._action_timeout):
                if selector is not None:
                    await page.locator(selector).first.fill(
                        text,
                        timeout=int(self._action_timeout * 1000),
                    )
                else:
                    click_x, click_y = self._coordinates(x, y)
                    await page.mouse.click(click_x, click_y)
                    await page.keyboard.insert_text(text)
        except BaseException as error:
            self._raise_operation_error(error, "Typing failed.")

    async def scroll(self, delta_x: int, delta_y: int) -> None:
        page = self._require_page()
        try:
            async with asyncio.timeout(self._action_timeout):
                await page.mouse.wheel(delta_x, delta_y)
        except BaseException as error:
            self._raise_operation_error(error, "Scroll failed.")

    async def press(self, key: str) -> None:
        page = self._require_page()
        try:
            async with asyncio.timeout(self._action_timeout):
                await page.keyboard.press(key)
        except BaseException as error:
            self._raise_operation_error(error, "Key press failed.")

    async def close(self) -> None:
        if self._closing:
            raise BrowserOperationError("Browser cleanup is already in progress.")
        self._closing = True
        self._ready = False
        cleanup_task = asyncio.create_task(
            self._close_owned_resources(),
            name="agent-browser-adapter-cleanup",
        )
        try:
            cancellation = await _drain_owned_task(cleanup_task)
            cleanup_error: BrowserOperationError | None = None
            try:
                cleanup_task.result()
            except asyncio.CancelledError as error:
                cancellation = cancellation or error
            except BrowserOperationError as error:
                cleanup_error = error
            except Exception:
                cleanup_error = BrowserOperationError("Browser cleanup did not complete.")

            if cancellation is not None:
                raise cancellation
            if cleanup_error is not None:
                raise cleanup_error
        finally:
            self._closing = False

    async def _close_owned_resources(self) -> None:
        failures = 0
        tasks = tuple(self._event_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._event_tasks.clear()

        if self._page is not None:
            if await self._close_resource(self._page):
                self._page = None
            else:
                failures += 1
        if self._context is not None:
            if await self._close_resource(self._context):
                self._context = None
            else:
                failures += 1
        if self._browser is not None:
            if await self._close_resource(self._browser):
                self._browser = None
            else:
                failures += 1
        if self._patchright is not None:
            try:
                async with asyncio.timeout(self._cleanup_timeout):
                    await self._patchright.stop()
            except asyncio.CancelledError:
                raise
            except Exception:
                failures += 1
            else:
                self._patchright = None
        if self._proxy is not None:
            try:
                async with asyncio.timeout(self._cleanup_timeout):
                    await self._proxy.close()
            except asyncio.CancelledError:
                raise
            except Exception:
                failures += 1
            else:
                self._proxy = None

        self._cleanup_failures = failures
        if failures:
            raise BrowserOperationError("Browser cleanup did not complete.")

    async def _extract_page_state(self, page: Any) -> BrowserPageState:
        title = _bounded(await page.title(), 512)
        readable_text = ""
        aria_text = ""
        body = page.locator("body")
        try:
            readable_text = _bounded(
                await body.inner_text(timeout=int(self._action_timeout * 1000)),
                MAX_READABLE_TEXT_CHARS,
            )
        except Exception:
            readable_text = ""
        try:
            aria_text = _bounded(
                await body.aria_snapshot(timeout=int(self._action_timeout * 1000)),
                MAX_READABLE_TEXT_CHARS,
            )
        except Exception:
            aria_text = ""
        return BrowserPageState(
            url=_bounded(page.url, 2048) or "about:blank",
            title=title,
            readable_text=readable_text,
            accessibility=_parse_aria_snapshot(aria_text),
        )

    async def _settle_failed_navigation(self, page: Any) -> None:
        """Wait for Chrome's internal failure document before another command can race it."""

        timeout_seconds = min(
            self._action_timeout,
            FAILED_NAVIGATION_SETTLE_TIMEOUT_SECONDS,
        )
        try:
            async with asyncio.timeout(timeout_seconds):
                await self._failed_navigation_commit.wait()
                wait_for_load_state = getattr(page, "wait_for_load_state", None)
                if callable(wait_for_load_state):
                    await wait_for_load_state(
                        "domcontentloaded",
                        timeout=int(timeout_seconds * 1000),
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _route_request(self, route: Any, request: Any) -> None:
        is_navigation = False
        is_primary = False
        authorization_complete = False
        try:
            url = str(request.url)
            try:
                scheme = urlsplit(url).scheme.casefold()
            except (TypeError, ValueError):
                scheme = ""
            if scheme not in {"http", "https"}:
                await route.abort("blockedbyclient")
                return
            is_navigation = bool(request.is_navigation_request())
            is_primary = self._page is not None and request.frame == self._page.main_frame
            if is_navigation:
                if not is_primary:
                    await route.abort("blockedbyclient")
                    return
                redirected_from = getattr(request, "redirected_from", None)
                guard = (
                    self._redirect_guard if redirected_from is not None else self._navigation_guard
                )
                await _call_guard(guard, url)
            else:
                network_guard = self._network_guard
                if network_guard is None:
                    await route.abort("blockedbyclient")
                    return
                await network_guard.authorize_request(url)
            authorization_complete = True
            await route.continue_()
        except BaseException as error:
            if isinstance(error, asyncio.CancelledError):
                raise
            if is_navigation and is_primary and not authorization_complete:
                self._blocked_navigation_error = error
            try:
                await route.abort("blockedbyclient")
            except Exception:
                self._crashed = True
                self._ready = False

    async def _route_web_socket(self, web_socket: Any) -> None:
        guard = self._network_guard
        if guard is None:
            return
        try:
            await guard.authorize_websocket(str(web_socket.url))
            connect_to_server = getattr(web_socket, "connect_to_server", None)
            if not callable(connect_to_server):
                return
            connect_to_server()
        except asyncio.CancelledError:
            raise
        except Exception:
            # A routed WebSocket is disconnected unless the handler explicitly
            # connects it, while the SOCKS planner remains authoritative.
            return

    def _install_page_boundaries(self, page: Any) -> None:
        page.on("download", self._handle_download_event)
        page.on("popup", self._handle_new_page_event)
        page.on("framenavigated", self._handle_frame_navigated)
        page.on("crash", self._handle_page_crash)
        page.on("close", self._handle_primary_page_close)

    def _handle_download_event(self, download: Any) -> None:
        self._schedule_event(self._cancel_download(download))

    def _handle_new_page_event(self, page: Any) -> None:
        if page is self._page:
            return
        self._schedule_event(self._close_extra_page(page))

    def _handle_frame_navigated(self, frame: Any) -> None:
        page = self._page
        if page is None or frame != page.main_frame:
            return
        try:
            scheme = urlsplit(str(frame.url)).scheme.casefold()
        except (TypeError, ValueError):
            return
        if scheme == "chrome-error":
            self._failed_navigation_commit.set()

    def _handle_browser_disconnect(self, *_args: object) -> None:
        if not self._closing:
            self._crashed = True
            self._ready = False

    def _handle_page_crash(self, *_args: object) -> None:
        if not self._closing:
            self._crashed = True
            self._ready = False

    def _handle_primary_page_close(self, page: Any | None = None) -> None:
        if not self._closing and (page is None or page is self._page):
            self._crashed = True
            self._ready = False

    async def _cancel_download(self, download: Any) -> None:
        try:
            async with asyncio.timeout(self._action_timeout):
                await download.cancel()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._crashed = True
            self._ready = False

    async def _close_extra_page(self, page: Any) -> None:
        try:
            async with asyncio.timeout(self._cleanup_timeout):
                await page.close()
                primary_page = self._page
                if primary_page is not None and not primary_page.is_closed():
                    await primary_page.bring_to_front()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._cleanup_failures += 1
            self._crashed = True
            self._ready = False

    def _schedule_event(self, operation: Awaitable[None]) -> None:
        async def run_operation() -> None:
            await operation

        task: asyncio.Task[None] = asyncio.create_task(run_operation())
        self._event_tasks.add(task)
        task.add_done_callback(self._event_tasks.discard)

    async def drain_boundary_events(self) -> None:
        """Wait for already-scheduled denial hooks; useful for deterministic tests."""

        cancellation: asyncio.CancelledError | None = None
        while self._event_tasks:
            tasks = asyncio.gather(*tuple(self._event_tasks), return_exceptions=True)
            cancellation = await _drain_owned_task(tasks, cancellation)
            if cancellation is not None:
                # The batch that existed when cancellation arrived is settled.
                # Later hooks remain adapter-owned, but cannot extend caller
                # cancellation indefinitely under an event-flooding page.
                break
        if cancellation is not None:
            raise cancellation

    def _require_page(self) -> Any:
        if not self.is_ready:
            raise BrowserNotReadyError("The browser is not ready.")
        return self._page

    def _coordinates(self, x: int | None, y: int | None) -> tuple[int, int]:
        if x is None or y is None:
            raise InvalidBrowserInteractionError("Complete coordinates are required.")
        if x < 0 or y < 0 or x >= self._viewport.width or y >= self._viewport.height:
            raise InvalidBrowserInteractionError("Coordinates are outside the viewport.")
        return x, y

    def _raise_operation_error(self, error: BaseException, message: str) -> NoReturn:
        if isinstance(error, asyncio.CancelledError):
            raise error
        if isinstance(error, BrowserRuntimeError):
            raise error
        if not self.is_ready:
            raise BrowserNotReadyError("The browser is not ready.") from None
        raise BrowserOperationError(message) from None

    async def _close_resource(self, resource: Any) -> bool:
        try:
            async with asyncio.timeout(self._cleanup_timeout):
                await resource.close()
        except asyncio.CancelledError:
            raise
        except Exception:
            return False
        return True


__all__ = [
    "BrowserAdapter",
    "BrowserDestinationBlockedError",
    "BrowserLaunchError",
    "BrowserNotReadyError",
    "BrowserOperationError",
    "BrowserPageState",
    "BrowserRuntimeError",
    "BrowserSnapshot",
    "InvalidBrowserInteractionError",
    "NavigationGuard",
    "PinnedNetworkGuard",
    "PatchrightBrowserAdapter",
]
