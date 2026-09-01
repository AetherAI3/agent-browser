"""Fail-closed authority checks for the Aether Browser API.

The module deliberately has no dependency on :mod:`aether_browser.main`.  The
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


def _extract_bind_host(bind: str) -> str:
    if not isinstance(bind, str) or not bind or bind != bind.strip():
        raise _configuration_error()
    if any(ord(character) < 0x21 or ord(character) == 0x7F for character in bind):
        raise _configuration_error()
    if any(character in bind for character in ("/", "\\", "@", "?", "#")):
        raise _configuration_error()

    if bind.startswith("["):
        match = re.fullmatch(r"\[([^\]]+)](?::([0-9]{1,5}))?", bind)
        if match is None:
            raise _configuration_error()
        host, port_text = match.groups()
        if port_text is not None and not 1 <= int(port_text) <= 65_535:
            raise _configuration_error()
        return host

    # A single colon can unambiguously delimit an IPv4/hostname port.  Two or
    # more colons are treated as a raw IPv6 literal.
    if bind.count(":") == 1:
        host, separator, port_text = bind.rpartition(":")
        if separator and port_text.isdecimal():
            if not host or not 1 <= int(port_text) <= 65_535:
                raise _configuration_error()
            return host
    return bind


def is_loopback_bind(bind: str) -> bool:
    """Return whether *bind* names a strict loopback listener.

    ``localhost`` is accepted because it is the conventional explicit local
    bind name.  Other hostnames are never resolved here: startup validation
    must not turn a DNS or hosts-file lookup into an authority decision.
    """

    try:
        host = _extract_bind_host(bind).lower().removesuffix(".")
    except AuthConfigurationError:
        return False
    if host == "localhost":
        return True
    if "%" in host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback and getattr(address, "ipv4_mapped", None) is None


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
        "api_bind",
        "novnc_bind",
        "remote_mode",
        "reverse_proxy_exposed",
        "test_mode",
        "test_origins",
    )

    def __init__(
        self,
        *,
        api_bind: str,
        novnc_bind: str,
        remote_mode: bool = False,
        reverse_proxy_exposed: bool = False,
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
        api_is_loopback = is_loopback_bind(api_bind)
        novnc_is_loopback = is_loopback_bind(novnc_bind)

        if not novnc_is_loopback:
            raise _configuration_error()
        if origins and not test_mode:
            raise _configuration_error()
        if test_mode and (not api_is_loopback or remote_mode or reverse_proxy_exposed):
            raise _configuration_error()
        if not api_is_loopback and not remote_mode:
            raise _configuration_error()
        if reverse_proxy_exposed and not remote_mode:
            raise _configuration_error()

        has_observer = observer_token is not None and observer_token != ""
        has_controller = controller_token is not None and controller_token != ""
        authenticated_mode = has_observer or has_controller
        remote_boundary = remote_mode or reverse_proxy_exposed or not api_is_loopback

        if authenticated_mode or remote_boundary:
            if not has_observer or not has_controller:
                raise _configuration_error()
            _validate_strong_token(observer_token)
            _validate_strong_token(controller_token)
            assert observer_token is not None
            assert controller_token is not None
            observer_digest = _token_digest(observer_token)
            controller_digest = _token_digest(controller_token)
            if hmac.compare_digest(observer_digest, controller_digest):
                raise _configuration_error()
        else:
            observer_digest = None
            controller_digest = None

        self.api_bind = api_bind
        self.novnc_bind = novnc_bind
        self.remote_mode = remote_mode
        self.reverse_proxy_exposed = reverse_proxy_exposed
        self.test_mode = test_mode
        self.test_origins = origins
        self._observer_digest = observer_digest
        self._controller_digest = controller_digest
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

    def to_loggable_dict(self) -> dict[str, object]:
        """Return configuration metadata with no credential material."""

        return {
            "api_bind": self.api_bind,
            "novnc_bind": self.novnc_bind,
            "remote_mode": self.remote_mode,
            "reverse_proxy_exposed": self.reverse_proxy_exposed,
            "test_mode": self.test_mode,
            "test_origins": self.test_origins,
            "authentication": "bearer" if self.authenticated_mode else "tokenless-loopback",
        }

    def __repr__(self) -> str:
        mode = "bearer" if self.authenticated_mode else "tokenless-loopback"
        return (
            "AuthSettings("
            f"api_bind={self.api_bind!r}, novnc_bind={self.novnc_bind!r}, "
            f"remote_mode={self.remote_mode!r}, "
            f"reverse_proxy_exposed={self.reverse_proxy_exposed!r}, "
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
    remote_mode: bool = False,
    reverse_proxy_exposed: bool = False,
    observer_token: str | None = None,
    controller_token: str | None = None,
    test_mode: bool = False,
    test_origins: tuple[str, ...] | list[str] = (),
) -> AuthSettings:
    """Validate startup authority configuration and return redacted settings."""

    return AuthSettings(
        api_bind=api_bind,
        novnc_bind=novnc_bind,
        remote_mode=remote_mode,
        reverse_proxy_exposed=reverse_proxy_exposed,
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
    assert observer_digest is not None
    assert controller_digest is not None

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
