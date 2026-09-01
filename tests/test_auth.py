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
        "api_bind": "127.0.0.1",
        "api_host": "browser.example",
        "novnc_bind": "127.0.0.1",
        "novnc_host": "127.0.0.1",
        "remote_mode": True,
        "reverse_proxy_exposed": True,
        "trusted_proxy_cidr": "127.0.0.1/32",
        "trusted_proxy_scheme": "https",
        "observer_token": OBSERVER_CANARY,
        "controller_token": CONTROLLER_CANARY,
    }
    values.update(overrides)
    return build_auth_settings(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "bind",
    ["127.0.0.1", "127.0.0.2", "::1"],
)
def test_strict_loopback_local_mode_is_tokenless(bind: str) -> None:
    settings = build_auth_settings(api_bind=bind, novnc_bind="127.0.0.1")
    assert settings.tokenless_local_mode
    assert authorize(settings, None, Authority.CONTROLLER) is Authority.CONTROLLER
    assert is_loopback_bind(bind)


@pytest.mark.parametrize(
    "bind",
    ["0.0.0.0", "192.0.2.10", "example.com", "::", "localhost", "127.0.0.1:8092"],
)
def test_actual_api_bind_is_always_numeric_loopback(bind: str) -> None:
    with pytest.raises(AuthConfigurationError):
        authenticated_settings(api_bind=bind)


def test_remote_mode_requires_both_distinct_strong_tokens() -> None:
    with pytest.raises(AuthConfigurationError):
        authenticated_settings(observer_token=None, controller_token=None)
    with pytest.raises(AuthConfigurationError):
        authenticated_settings(controller_token=None)
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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("novnc_bind", "0.0.0.0"),
        ("novnc_bind", "192.168.1.10"),
        ("novnc_bind", "example.com"),
        ("novnc_bind", "localhost"),
        ("novnc_host", "::"),
        ("novnc_host", "localhost"),
    ],
)
def test_novnc_is_always_numeric_loopback_only(field: str, value: str) -> None:
    with pytest.raises(AuthConfigurationError):
        authenticated_settings(**{field: value})


def test_complete_tls_reverse_proxy_tuple_is_authenticated() -> None:
    settings = authenticated_settings()
    assert settings.authenticated_mode
    assert settings.proxy_mode
    assert settings.trusts_proxy_peer("127.0.0.1")
    assert not settings.trusts_proxy_peer("127.0.0.2")


def test_exact_ipv6_loopback_proxy_peer_is_supported() -> None:
    settings = authenticated_settings(
        api_bind="::1",
        novnc_bind="::1",
        novnc_host="::1",
        trusted_proxy_cidr="::1/128",
    )
    assert settings.trusts_proxy_peer("::1")
    assert not settings.trusts_proxy_peer("127.0.0.1")


@pytest.mark.parametrize(
    "overrides",
    [
        {"remote_mode": False},
        {"reverse_proxy_exposed": False},
        {"trusted_proxy_cidr": None},
        {"trusted_proxy_scheme": None},
        {"trusted_proxy_scheme": "http"},
        {"trusted_proxy_scheme": "HTTPS"},
        {"api_host": "127.0.0.1"},
        {"api_host": "localhost"},
        {"api_host": "0.0.0.0"},
    ],
)
def test_partial_or_unsafe_proxy_tuple_fails_closed(overrides: dict[str, object]) -> None:
    with pytest.raises(AuthConfigurationError):
        authenticated_settings(**overrides)


@pytest.mark.parametrize(
    "cidr",
    ["127.0.0.0/8", "::1/64", "::ffff:127.0.0.1/128", "bad"],
)
def test_trusted_proxy_cidr_is_one_exact_loopback_peer(cidr: str) -> None:
    with pytest.raises(AuthConfigurationError):
        authenticated_settings(trusted_proxy_cidr=cidr)


@pytest.mark.parametrize(
    "overrides",
    [
        {"trusted_proxy_cidr": "127.0.0.1/32"},
        {"trusted_proxy_scheme": "https"},
        {"remote_mode": True},
        {"reverse_proxy_exposed": True},
    ],
)
def test_local_mode_rejects_partial_proxy_configuration(overrides: dict[str, object]) -> None:
    values: dict[str, object] = {
        "api_bind": "127.0.0.1",
        "novnc_bind": "127.0.0.1",
    }
    values.update(overrides)
    with pytest.raises(AuthConfigurationError):
        build_auth_settings(**values)  # type: ignore[arg-type]


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
        {"trusted_proxy_cidr": 1},
        {"trusted_proxy_scheme": True},
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
