"""Atomic single-session ownership for Agent Browser."""

from __future__ import annotations

import asyncio
import inspect
import math
import shutil
import tempfile
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, TypeVar
from uuid import UUID, uuid4

from agent_browser.models import InteractionAction, InteractRequest, SessionState
from agent_browser.runtime import (
    BrowserAdapter,
    BrowserLaunchError,
    BrowserNotReadyError,
    BrowserOperationError,
    BrowserPageState,
    BrowserSnapshot,
)

DEFAULT_IDLE_TIMEOUT_SECONDS = 300.0
DEFAULT_ABSOLUTE_LIFETIME_SECONDS = 3600.0
DEFAULT_CLEANUP_TIMEOUT_SECONDS = 15.0
DEFAULT_REAPER_RESOLUTION_SECONDS = 1.0
DEFAULT_TOMBSTONE_LIMIT = 256
DEFAULT_VIEW_URL = "http://127.0.0.1:6080/vnc.html"

AdapterFactory = Callable[[Path], BrowserAdapter | Awaitable[BrowserAdapter]]
UtcClock = Callable[[], datetime]
MonotonicClock = Callable[[], float]
_OperationResult = TypeVar("_OperationResult")


async def _drain_owned_task(
    task: asyncio.Future[_OperationResult],
    cancellation: asyncio.CancelledError | None = None,
) -> asyncio.CancelledError | None:
    """Drain an owned child despite repeated cancellation of its parent."""

    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            cancellation = cancellation or error
        except Exception:
            break
    return cancellation


class SessionError(RuntimeError):
    """Base class for typed session failures."""


