"""SSRF-resistant top-level navigation policy for Aether Browser."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import ipaddress
import math
import re
import socket
import unicodedata
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, cast
from urllib.parse import unquote_to_bytes, urlsplit, urlunsplit

MAX_URL_LENGTH: Final = 2_048
DEFAULT_RESOLUTION_TIMEOUT_SECONDS: Final = 1.0
DEFAULT_MAX_DNS_ANSWERS: Final = 16
DEFAULT_MAX_RESOLUTIONS: Final = 16
DEFAULT_MAX_REDIRECTS: Final = 10
DEFAULT_MAX_TOP_LEVEL_NAVIGATIONS: Final = 32

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
ResolverAnswer = str | IPAddress
ResolverResult = Iterable[ResolverAnswer]
Resolver = Callable[[str], ResolverResult | Awaitable[ResolverResult]]

_DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_NUMERIC_LABEL_RE = re.compile(r"(?:0[xX][0-9a-fA-F]+|[0-9]+)\Z")
_PERCENT_ESCAPE_RE = re.compile(r"[0-9a-fA-F]{2}\Z")

_PROHIBITED_HOSTS: Final = frozenset(
    {
        "instance-data",
        "instance-data.ec2.internal",
        "ip6-localhost",
        "ip6-loopback",
        "localtest.me",
        "localhost",
        "localhost.localdomain",
        "localhost4",
        "localhost4.localdomain4",
        "localhost6",
        "localhost6.localdomain6",
        "lvh.me",
        "metadata",
        "metadata.google",
        "metadata.google.internal",
        "metadata.internal",
    }
)
_PROHIBITED_SUFFIXES: Final = (
    ".home",
    ".home.arpa",
    ".example",
    ".internal",
    ".invalid",
    ".lan",
    ".local",
    ".localdomain",
    ".localtest.me",
    ".localhost",
    ".lvh.me",
    ".test",
)
_PROHIBITED_IPV6_NETWORKS: Final = (
    ipaddress.IPv6Network("64:ff9b::/96"),  # well-known NAT64 embeds an IPv4 target
    ipaddress.IPv6Network("64:ff9b:1::/48"),
    ipaddress.IPv6Network("2002::/16"),  # 6to4 embeds an IPv4 target
)


class PolicyReason(StrEnum):
    """Internal, sanitized reason categories safe for metrics and tests."""

    MALFORMED_URL = "malformed_url"
    UNSUPPORTED_SCHEME = "unsupported_scheme"
    EMBEDDED_CREDENTIALS = "embedded_credentials"
    PROHIBITED_DESTINATION = "prohibited_destination"
    RESOLUTION_FAILED = "resolution_failed"
    DNS_REBINDING = "dns_rebinding"
    REDIRECT_LIMIT = "redirect_limit"
    NAVIGATION_LIMIT = "navigation_limit"


class PolicyError(RuntimeError):
    """A typed navigation refusal that never contains the target or its IPs."""

    __slots__ = ("code", "reason", "status_code")

    def __init__(self, code: str, reason: PolicyReason, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.reason = reason
        self.status_code = 400

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(code={self.code!r}, reason={self.reason.value!r}, "
            f"status_code={self.status_code!r}, message={str(self)!r})"
        )


class PolicyConfigurationError(ValueError):
    """Fail-closed startup refusal for an unsafe navigation configuration."""

    def __init__(self) -> None:
        super().__init__("Navigation policy configuration is not permitted.")


def _invalid(reason: PolicyReason = PolicyReason.MALFORMED_URL) -> PolicyError:
    return PolicyError("INVALID_URL", reason, "The navigation URL is invalid.")


def _blocked(reason: PolicyReason = PolicyReason.PROHIBITED_DESTINATION) -> PolicyError:
    return PolicyError("DESTINATION_BLOCKED", reason, "The navigation target is not permitted.")


@dataclass(frozen=True, slots=True)
class _ParsedTarget:
    url: str
    origin: str
    scheme: str
    hostname: str
    port: int
    literal_address: IPAddress | None = field(repr=False)


@dataclass(frozen=True, slots=True)
class ValidatedUrl:
    """A canonical URL approved for one top-level navigation check."""

    url: str
    origin: str
    scheme: str
    hostname: str
    port: int
    resolved_address_count: int
    _address_fingerprints: frozenset[bytes] = field(repr=False, compare=False)
    _used_dns: bool = field(repr=False, compare=False)


def _contains_control_encoding(url: str) -> bool:
    if any(unicodedata.category(character) == "Cc" for character in url):
        return True
    for index, character in enumerate(url):
        if character == "%" and _PERCENT_ESCAPE_RE.fullmatch(url[index + 1 : index + 3]) is None:
            return True
    try:
        decoded = unquote_to_bytes(url)
    except (UnicodeEncodeError, ValueError):
        return True
    return any(byte < 0x20 or byte == 0x7F for byte in decoded)


def _normalize_hostname(hostname: str) -> tuple[str, IPAddress | None]:
    if not hostname or "%" in hostname or "*" in hostname:
        raise _invalid()
    hostname = hostname.lower()
    if hostname.endswith("."):
        hostname = hostname[:-1]
    if not hostname or hostname.endswith("."):
        raise _invalid()

    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        return literal.compressed.lower(), literal

    labels = hostname.split(".")
    if all(_NUMERIC_LABEL_RE.fullmatch(label) is not None for label in labels):
        # Reject the integer, shortened, octal-like, hexadecimal, and mixed
        # numeric forms that browsers/socket APIs may reinterpret as IPv4.
        raise _invalid()

    try:
        ascii_hostname = hostname.encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        raise _invalid() from None
    try:
        ipaddress.ip_address(ascii_hostname)
    except ValueError:
        pass
    else:
        # IDNA mappings such as full-width or circled digits must not turn an
        # apparently named host into a numeric address after the first check.
        raise _invalid()
    if all(_NUMERIC_LABEL_RE.fullmatch(label) is not None for label in ascii_hostname.split(".")):
        raise _invalid()
    if len(ascii_hostname) > 253:
        raise _invalid()
    ascii_labels = ascii_hostname.split(".")
    if any(_DNS_LABEL_RE.fullmatch(label) is None for label in ascii_labels):
        raise _invalid()
    return ascii_hostname, None


def _parse_target(url: str, *, max_url_length: int, origin_only: bool = False) -> _ParsedTarget:
    if (
        not isinstance(url, str)
        or not url
        or len(url) > max_url_length
        or url != url.strip()
        or "\\" in url
        or "#" in url
        or _contains_control_encoding(url)
    ):
        raise _invalid()

    try:
        parts = urlsplit(url)
    except (TypeError, ValueError, UnicodeError):
        raise _invalid() from None
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise _invalid(PolicyReason.UNSUPPORTED_SCHEME)
    if not parts.netloc or parts.hostname is None:
        raise _invalid()
    if parts.username is not None or parts.password is not None:
        raise _invalid(PolicyReason.EMBEDDED_CREDENTIALS)
    if origin_only and (parts.path not in {"", "/"} or parts.query or parts.fragment):
        raise PolicyConfigurationError()

    try:
        port = parts.port
    except ValueError:
        raise _invalid() from None
    default_port = 80 if scheme == "http" else 443
    port = default_port if port is None else port
    if not 1 <= port <= 65_535:
        raise _invalid()

    hostname, literal = _normalize_hostname(parts.hostname)
    if parts.netloc.startswith("[") and literal is None:
        raise _invalid()
    display_host = f"[{hostname}]" if literal is not None and literal.version == 6 else hostname
    port_suffix = "" if port == default_port else f":{port}"
    netloc = f"{display_host}{port_suffix}"
    origin = f"{scheme}://{netloc}"
    normalized_url = urlunsplit((scheme, netloc, parts.path, parts.query, ""))
    return _ParsedTarget(
        url=normalized_url,
        origin=origin,
        scheme=scheme,
        hostname=hostname,
        port=port,
        literal_address=literal,
    )


def _hostname_is_prohibited(hostname: str) -> bool:
    if hostname in _PROHIBITED_HOSTS or "." not in hostname:
        return True
    return any(hostname.endswith(suffix) for suffix in _PROHIBITED_SUFFIXES)


def _address_is_prohibited(address: IPAddress) -> bool:
    # ``is_global`` closes gaps such as documentation, benchmarking, carrier
    # grade NAT, reserved, and future-use ranges.  Explicit properties make the
    # intended policy stable and reviewable across Python data-table changes.
    if (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_multicast
        or address.is_unspecified
        or address.is_reserved
        or not address.is_global
    ):
        return True

    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None or address.sixtofour is not None or address.teredo:
            return True
        if any(address in network for network in _PROHIBITED_IPV6_NETWORKS):
            return True
    return False


def _fingerprint(address: IPAddress) -> bytes:
    family = b"\x04" if address.version == 4 else b"\x06"
    return family + address.packed


async def _system_resolver(hostname: str) -> ResolverResult:
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    return tuple(cast(str, record[4][0]) for record in records)


async def _await_resolution(
    result: ResolverResult | Awaitable[ResolverResult],
) -> ResolverResult:
    if inspect.isawaitable(result):
        return await result
    return result


class NavigationPolicy:
    """Validate one URL at every top-level browser navigation boundary."""

    __slots__ = (
        "_resolver",
        "_test_origin_set",
        "max_dns_answers",
        "max_redirects",
        "max_resolutions",
        "max_top_level_navigations",
        "max_url_length",
        "resolution_timeout_seconds",
        "test_mode",
        "test_origins",
    )

    def __init__(
        self,
        resolver: Resolver | None = None,
        *,
        test_mode: bool = False,
        test_origins: Iterable[str] = (),
        max_url_length: int = MAX_URL_LENGTH,
        resolution_timeout_seconds: float = DEFAULT_RESOLUTION_TIMEOUT_SECONDS,
        max_dns_answers: int = DEFAULT_MAX_DNS_ANSWERS,
        max_resolutions: int = DEFAULT_MAX_RESOLUTIONS,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        max_top_level_navigations: int = DEFAULT_MAX_TOP_LEVEL_NAVIGATIONS,
    ) -> None:
        if type(test_mode) is not bool or (resolver is not None and not callable(resolver)):
            raise PolicyConfigurationError()
        if isinstance(test_origins, (str, bytes)):
            raise PolicyConfigurationError()
        try:
            origins = tuple(test_origins)
        except TypeError:
            raise PolicyConfigurationError() from None
        if (
            type(max_url_length) is not int
            or type(max_dns_answers) is not int
            or type(max_resolutions) is not int
            or type(max_redirects) is not int
            or type(max_top_level_navigations) is not int
            or isinstance(resolution_timeout_seconds, bool)
            or not isinstance(resolution_timeout_seconds, (int, float))
            or not math.isfinite(resolution_timeout_seconds)
        ):
            raise PolicyConfigurationError()
        if origins and not test_mode:
            raise PolicyConfigurationError()
        if (
            not 1 <= max_url_length <= 16_384
            or not 0.01 <= resolution_timeout_seconds <= 10.0
            or not 1 <= max_dns_answers <= 64
            or not 1 <= max_resolutions <= 128
            or not 0 <= max_redirects <= 20
            or not 1 <= max_top_level_navigations <= 128
        ):
            raise PolicyConfigurationError()

        normalized_origins: list[str] = []
        for origin in origins:
            if not isinstance(origin, str) or "*" in origin:
                raise PolicyConfigurationError()
            try:
                parsed = _parse_target(origin, max_url_length=max_url_length, origin_only=True)
            except PolicyError:
                raise PolicyConfigurationError() from None
            normalized_origins.append(parsed.origin)

        self._resolver = resolver or _system_resolver
        self.test_mode = test_mode
        self.test_origins = tuple(dict.fromkeys(normalized_origins))
        self._test_origin_set = frozenset(self.test_origins)
        self.max_url_length = max_url_length
        self.resolution_timeout_seconds = resolution_timeout_seconds
        self.max_dns_answers = max_dns_answers
        self.max_resolutions = max_resolutions
        self.max_redirects = max_redirects
        self.max_top_level_navigations = max_top_level_navigations

    def to_loggable_dict(self) -> dict[str, object]:
        return {
            "test_mode": self.test_mode,
            "test_origins": self.test_origins,
            "max_url_length": self.max_url_length,
            "resolution_timeout_seconds": self.resolution_timeout_seconds,
            "max_dns_answers": self.max_dns_answers,
            "max_resolutions": self.max_resolutions,
            "max_redirects": self.max_redirects,
            "max_top_level_navigations": self.max_top_level_navigations,
        }

    async def _resolve(self, hostname: str) -> tuple[IPAddress, ...]:
        try:
            pending = self._resolver(hostname)
            answers = await asyncio.wait_for(
                _await_resolution(pending),
                timeout=self.resolution_timeout_seconds,
            )
            if isinstance(answers, (str, bytes)):
                raise TypeError

            unique: dict[bytes, IPAddress] = {}
            iteration_limit = self.max_dns_answers * 4
            for index, answer in enumerate(answers, start=1):
                if index > iteration_limit:
                    raise ValueError
                if isinstance(answer, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
                    address = answer
                elif isinstance(answer, str) and "%" not in answer:
                    address = ipaddress.ip_address(answer)
                else:
                    raise ValueError
                unique[_fingerprint(address)] = address
                if len(unique) > self.max_dns_answers:
                    raise ValueError
            if not unique:
                raise ValueError
            return tuple(unique.values())
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise _blocked(PolicyReason.RESOLUTION_FAILED) from None
        except PolicyError:
            raise
        except Exception:
            raise _blocked(PolicyReason.RESOLUTION_FAILED) from None

    async def validate_url(self, url: str) -> ValidatedUrl:
        """Parse, resolve, and approve a single top-level URL."""

        parsed = _parse_target(url, max_url_length=self.max_url_length)
        fixture_allowed = self.test_mode and parsed.origin in self._test_origin_set

        addresses: tuple[IPAddress, ...]
        if parsed.literal_address is not None:
            addresses = (parsed.literal_address,)
        else:
            if _hostname_is_prohibited(parsed.hostname) and not fixture_allowed:
                raise _blocked()
            addresses = await self._resolve(parsed.hostname)

        if not fixture_allowed and any(_address_is_prohibited(address) for address in addresses):
            raise _blocked()

        fingerprints = frozenset(_fingerprint(address) for address in addresses)
        return ValidatedUrl(
            url=parsed.url,
            origin=parsed.origin,
            scheme=parsed.scheme,
            hostname=parsed.hostname,
            port=parsed.port,
            resolved_address_count=len(fingerprints),
            _address_fingerprints=fingerprints,
            _used_dns=parsed.literal_address is None,
        )

    def new_guard(self) -> NavigationGuard:
        """Create per-browser state for redirect and DNS-rebinding checks."""

        return NavigationGuard(self)


class NavigationGuard:
    """Revalidate original, redirect, and later top-level navigations."""

    __slots__ = (
        "_approved_addresses",
        "_checks",
        "_initial_seen",
        "_policy",
        "_redirects",
        "_top_level_navigations",
        "max_redirects",
        "max_resolutions",
        "max_top_level_navigations",
    )

    def __init__(
        self,
        policy: NavigationPolicy,
        *,
        max_resolutions: int | None = None,
        max_redirects: int | None = None,
        max_top_level_navigations: int | None = None,
    ) -> None:
        if any(
            value is not None and type(value) is not int
            for value in (max_resolutions, max_redirects, max_top_level_navigations)
        ):
            raise PolicyConfigurationError()
        self._policy = policy
        self.max_resolutions = (
            policy.max_resolutions if max_resolutions is None else max_resolutions
        )
        self.max_redirects = policy.max_redirects if max_redirects is None else max_redirects
        self.max_top_level_navigations = (
            policy.max_top_level_navigations
            if max_top_level_navigations is None
            else max_top_level_navigations
        )
        if (
            not 1 <= self.max_resolutions <= 128
            or not 0 <= self.max_redirects <= 20
            or not 1 <= self.max_top_level_navigations <= 128
        ):
            raise PolicyConfigurationError()
        self._approved_addresses: dict[str, frozenset[bytes]] = {}
        self._checks = 0
        self._redirects = 0
        self._top_level_navigations = 0
        self._initial_seen = False

    @property
    def redirect_count(self) -> int:
        return self._redirects

    @property
    def validation_count(self) -> int:
        return self._checks

    async def _validate_and_pin(self, url: str) -> ValidatedUrl:
        if self._checks >= self.max_resolutions:
            raise _blocked(PolicyReason.NAVIGATION_LIMIT)
        self._checks += 1
        validated = await self._policy.validate_url(url)
        if validated._used_dns:
            approved = self._approved_addresses.get(validated.hostname)
            if approved is None:
                self._approved_addresses[validated.hostname] = validated._address_fingerprints
            elif not hmac_compare_fingerprints(approved, validated._address_fingerprints):
                raise _blocked(PolicyReason.DNS_REBINDING)
        return validated

    async def validate_initial(self, url: str) -> ValidatedUrl:
        if self._initial_seen:
            raise _blocked(PolicyReason.NAVIGATION_LIMIT)
        self._initial_seen = True
        self._top_level_navigations = 1
        return await self._validate_and_pin(url)

    async def validate_redirect(self, url: str) -> ValidatedUrl:
        if not self._initial_seen:
            raise _blocked(PolicyReason.NAVIGATION_LIMIT)
        if self._redirects >= self.max_redirects:
            raise _blocked(PolicyReason.REDIRECT_LIMIT)
        self._redirects += 1
        return await self._validate_and_pin(url)

    async def validate_navigation(self, url: str) -> ValidatedUrl:
        if not self._initial_seen:
            return await self.validate_initial(url)
        if self._top_level_navigations >= self.max_top_level_navigations:
            raise _blocked(PolicyReason.NAVIGATION_LIMIT)
        self._top_level_navigations += 1
        return await self._validate_and_pin(url)

    async def validate(self, url: str) -> ValidatedUrl:
        """Callback-friendly alias for initial and later top-level checks."""

        return await self.validate_navigation(url)

    validate_top_level_navigation = validate_navigation


def hmac_compare_fingerprints(left: frozenset[bytes], right: frozenset[bytes]) -> bool:
    """Compare DNS answer sets without rendering address text."""

    # Fingerprints are public routing data rather than secrets, but comparing a
    # stable digest keeps them out of debug formatting and error construction.
    def digest(values: frozenset[bytes]) -> bytes:
        hasher = hashlib.sha256()
        for value in sorted(values):
            hasher.update(len(value).to_bytes(2, "big"))
            hasher.update(value)
        return hasher.digest()

    return hmac.compare_digest(digest(left), digest(right))


async def validate_navigation_url(
    url: str,
    *,
    resolver: Resolver | None = None,
) -> ValidatedUrl:
    """Convenience wrapper for integrations that do not need a persistent guard."""

    return await NavigationPolicy(resolver=resolver).validate_url(url)


__all__ = [
    "DEFAULT_MAX_DNS_ANSWERS",
    "DEFAULT_MAX_REDIRECTS",
    "DEFAULT_MAX_RESOLUTIONS",
    "DEFAULT_MAX_TOP_LEVEL_NAVIGATIONS",
    "DEFAULT_RESOLUTION_TIMEOUT_SECONDS",
    "MAX_URL_LENGTH",
    "NavigationGuard",
    "NavigationPolicy",
    "PolicyConfigurationError",
    "PolicyError",
    "PolicyReason",
    "Resolver",
    "ValidatedUrl",
    "validate_navigation_url",
]
