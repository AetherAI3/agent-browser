from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from fixtures.runtime_fakes import FakeAdapter, FakeAdapterFactory, FakeClock

from aether_browser.models import InteractRequest
from aether_browser.runtime import BrowserLaunchError, BrowserNotReadyError, BrowserOperationError
from aether_browser.sessions import (
    SessionCapacityError,
    SessionExpiredError,
    SessionManager,
    SessionNotFoundError,
    VisionBudgetExhaustedError,
)


def build_manager(
    factory: FakeAdapterFactory,
    clock: FakeClock,
    profile_root: Path,
    *,
    idle: float = 10.0,
    absolute: float = 60.0,
) -> SessionManager:
    return SessionManager(
        factory,
        idle_timeout_seconds=idle,
        absolute_lifetime_seconds=absolute,
        reaper_resolution_seconds=60.0,
        profile_root=profile_root,
        utc_clock=clock.utc_now,
        monotonic_clock=clock.monotonic,
    )


@pytest.mark.asyncio
async def test_create_owns_one_uuid_and_temporary_profile(tmp_path: Path) -> None:
    factory = FakeAdapterFactory()
    clock = FakeClock()
    manager = build_manager(factory, clock, tmp_path)

    created = await manager.create(7)

    assert created.session_id == manager.active_session_id
    assert created.max_vision_steps == 7
    assert created.expires_at > created.created_at
    profile = factory.adapters[0].launched_profile
    assert profile is not None and profile.is_dir()

    await manager.shutdown()
    assert not profile.exists()


@pytest.mark.asyncio
async def test_concurrent_create_has_one_winner_and_stable_capacity(tmp_path: Path) -> None:
    factory = FakeAdapterFactory()
    gate = asyncio.Event()
    factory.next_launch_gate = gate
    clock = FakeClock()
    manager = build_manager(factory, clock, tmp_path)

    first = asyncio.create_task(manager.create(2))
    await asyncio.sleep(0)
    second = asyncio.create_task(manager.create(2))
    await asyncio.sleep(0)
    gate.set()
    outcomes = await asyncio.gather(first, second, return_exceptions=True)

    created = [result for result in outcomes if not isinstance(result, BaseException)]
    rejected = [result for result in outcomes if isinstance(result, SessionCapacityError)]
    assert len(created) == 1
    assert len(rejected) == 1
    assert 1 <= rejected[0].retry_after_seconds <= 300
    assert len(factory.adapters) == 1
    await manager.shutdown()


@pytest.mark.asyncio
async def test_capacity_and_unknown_session_are_typed(tmp_path: Path) -> None:
    manager = build_manager(FakeAdapterFactory(), FakeClock(), tmp_path)
    created = await manager.create(2)

    with pytest.raises(SessionCapacityError):
        await manager.create(2)
    with pytest.raises(SessionNotFoundError):
        await manager.snapshot(uuid4())

    assert manager.active_session_id == created.session_id
    await manager.shutdown()


@pytest.mark.asyncio
async def test_idle_expiry_closes_browser_and_deletes_profile(tmp_path: Path) -> None:
    factory = FakeAdapterFactory()
    clock = FakeClock()
    manager = build_manager(factory, clock, tmp_path, idle=5.0, absolute=100.0)
    created = await manager.create(2)
    profile = factory.adapters[0].launched_profile
    assert profile is not None

    clock.advance(5.0)
    with pytest.raises(SessionExpiredError):
        await manager.snapshot(created.session_id)

    assert factory.adapters[0].closed
    assert not profile.exists()
    assert manager.active_session_id is None


@pytest.mark.asyncio
async def test_absolute_expiry_wins_despite_recent_activity(tmp_path: Path) -> None:
    factory = FakeAdapterFactory()
    clock = FakeClock()
    manager = build_manager(factory, clock, tmp_path, idle=20.0, absolute=6.0)
    created = await manager.create(3)

    clock.advance(4.0)
    await manager.navigate(created.session_id, "https://example.com/one")
    clock.advance(2.0)
    with pytest.raises(SessionExpiredError):
        await manager.snapshot(created.session_id)


