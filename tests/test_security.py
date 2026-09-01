from __future__ import annotations

# ruff: noqa: S104 -- wildcard binds are deliberate fail-closed test inputs.
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fixtures.runtime_fakes import FakeAdapter, FakeAdapterFactory

import aether_browser.main as main_module
from aether_browser.auth import (
    AuthConfigurationError,
    Authority,
    AuthorityForbidden,
    AuthSettings,
    authorize,
    build_auth_settings,
)
from aether_browser.main import RequiredAuthority, RuntimeSettings, create_app, run
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
async def api_client(
    application: FastAPI,
    *,
    base_url: str = "http://127.0.0.1",
    peer: tuple[str, int] = ("127.0.0.1", 12345),
) -> AsyncIterator[httpx.AsyncClient]:
    async with application.router.lifespan_context(application):
        transport = httpx.ASGITransport(
            app=application,
            raise_app_exceptions=False,
            client=peer,
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url=base_url,
        ) as client:
            yield client


def make_manager(factory: FakeAdapterFactory, profile_root: Path) -> SessionManager:
    return SessionManager(factory, profile_root=profile_root)


def proxy_auth_settings() -> AuthSettings:
    return build_auth_settings(
        api_bind="127.0.0.1",
        api_host="browser.example",
        novnc_bind="127.0.0.1",
        novnc_host="127.0.0.1",
        remote_mode=True,
        reverse_proxy_exposed=True,
        trusted_proxy_cidr="127.0.0.1/32",
        trusted_proxy_scheme="https",
        observer_token=OBSERVER_CANARY,
        controller_token=CONTROLLER_CANARY,
    )


def proxy_runtime_settings(**overrides: object) -> RuntimeSettings:
    values: dict[str, object] = {
        "api_bind": "127.0.0.1",
        "api_host": "browser.example",
        "remote_mode": True,
        "reverse_proxy_exposed": True,
        "trusted_proxy_cidr": "127.0.0.1/32",
        "trusted_proxy_scheme": "https",
        "observer_token": OBSERVER_CANARY,
        "controller_token": CONTROLLER_CANARY,
    }
    values.update(overrides)
    return RuntimeSettings(**values)  # type: ignore[arg-type]


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
    settings = proxy_auth_settings()
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
    settings = proxy_auth_settings()
    output = repr(settings) + repr(settings.to_loggable_dict())
    assert OBSERVER_CANARY not in output
    assert CONTROLLER_CANARY not in output


@pytest.mark.asyncio
async def test_create_app_uses_real_remote_authority_and_ip_policy(tmp_path: Path) -> None:
    manager = make_manager(FakeAdapterFactory(), tmp_path)
    application = create_app(
        manager=manager,
        settings=proxy_runtime_settings(),
    )
    observer = {"Authorization": f"Bearer {OBSERVER_CANARY}"}
    controller = {"Authorization": f"Bearer {CONTROLLER_CANARY}"}

    async with api_client(application, base_url="https://browser.example") as client:
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
        proxy_runtime_settings(controller_token=None),
        proxy_runtime_settings(test_mode=True),
        RuntimeSettings(view_url="http://example.com:6080/vnc.html"),
    )
    for index, settings in enumerate(unsafe_settings):
        with pytest.raises((AuthConfigurationError, ValueError)):
            application = create_app(
                manager=make_manager(FakeAdapterFactory(), tmp_path / str(index)),
                authority=injected_authority,
                navigation_policy=injected_policy,
                settings=settings,
            )
            async with application.router.lifespan_context(application):
                pass