class SessionCapacityError(SessionError):
    """The single runtime slot is occupied."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("The browser session slot is occupied.")
        self.retry_after_seconds = retry_after_seconds


class SessionNotFoundError(SessionError):
    """The supplied UUID does not identify the current session."""


class SessionExpiredError(SessionError):
    """The supplied UUID belongs to a session that expired."""


class VisionBudgetExhaustedError(SessionError):
    """The session has no remaining snapshot steps."""


@dataclass(frozen=True, slots=True)
class SessionInfo:
    session_id: UUID
    state: SessionState
    max_vision_steps: int
    view_url: str
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class NavigationResult:
    session_id: UUID
    page: BrowserPageState
    navigated_at: datetime


@dataclass(frozen=True, slots=True)
class SnapshotResult:
    session_id: UUID
    snapshot: BrowserSnapshot
    sequence: int
    captured_at: datetime
    vision_steps_used: int
    vision_steps_remaining: int


@dataclass(frozen=True, slots=True)
class InteractionResult:
    session_id: UUID
    action: InteractionAction
    sequence: int
    interacted_at: datetime


@dataclass(frozen=True, slots=True)
class EndResult:
    session_id: UUID
    status: Literal["ended", "already_ended"]
    ended_at: datetime


@dataclass(frozen=True, slots=True)
class ManagerHealth:
    browser_ready: bool
    session_active: bool
    slots_available: int


@dataclass(slots=True)
class _SessionRecord:
    session_id: UUID
    state: SessionState
    profile_directory: Path
    adapter: BrowserAdapter | None
    created_at: datetime
    expires_at: datetime
    created_monotonic: float
    last_activity_monotonic: float
    absolute_deadline_monotonic: float
    max_vision_steps: int
    vision_steps_used: int = 0
    sequence: int = 0
    expiry_task: asyncio.Task[None] | None = None


@dataclass(frozen=True, slots=True)
class _Tombstone:
    state: SessionState
    ended_at: datetime


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SessionManager:
    """Own at most one browser session and every resource underneath it."""

    def __init__(
        self,
        adapter_factory: AdapterFactory,
        *,
        idle_timeout_seconds: float = DEFAULT_IDLE_TIMEOUT_SECONDS,
        absolute_lifetime_seconds: float = DEFAULT_ABSOLUTE_LIFETIME_SECONDS,
        cleanup_timeout_seconds: float = DEFAULT_CLEANUP_TIMEOUT_SECONDS,
        reaper_resolution_seconds: float = DEFAULT_REAPER_RESOLUTION_SECONDS,
        view_url: str = DEFAULT_VIEW_URL,
        profile_root: Path | None = None,
        utc_clock: UtcClock = _utc_now,
        monotonic_clock: MonotonicClock = time.monotonic,
        uuid_factory: Callable[[], UUID] = uuid4,
        tombstone_limit: int = DEFAULT_TOMBSTONE_LIMIT,
    ) -> None:
        for value in (
            idle_timeout_seconds,
            absolute_lifetime_seconds,
            cleanup_timeout_seconds,
            reaper_resolution_seconds,
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("session time bounds must be positive")
        if tombstone_limit < 1:
            raise ValueError("tombstone_limit must be positive")

        self._adapter_factory = adapter_factory
        self._idle_timeout = idle_timeout_seconds
        self._absolute_lifetime = absolute_lifetime_seconds
        self._cleanup_timeout = cleanup_timeout_seconds
        self._reaper_resolution = reaper_resolution_seconds
        self._view_url = view_url
        self._profile_root = profile_root
        self._utc_clock = utc_clock
        self._monotonic_clock = monotonic_clock
        self._uuid_factory = uuid_factory
        self._tombstone_limit = tombstone_limit

        self._lock = asyncio.Lock()
        self._current: _SessionRecord | None = None
        self._tombstones: OrderedDict[UUID, _Tombstone] = OrderedDict()
        self._last_state = SessionState.IDLE
        self._shutdown = False
        self._cleanup_failures = 0
        self._cleanup_compromised = False

    @property
    def current_state(self) -> SessionState:
        current = self._current
        return current.state if current is not None else self._last_state

    @property
    def active_session_id(self) -> UUID | None:
        current = self._current
        if current is not None and current.state is SessionState.ACTIVE:
            return current.session_id
        return None

    async def create(self, max_vision_steps: int) -> SessionInfo:
        if not 1 <= max_vision_steps <= 100:
            raise ValueError("max_vision_steps is outside the supported range")
        async with self._lock:
            if self._shutdown:
                raise BrowserNotReadyError("The browser runtime is shutting down.")
            if self._current is not None:
                await self._expire_if_needed_locked(self._current)
            if self._current is not None:
                adapter = self._current.adapter
                if adapter is None or not adapter.is_ready:
                    await self._fail_record_locked(self._current)
            if self._cleanup_compromised:
                raise BrowserNotReadyError("Browser cleanup did not complete.")
            if self._current is not None:
                raise SessionCapacityError(self._capacity_retry_after(self._current))

            session_id = self._uuid_factory()
            now = self._aware_now()
            monotonic_now = self._monotonic_clock()
            profile_directory = Path(
                tempfile.mkdtemp(prefix="agent-browser-", dir=self._profile_root)
            )
            record = _SessionRecord(
                session_id=session_id,
                state=SessionState.STARTING,
                profile_directory=profile_directory,
                adapter=None,
                created_at=now,
                expires_at=now + timedelta(seconds=self._absolute_lifetime),
                created_monotonic=monotonic_now,
                last_activity_monotonic=monotonic_now,
                absolute_deadline_monotonic=monotonic_now + self._absolute_lifetime,
                max_vision_steps=max_vision_steps,
            )
            self._current = record
            self._last_state = SessionState.STARTING

            try:
                candidate = self._adapter_factory(profile_directory)
                adapter = await candidate if inspect.isawaitable(candidate) else candidate
                record.adapter = adapter
                await adapter.launch(profile_directory)
                if not adapter.is_ready:
                    raise BrowserLaunchError("The browser did not become ready.")
            except BaseException as error:
                record.state = SessionState.FAILED
                self._last_state = SessionState.FAILED
                await self._cleanup_record_locked(record)
                if self._current is record:
                    self._current = None
                if isinstance(error, asyncio.CancelledError):
                    raise
                if isinstance(error, BrowserLaunchError):
                    raise
                raise BrowserLaunchError("The browser could not be started.") from None

            record.state = SessionState.ACTIVE
            self._last_state = SessionState.ACTIVE
            record.expiry_task = asyncio.create_task(
                self._expiry_watch(session_id),
                name="agent-browser-session-expiry",
            )
            return self._session_info(record)

    async def navigate(self, session_id: UUID, url: str) -> NavigationResult:
        async with self._lock:
            record = await self._active_record_locked(session_id)
            adapter = self._adapter(record)
            try:
                page = await self._await_before_absolute_deadline_locked(
                    record,
                    lambda: adapter.navigate(url),
                )
            except BaseException as error:
                if record.state is SessionState.EXPIRED:
                    raise
                await self._handle_adapter_failure_locked(record, error)
                raise
            timestamp = self._aware_now()
            self._touch(record)
            return NavigationResult(
                session_id=session_id,
                page=page,
                navigated_at=timestamp,
            )

    async def snapshot(self, session_id: UUID) -> SnapshotResult:
        async with self._lock:
            record = await self._active_record_locked(session_id)
            if record.vision_steps_used >= record.max_vision_steps:
                raise VisionBudgetExhaustedError("The snapshot budget is exhausted.")
            adapter = self._adapter(record)
            try:
                snapshot = await self._await_before_absolute_deadline_locked(
                    record,
                    adapter.snapshot,
                )
            except BaseException as error:
                if record.state is SessionState.EXPIRED:
                    raise
                await self._handle_adapter_failure_locked(record, error)
                raise

            record.vision_steps_used += 1
            record.sequence += 1
            captured_at = self._aware_now()
            self._touch(record)
            return SnapshotResult(
                session_id=session_id,
                snapshot=snapshot,
                sequence=record.sequence,
                captured_at=captured_at,
                vision_steps_used=record.vision_steps_used,
                vision_steps_remaining=record.max_vision_steps - record.vision_steps_used,
            )

    async def interact(self, request: InteractRequest) -> InteractionResult:
        async with self._lock:
            record = await self._active_record_locked(request.session_id)
            adapter = self._adapter(record)
            target = request.target
            selector = target.selector if target is not None else None
            x = target.x if target is not None else None
            y = target.y if target is not None else None

            async def apply_interaction() -> None:
                if request.action is InteractionAction.CLICK:
                    await adapter.click(selector=selector, x=x, y=y)
                elif request.action is InteractionAction.TYPE:
                    await adapter.type_text(request.text or "", selector=selector, x=x, y=y)
                elif request.action is InteractionAction.SCROLL:
                    await adapter.scroll(request.delta_x or 0, request.delta_y or 0)
                elif request.action is InteractionAction.PRESS:
                    if request.key is None:
                        raise AssertionError("validated press request is missing a key")
                    await adapter.press(request.key.value)
                else:
                    raise AssertionError("closed interaction model produced an unknown action")

            try:
                await self._await_before_absolute_deadline_locked(record, apply_interaction)
            except BaseException as error:
                if record.state is SessionState.EXPIRED:
                    raise
                await self._handle_adapter_failure_locked(record, error)
                raise

            record.sequence += 1
            interacted_at = self._aware_now()
            self._touch(record)
            return InteractionResult(
                session_id=request.session_id,
                action=request.action,
                sequence=record.sequence,
                interacted_at=interacted_at,
            )

    async def end(self, session_id: UUID) -> EndResult:
        async with self._lock:
            tombstone = self._tombstones.get(session_id)
            if tombstone is not None:
                return EndResult(
                    session_id=session_id,
                    status="already_ended",
                    ended_at=tombstone.ended_at,
                )

            record = self._current
            if record is None or record.session_id != session_id:
                raise SessionNotFoundError("Session was not found.")
            if await self._expire_if_needed_locked(record):
                tombstone = self._tombstones[session_id]
                return EndResult(
                    session_id=session_id,
                    status="already_ended",
                    ended_at=tombstone.ended_at,
                )

            record.state = SessionState.ENDING
            self._last_state = SessionState.ENDING
            await self._cleanup_record_locked(record)
            ended_at = self._aware_now()
            record.state = SessionState.ENDED
            self._last_state = SessionState.ENDED
            self._remember_tombstone(record, ended_at)
            if self._current is record:
                self._current = None
            return EndResult(session_id=session_id, status="ended", ended_at=ended_at)

    async def health(self) -> ManagerHealth:
        async with self._lock:
            record = self._current
            if record is not None:
                await self._expire_if_needed_locked(record)
            record = self._current
            if record is not None and record.state is SessionState.ACTIVE:
                adapter = record.adapter
                if adapter is None or not adapter.is_ready:
                    await self._fail_record_locked(record)
                    record = None
            active = record is not None and record.state is SessionState.ACTIVE
            ready = not self._shutdown and not self._cleanup_compromised
            return ManagerHealth(
                browser_ready=ready,
                session_active=active,
                slots_available=1 if ready and not active else 0,
            )

    async def shutdown(self) -> None:
        async with self._lock:
            if self._shutdown and self._current is None:
                return
            self._shutdown = True
            record = self._current
            if record is None:
                return
            record.state = SessionState.ENDING
            self._last_state = SessionState.ENDING
            await self._cleanup_record_locked(record)
            ended_at = self._aware_now()
            record.state = SessionState.ENDED
            self._last_state = SessionState.ENDED
            self._remember_tombstone(record, ended_at)
            if self._current is record:
                self._current = None

    async def __aenter__(self) -> SessionManager:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.shutdown()

    async def _active_record_locked(self, session_id: UUID) -> _SessionRecord:
        record = self._current
        if record is None or record.session_id != session_id:
            tombstone = self._tombstones.get(session_id)
            if tombstone is not None and tombstone.state is SessionState.EXPIRED:
                raise SessionExpiredError("Session expired.")
            raise SessionNotFoundError("Session was not found.")
        if await self._expire_if_needed_locked(record):
            raise SessionExpiredError("Session expired.")
        if record.state is not SessionState.ACTIVE:
            raise BrowserNotReadyError("The browser is not ready.")
        adapter = record.adapter
        if adapter is None or not adapter.is_ready:
            await self._fail_record_locked(record)
            raise BrowserNotReadyError("The browser is not ready.")
        return record

    async def _expire_if_needed_locked(self, record: _SessionRecord) -> bool:
        if record is not self._current:
            return False
        if record.state is SessionState.EXPIRED:
            await self._finish_expired_record_locked(record)
            return True
        if record.state is not SessionState.ACTIVE:
            return False
        now = self._monotonic_clock()
        idle_deadline = record.last_activity_monotonic + self._idle_timeout
        if now < idle_deadline and now < record.absolute_deadline_monotonic:
            return False

        await self._finish_expired_record_locked(record)
        return True

    async def _finish_expired_record_locked(self, record: _SessionRecord) -> None:
        record.state = SessionState.EXPIRED
        self._last_state = SessionState.EXPIRED
        await self._cleanup_record_locked(record)
        ended_at = self._aware_now()
        self._remember_tombstone(record, ended_at)
        if self._current is record:
            self._current = None

    async def _await_before_absolute_deadline_locked(
        self,
        record: _SessionRecord,
        operation: Callable[[], Awaitable[_OperationResult]],
    ) -> _OperationResult:
        remaining = record.absolute_deadline_monotonic - self._monotonic_clock()
        if remaining <= 0:
            await self._finish_expired_record_locked(record)
            raise SessionExpiredError("Session expired.")
        deadline_timeout = asyncio.timeout(remaining)
        try:
            async with deadline_timeout:
                result = await operation()
        except TimeoutError:
            if not deadline_timeout.expired():
                raise
            await self._finish_expired_record_locked(record)
            raise SessionExpiredError("Session expired.") from None
        if self._monotonic_clock() >= record.absolute_deadline_monotonic:
            await self._finish_expired_record_locked(record)
            raise SessionExpiredError("Session expired.")
        return result

    async def _fail_record_locked(self, record: _SessionRecord) -> None:
        record.state = SessionState.FAILED
        self._last_state = SessionState.FAILED
        await self._cleanup_record_locked(record)
        self._remember_tombstone(record, self._aware_now())
        if self._current is record:
            self._current = None

    async def _handle_adapter_failure_locked(
        self,
        record: _SessionRecord,
        error: BaseException,
    ) -> None:
        if isinstance(error, asyncio.CancelledError):
            await self._fail_record_locked(record)
            return
        adapter = record.adapter
        if isinstance(error, BrowserNotReadyError) or adapter is None or not adapter.is_ready:
            await self._fail_record_locked(record)

    async def _cleanup_record_locked(self, record: _SessionRecord) -> None:
        cancelled: asyncio.CancelledError | None = None
        failed = False
        expiry_task = record.expiry_task
        current_task = asyncio.current_task()
        if expiry_task is not None and expiry_task is not current_task:
            expiry_task.cancel()
            expiry_waiter = asyncio.gather(expiry_task, return_exceptions=True)
            cancelled = await _drain_owned_task(expiry_waiter, cancelled)
            expiry_waiter.result()
            if expiry_task.done():
                record.expiry_task = None
        elif expiry_task is current_task:
            record.expiry_task = None

        adapter = record.adapter
        if adapter is not None:
            try:
                async with asyncio.timeout(self._cleanup_timeout):
                    await adapter.close()
            except asyncio.CancelledError as error:
                cancelled = cancelled or error
                failed = True
            except Exception:
                failed = True
            else:
                record.adapter = None

        profile_task = asyncio.create_task(
            self._remove_profile(record.profile_directory),
            name="agent-browser-profile-cleanup",
        )
        cancelled = await _drain_owned_task(profile_task, cancelled)
        try:
            profile_removed = profile_task.result()
        except asyncio.CancelledError as error:
            cancelled = cancelled or error
            profile_removed = False
        except Exception:
            profile_removed = False
        if not profile_removed:
            failed = True

        if failed or cancelled is not None:
            self._cleanup_failures += 1
            self._cleanup_compromised = True
            if cancelled is not None:
                raise cancelled
            raise BrowserOperationError("Browser cleanup did not complete.")

        self._cleanup_failures = 0
        self._cleanup_compromised = False

    async def _remove_profile(self, profile_directory: Path) -> bool:
        for attempt in range(2):
            try:
                async with asyncio.timeout(self._cleanup_timeout):
                    await asyncio.to_thread(shutil.rmtree, profile_directory)
                return True
            except FileNotFoundError:
                return True
            except Exception:
                if attempt == 0:
                    await asyncio.sleep(0)
                    continue
        return False

    async def _expiry_watch(self, session_id: UUID) -> None:
        try:
            while True:
                async with self._lock:
                    record = self._current
                    if record is None or record.session_id != session_id:
                        return
                    adapter = record.adapter
                    if adapter is None or not adapter.is_ready:
                        await self._fail_record_locked(record)
                        return
                    if await self._expire_if_needed_locked(record):
                        return
                    now = self._monotonic_clock()
                    remaining = min(
                        record.last_activity_monotonic + self._idle_timeout - now,
                        record.absolute_deadline_monotonic - now,
                    )
                await asyncio.sleep(max(0.001, min(remaining, self._reaper_resolution)))
        except asyncio.CancelledError:
            return
        except BrowserOperationError:
            return

    def _remember_tombstone(self, record: _SessionRecord, ended_at: datetime) -> None:
        self._tombstones[record.session_id] = _Tombstone(
            state=record.state,
            ended_at=ended_at,
        )
        self._tombstones.move_to_end(record.session_id)
        while len(self._tombstones) > self._tombstone_limit:
            self._tombstones.popitem(last=False)

    def _capacity_retry_after(self, record: _SessionRecord) -> int:
        if record.state is not SessionState.ACTIVE:
            return 1
        now = self._monotonic_clock()
        remaining = min(
            record.last_activity_monotonic + self._idle_timeout - now,
            record.absolute_deadline_monotonic - now,
        )
        return max(1, min(300, int(remaining) or 1))

    def _session_info(self, record: _SessionRecord) -> SessionInfo:
        return SessionInfo(
            session_id=record.session_id,
            state=record.state,
            max_vision_steps=record.max_vision_steps,
            view_url=self._view_url,
            created_at=record.created_at,
            expires_at=record.expires_at,
        )

    def _touch(self, record: _SessionRecord) -> None:
        record.last_activity_monotonic = self._monotonic_clock()

    def _adapter(self, record: _SessionRecord) -> BrowserAdapter:
        if record.adapter is None:
            raise BrowserNotReadyError("The browser is not ready.")
        return record.adapter

    def _aware_now(self) -> datetime:
        value = self._utc_clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("utc_clock must return an offset-aware datetime")
        return value


__all__ = [
    "AdapterFactory",
    "EndResult",
    "InteractionResult",
    "ManagerHealth",
    "NavigationResult",
    "SessionCapacityError",
    "SessionError",
    "SessionExpiredError",
    "SessionInfo",
    "SessionManager",
    "SessionNotFoundError",
    "SnapshotResult",
    "VisionBudgetExhaustedError",
]
