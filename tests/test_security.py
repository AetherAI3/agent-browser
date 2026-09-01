from __future__ import annotations

# ruff: noqa: S104 -- wildcard binds are deliberate fail-closed test inputs.
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fixtures.runtime_fakes import FakeAdapter, FakeAdapterFactory

from aether_browser.auth import (
    AuthConfigurationError,
    Authority,
    AuthorityForbidden,
    authorize,
    build_auth_settings,
)
from aether_browser.main import RequiredAuthority, RuntimeSettings, create_app
from aether_browser.policy import (
    NavigationPolicy,
    PolicyConfigurationError,
    PolicyError,
    PolicyReason,
)
from aether_browser.runtime import NavigationGuard
from aether_browser.sessions import SessionManager

OBSERVER_CANARY = "Observer-Security-Canary-0123456789!Alpha"
CONTROLLER_CANARY = "Controller-Security-Canary-9876543210!Beta"


@asynccontextmanager
async def api_client(application: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1",
        ) as client:
            yield client


def make_manager(factory: FakeAdapterFactory, profile_root: Path) -> SessionManager:
    return SessionManager(factory, profile_root=profile_root)


@pytest.mark.asyncio
async def test_local_fixture_exception_needs_both_startup_and_policy_test_gates() -> None:
    origin = "http://127.0.0.1:8765"
    with pytest.raises(AuthConfigurationError):
        build_auth_settings(
            api_bind="127.0.0.1",
            novnc_bind="127.0.0.1",
            test_origins=[origin],
        )
    with pytest.raises(PolicyConfigurationError):
        NavigationPolicy(test_origins=[origin])

    settings = build_auth_settings(
        api_bind="127.0.0.1",
        novnc_bind="127.0.0.1",
        test_mode=True,
        test_origins=[origin],
    )
    policy = NavigationPolicy(test_mode=settings.test_mode, test_origins=settings.test_origins)
    validated = await policy.validate_url(f"{origin}/fixture")
    assert validated.origin == origin


@pytest.mark.asyncio
async def test_exact_container_fixture_origin_can_resolve_private_only_in_test_mode() -> None:
    origin = "http://fixture:8765"

    async def resolver(hostname: str) -> Iterable[str]:
        assert hostname == "fixture"
        return ["172.20.0.5"]

    production = NavigationPolicy(resolver)
    with pytest.raises(PolicyError):
        await production.validate_url(f"{origin}/ready")

    test_policy = NavigationPolicy(resolver, test_mode=True, test_origins=[origin])
    validated = await test_policy.validate_url(f"{origin}/ready")
    assert validated.origin == origin

    with pytest.raises(PolicyError):
        await test_policy.validate_url("http://fixture:8766/ready")


def test_observer_controller_matrix_is_operation_independent_and_fail_closed() -> None:
    settings = build_auth_settings(
        api_bind="0.0.0.0",
        novnc_bind="127.0.0.1",
        remote_mode=True,
        observer_token=OBSERVER_CANARY,
        controller_token=CONTROLLER_CANARY,
    )
    observer_header = f"Bearer {OBSERVER_CANARY}"
    controller_header = f"Bearer {CONTROLLER_CANARY}"

    for read_operation in ("health", "state", "snapshot"):
        assert authorize(settings, observer_header, Authority.OBSERVER) is Authority.OBSERVER
        assert read_operation not in repr(settings)
    for mutation in ("create", "navigate", "interact", "end", "configuration"):
        with pytest.raises(AuthorityForbidden):
            authorize(settings, observer_header, Authority.CONTROLLER)
        assert authorize(settings, controller_header, Authority.CONTROLLER) is Authority.CONTROLLER
        assert mutation not in repr(settings)


@pytest.mark.asyncio
async def test_redirect_to_mixed_private_dns_is_rejected_without_network() -> None:
    answers = {
        "public.example.com": ["93.184.216.34"],
        "redirect.example.com": ["93.184.216.34", "192.168.10.5"],
    }

    async def resolver(hostname: str) -> Iterable[str]:
        return answers[hostname]

    guard = NavigationPolicy(resolver).new_guard()
    await guard.validate_initial("https://public.example.com/")
    with pytest.raises(PolicyError) as raised:
        await guard.validate_redirect("https://redirect.example.com/")
    assert raised.value.code == "DESTINATION_BLOCKED"


def test_security_canary_tokens_are_absent_from_all_loggable_auth_state() -> None:
    settings = build_auth_settings(
        api_bind="0.0.0.0",
        novnc_bind="127.0.0.1",
        remote_mode=True,
        observer_token=OBSERVER_CANARY,
        controller_token=CONTROLLER_CANARY,
    )
    output = repr(settings) + repr(settings.to_loggable_dict())
    assert OBSERVER_CANARY not in output
    assert CONTROLLER_CANARY not in output


@pytest.mark.asyncio
async def test_create_app_uses_real_remote_authority_and_ip_policy(tmp_path: Path) -> None:
    manager = make_manager(FakeAdapterFactory(), tmp_path)
    application = create_app(
        manager=manager,
        settings=RuntimeSettings(
            api_bind="0.0.0.0",
            api_host="0.0.0.0",
            remote_mode=True,
            observer_token=OBSERVER_CANARY,
            controller_token=CONTROLLER_CANARY,
        ),
    )
    observer = {"Authorization": f"Bearer {OBSERVER_CANARY}"}
    controller = {"Authorization": f"Bearer {CONTROLLER_CANARY}"}

    async with api_client(application) as client:
        missing = await client.get("/browser/health")
        observed = await client.get("/browser/health", headers=observer)
        forbidden = await client.post(
            "/browser/session/create",
            headers=observer,
            json={},
        )
        created = await client.post(
            "/browser/session/create",
            headers=controller,
            json={},
        )
        blocked = await client.post(
            "/browser/navigate",
            headers=controller,
            json={
                "session_id": created.json()["session_id"],
                "url": "http://127.0.0.1/private",
            },
        )
        snapshot = await client.post(
            "/browser/snapshot",
            headers=observer,
            json={"session_id": created.json()["session_id"]},
        )

    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert observed.status_code == 200
    assert forbidden.status_code == 403
    assert created.status_code == 200
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "DESTINATION_BLOCKED"
    assert snapshot.status_code == 200