@pytest.mark.asyncio
async def test_injected_authority_is_additive_to_remote_request_boundary(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str | None, RequiredAuthority]] = []

    async def no_op_authority(
        authorization: str | None,
        required: RequiredAuthority,
    ) -> None:
        calls.append((authorization, required))

    application = create_app(
        manager=make_manager(FakeAdapterFactory(), tmp_path / "trusted"),
        authority=no_op_authority,
        settings=proxy_runtime_settings(),
    )
    observer_value = f"Bearer {OBSERVER_CANARY}"
    observer = {"Authorization": observer_value}
    async with api_client(application, base_url="https://browser.example") as client:
        missing_authorization = await client.get("/browser/health")
        duplicate_authorization = await client.get(
            "/browser/health",
            headers=[
                ("Authorization", observer_value),
                ("Authorization", f"Bearer {CONTROLLER_CANARY}"),
            ],
        )
        missing_host = await client.get(
            "/browser/health",
            headers={**observer, "Host": ""},
        )
        duplicate_host = await client.get(
            "/browser/health",
            headers=[
                ("Authorization", observer_value),
                ("Host", "browser.example"),
                ("Host", "browser.example"),
            ],
        )
        forwarding_responses = []
        for header_name in (
            "Forwarded",
            "X-Forwarded-For",
            "X-Real-IP",
            "X-Original-Host",
        ):
            forwarding_responses.append(
                await client.get(
                    "/browser/health",
                    headers={**observer, header_name: "attacker-controlled"},
                )
            )
        invalid_token = await client.get(
            "/browser/health",
            headers={"Authorization": "Bearer Invalid-Security-Token-0123456789!Gamma"},
        )
        wrong_role = await client.post(
            "/browser/session/create",
            headers=observer,
            json={},
        )
        accepted = await client.get("/browser/health", headers=observer)

    assert missing_authorization.status_code == 401
    assert duplicate_authorization.status_code == 401
    assert missing_host.status_code == 401
    assert duplicate_host.status_code == 401
    assert all(response.status_code == 401 for response in forwarding_responses)
    assert invalid_token.status_code == 401
    assert wrong_role.status_code == 403
    assert accepted.status_code == 200
    assert calls == [(observer_value, RequiredAuthority.OBSERVER)]

    untrusted_peer_application = create_app(
        manager=make_manager(FakeAdapterFactory(), tmp_path / "untrusted"),
        authority=no_op_authority,
        settings=proxy_runtime_settings(),
    )
    async with api_client(
        untrusted_peer_application,
        base_url="https://browser.example",
        peer=("127.0.0.2", 12345),
    ) as client:
        wrong_peer = await client.get("/browser/health", headers=observer)

    assert wrong_peer.status_code == 401
    assert calls == [(observer_value, RequiredAuthority.OBSERVER)]


