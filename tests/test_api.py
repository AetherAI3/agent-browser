from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fixtures.runtime_fakes import FakeAdapterFactory, FakeClock

from aether_browser.main import RequiredAuthority, RuntimeSettings, create_app
from aether_browser.sessions import SessionManager


async def allow_authority(
    _authorization: str | None,
    _required: RequiredAuthority,
) -> None:
    return None


async def allow_navigation(_url: str) -> None:
    return None


def make_manager(
    factory: FakeAdapterFactory,
    clock: FakeClock,
    profile_root: Path,
) -> SessionManager:
    return SessionManager(
        factory,
        profile_root=profile_root,
        utc_clock=clock.utc_now,
        monotonic_clock=clock.monotonic,
        reaper_resolution_seconds=60.0,
    )


@asynccontextmanager
async def api_client(application: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1",
        ) as client:
            yield client


@pytest.mark.asyncio
async def test_all_six_routes_follow_the_closed_v1_contract(tmp_path: Path) -> None:
    factory = FakeAdapterFactory()
    clock = FakeClock()
    manager = make_manager(factory, clock, tmp_path)
    app = create_app(
        manager=manager,
        authority=allow_authority,
        navigation_policy=allow_navigation,
        utc_clock=clock.utc_now,
    )

    async with api_client(app) as client:
        health = await client.get("/browser/health")
        assert health.status_code == 200
        assert health.json()["slots_available"] == 1

        create = await client.post("/browser/session/create", json={"max_vision_steps": 2})
        assert create.status_code == 200
        created = create.json()
        assert created["api_version"] == "v1"
        assert created["status"] == "created"
        session_id = created["session_id"]

        navigate = await client.post(
            "/browser/navigate",
            json={"session_id": session_id, "url": "https://example.com/path"},
        )
        assert navigate.status_code == 200
        assert navigate.json()["final_url"] == "https://example.com/path"
        assert navigate.json()["accessibility"]["nodes"][0]["role"] == "heading"

        snapshot = await client.post(
            "/browser/snapshot",
            json={"session_id": session_id},
        )
        assert snapshot.status_code == 200
        assert snapshot.json()["status"] == "snapshot"
        assert snapshot.json()["sequence"] == 1
        assert snapshot.json()["vision_steps_remaining"] == 1

        interact = await client.post(
            "/browser/interact",
            json={
                "session_id": session_id,
                "action": "click",
                "target": {"selector": "#submit"},
            },
        )
        assert interact.status_code == 200
        assert interact.json()["sequence"] == 2

        ended = await client.post(
            "/browser/session/end",
            json={"session_id": session_id},
        )
        repeated = await client.post(
            "/browser/session/end",
            json={"session_id": session_id},
        )
        assert ended.json()["status"] == "ended"
        assert repeated.json()["status"] == "already_ended"
        assert all(
            response.headers["cache-control"] == "no-store"
            for response in [
                health,
                create,
                navigate,
                snapshot,
                interact,
                ended,
            ]
        )


@pytest.mark.asyncio
async def test_capacity_unknown_budget_and_validation_errors_are_stable(
    tmp_path: Path,
) -> None:
    manager = make_manager(FakeAdapterFactory(), FakeClock(), tmp_path)
    app = create_app(
        manager=manager,
        authority=allow_authority,
        navigation_policy=allow_navigation,
    )

    async with api_client(app) as client:
        create = await client.post("/browser/session/create", json={"max_vision_steps": 1})
        session_id = create.json()["session_id"]

        capacity = await client.post("/browser/session/create", json={})
        assert capacity.status_code == 503
        assert capacity.json()["error"]["code"] == "SESSION_CAPACITY_REACHED"
        assert 1 <= int(capacity.headers["retry-after"]) <= 300

        unknown = await client.post(
            "/browser/snapshot",
            json={"session_id": str(uuid4())},
        )
        assert unknown.status_code == 404
        assert unknown.json()["error"]["code"] == "SESSION_NOT_FOUND"

        first = await client.post("/browser/snapshot", json={"session_id": session_id})
        exhausted = await client.post("/browser/snapshot", json={"session_id": session_id})
        assert first.status_code == 200
        assert exhausted.status_code == 409
        assert exhausted.json()["error"]["code"] == "VISION_BUDGET_EXHAUSTED"

        invalid = await client.post(
            "/browser/interact",
            json={
                "session_id": session_id,
                "action": "click",
                "target": {"selector": "button"},
                "javascript": "secret()",
            },
        )
        assert invalid.status_code == 422
        assert invalid.json() == {
            "api_version": "v1",
            "status": "error",
            "error": {
                "code": "INVALID_INTERACTION",
                "message": "The request or interaction is invalid.",
            },
        }


