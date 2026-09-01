"""FastAPI entrypoint for the closed Aether Browser v1 API."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import math
import os
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

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
            api_bind=os.getenv("AETHER_BROWSER_API_BIND", api_host),
            api_host=api_host,
            api_port=_bounded_int(os.getenv("AETHER_BROWSER_API_PORT"), 8092, 1, 65_535),
            novnc_bind=os.getenv("AETHER_BROWSER_NOVNC_BIND", novnc_host),
            novnc_host=novnc_host,
            container_mode=_environment_flag("AETHER_BROWSER_CONTAINER_MODE"),
            remote_mode=_environment_flag("AETHER_BROWSER_REMOTE_MODE"),
            reverse_proxy_exposed=_environment_flag("AETHER_BROWSER_REVERSE_PROXY_EXPOSED"),
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
        for value in (self.api_bind, self.api_host, self.novnc_bind, self.novnc_host):
            if not value.strip() or len(value) > 255:
                raise ValueError("listener and effective hosts must be bounded")
        if not self.container_mode and self.api_bind != self.api_host:
            raise ValueError("API bind and effective host differ outside container mode")
        if not self.container_mode and self.novnc_bind != self.novnc_host:
            raise ValueError("noVNC bind and effective host differ outside container mode")
        if not 1 <= self.api_port <= 65_535:
            raise ValueError("API port is outside the supported range")
        if not 1 <= len(self.view_url) <= 2048:
            raise ValueError("view URL is outside the supported range")


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

    async def startup(self, *, auth: bool, policy: bool) -> None:
        if auth:
            self._ensure_auth()
        if policy:
            self._ensure_policy()

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
        self._auth_module = auth_module
        self._auth_settings = auth_module.build_auth_settings(
            api_bind=self._settings.api_host,
            novnc_bind=self._settings.novnc_host,
            remote_mode=self._settings.remote_mode,
            reverse_proxy_exposed=self._settings.reverse_proxy_exposed,
            observer_token=self._settings.observer_token,
            controller_token=self._settings.controller_token,
            test_mode=self._settings.test_mode,
            test_origins=self._settings.test_origins,
        )

    def _ensure_policy(self) -> None:
        if self._policy is not None:
            return
        try:
            policy_module = importlib.import_module("aether_browser.policy")
        except ImportError:
            raise BrowserNotReadyError("The navigation boundary is unavailable.") from None
        self._policy = policy_module.NavigationPolicy(
            test_mode=self._settings.test_mode,
            test_origins=self._settings.test_origins,
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

    if manager is not None and adapter_factory is not None:
        raise ValueError("manager and adapter_factory are mutually exclusive")

    resolved_settings = settings or RuntimeSettings.from_environment()
    resolved_settings.validate()
    now = utc_clock or (lambda: datetime.now(UTC))
    security = _LazySecurity(resolved_settings)
    uses_default_authority = authority is None
    uses_default_policy = navigation_policy is None
    authority_callback = authority or security.authorize
    policy_callback = navigation_policy or security.validate_url

    if manager is None:
        if adapter_factory is None:

            async def default_adapter_factory(_profile: Path) -> PatchrightBrowserAdapter:
                guard: NavigationGuard
                redirect_guard: NavigationGuard
                if uses_default_policy:
                    guard, redirect_guard = await security.navigation_guards()
                else:
                    guard = policy_callback
                    redirect_guard = policy_callback
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
            if uses_default_authority or uses_default_policy:
                await security.startup(
                    auth=uses_default_authority,
                    policy=uses_default_policy,
                )
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


app = create_app()


def run() -> None:
    """Run the loopback API using validated environment settings."""

    settings = RuntimeSettings.from_environment()
    uvicorn.run(
        "aether_browser.main:app",
        host=settings.api_bind,
        port=settings.api_port,
        log_level="info",
    )


__all__ = [
    "AuthorityCallback",
    "NavigationPolicyCallback",
    "RequiredAuthority",
    "RuntimeSettings",
    "app",
    "create_app",
    "run",
]