@pytest.mark.asyncio
async def test_real_authority_rejects_ambiguous_headers_and_rebinding_host(
    tmp_path: Path,
) -> None:
    remote = create_app(
        manager=make_manager(FakeAdapterFactory(), tmp_path / "remote"),
        settings=proxy_runtime_settings(),
    )
    observer = {"Authorization": f"Bearer {OBSERVER_CANARY}"}
    async with api_client(remote, base_url="https://browser.example") as client:
        accepted = await client.get("/browser/health", headers=observer)
        accepted_default_port = await client.get(
            "/browser/health",
            headers={**observer, "Host": "browser.example:443"},
        )
        duplicate = await client.get(
            "/browser/health",
            headers=[
                ("Authorization", f"Bearer {OBSERVER_CANARY}"),
                ("Authorization", f"Bearer {CONTROLLER_CANARY}"),
            ],
        )
        duplicate_host = await client.get(
            "/browser/health",
            headers=[
                ("Authorization", f"Bearer {OBSERVER_CANARY}"),
                ("Host", "browser.example"),
                ("Host", "browser.example"),
            ],
        )
        wrong_host = await client.get(
            "/browser/health",
            headers={**observer, "Host": "attacker.example"},
        )
        forwarded = await client.get(
            "/browser/health",
            headers={**observer, "X-Forwarded-For": "127.0.0.1"},
        )
    async with api_client(
        remote,
        base_url="https://browser.example",
        peer=("127.0.0.2", 12345),
    ) as client:
        wrong_peer = await client.get("/browser/health", headers=observer)

    assert accepted.status_code == 200
    assert accepted_default_port.status_code == 200
    assert duplicate.status_code == 401
    assert duplicate_host.status_code == 401
    assert wrong_host.status_code == 401
    assert forwarded.status_code == 401
    assert wrong_peer.status_code == 401

    local = create_app(
        manager=make_manager(FakeAdapterFactory(), tmp_path / "local"),
        settings=RuntimeSettings(),
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


@pytest.mark.parametrize(
    "host",
    [
        "[browser.example]",
        "[127.0.0.1]",
        "browser.example:0443",
        "browser.example:444",
    ],
)
@pytest.mark.asyncio
async def test_proxy_rejects_ambiguous_host_authorities(tmp_path: Path, host: str) -> None:
    application = create_app(
        manager=make_manager(FakeAdapterFactory(), tmp_path / "host"),
        settings=proxy_runtime_settings(),
    )
    headers = {"Authorization": f"Bearer {OBSERVER_CANARY}", "Host": host}
    async with api_client(application, base_url="https://browser.example") as client:
        response = await client.get("/browser/health", headers=headers)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_authenticated_local_mode_still_rejects_nonloopback_peer(tmp_path: Path) -> None:
    application = create_app(
        manager=make_manager(FakeAdapterFactory(), tmp_path),
        settings=RuntimeSettings(
            observer_token=OBSERVER_CANARY,
            controller_token=CONTROLLER_CANARY,
        ),
    )
    headers = {"Authorization": f"Bearer {OBSERVER_CANARY}"}
    async with api_client(application, peer=("192.0.2.1", 12345)) as client:
        response = await client.get("/browser/health", headers=headers)
    assert response.status_code == 401


@pytest.mark.parametrize(
    "header_name",
    [
        "Forwarded",
        "X-Forwarded-For",
        "X-Forwarded-Host",
        "X-Forwarded-Port",
        "X-Forwarded-Proto",
        "X-Forwarded-Anything",
        "X-Real-IP",
        "X-Original-Host",
    ],
)
@pytest.mark.asyncio
async def test_proxy_rejects_every_forwarding_header(
    tmp_path: Path,
    header_name: str,
) -> None:
    application = create_app(
        manager=make_manager(FakeAdapterFactory(), tmp_path / header_name),
        settings=proxy_runtime_settings(),
    )
    headers = {
        "Authorization": f"Bearer {OBSERVER_CANARY}",
        header_name: "attacker-controlled",
    }
    async with api_client(application, base_url="https://browser.example") as client:
        response = await client.get("/browser/health", headers=headers)
    assert response.status_code == 401


def test_environment_loads_complete_proxy_tuple(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AETHER_BROWSER_API_BIND", "127.0.0.1")
    monkeypatch.setenv("AETHER_BROWSER_API_HOST", "browser.example")
    monkeypatch.setenv("AETHER_BROWSER_REMOTE_MODE", "1")
    monkeypatch.setenv("AETHER_BROWSER_REVERSE_PROXY_EXPOSED", "1")
    monkeypatch.setenv("AETHER_BROWSER_TRUSTED_PROXY_CIDR", "127.0.0.1/32")
    monkeypatch.setenv("AETHER_BROWSER_TRUSTED_PROXY_SCHEME", "https")
    monkeypatch.setenv("AETHER_BROWSER_OBSERVER_TOKEN", OBSERVER_CANARY)
    monkeypatch.setenv("AETHER_BROWSER_CONTROLLER_TOKEN", CONTROLLER_CANARY)

    settings = RuntimeSettings.from_environment()

    assert settings.trusted_proxy_cidr == "127.0.0.1/32"
    assert settings.trusted_proxy_scheme == "https"


def test_run_disables_uvicorn_proxy_header_trust(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        RuntimeSettings,
        "from_environment",
        classmethod(lambda _cls: RuntimeSettings()),
    )
    monkeypatch.setattr(
        "aether_browser.main.uvicorn.run",
        lambda *args, **kwargs: captured.update({"args": args, **kwargs}),
    )

    run()

    assert captured["proxy_headers"] is False


def test_raw_uvicorn_import_string_entrypoints_are_refused() -> None:
    assert not hasattr(main_module, "app")
    with pytest.raises(ValueError, match="validated module launcher"):
        main_module.create_app()


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