@pytest.mark.asyncio
async def test_snapshot_budget_and_sequence_are_atomic(tmp_path: Path) -> None:
    factory = FakeAdapterFactory()
    manager = build_manager(factory, FakeClock(), tmp_path)
    created = await manager.create(1)

    outcomes = await asyncio.gather(
        manager.snapshot(created.session_id),
        manager.snapshot(created.session_id),
        return_exceptions=True,
    )

    captured = [result for result in outcomes if not isinstance(result, BaseException)]
    exhausted = [result for result in outcomes if isinstance(result, VisionBudgetExhaustedError)]
    assert len(captured) == len(exhausted) == 1
    assert captured[0].sequence == 1
    assert captured[0].vision_steps_used == 1
    assert captured[0].vision_steps_remaining == 0
    assert factory.adapters[0].calls.count(("snapshot",)) == 1
    await manager.shutdown()


@pytest.mark.asyncio
async def test_failed_snapshot_does_not_consume_budget_or_sequence(tmp_path: Path) -> None:
    factory = FakeAdapterFactory()
    manager = build_manager(factory, FakeClock(), tmp_path)
    created = await manager.create(1)
    factory.adapters[0].snapshot_error = RuntimeError("capture failed")

    with pytest.raises(RuntimeError, match="capture failed"):
        await manager.snapshot(created.session_id)
    factory.adapters[0].snapshot_error = None
    captured = await manager.snapshot(created.session_id)

    assert captured.sequence == 1
    assert captured.vision_steps_used == 1
    assert captured.vision_steps_remaining == 0
    await manager.shutdown()


@pytest.mark.asyncio
async def test_all_closed_interactions_use_one_atomic_sequence(tmp_path: Path) -> None:
    factory = FakeAdapterFactory()
    manager = build_manager(factory, FakeClock(), tmp_path)
    created = await manager.create(2)
    session_id = created.session_id

    click = await manager.interact(
        InteractRequest(
            session_id=session_id,
            action="click",
            target={"selector": "#submit"},
        )
    )
    typed = await manager.interact(
        InteractRequest(
            session_id=session_id,
            action="type",
            target={"x": 10, "y": 20},
            text="hello",
        )
    )
    scrolled = await manager.interact(
        InteractRequest(session_id=session_id, action="scroll", delta_y=200)
    )
    pressed = await manager.interact(
        InteractRequest(session_id=session_id, action="press", key="Enter")
    )
    snapshot = await manager.snapshot(session_id)

    assert [click.sequence, typed.sequence, scrolled.sequence, pressed.sequence] == [
        1,
        2,
        3,
        4,
    ]
    assert snapshot.sequence == 5
    assert ("click", "#submit", None, None) in factory.adapters[0].calls
    assert ("type", "hello", None, 10, 20) in factory.adapters[0].calls
    assert ("scroll", 0, 200) in factory.adapters[0].calls
    assert ("press", "Enter") in factory.adapters[0].calls
    await manager.shutdown()


@pytest.mark.asyncio
async def test_end_is_idempotent_and_does_not_end_a_new_session(tmp_path: Path) -> None:
    factory = FakeAdapterFactory()
    manager = build_manager(factory, FakeClock(), tmp_path)
    first = await manager.create(2)
    first_adapter = factory.adapters[0]
    first_profile = first_adapter.launched_profile

    ended = await manager.end(first.session_id)
    repeated = await manager.end(first.session_id)
    second = await manager.create(2)
    old_again = await manager.end(first.session_id)

    assert ended.status == "ended"
    assert repeated.status == old_again.status == "already_ended"
    assert repeated.ended_at == ended.ended_at
    assert first_adapter.closed
    assert first_profile is not None and not first_profile.exists()
    assert manager.active_session_id == second.session_id
    await manager.shutdown()


