"""In-memory browser and clock fakes used by runtime-lane tests."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aether_browser.models import AccessibilityNode, AccessibilitySnapshot, Viewport
from aether_browser.runtime import BrowserPageState, BrowserSnapshot


class FakeClock:
    def __init__(self) -> None:
        self.wall = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
        self.monotonic_value = 1000.0

    def utc_now(self) -> datetime:
        return self.wall

    def monotonic(self) -> float:
        return self.monotonic_value

    def advance(self, seconds: float) -> None:
        self.wall += timedelta(seconds=seconds)
        self.monotonic_value += seconds


@dataclass(slots=True)
class FakeAdapter:
    launch_error: BaseException | None = None
    snapshot_error: BaseException | None = None
    launch_gate: asyncio.Event | None = None
    ready: bool = False
    closed: bool = False
    launched_profile: Path | None = None
    calls: list[tuple[object, ...]] = field(default_factory=list)
    page_state: BrowserPageState = field(
        default_factory=lambda: BrowserPageState(
            url="https://example.com/",
            title="Example",
            readable_text="Readable example page",
            accessibility=AccessibilitySnapshot(
                nodes=[AccessibilityNode(role="heading", name="Example")]
            ),
        )
    )

    @property
    def is_ready(self) -> bool:
        return self.ready and not self.closed

    async def launch(self, profile_directory: Path) -> None:
        self.calls.append(("launch",))
        self.launched_profile = profile_directory
        if self.launch_gate is not None:
            await self.launch_gate.wait()
        if self.launch_error is not None:
            raise self.launch_error
        self.ready = True

    async def navigate(self, url: str) -> BrowserPageState:
        self.calls.append(("navigate", url))
        self.page_state = BrowserPageState(
            url=url,
            title=self.page_state.title,
            readable_text=self.page_state.readable_text,
            accessibility=self.page_state.accessibility,
        )
        return self.page_state

    async def snapshot(self) -> BrowserSnapshot:
        self.calls.append(("snapshot",))
        if self.snapshot_error is not None:
            raise self.snapshot_error
        return BrowserSnapshot(
            page=self.page_state,
            screenshot_base64=base64.b64encode(b"png").decode("ascii"),
            viewport=Viewport(width=1280, height=720),
        )

    async def click(
        self,
        *,
        selector: str | None = None,
        x: int | None = None,
        y: int | None = None,
    ) -> None:
        self.calls.append(("click", selector, x, y))

    async def type_text(
        self,
        text: str,
        *,
        selector: str | None = None,
        x: int | None = None,
        y: int | None = None,
    ) -> None:
        self.calls.append(("type", text, selector, x, y))

    async def scroll(self, delta_x: int, delta_y: int) -> None:
        self.calls.append(("scroll", delta_x, delta_y))

    async def press(self, key: str) -> None:
        self.calls.append(("press", key))

    async def close(self) -> None:
        self.calls.append(("close",))
        self.ready = False
        self.closed = True


@dataclass(slots=True)
class FakeAdapterFactory:
    adapters: list[FakeAdapter] = field(default_factory=list)
    next_launch_error: BaseException | None = None
    next_launch_gate: asyncio.Event | None = None

    def __call__(self, _profile_directory: Path) -> FakeAdapter:
        adapter = FakeAdapter(
            launch_error=self.next_launch_error,
            launch_gate=self.next_launch_gate,
        )
        self.adapters.append(adapter)
        self.next_launch_error = None
        self.next_launch_gate = None
        return adapter
