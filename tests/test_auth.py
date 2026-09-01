from __future__ import annotations

# ruff: noqa: S104 -- wildcard binds are deliberate fail-closed test inputs.
import hmac
from collections.abc import Callable

import pytest
from fastapi import HTTPException

from aether_browser.auth import (
    AuthConfigurationError,
    AuthenticationRequired,
    AuthError,
    Authority,
    AuthorityForbidden,
    AuthSettings,
    authorize,
    build_auth_settings,
    is_loopback_bind,
    parse_bearer_authorization,
    require_authority,
)

OBSERVER_CANARY = "Observer-Canary-Token-0123456789-Alpha"
CONTROLLER_CANARY = "Controller-Canary-Token-9876543210-Beta"


def authenticated_settings(**overrides: object) -> AuthSettings:
    values: dict[str, object] = {
        "api_bind": "0.0.0.0",
        "novnc_bind": "127.0.0.1",
        "remote_mode": True,
        "observer_token": OBSERVER_CANARY,
        "controller_token": CONTROLLER_CANARY,
    }
    values.update(overrides)
    return build_auth_settings(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "bind",
    ["127.0.0.1", "127.0.0.1:8000", "::1", "[::1]:6080", "localhost"],
)
def test_strict_loopback_local_mode_is_tokenless(bind: str) -> None:
    settings = build_auth_settings(api_bind=bind, novnc_bind="127.0.0.1")
    assert settings.tokenless_local_mode
    assert authorize(settings, None, Authority.CONTROLLER) is Authority.CONTROLLER
    assert is_loopback_bind(bind)


@pytest.mark.parametrize("bind", ["0.0.0.0", "192.0.2.10", "example.com", "::"])
def test_non_loopback_api_requires_explicit_remote_mode(bind: str) -> None:
    with pytest.raises(AuthConfigurationError):
        build_auth_settings(api_bind=bind, novnc_bind="127.0.0.1")


def test_remote_mode_requires_both_distinct_strong_tokens() -> None:
    with pytest.raises(AuthConfigurationError):
        build_auth_settings(
            api_bind="0.0.0.0",
            novnc_bind="127.0.0.1",
            remote_mode=True,
        )
    with pytest.raises(AuthConfigurationError):
        build_auth_settings(
            api_bind="0.0.0.0",
            novnc_bind="127.0.0.1",
            remote_mode=True,
            observer_token=OBSERVER_CANARY,
        )
    with pytest.raises(AuthConfigurationError):
        authenticated_settings(controller_token=OBSERVER_CANARY)


@pytest.mark.parametrize(
    "weak",
    [
        "short-A1!",
        "a" * 40,
        "A" * 20 + "1" * 20,
        "lowercase-with-symbols----------------",
        "UppercaseAndLowercaseOnlyTokenValueXYZ",
        "ValidLooking-Token-1234567890-With Space",
        "VálidLooking-Token-1234567890-Symbol!",
        "Aa1!" * 1_025,
    ],
)
def test_weak_tokens_fail_closed(weak: str) -> None:
    with pytest.raises(AuthConfigurationError):
        authenticated_settings(observer_token=weak)


@pytest.mark.parametrize("novnc_bind", ["0.0.0.0", "192.168.1.10", "example.com", "::"])
def test_novnc_is_always_loopback_only(novnc_bind: str) -> None:
    with pytest.raises(AuthConfigurationError):
        authenticated_settings(novnc_bind=novnc_bind)


def test_reverse_proxy_exposure_requires_remote_mode_and_tokens() -> None:
    with pytest.raises(AuthConfigurationError):
        build_auth_settings(
            api_bind="127.0.0.1",
            novnc_bind="127.0.0.1",
            reverse_proxy_exposed=True,
        )
    settings = authenticated_settings(
        api_bind="127.0.0.1",
        reverse_proxy_exposed=True,
    )
    assert settings.authenticated_mode


def test_test_origins_require_explicit_strict_local_test_mode() -> None:
    with pytest.raises(AuthConfigurationError):
        build_auth_settings(
            api_bind="127.0.0.1",
            novnc_bind="127.0.0.1",
            test_origins=["http://127.0.0.1:8765"],
        )
    with pytest.raises(AuthConfigurationError):
        authenticated_settings(test_mode=True, test_origins=["http://127.0.0.1:8765"])
    settings = build_auth_settings(
        api_bind="127.0.0.1",
        novnc_bind="127.0.0.1",
        test_mode=True,
        test_origins=["http://127.0.0.1:8765"],
    )
    assert settings.test_mode