@pytest.mark.asyncio
async def test_shutdown_is_idempotent_and_cleans_all_owned_state(tmp_path: Path) -> None:
    factory = FakeAdapterFactory()
    manager = build_manager(factory, FakeClock(), tmp_path)
    await manager.create(2)
    profile = factory.adapters[0].launched_profile
    assert profile is not None

    await manager.shutdown()
    await manager.shutdown()

    assert factory.adapters[0].calls.count(("close",)) == 1
    assert not profile.exists()
    health = await manager.health()
    assert not health.browser_ready
    assert not health.session_active


@pytest.mark.asyncio
async def test_launch_failure_cleans_partial_adapter_and_profile(tmp_path: Path) -> None:
    factory = FakeAdapterFactory(next_launch_error=RuntimeError("private path: secret"))
    manager = build_manager(factory, FakeClock(), tmp_path)

    with pytest.raises(BrowserLaunchError, match="could not be started") as raised:
        await manager.create(2)

    profile = factory.adapters[0].launched_profile
    assert profile is not None
    assert factory.adapters[0].closed
    assert not profile.exists()
    assert "private path" not in str(raised.value)
    assert manager.active_session_id is None


@pytest.mark.asyncio
async def test_process_crash_is_cleaned_and_capacity_recovers(tmp_path: Path) -> None:
    factory = FakeAdapterFactory()
    manager = build_manager(factory, FakeClock(), tmp_path)
    first = await manager.create(2)
    first_profile = factory.adapters[0].launched_profile
    factory.adapters[0].ready = False

    with pytest.raises(BrowserNotReadyError):
        await manager.snapshot(first.session_id)

    assert first_profile is not None and not first_profile.exists()
    second = await manager.create(2)
    assert second.session_id != first.session_id
    assert len(factory.adapters) == 2
    await manager.shutdown()


@pytest.mark.asyncio
async def test_reaper_cleans_process_crash_without_a_followup_request(tmp_path: Path) -> None:
    factory = FakeAdapterFactory()
    clock = FakeClock()
    manager = SessionManager(
        factory,
        idle_timeout_seconds=60.0,
        absolute_lifetime_seconds=120.0,
        reaper_resolution_seconds=0.01,
        profile_root=tmp_path,
        utc_clock=clock.utc_now,
        monotonic_clock=clock.monotonic,
    )
    await manager.create(2)
    profile = factory.adapters[0].launched_profile
    factory.adapters[0].ready = False

    for _attempt in range(20):
        if profile is not None and not profile.exists():
            break
        await asyncio.sleep(0.01)

    assert profile is not None and not profile.exists()
    assert factory.adapters[0].closed
    assert manager.active_session_id is None
    await manager.shutdown()


