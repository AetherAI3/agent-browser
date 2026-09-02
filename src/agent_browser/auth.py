"""Fail-closed authority checks for the Agent Browser API.

The module deliberately has no dependency on :mod:`agent_browser.main`.  The
pure ``authorize`` function can be called directly by a runtime adapter, while
``require_authority`` is a small FastAPI dependency factory.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
from enum import StrEnum
from typing import Annotated, Any, Final

from fastapi import Header, HTTPException

MIN_TOKEN_LENGTH: Final = 32
MAX_TOKEN_LENGTH: Final = 4_096
MAX_AUTHORIZATION_HEADER_LENGTH: Final = 8_192

_BEARER_RE = re.compile(r"(?i:Bearer) ([\x21-\x7e]{1,4096})\Z")
_DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_REDACTED: Final = "<redacted>"


class Authority(StrEnum):
    """The two public API authority levels."""

    OBSERVER = "observer"
    CONTROLLER = "controller"


class AuthError(RuntimeError):
    """A sanitized authentication or authorization refusal."""

    __slots__ = ("code", "status_code")

    def __init__(self, code: str, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code

    @property
    def safe_message(self) -> str:
        return str(self)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(code={self.code!r}, "
            f"status_code={self.status_code!r}, message={str(self)!r})"
        )


class AuthConfigurationError(AuthError):
    """The service cannot start because its authority boundary is unsafe."""

    def __init__(self) -> None:
        super().__init__(
            "AUTH_CONFIGURATION_INVALID",
            "Authentication configuration is not permitted.",
            status_code=500,
        )


class AuthenticationRequired(AuthError):
    """No acceptable controller or observer credential was supplied."""

    def __init__(self) -> None:
        super().__init__("AUTH_REQUIRED", "Bearer authentication is required.", status_code=401)


class AuthorityForbidden(AuthError):
    """The authenticated principal lacks the requested authority."""

    def __init__(self) -> None:
        super().__init__("AUTH_FORBIDDEN", "Controller authority is required.", status_code=403)


def _configuration_error() -> AuthConfigurationError:
    # Keep every startup error intentionally generic.  In particular, never
    # interpolate token values or caller-controlled configuration.
    return AuthConfigurationError()


def is_loopback_bind(bind: str) -> bool:
    """Return whether *bind* is an unambiguous numeric loopback address."""

    if not isinstance(bind, str) or not bind or bind != bind.strip() or "%" in bind:
        return False
    try:
        address = ipaddress.ip_address(bind)
    except ValueError:
        return False
    return address.is_loopback and getattr(address, "ipv4_mapped", None) is None


def _normalize_effective_host(host: str) -> tuple[str, bool]:
    if (
        not isinstance(host, str)
        or not host
        or host != host.strip()
        or len(host) > 253
        or not host.isascii()
        or any(character in host for character in ("/", "\\", "@", "?", "#", "%"))
    ):
        raise _configuration_error()

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        normalized = host.casefold()
        if normalized.endswith(".") or re.fullmatch(r"[0-9.]+", normalized):
            raise _configuration_error() from None
        if any(_DNS_LABEL_RE.fullmatch(label) is None for label in normalized.split(".")):
            raise _configuration_error() from None
        is_loopback = normalized == "localhost" or normalized.endswith(".localhost")
        return normalized, is_loopback

    if (
        getattr(address, "ipv4_mapped", None) is not None
        or address.is_unspecified
        or address.is_multicast
    ):
        raise _configuration_error()
    return address.compressed.casefold(), address.is_loopback


def _parse_exact_loopback_network(
    value: str | None,
) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    if not isinstance(value, str) or not value or value != value.strip():
        raise _configuration_error()
    try:
        network = ipaddress.ip_network(value, strict=True)
    except ValueError:
        raise _configuration_error() from None
    address = network.network_address
    if (
        network.prefixlen != network.max_prefixlen
        or not address.is_loopback
        or getattr(address, "ipv4_mapped", None) is not None
        or value != network.with_prefixlen
    ):
        raise _configuration_error()
    return network


def _validate_strong_token(token: str | None) -> None:
    if not isinstance(token, str) or not MIN_TOKEN_LENGTH <= len(token) <= MAX_TOKEN_LENGTH:
        raise _configuration_error()
    if not token.isascii() or not token.isprintable() or any(char.isspace() for char in token):
        raise _configuration_error()

    diversity = sum(
        (
            any(char.islower() for char in token),
            any(char.isupper() for char in token),
            any(char.isdigit() for char in token),
            any(not char.isalnum() for char in token),
        )
    )
    if diversity < 3:
        raise _configuration_error()


def _token_digest(token: str) -> bytes:
    # Settings retain only fixed-size digests.  This both makes comparisons
    # length-independent and prevents accidental serialization of raw tokens.
    return hashlib.sha256(token.encode("ascii")).digest()


class AuthSettings:
    """Validated, redacted authority settings.

    Raw credentials are accepted only during construction and are immediately
    reduced to SHA-256 digests.  They therefore cannot appear in ``repr()``,
    ``vars()``, settings dictionaries, exceptions, or API responses.
    """

    __slots__ = (
        "_controller_digest",
        "_observer_digest",
        "_sealed",
        "_trusted_proxy_network",
        "api_bind",
        "api_host",
        "novnc_bind",
        "novnc_host",
        "remote_mode",
        "reverse_proxy_exposed",
        "test_mode",
        "test_origins",
        "trusted_proxy_cidr",
        "trusted_proxy_scheme",
    )

    def __init__(
        self,
        *,
        api_bind: str,
        novnc_bind: str,
        api_host: str | None = None,
        novnc_host: str | None = None,
        remote_mode: bool = False,
        reverse_proxy_exposed: bool = False,
        trusted_proxy_cidr: str | None = None,
        trusted_proxy_scheme: str | None = None,
        observer_token: str | None = None,
        controller_token: str | None = None,
        test_mode: bool = False,
        test_origins: tuple[str, ...] | list[str] = (),
    ) -> None:
        if any(type(flag) is not bool for flag in (remote_mode, reverse_proxy_exposed, test_mode)):
            raise _configuration_error()
        if isinstance(test_origins, (str, bytes)):
            raise _configuration_error()
        try:
            origins = tuple(test_origins)
        except TypeError:
            raise _configuration_error() from None
        if any(not isinstance(origin, str) for origin in origins):
            raise _configuration_error()
        effective_api_host = api_bind if api_host is None else api_host
        effective_novnc_host = novnc_bind if novnc_host is None else novnc_host
        if not all(
            is_loopback_bind(value) for value in (api_bind, novnc_bind, effective_novnc_host)
        ):
            raise _configuration_error()

        proxy_configured = any(
            (
                remote_mode,
                reverse_proxy_exposed,
                trusted_proxy_cidr is not None,
                trusted_proxy_scheme is not None,
            )
        )
        if proxy_configured:
            if not (
                remote_mode
                and reverse_proxy_exposed
                and trusted_proxy_cidr is not None
                and trusted_proxy_scheme == "https"
            ):
                raise _configuration_error()
            normalized_api_host, api_host_is_loopback = _normalize_effective_host(
                effective_api_host
            )
            if api_host_is_loopback:
                raise _configuration_error()
            trusted_proxy_network = _parse_exact_loopback_network(trusted_proxy_cidr)
        else:
            if not is_loopback_bind(effective_api_host):
                raise _configuration_error()
            normalized_api_host = ipaddress.ip_address(effective_api_host).compressed.casefold()
            trusted_proxy_network = None

        if origins and not test_mode:
            raise _configuration_error()
        if test_mode and proxy_configured:
            raise _configuration_error()

        has_observer = bool(observer_token)
        has_controller = bool(controller_token)
        authenticated_mode = has_observer or has_controller
        remote_boundary = proxy_configured

        if authenticated_mode or remote_boundary:
            if not observer_token or not controller_token:
                raise _configuration_error()
            _validate_strong_token(observer_token)
            _validate_strong_token(controller_token)
            observer_digest = _token_digest(observer_token)
            controller_digest = _token_digest(controller_token)
            if hmac.compare_digest(observer_digest, controller_digest):
                raise _configuration_error()
        else:
            observer_digest = None
            controller_digest = None

        self.api_bind = ipaddress.ip_address(api_bind).compressed.casefold()
        self.api_host = normalized_api_host
        self.novnc_bind = ipaddress.ip_address(novnc_bind).compressed.casefold()
        self.novnc_host = ipaddress.ip_address(effective_novnc_host).compressed.casefold()
        self.remote_mode = remote_mode
        self.reverse_proxy_exposed = reverse_proxy_exposed
        self.trusted_proxy_cidr = (
            trusted_proxy_network.with_prefixlen if trusted_proxy_network is not None else None
        )
        self.trusted_proxy_scheme = trusted_proxy_scheme
        self.test_mode = test_mode
        self.test_origins = origins
        self._observer_digest = observer_digest
        self._controller_digest = controller_digest
        self._trusted_proxy_network = trusted_proxy_network
        self._sealed = True

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_sealed", False):
            raise AttributeError("AuthSettings are immutable")
        object.__setattr__(self, name, value)

    @property
    def tokenless_local_mode(self) -> bool:
        return self._observer_digest is None and self._controller_digest is None

    @property
    def authenticated_mode(self) -> bool:
        return not self.tokenless_local_mode

    @property
    def proxy_mode(self) -> bool:
        return self.remote_mode and self.reverse_proxy_exposed

    def trusts_proxy_peer(self, peer: str) -> bool:
        network = self._trusted_proxy_network
        if network is None or not isinstance(peer, str) or "%" in peer:
            return False
        try:
            address = ipaddress.ip_address(peer)
        except ValueError:
            return False
        return getattr(address, "ipv4_mapped", None) is None and address in network

    def to_loggable_dict(self) -> dict[str, object]:
        """Return configuration metadata with no credential material."""

        return {
            "api_bind": self.api_bind,
            "api_host": self.api_host,
            "novnc_bind": self.novnc_bind,
            "novnc_host": self.novnc_host,
            "remote_mode": self.remote_mode,
            "reverse_proxy_exposed": self.reverse_proxy_exposed,
            "trusted_proxy_cidr": self.trusted_proxy_cidr,
            "trusted_proxy_scheme": self.trusted_proxy_scheme,
            "test_mode": self.test_mode,
            "test_origins": self.test_origins,
            "authentication": "bearer" if self.authenticated_mode else "tokenless-loopback",
        }

    def __repr__(self) -> str:
        mode = "bearer" if self.authenticated_mode else "tokenless-loopback"
        return (
            "AuthSettings("
            f"api_bind={self.api_bind!r}, api_host={self.api_host!r}, "
            f"novnc_bind={self.novnc_bind!r}, novnc_host={self.novnc_host!r}, "
            f"remote_mode={self.remote_mode!r}, "
            f"reverse_proxy_exposed={self.reverse_proxy_exposed!r}, "
            f"trusted_proxy_cidr={self.trusted_proxy_cidr!r}, "
            f"trusted_proxy_scheme={self.trusted_proxy_scheme!r}, "
            f"test_mode={self.test_mode!r}, test_origins={self.test_origins!r}, "
            f"authentication={mode!r}, observer_token={_REDACTED!r}, "
            f"controller_token={_REDACTED!r})"
        )


# A compatibility-friendly name for callers that prefer "config" terminology.
AuthConfig = AuthSettings


def build_auth_settings(
    *,
    api_bind: str,
    novnc_bind: str,
    api_host: str | None = None,
    novnc_host: str | None = None,
    remote_mode: bool = False,
    reverse_proxy_exposed: bool = False,
    trusted_proxy_cidr: str | None = None,
    trusted_proxy_scheme: str | None = None,
    observer_token: str | None = None,
    controller_token: str | None = None,
    test_mode: bool = False,
    test_origins: tuple[str, ...] | list[str] = (),
) -> AuthSettings:
    """Validate startup authority configuration and return redacted settings."""

    return AuthSettings(
        api_bind=api_bind,
        api_host=api_host,
        novnc_bind=novnc_bind,
        novnc_host=novnc_host,
        remote_mode=remote_mode,
        reverse_proxy_exposed=reverse_proxy_exposed,
        trusted_proxy_cidr=trusted_proxy_cidr,
        trusted_proxy_scheme=trusted_proxy_scheme,
        observer_token=observer_token,
        controller_token=controller_token,
        test_mode=test_mode,
        test_origins=test_origins,
    )


validate_auth_config = build_auth_settings


def parse_bearer_authorization(authorization: str | None) -> str:
    """Parse one strict ``Authorization: Bearer`` value.

    The scheme is case-insensitive as required by HTTP, while whitespace and
    token syntax are intentionally strict to avoid ambiguous proxy parsing.
    """

    if (
        authorization is None
        or not isinstance(authorization, str)
        or len(authorization) > MAX_AUTHORIZATION_HEADER_LENGTH
    ):
        raise AuthenticationRequired()
    match = _BEARER_RE.fullmatch(authorization)
    if match is None:
        raise AuthenticationRequired()
    return match.group(1)


def authorize(
    settings: AuthSettings,
    authorization: str | None,
    required: Authority = Authority.OBSERVER,
) -> Authority:
    """Authorize a request for *required* authority.

    A controller credential satisfies observer reads.  An observer credential
    is deliberately distinguished from an invalid credential so integrations
    can return a sanitized 403 for attempted mutation.
    """

    if not isinstance(required, Authority):
        raise TypeError("required must be an Authority")
    if settings.tokenless_local_mode:
        return Authority.CONTROLLER

    candidate = parse_bearer_authorization(authorization)
    try:
        candidate_digest = _token_digest(candidate)
    except (UnicodeEncodeError, ValueError):
        raise AuthenticationRequired() from None

    observer_digest = settings._observer_digest
    controller_digest = settings._controller_digest
    if observer_digest is None or controller_digest is None:
        raise AuthenticationRequired()

    # Always perform both fixed-size comparisons before making an authority
    # decision.  This avoids role-dependent comparison short-circuiting.
    observer_matches = hmac.compare_digest(candidate_digest, observer_digest)
    controller_matches = hmac.compare_digest(candidate_digest, controller_digest)

    if controller_matches:
        return Authority.CONTROLLER
    if observer_matches:
        if required is Authority.CONTROLLER:
            raise AuthorityForbidden()
        return Authority.OBSERVER
    raise AuthenticationRequired()


def require_authority(settings: AuthSettings, required: Authority) -> Any:
    """Create a FastAPI dependency for one authority level.

    The returned dependency exposes only sanitized error codes/messages and can
    be installed by ``Depends`` without this module importing the application.
    """

    def dependency(
        authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    ) -> Authority:
        try:
            return authorize(settings, authorization, required)
        except AuthError as exc:
            headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
            raise HTTPException(
                status_code=exc.status_code,
                detail={"code": exc.code, "message": exc.safe_message},
                headers=headers,
            ) from None

    return dependency


__all__ = [
    "MAX_AUTHORIZATION_HEADER_LENGTH",
    "MAX_TOKEN_LENGTH",
    "MIN_TOKEN_LENGTH",
    "AuthConfig",
    "AuthConfigurationError",
    "AuthError",
    "AuthSettings",
    "AuthenticationRequired",
    "Authority",
    "AuthorityForbidden",
    "authorize",
    "build_auth_settings",
    "is_loopback_bind",
    "parse_bearer_authorization",
    "require_authority",
    "validate_auth_config",
]