@pytest.mark.parametrize(
    "override",
    [
        {"remote_mode": "true"},
        {"reverse_proxy_exposed": 1},
        {"test_mode": "0"},
        {"test_origins": "http://127.0.0.1:8765"},
    ],
)
def test_ambiguous_configuration_types_fail_closed(override: dict[str, object]) -> None:
    values: dict[str, object] = {
        "api_bind": "127.0.0.1",
        "novnc_bind": "127.0.0.1",
    }
    values.update(override)
    with pytest.raises(AuthConfigurationError):
        build_auth_settings(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "Basic abc",
        f"Bearer  {OBSERVER_CANARY}",
        f"Bearer\t{OBSERVER_CANARY}",
        f"Bearer {OBSERVER_CANARY} trailing",
        f"Bearer {OBSERVER_CANARY}\r\nX-Evil: yes",
        f"Token {OBSERVER_CANARY}",
    ],
)
def test_only_strict_bearer_authorization_is_parsed(header: str | None) -> None:
    with pytest.raises(AuthenticationRequired):
        parse_bearer_authorization(header)
    with pytest.raises(AuthenticationRequired):
        authorize(authenticated_settings(), header, Authority.OBSERVER)


def test_bearer_scheme_is_http_case_insensitive() -> None:
    assert parse_bearer_authorization(f"bearer {OBSERVER_CANARY}") == OBSERVER_CANARY


def test_observer_can_read_but_cannot_mutate() -> None:
    settings = authenticated_settings()
    header = f"Bearer {OBSERVER_CANARY}"
    assert authorize(settings, header, Authority.OBSERVER) is Authority.OBSERVER
    for operation in ("create", "navigate", "interact", "end"):
        with pytest.raises(AuthorityForbidden, match="Controller authority") as raised:
            authorize(settings, header, Authority.CONTROLLER)
        assert operation not in str(raised.value)


def test_controller_satisfies_observer_and_controller_authority() -> None:
    settings = authenticated_settings()
    header = f"Bearer {CONTROLLER_CANARY}"
    assert authorize(settings, header, Authority.OBSERVER) is Authority.CONTROLLER
    assert authorize(settings, header, Authority.CONTROLLER) is Authority.CONTROLLER


def test_validated_auth_settings_cannot_be_mutated_into_tokenless_mode() -> None:
    settings = authenticated_settings()
    with pytest.raises(AttributeError, match="immutable"):
        settings.remote_mode = False
    with pytest.raises(AttributeError, match="immutable"):
        settings._controller_digest = None  # type: ignore[attr-defined]


def test_invalid_token_is_sanitized_auth_required() -> None:
    with pytest.raises(AuthError) as raised:
        authorize(
            authenticated_settings(),
            "Bearer Invalid-Canary-Token-0123456789-Value!",
            Authority.OBSERVER,
        )
    assert raised.value.code == "AUTH_REQUIRED"
    assert raised.value.status_code == 401


def test_authorization_always_uses_two_fixed_size_constant_time_comparisons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = authenticated_settings()
    calls: list[tuple[bytes, bytes]] = []
    original: Callable[[bytes, bytes], bool] = hmac.compare_digest

    def recording_compare(left: bytes, right: bytes) -> bool:
        calls.append((left, right))
        return original(left, right)

    monkeypatch.setattr("aether_browser.auth.hmac.compare_digest", recording_compare)
    authorize(settings, f"Bearer {OBSERVER_CANARY}", Authority.OBSERVER)
    assert len(calls) == 2
    assert all(len(left) == len(right) == 32 for left, right in calls)


def test_tokens_never_appear_in_repr_errors_settings_or_dependency_responses() -> None:
    settings = authenticated_settings()
    rendered = repr(settings) + repr(settings.to_loggable_dict())
    assert OBSERVER_CANARY not in rendered
    assert CONTROLLER_CANARY not in rendered

    with pytest.raises(AuthConfigurationError) as config_error:
        authenticated_settings(controller_token=OBSERVER_CANARY)
    with pytest.raises(AuthenticationRequired) as auth_error:
        authorize(settings, f"Bearer {OBSERVER_CANARY}x", Authority.OBSERVER)

    dependency = require_authority(settings, Authority.CONTROLLER)
    with pytest.raises(HTTPException) as response_error:
        dependency(f"Bearer {OBSERVER_CANARY}")

    all_output = " ".join(
        (
            str(config_error.value),
            repr(config_error.value),
            str(auth_error.value),
            repr(auth_error.value),
            repr(response_error.value.detail),
        )
    )
    assert OBSERVER_CANARY not in all_output
    assert CONTROLLER_CANARY not in all_output
    assert response_error.value.status_code == 403