@pytest.mark.asyncio
async def test_authority_and_policy_failures_never_echo_token_or_details(
    tmp_path: Path,
) -> None:
    sentinel = "nonsecret-sensitive-sentinel"
    private_detail = "C:\\private\\profile\\secret"

    class SecurityFailure(RuntimeError):
        def __init__(self, code: str) -> None:
            super().__init__(f"{sentinel} {private_detail}")
            self.code = code

    async def deny_authority(
        _authorization: str | None,
        _required: RequiredAuthority,
    ) -> None:
        raise SecurityFailure("AUTH_REQUIRED")

    manager = make_manager(FakeAdapterFactory(), FakeClock(), tmp_path)
    auth_app = create_app(
        manager=manager,
        authority=deny_authority,
        navigation_policy=allow_navigation,
    )
    async with api_client(auth_app) as client:
        response = await client.get(
            "/browser/health",
            headers={"Authorization": f"Bearer {sentinel}"},
        )
        serialized = f"{response.text} {dict(response.headers)}"
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTH_REQUIRED"
        assert sentinel not in serialized
        assert private_detail not in serialized

    async def deny_policy(_url: str) -> None:
        raise SecurityFailure("DESTINATION_BLOCKED")

    second_manager = make_manager(FakeAdapterFactory(), FakeClock(), tmp_path)
    policy_app = create_app(
        manager=second_manager,
        authority=allow_authority,
        navigation_policy=deny_policy,
    )
    async with api_client(policy_app) as client:
        create = await client.post("/browser/session/create", json={})
        response = await client.post(
            "/browser/navigate",
            json={
                "session_id": create.json()["session_id"],
                "url": "https://example.com/",
            },
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "DESTINATION_BLOCKED"
        assert sentinel not in response.text
        assert private_detail not in response.text


@pytest.mark.asyncio
async def test_launch_failure_uses_bounded_browser_error_envelope(tmp_path: Path) -> None:
    factory = FakeAdapterFactory(next_launch_error=RuntimeError("token at C:\\private"))
    manager = make_manager(factory, FakeClock(), tmp_path)
    app = create_app(
        manager=manager,
        authority=allow_authority,
        navigation_policy=allow_navigation,
    )

    async with api_client(app) as client:
        response = await client.post("/browser/session/create", json={})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "BROWSER_NOT_READY"
    assert "token" not in response.text.casefold()
    assert "private" not in response.text.casefold()


@pytest.mark.asyncio
async def test_authority_levels_and_lifespan_shutdown_are_injected(tmp_path: Path) -> None:
    calls: list[tuple[str | None, RequiredAuthority]] = []

    async def record_authority(
        authorization: str | None,
        required: RequiredAuthority,
    ) -> None:
        calls.append((authorization, required))

    factory = FakeAdapterFactory()
    manager = make_manager(factory, FakeClock(), tmp_path)
    app = create_app(
        manager=manager,
        authority=record_authority,
        navigation_policy=allow_navigation,
    )

    async with api_client(app) as client:
        await client.get("/browser/health", headers={"Authorization": "Bearer observer"})
        create = await client.post(
            "/browser/session/create",
            headers={"Authorization": "Bearer controller"},
            json={},
        )
        await client.post(
            "/browser/snapshot",
            headers={"Authorization": "Bearer observer"},
            json={"session_id": create.json()["session_id"]},
        )

    assert calls == [
        ("Bearer observer", RequiredAuthority.OBSERVER),
        ("Bearer controller", RequiredAuthority.CONTROLLER),
        ("Bearer observer", RequiredAuthority.OBSERVER),
    ]
    assert factory.adapters[0].closed
    profile = factory.adapters[0].launched_profile
    assert profile is not None and not profile.exists()


@pytest.mark.asyncio
async def test_response_never_contains_unmodeled_runtime_state(tmp_path: Path) -> None:
    manager = make_manager(FakeAdapterFactory(), FakeClock(), tmp_path)
    app = create_app(
        manager=manager,
        authority=allow_authority,
        navigation_policy=allow_navigation,
    )

    async with api_client(app) as client:
        response = await client.post("/browser/session/create", json={})
        body: dict[str, Any] = response.json()

    assert set(body) == {
        "api_version",
        "status",
        "session_id",
        "state",
        "max_vision_steps",
        "view_url",
        "created_at",
        "expires_at",
    }
    assert not any(key in body for key in ("token", "profile", "process", "arguments"))


def test_listener_and_effective_host_mismatch_requires_container_mode() -> None:
    with pytest.raises(ValueError, match="outside container mode"):
        RuntimeSettings(api_bind="0.0.0.0", api_host="127.0.0.1").validate()  # noqa: S104

    RuntimeSettings(
        api_bind="0.0.0.0",  # noqa: S104
        api_host="127.0.0.1",
        novnc_bind="0.0.0.0",  # noqa: S104
        novnc_host="127.0.0.1",
        container_mode=True,
    ).validate()