class _FailOnceCloseAdapter(FakeAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.close_attempts = 0

    async def close(self) -> None:
        self.close_attempts += 1
        if self.close_attempts == 1:
            raise RuntimeError("private cleanup detail")
        await super().close()


class _BlockingCloseAdapter(FakeAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.close_started = asyncio.Event()
        self.close_release = asyncio.Event()
        self.close_attempts = 0

    async def close(self) -> None:
        self.close_attempts += 1
        self.close_started.set()
        await self.close_release.wait()
        await super().close()


class _DeadlineAdapter(FakeAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.operation_started = asyncio.Event()
        self.operation_release = asyncio.Event()

    async def _block(self) -> None:
        self.operation_started.set()
        await self.operation_release.wait()

    async def navigate(self, url: str):
        await self._block()
        return await super().navigate(url)

    async def snapshot(self):
        await self._block()
        return await super().snapshot()

    async def click(
        self,
        *,
        selector: str | None = None,
        x: int | None = None,
        y: int | None = None,
    ) -> None:
        await self._block()
        await super().click(selector=selector, x=x, y=y)


@pytest.mark.asyncio
async def test_end_cleanup_failure_is_generic_retriable_and_does_not_report_success(
    tmp_path: Path,
) -> None:
    adapter = _FailOnceCloseAdapter()
    manager = build_manager(lambda _profile: adapter, FakeClock(), tmp_path)
    created = await manager.create(2)

    with pytest.raises(BrowserOperationError, match="Browser cleanup did not complete") as raised:
        await manager.end(created.session_id)

    assert "private cleanup detail" not in str(raised.value)
    assert adapter.close_attempts == 1
    assert manager.current_state.value == "ending"

    ended = await manager.end(created.session_id)
    assert ended.status == "ended"
    assert adapter.close_attempts == 2
    assert adapter.closed
    await manager.shutdown()


@pytest.mark.asyncio
async def test_shutdown_retries_a_failed_cleanup(tmp_path: Path) -> None:
    adapter = _FailOnceCloseAdapter()
    manager = build_manager(lambda _profile: adapter, FakeClock(), tmp_path)
    await manager.create(2)

    with pytest.raises(BrowserOperationError, match="Browser cleanup did not complete"):
        await manager.shutdown()

    assert adapter.close_attempts == 1
    await manager.shutdown()
    assert adapter.close_attempts == 2
    assert adapter.closed


@pytest.mark.asyncio
async def test_cancelled_end_retains_cleanup_state_for_retry(tmp_path: Path) -> None:
    adapter = _BlockingCloseAdapter()
    manager = build_manager(lambda _profile: adapter, FakeClock(), tmp_path)
    created = await manager.create(2)

    ending = asyncio.create_task(manager.end(created.session_id))
    await adapter.close_started.wait()
    ending.cancel()
    adapter.close_release.set()

    with pytest.raises(asyncio.CancelledError):
        await ending
    assert manager.current_state.value == "ending"
    assert adapter.close_attempts == 1

    ended = await manager.end(created.session_id)
    assert ended.status == "ended"
    assert adapter.close_attempts == 2
    assert adapter.closed
    await manager.shutdown()


@pytest.mark.asyncio
async def test_profile_cleanup_failure_is_retriable_and_blocks_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aether_browser.sessions as sessions_module

    factory = FakeAdapterFactory()
    manager = build_manager(factory, FakeClock(), tmp_path)
    created = await manager.create(2)
    profile = factory.adapters[0].launched_profile
    assert profile is not None
    original_rmtree = sessions_module.shutil.rmtree
    attempts = 0

    def fail_first_cleanup(path: Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            raise OSError("private profile detail")
        original_rmtree(path)

    monkeypatch.setattr(sessions_module.shutil, "rmtree", fail_first_cleanup)

    with pytest.raises(BrowserOperationError, match="Browser cleanup did not complete") as raised:
        await manager.end(created.session_id)

    assert "private profile detail" not in str(raised.value)
    assert profile.exists()
    assert manager.current_state.value == "ending"

    ended = await manager.end(created.session_id)
    assert ended.status == "ended"
    assert not profile.exists()
    await manager.shutdown()


@pytest.mark.parametrize("operation", ["navigate", "snapshot", "interact"])
@pytest.mark.asyncio
async def test_absolute_deadline_overrun_never_commits_success(
    tmp_path: Path,
    operation: str,
) -> None:
    adapter = _DeadlineAdapter()
    clock = FakeClock()
    manager = SessionManager(
        lambda _profile: adapter,
        idle_timeout_seconds=60.0,
        absolute_lifetime_seconds=5.0,
        reaper_resolution_seconds=60.0,
        profile_root=tmp_path,
        utc_clock=clock.utc_now,
        monotonic_clock=clock.monotonic,
    )
    created = await manager.create(2)
    profile = adapter.launched_profile

    if operation == "navigate":
        pending = asyncio.create_task(manager.navigate(created.session_id, "https://example.com"))
    elif operation == "snapshot":
        pending = asyncio.create_task(manager.snapshot(created.session_id))
    else:
        pending = asyncio.create_task(
            manager.interact(
                InteractRequest(
                    session_id=created.session_id,
                    action="click",
                    target={"selector": "#submit"},
                )
            )
        )

    await adapter.operation_started.wait()
    clock.advance(5.0)
    adapter.operation_release.set()

    with pytest.raises(SessionExpiredError):
        await pending
    assert adapter.closed
    assert profile is not None and not profile.exists()
    assert manager.active_session_id is None
    await manager.shutdown()


@pytest.mark.parametrize("value", ["", "true", " 1 ", "1 ", "yes"])
def test_boolean_environment_settings_reject_noncanonical_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    from aether_browser.main import RuntimeSettings

    monkeypatch.setenv("AETHER_BROWSER_TEST_MODE", value)
    with pytest.raises(ValueError, match="must be exactly '0' or '1'"):
        RuntimeSettings.from_environment()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("AETHER_BROWSER_API_PORT", "0"),
        ("AETHER_BROWSER_API_PORT", "not-a-port"),
        ("AETHER_BROWSER_IDLE_TIMEOUT_SECONDS", "nan"),
        ("AETHER_BROWSER_IDLE_TIMEOUT_SECONDS", " 5 "),
        ("AETHER_BROWSER_ABSOLUTE_LIFETIME_SECONDS", "inf"),
    ],
)
def test_numeric_environment_settings_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    from aether_browser.main import RuntimeSettings

    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        RuntimeSettings.from_environment()


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/browser/navigate",
            {
                "api_version": "v1",
                "session_id": str(uuid4()),
                "url": "https://example.com",
            },
        ),
        (
            "/browser/interact",
            {
                "api_version": "v1",
                "session_id": str(uuid4()),
                "action": "click",
                "target": {"selector": "#submit"},
            },
        ),
        (
            "/browser/session/end",
            {"api_version": "v1", "session_id": str(uuid4())},
        ),
    ],
)
@pytest.mark.asyncio
async def test_observer_authority_cannot_reach_mutating_routes(
    path: str,
    payload: dict[str, object],
) -> None:
    from httpx import ASGITransport, AsyncClient
    from starlette.exceptions import HTTPException as StarletteHTTPException

    from aether_browser.main import RequiredAuthority, RuntimeSettings, create_app

    factory = FakeAdapterFactory()
    required_levels: list[RequiredAuthority] = []

    async def observer_authority(
        _authorization: str | None,
        required: RequiredAuthority,
    ) -> None:
        required_levels.append(required)
        if required is RequiredAuthority.CONTROLLER:
            raise StarletteHTTPException(status_code=403)

    async def allow_navigation(_url: str) -> None:
        return None

    application = create_app(
        adapter_factory=factory,
        authority=observer_authority,
        navigation_policy=allow_navigation,
        settings=RuntimeSettings(),
    )
    transport = ASGITransport(app=application, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(path, json=payload)

    assert response.status_code == 403
    assert required_levels == [RequiredAuthority.CONTROLLER]
    assert factory.adapters == []


@pytest.mark.asyncio
async def test_navigation_policy_denial_happens_before_the_adapter_call(tmp_path: Path) -> None:
    from httpx import ASGITransport, AsyncClient
    from starlette.exceptions import HTTPException as StarletteHTTPException

    from aether_browser.main import RuntimeSettings, create_app

    factory = FakeAdapterFactory()
    manager = build_manager(factory, FakeClock(), tmp_path)
    created = await manager.create(2)
    adapter = factory.adapters[0]
    calls_before_request = list(adapter.calls)

    async def allow_authority(_authorization: str | None, _required: object) -> None:
        return None

    async def deny_navigation(_url: str) -> None:
        raise StarletteHTTPException(status_code=403)

    application = create_app(
        manager=manager,
        authority=allow_authority,
        navigation_policy=deny_navigation,
        settings=RuntimeSettings(),
    )
    transport = ASGITransport(app=application, raise_app_exceptions=False)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/browser/navigate",
                json={
                    "api_version": "v1",
                    "session_id": str(created.session_id),
                    "url": "https://blocked.invalid",
                },
            )
        assert response.status_code == 403
        assert adapter.calls == calls_before_request
    finally:
        await manager.shutdown()
