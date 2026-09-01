"""FastAPI entrypoint for the closed Aether Browser v1 API."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import ipaddress
import math
import os
import re
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from aether_browser.models import (
    CreateSessionRequest,
    CreateSessionResponse,
    EndSessionRequest,
    EndSessionResponse,
    ErrorCode,
    ErrorDetail,
    ErrorResponse,
    HealthResponse,
    InteractRequest,
    InteractResponse,
    NavigateRequest,
    NavigateResponse,
    SnapshotRequest,
    SnapshotResponse,
)
from aether_browser.runtime import (
    BrowserLaunchError,
    BrowserNotReadyError,
    BrowserOperationError,
    BrowserRuntimeError,
    InvalidBrowserInteractionError,
    NavigationGuard,
    PatchrightBrowserAdapter,
)
from aether_browser.sessions import (
    AdapterFactory,
    SessionCapacityError,
    SessionExpiredError,
    SessionManager,
    SessionNotFoundError,
    VisionBudgetExhaustedError,
)

AuthorityCallback = Callable[[str | None, "RequiredAuthority"], Awaitable[None] | None]
NavigationPolicyCallback = Callable[[str], Awaitable[None] | None]

_DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


class RequiredAuthority(StrEnum):
    OBSERVER = "observer"
    CONTROLLER = "controller"


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    api_bind: str = "127.0.0.1"
    api_host: str = "127.0.0.1"
    api_port: int = 8092
    novnc_bind: str = "127.0.0.1"
    novnc_host: str = "127.0.0.1"
    container_mode: bool = False
    remote_mode: bool = False
    reverse_proxy_exposed: bool = False
    trusted_proxy_cidr: str | None = None
    trusted_proxy_scheme: str | None = None
    test_mode: bool = False
    test_origins: tuple[str, ...] = ()
    observer_token: str | None = field(default=None, repr=False)
    controller_token: str | None = field(default=None, repr=False)
    idle_timeout_seconds: float = 300.0
    absolute_lifetime_seconds: float = 3600.0
    view_url: str = "http://127.0.0.1:6080/vnc.html"

    @classmethod
    def from_environment(cls) -> RuntimeSettings:
        api_host = os.getenv("AETHER_BROWSER_API_HOST", "127.0.0.1")
        novnc_host = os.getenv("AETHER_BROWSER_NOVNC_HOST", "127.0.0.1")
        settings = cls(
            api_bind=os.getenv("AETHER_BROWSER_API_BIND", "127.0.0.1"),
            api_host=api_host,
            api_port=_bounded_int(os.getenv("AETHER_BROWSER_API_PORT"), 8092, 1, 65_535),
            novnc_bind=os.getenv("AETHER_BROWSER_NOVNC_BIND", "127.0.0.1"),
            novnc_host=novnc_host,
            container_mode=_environment_flag("AETHER_BROWSER_CONTAINER_MODE"),
            remote_mode=_environment_flag("AETHER_BROWSER_REMOTE_MODE"),
            reverse_proxy_exposed=_environment_flag("AETHER_BROWSER_REVERSE_PROXY_EXPOSED"),
            trusted_proxy_cidr=os.getenv("AETHER_BROWSER_TRUSTED_PROXY_CIDR"),
            trusted_proxy_scheme=os.getenv("AETHER_BROWSER_TRUSTED_PROXY_SCHEME"),
            test_mode=_environment_flag("AETHER_BROWSER_TEST_MODE"),
            test_origins=tuple(
                origin.strip()
                for origin in os.getenv("AETHER_BROWSER_TEST_ORIGINS", "").split(",")
                if origin.strip()
            ),
            observer_token=os.getenv("AETHER_BROWSER_OBSERVER_TOKEN"),
            controller_token=os.getenv("AETHER_BROWSER_CONTROLLER_TOKEN"),
            idle_timeout_seconds=_bounded_float(
                os.getenv("AETHER_BROWSER_IDLE_TIMEOUT_SECONDS"),
                300.0,
                1.0,
                86_400.0,
            ),
            absolute_lifetime_seconds=_bounded_float(
                os.getenv("AETHER_BROWSER_ABSOLUTE_LIFETIME_SECONDS"),
                3600.0,
                1.0,
                86_400.0,
            ),
            view_url=os.getenv(
                "AETHER_BROWSER_VIEW_URL",
                "http://127.0.0.1:6080/vnc.html",
            ),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if any(
            type(flag) is not bool
            for flag in (
                self.container_mode,
                self.remote_mode,
                self.reverse_proxy_exposed,
                self.test_mode,
            )
        ):
            raise ValueError("listener mode flags must be booleans")
        for value in (self.api_bind, self.api_host, self.novnc_bind, self.novnc_host):
            if not isinstance(value, str) or not value.strip() or len(value) > 255:
                raise ValueError("listener and effective hosts must be bounded")
        if not _is_numeric_loopback_address(self.api_bind):
            raise ValueError("API must bind to a numeric loopback address")
        if not all(
            _is_numeric_loopback_address(value)
            for value in (self.novnc_bind, self.novnc_host)
        ):
            raise ValueError("noVNC must remain numeric-loopback-only")
        if ipaddress.ip_address(self.novnc_bind) != ipaddress.ip_address(self.novnc_host):
            raise ValueError("noVNC bind and effective host must match")

        proxy_configured = any(
            (
                self.remote_mode,
                self.reverse_proxy_exposed,
                self.trusted_proxy_cidr is not None,
                self.trusted_proxy_scheme is not None,
            )
        )
        if proxy_configured:
            if not (
                self.remote_mode
                and self.reverse_proxy_exposed
                and self.trusted_proxy_cidr is not None
                and self.trusted_proxy_scheme == "https"
                and _is_exact_loopback_cidr(self.trusted_proxy_cidr)
                and _is_nonloopback_effective_host(self.api_host)
                and _is_strong_token(self.observer_token)
                and _is_strong_token(self.controller_token)
                and self.observer_token != self.controller_token
                and not self.test_mode
                and not self.test_origins
            ):
                raise ValueError("trusted TLS proxy configuration is incomplete")
        elif not _is_numeric_loopback_address(self.api_host):
            raise ValueError("local API effective host must be numeric loopback")

        if not proxy_configured and ipaddress.ip_address(self.api_bind) != ipaddress.ip_address(
            self.api_host
        ):
            raise ValueError("local API bind and effective host must match")
        if not 1 <= self.api_port <= 65_535:
            raise ValueError("API port is outside the supported range")
        if (
            not isinstance(self.view_url, str)
            or not 1 <= len(self.view_url) <= 2048
            or not _is_loopback_view_url(self.view_url)
        ):
            raise ValueError("view URL must remain numeric-loopback-only")


class _ApiFault(RuntimeError):
    def __init__(
        self,
        code: ErrorCode,
        status_code: int,
        *,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(code.value)
        self.code = code
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


class _LazySecurity:
    """Import the security lane only when the ASGI runtime starts."""

    def __init__(self, settings: RuntimeSettings) -> None:
        self._settings = settings
        self._auth_module: Any | None = None
        self._auth_settings: Any | None = None
        self._policy: Any | None = None

    async def startup(self) -> None:
        """Validate the complete production boundary before serving requests.

        Injectable callbacks replace request-time behavior for deterministic
        tests; they must never bypass listener, token, or test-origin startup
        validation.
        """

        self._ensure_auth()
        self._ensure_policy()

    async def authorize_request(
        self,
        request: Request,
        required: RequiredAuthority,
    ) -> None:
        """Authorize one real request without ambiguous proxy/header parsing."""

        self._ensure_auth()
        module = self._auth_module
        settings = self._auth_settings
        if module is None or settings is None:
            raise BrowserNotReadyError("The authority boundary is unavailable.")

        authorization_values = request.headers.getlist("authorization")
        if len(authorization_values) > 1:
            raise module.AuthenticationRequired()

        host_values = request.headers.getlist("host")
        forwarded = any(_is_forwarding_header(name) for name in request.headers)
        if (
            len(host_values) != 1
            or forwarded
            or not _host_matches_effective_authority(
                host_values[0],
                expected_host=settings.api_host,
                expected_port=443 if settings.proxy_mode else self._settings.api_port,
            )
        ):
            raise module.AuthenticationRequired()

        peer = request.client
        if settings.proxy_mode:
            if peer is None or not settings.trusts_proxy_peer(peer.host):
                raise module.AuthenticationRequired()
        elif peer is None or not _is_numeric_loopback_address(peer.host):
            raise module.AuthenticationRequired()

        authorization = authorization_values[0] if authorization_values else None
        await self.authorize(authorization, required)

    async def authorize(
        self,
        authorization: str | None,
        required: RequiredAuthority,
    ) -> None:
        self._ensure_auth()
        module = self._auth_module
        if module is None or self._auth_settings is None:
            raise BrowserNotReadyError("The authority boundary is unavailable.")
        authority = (
            module.Authority.OBSERVER
            if required is RequiredAuthority.OBSERVER
            else module.Authority.CONTROLLER
        )
        result = module.authorize(self._auth_settings, authorization, authority)
        if inspect.isawaitable(result):
            await result

    async def validate_url(self, url: str) -> None:
        self._ensure_policy()
        if self._policy is None:
            raise BrowserNotReadyError("The navigation boundary is unavailable.")
        await self._policy.validate_url(url)

    async def navigation_guards(self) -> tuple[NavigationGuard, NavigationGuard]:
        self._ensure_policy()
        if self._policy is None:
            raise BrowserNotReadyError("The navigation boundary is unavailable.")
        guard = self._policy.new_guard()
        return (
            cast(NavigationGuard, guard.validate),
            cast(NavigationGuard, guard.validate_redirect),
        )

    def _ensure_auth(self) -> None:
        if self._auth_settings is not None:
            return
        try:
            auth_module = importlib.import_module("aether_browser.auth")
        except ImportError:
            raise BrowserNotReadyError("The authority boundary is unavailable.") from None
        auth_settings = auth_module.build_auth_settings(
            api_bind=self._settings.api_bind,
            api_host=self._settings.api_host,
            novnc_bind=self._settings.novnc_bind,
            novnc_host=self._settings.novnc_host,
            remote_mode=self._settings.remote_mode,
            reverse_proxy_exposed=self._settings.reverse_proxy_exposed,
            trusted_proxy_cidr=self._settings.trusted_proxy_cidr,
            trusted_proxy_scheme=self._settings.trusted_proxy_scheme,
            observer_token=self._settings.observer_token,
            controller_token=self._settings.controller_token,
            test_mode=self._settings.test_mode,
            test_origins=self._settings.test_origins,
        )
        self._auth_module = auth_module
        self._auth_settings = auth_settings

    def _ensure_policy(self) -> None:
        if self._policy is not None:
            return
        self._ensure_auth()
        auth_settings = self._auth_settings
        if auth_settings is None:
            raise BrowserNotReadyError("The authority boundary is unavailable.")
        try:
            policy_module = importlib.import_module("aether_browser.policy")
        except ImportError:
            raise BrowserNotReadyError("The navigation boundary is unavailable.") from None
        self._policy = policy_module.NavigationPolicy(
            test_mode=auth_settings.test_mode,
            test_origins=auth_settings.test_origins,
        )


def create_app(
    *,
    manager: SessionManager | None = None,
    adapter_factory: AdapterFactory | None = None,
    authority: AuthorityCallback | None = None,
    navigation_policy: NavigationPolicyCallback | None = None,
    settings: RuntimeSettings | None = None,
    utc_clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    """Build the API with injectable authority, policy, browser, and clock seams."""

    if all(
        value is None
        for value in (
            manager,
            adapter_factory,
            authority,
            navigation_policy,
            settings,
            utc_clock,
        )
    ):
        raise ValueError("use the validated module launcher")
    if manager is not None and adapter_factory is not None:
        raise ValueError("manager and adapter_factory are mutually exclusive")

    resolved_settings = settings or RuntimeSettings.from_environment()
    resolved_settings.validate()
    now = utc_clock or (lambda: datetime.now(UTC))
    security = _LazySecurity(resolved_settings)
    uses_default_authority = authority is None
    authority_callback = authority if authority is not None else security.authorize
    policy_callback = navigation_policy if navigation_policy is not None else security.validate_url

    if manager is None:
        if adapter_factory is None:

            async def default_adapter_factory(_profile: Path) -> PatchrightBrowserAdapter:
                # Injectable request callbacks are additive test seams.  The
                # real adapter always receives the stateful core guard whose
                # immutable pins are authoritative for the SOCKS dialer.
                guard, redirect_guard = await security.navigation_guards()
                return PatchrightBrowserAdapter(
                    navigation_guard=guard,
                    redirect_guard=redirect_guard,
                )

            adapter_factory = default_adapter_factory
        manager = SessionManager(
            adapter_factory,
            idle_timeout_seconds=resolved_settings.idle_timeout_seconds,
            absolute_lifetime_seconds=resolved_settings.absolute_lifetime_seconds,
            view_url=resolved_settings.view_url,
            utc_clock=now,
        )
    session_manager = manager

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> Any:
        application.state.started_at = _aware_timestamp(now())
        try:
            await security.startup()
            yield
        finally:
            await session_manager.shutdown()

    application = FastAPI(
        title="Aether Browser",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    application.state.session_manager = session_manager
    application.state.started_at = _aware_timestamp(now())

    @application.middleware("http")
    async def no_store_responses(request: Request, call_next: Callable[[Request], Any]) -> Any:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @application.exception_handler(_ApiFault)
    async def api_fault_handler(_request: Request, error: _ApiFault) -> JSONResponse:
        return _error_response(
            error.code,
            error.status_code,
            retry_after_seconds=error.retry_after_seconds,
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        _error: RequestValidationError,
    ) -> JSONResponse:
        code = ErrorCode.INVALID_INTERACTION
        if request.url.path == "/browser/navigate":
            code = ErrorCode.INVALID_URL
        return _error_response(code, 422)

    @application.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        _request: Request,
        error: StarletteHTTPException,
    ) -> JSONResponse:
        if error.status_code == 401:
            return _error_response(ErrorCode.AUTH_REQUIRED, 401)
        if error.status_code == 403:
            return _error_response(ErrorCode.AUTH_FORBIDDEN, 403)
        if error.status_code == 404:
            return _error_response(ErrorCode.SESSION_NOT_FOUND, 404)
        return _error_response(ErrorCode.INTERNAL_ERROR, 500)

    @application.exception_handler(Exception)
    async def unexpected_error_handler(_request: Request, error: Exception) -> JSONResponse:
        fault = _known_fault(error)
        if fault is not None:
            return _error_response(
                fault.code,
                fault.status_code,
                retry_after_seconds=fault.retry_after_seconds,
            )
        return _error_response(ErrorCode.INTERNAL_ERROR, 500)

    async def require(request: Request, level: RequiredAuthority) -> None:
        try:
            if uses_default_authority:
                await security.authorize_request(request, level)
                return
            result = authority_callback(request.headers.get("authorization"), level)
            if inspect.isawaitable(result):
                await result
        except BaseException as error:
            if isinstance(error, asyncio.CancelledError):
                raise
            fault = _external_security_fault(error)
            if fault is not None:
                raise fault from None
            raise

    async def validate_navigation(url: str) -> None:
        try:
            result = policy_callback(url)
            if inspect.isawaitable(result):
                await result
        except BaseException as error:
            if isinstance(error, asyncio.CancelledError):
                raise
            fault = _external_security_fault(error)
            if fault is not None:
                raise fault from None
            raise

    @application.get("/browser/health", response_model=HealthResponse)
    async def health(request: Request) -> HealthResponse:
        await require(request, RequiredAuthority.OBSERVER)
        state = await session_manager.health()
        return HealthResponse(
            browser_ready=state.browser_ready,
            session_active=state.session_active,
            slots_available=state.slots_available,
            started_at=application.state.started_at,
        )

    @application.post("/browser/session/create", response_model=CreateSessionResponse)
    async def create_session(
        payload: CreateSessionRequest,
        request: Request,
    ) -> CreateSessionResponse:
        await require(request, RequiredAuthority.CONTROLLER)
        created = await session_manager.create(payload.max_vision_steps)
        return CreateSessionResponse(
            session_id=created.session_id,
            max_vision_steps=created.max_vision_steps,
            view_url=created.view_url,
            created_at=created.created_at,
            expires_at=created.expires_at,
        )

    @application.post("/browser/navigate", response_model=NavigateResponse)
    async def navigate(payload: NavigateRequest, request: Request) -> NavigateResponse:
        await require(request, RequiredAuthority.CONTROLLER)
        await validate_navigation(payload.url)
        try:
            result = await session_manager.navigate(payload.session_id, payload.url)
        except BaseException as error:
            if isinstance(error, asyncio.CancelledError):
                raise
            fault = _external_security_fault(error)
            if fault is not None:
                raise fault from None
            raise
        return NavigateResponse(
            session_id=result.session_id,
            final_url=result.page.url,
            title=result.page.title,
            readable_text=result.page.readable_text,
            accessibility=result.page.accessibility,
            navigated_at=result.navigated_at,
        )

    @application.post("/browser/snapshot", response_model=SnapshotResponse)
    async def snapshot(payload: SnapshotRequest, request: Request) -> SnapshotResponse:
        await require(request, RequiredAuthority.OBSERVER)
        result = await session_manager.snapshot(payload.session_id)
        return SnapshotResponse(
            session_id=result.session_id,
            url=result.snapshot.page.url,
            title=result.snapshot.page.title,
            readable_text=result.snapshot.page.readable_text,
            accessibility=result.snapshot.page.accessibility,
            screenshot_base64=result.snapshot.screenshot_base64,
            viewport=result.snapshot.viewport,
            sequence=result.sequence,
            captured_at=result.captured_at,
            vision_steps_used=result.vision_steps_used,
            vision_steps_remaining=result.vision_steps_remaining,
        )

    @application.post("/browser/interact", response_model=InteractResponse)
    async def interact(payload: InteractRequest, request: Request) -> InteractResponse:
        await require(request, RequiredAuthority.CONTROLLER)
        result = await session_manager.interact(payload)
        return InteractResponse(
            session_id=result.session_id,
            action=result.action,
            sequence=result.sequence,
            interacted_at=result.interacted_at,
        )

    @application.post("/browser/session/end", response_model=EndSessionResponse)
    async def end_session(
        payload: EndSessionRequest,
        request: Request,
    ) -> EndSessionResponse:
        await require(request, RequiredAuthority.CONTROLLER)
        result = await session_manager.end(payload.session_id)
        return EndSessionResponse(
            status=result.status,
            session_id=result.session_id,
            ended_at=result.ended_at,
        )

    return application


def _known_fault(error: Exception) -> _ApiFault | None:
    if isinstance(error, _ApiFault):
        return error
    if isinstance(error, SessionCapacityError):
        return _ApiFault(
            ErrorCode.SESSION_CAPACITY_REACHED,
            503,
            retry_after_seconds=error.retry_after_seconds,
        )
    if isinstance(error, SessionNotFoundError):
        return _ApiFault(ErrorCode.SESSION_NOT_FOUND, 404)
    if isinstance(error, SessionExpiredError):
        return _ApiFault(ErrorCode.SESSION_EXPIRED, 410)
    if isinstance(error, VisionBudgetExhaustedError):
        return _ApiFault(ErrorCode.VISION_BUDGET_EXHAUSTED, 409)
    if isinstance(error, InvalidBrowserInteractionError):
        return _ApiFault(ErrorCode.INVALID_INTERACTION, 400)
    if isinstance(error, (BrowserLaunchError, BrowserNotReadyError)):
        return _ApiFault(ErrorCode.BROWSER_NOT_READY, 503)
    if isinstance(error, (BrowserOperationError, BrowserRuntimeError)):
        return _ApiFault(ErrorCode.INTERNAL_ERROR, 500)
    return None


def _external_security_fault(error: BaseException) -> _ApiFault | None:
    if isinstance(error, _ApiFault):
        return error
    if isinstance(error, StarletteHTTPException):
        if error.status_code == 401:
            return _ApiFault(ErrorCode.AUTH_REQUIRED, 401)
        if error.status_code == 403:
            return _ApiFault(ErrorCode.AUTH_FORBIDDEN, 403)
        return None
    raw_code = getattr(error, "code", None)
    if isinstance(raw_code, StrEnum):
        raw_code = raw_code.value
    try:
        code = ErrorCode(str(raw_code))
    except ValueError:
        return None
    statuses = {
        ErrorCode.AUTH_REQUIRED: 401,
        ErrorCode.AUTH_FORBIDDEN: 403,
        ErrorCode.INVALID_URL: 400,
        ErrorCode.DESTINATION_BLOCKED: 403,
    }
    status_code = statuses.get(code)
    if status_code is None:
        return None
    return _ApiFault(code, status_code)


_ERROR_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.AUTH_REQUIRED: "Authentication is required.",
    ErrorCode.AUTH_FORBIDDEN: "Authority is insufficient.",
    ErrorCode.SESSION_CAPACITY_REACHED: "The browser session slot is occupied.",
    ErrorCode.SESSION_NOT_FOUND: "Session was not found.",
    ErrorCode.SESSION_EXPIRED: "Session expired.",
    ErrorCode.VISION_BUDGET_EXHAUSTED: "The snapshot budget is exhausted.",
    ErrorCode.INVALID_URL: "The navigation URL is invalid.",
    ErrorCode.DESTINATION_BLOCKED: "The navigation destination is blocked.",
    ErrorCode.INVALID_INTERACTION: "The request or interaction is invalid.",
    ErrorCode.BROWSER_NOT_READY: "The browser is not ready.",
    ErrorCode.INTERNAL_ERROR: "The request could not be completed.",
}


def _error_response(
    code: ErrorCode,
    status_code: int,
    *,
    retry_after_seconds: int | None = None,
) -> JSONResponse:
    payload = ErrorResponse(
        error=ErrorDetail(
            code=code,
            message=_ERROR_MESSAGES[code],
            retry_after_seconds=retry_after_seconds,
        )
    )
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    if retry_after_seconds is not None:
        headers["Retry-After"] = str(retry_after_seconds)
    if status_code == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json", exclude_none=True),
        headers=headers,
    )


def _environment_flag(name: str) -> bool:
    value = os.getenv(name)
    if value is None or value == "0":
        return False
    if value == "1":
        return True
    raise ValueError(f"{name} must be exactly '0' or '1'")


def _host_matches_effective_authority(
    value: str,
    *,
    expected_host: str,
    expected_port: int,
) -> bool:
    """Match one canonical Host authority without consulting forwarding headers."""

    if not value or len(value) > 255 or value != value.strip() or not value.isascii():
        return False

    address_text = value
    port: int | None = None
    bracketed = value.startswith("[")
    if value.startswith("["):
        closing = value.find("]")
        if closing < 0:
            return False
        address_text = value[1:closing]
        suffix = value[closing + 1 :]
        if suffix:
            port_text = suffix[1:] if suffix.startswith(":") else ""
            if not port_text.isdecimal() or str(int(port_text)) != port_text:
                return False
            port = int(port_text)
    elif value.count(":") == 1:
        address_text, port_text = value.rsplit(":", 1)
        if not port_text.isdecimal() or str(int(port_text)) != port_text:
            return False
        port = int(port_text)
    elif ":" in value:
        # RFC Host/authority syntax requires brackets around IPv6 literals.
        return False

    if port is not None and port != expected_port:
        return False
    try:
        candidate_address = ipaddress.ip_address(address_text)
    except ValueError:
        if bracketed:
            return False
        return address_text.casefold() == expected_host.casefold()
    if bracketed and not isinstance(candidate_address, ipaddress.IPv6Address):
        return False
    try:
        expected_address = ipaddress.ip_address(expected_host)
    except ValueError:
        return False
    return (
        candidate_address == expected_address
        and getattr(candidate_address, "ipv4_mapped", None) is None
    )


def _is_forwarding_header(name: str) -> bool:
    normalized = name.casefold()
    return (
        normalized == "forwarded"
        or normalized.startswith("x-forwarded-")
        or normalized in {"x-original-host", "x-real-ip"}
    )


def _is_numeric_loopback_address(value: str) -> bool:
    if not isinstance(value, str) or not value or value != value.strip() or "%" in value:
        return False
    try:
        address = ipaddress.ip_address(value)
    except (TypeError, ValueError):
        return False
    return address.is_loopback and getattr(address, "ipv4_mapped", None) is None


def _is_nonloopback_effective_host(value: str) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 253
        or not value.isascii()
        or any(character in value for character in ("/", "\\", "@", "?", "#", "%"))
    ):
        return False
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        normalized = value.casefold()
        return (
            not normalized.endswith(".")
            and normalized != "localhost"
            and not normalized.endswith(".localhost")
            and re.fullmatch(r"[0-9.]+", normalized) is None
            and all(_DNS_LABEL_RE.fullmatch(label) is not None for label in normalized.split("."))
        )
    return not (
        address.is_loopback
        or address.is_unspecified
        or address.is_multicast
        or getattr(address, "ipv4_mapped", None) is not None
    )


def _is_strong_token(value: str | None) -> bool:
    if not isinstance(value, str) or not 32 <= len(value) <= 4_096:
        return False
    if not value.isascii() or not value.isprintable() or any(char.isspace() for char in value):
        return False
    diversity = sum(
        (
            any(char.islower() for char in value),
            any(char.isupper() for char in value),
            any(char.isdigit() for char in value),
            any(not char.isalnum() for char in value),
        )
    )
    return diversity >= 3


def _is_exact_loopback_cidr(value: str) -> bool:
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    try:
        network = ipaddress.ip_network(value, strict=True)
    except ValueError:
        return False
    address = network.network_address
    return (
        network.prefixlen == network.max_prefixlen
        and address.is_loopback
        and getattr(address, "ipv4_mapped", None) is None
        and value == network.with_prefixlen
    )


def _is_loopback_view_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname is not None
        and _is_numeric_loopback_address(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and port is not None
    )


def _bounded_int(value: str | None, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    if not value or value != value.strip():
        raise ValueError("integer environment setting is malformed")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError("integer environment setting is malformed") from None
    if not minimum <= parsed <= maximum:
        raise ValueError("integer environment setting is outside the supported range")
    return parsed


def _bounded_float(
    value: str | None,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    if value is None:
        return default
    if not value or value != value.strip():
        raise ValueError("numeric environment setting is malformed")
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise ValueError("numeric environment setting is malformed") from None
    if not math.isfinite(parsed) or not minimum <= parsed <= maximum:
        raise ValueError("numeric environment setting is outside the supported range")
    return parsed


def _aware_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("utc_clock must return an offset-aware datetime")
    return value


def run() -> None:
    """Run the loopback API using validated environment settings."""

    settings = RuntimeSettings.from_environment()
    application = create_app(settings=settings)
    uvicorn.run(
        application,
        host=settings.api_bind,
        port=settings.api_port,
        log_level="info",
        proxy_headers=False,
    )


__all__ = [
    "AuthorityCallback",
    "NavigationPolicyCallback",
    "RequiredAuthority",
    "RuntimeSettings",
    "create_app",
    "run",
]


if __name__ == "__main__":
    run()
