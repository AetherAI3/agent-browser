from __future__ import annotations

# ruff: noqa: S104 -- wildcard binds are deliberate fail-closed test inputs.
from collections.abc import Iterable

import pytest

from aether_browser.auth import (
    AuthConfigurationError,
    Authority,
    AuthorityForbidden,
    authorize,
    build_auth_settings,
)
from aether_browser.policy import NavigationPolicy, PolicyConfigurationError, PolicyError

OBSERVER_CANARY = "Observer-Security-Canary-0123456789!Alpha"
CONTROLLER_CANARY = "Controller-Security-Canary-9876543210!Beta"


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