@pytest.mark.asyncio
async def test_injected_doubles_do_not_bypass_fail_closed_startup(tmp_path: Path) -> None:
    async def injected_authority(
        _authorization: str | None,
        _required: RequiredAuthority,
    ) -> None:
        return None

    async def injected_policy(_url: str) -> None:
        return None

    unsafe_settings = (
        RuntimeSettings(
            api_bind="0.0.0.0",
            api_host="0.0.0.0",
        ),
        RuntimeSettings(test_origins=("http://127.0.0.1:8765",)),
        RuntimeSettings(api_bind="localhost", api_host="localhost"),
        RuntimeSettings(novnc_bind="localhost", novnc_host="localhost"),
    )
    for index, settings in enumerate(unsafe_settings):
        application = create_app(
            manager=make_manager(FakeAdapterFactory(), tmp_path / str(index)),
            authority=injected_authority,
            navigation_policy=injected_policy,
            settings=settings,
        )
        with pytest.raises(AuthConfigurationError):
            async with application.router.lifespan_context(application):
                pass


@pytest.mark.asyncio
async def test_real_authority_rejects_ambiguous_headers_and_rebinding_host(
    tmp_path: Path,
) -> None:
    remote = create_app(
        manager=make_manager(FakeAdapterFactory(), tmp_path / "remote"),
        settings=RuntimeSettings(
            api_bind="0.0.0.0",
            api_host="0.0.0.0",
            remote_mode=True,
            observer_token=OBSERVER_CANARY,
            controller_token=CONTROLLER_CANARY,
        ),
    )
    async with api_client(remote) as client:
        duplicate = await client.get(
            "/browser/health",
            headers=[
                ("Authorization", f"Bearer {OBSERVER_CANARY}"),
                ("Authorization", f"Bearer {CONTROLLER_CANARY}"),
            ],
        )
    assert duplicate.status_code == 401

    local = create_app(
        manager=make_manager(FakeAdapterFactory(), tmp_path / "local"),
        settings=RuntimeSettings(
            api_bind="0.0.0.0",
            api_host="127.0.0.1",
            novnc_bind="0.0.0.0",
            novnc_host="127.0.0.1",
            container_mode=True,
        ),
    )
    async with api_client(local) as client:
        healthy = await client.get("/browser/health")
        rebound = await client.get(
            "/browser/health",
            headers={"Host": "attacker.example"},
        )
        named_loopback = await client.get(
            "/browser/health",
            headers={"Host": "localhost"},
        )
        forwarded = await client.get(
            "/browser/health",
            headers={"Forwarded": "host=attacker.example"},
        )

    assert healthy.status_code == 200
    assert rebound.status_code == 401
    assert named_loopback.status_code == 401
    assert forwarded.status_code == 401


@pytest.mark.asyncio
async def test_default_adapter_receives_stateful_dns_and_redirect_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver_answers = {
        "rebind.example.com": [["93.184.216.34"], ["8.8.8.8"]],
        "redirect.example.com": [["10.0.0.8"]],
    }
    resolver_calls: dict[str, int] = {}

    async def resolver(hostname: str) -> Iterable[str]:
        call_index = resolver_calls.get(hostname, 0)
        resolver_calls[hostname] = call_index + 1
        answers = resolver_answers[hostname]
        return answers[min(call_index, len(answers) - 1)]

    monkeypatch.setattr("aether_browser.policy._system_resolver", resolver)
    captured: list[FakeAdapter] = []
    injected_calls: list[str] = []

    async def injected_policy(url: str) -> None:
        injected_calls.append(url)

    class CapturingAdapter(FakeAdapter):
        def __init__(
            self,
            *,
            navigation_guard: NavigationGuard | None = None,
            redirect_guard: NavigationGuard | None = None,
        ) -> None:
            super().__init__()
            self.navigation_guard = navigation_guard
            self.redirect_guard = redirect_guard
            captured.append(self)

    monkeypatch.setattr("aether_browser.main.PatchrightBrowserAdapter", CapturingAdapter)
    application = create_app(
        settings=RuntimeSettings(),
        navigation_policy=injected_policy,
    )

    async with api_client(application) as client:
        created = await client.post("/browser/session/create", json={})
        assert created.status_code == 200
        adapter = captured[0]
        assert adapter.navigation_guard is not None
        assert adapter.redirect_guard is not None

        await adapter.navigation_guard("https://rebind.example.com/one")
        with pytest.raises(PolicyError) as rebound:
            await adapter.navigation_guard("https://rebind.example.com/two")
        with pytest.raises(PolicyError) as redirect:
            await adapter.redirect_guard("https://redirect.example.com/final")

    assert rebound.value.reason is PolicyReason.DNS_REBINDING
    assert redirect.value.reason is PolicyReason.PROHIBITED_DESTINATION
    assert injected_calls == []
