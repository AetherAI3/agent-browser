"""Closed, bounded API v1 models for the Agent Browser runtime."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from agent_browser import __version__

API_VERSION: Literal["v1"] = "v1"
DEFAULT_MAX_VISION_STEPS = 25
MAX_SCREENSHOT_BASE64_CHARS = 14_000_000
MAX_READABLE_TEXT_CHARS = 65_536
MAX_ACCESSIBILITY_NODES = 500

ApiVersion = Literal["v1"]
BoundedUrl = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2048),
]
BoundedTitle = Annotated[str, StringConstraints(max_length=512)]
BoundedReadableText = Annotated[str, StringConstraints(max_length=MAX_READABLE_TEXT_CHARS)]
BoundedSelector = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2048),
]
BoundedTypedText = Annotated[str, StringConstraints(max_length=16_384)]
BoundedMessage = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=512),
]


def _validate_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must use UTC")
    return value


class ClosedModel(BaseModel):
    """Base class that rejects unknown fields at every public boundary."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
    )


class SessionState(StrEnum):
    IDLE = "idle"
    STARTING = "starting"
    ACTIVE = "active"
    ENDING = "ending"
    ENDED = "ended"
    EXPIRED = "expired"
    FAILED = "failed"


class ErrorCode(StrEnum):
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTH_FORBIDDEN = "AUTH_FORBIDDEN"
    SESSION_CAPACITY_REACHED = "SESSION_CAPACITY_REACHED"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    VISION_BUDGET_EXHAUSTED = "VISION_BUDGET_EXHAUSTED"
    INVALID_URL = "INVALID_URL"
    DESTINATION_BLOCKED = "DESTINATION_BLOCKED"
    INVALID_INTERACTION = "INVALID_INTERACTION"
    BROWSER_NOT_READY = "BROWSER_NOT_READY"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class InteractionAction(StrEnum):
    CLICK = "click"
    TYPE = "type"
    SCROLL = "scroll"
    PRESS = "press"


class AllowedKey(StrEnum):
    ENTER = "Enter"
    ESCAPE = "Escape"
    TAB = "Tab"
    BACKSPACE = "Backspace"
    DELETE = "Delete"
    SPACE = "Space"
    ARROW_UP = "ArrowUp"
    ARROW_DOWN = "ArrowDown"
    ARROW_LEFT = "ArrowLeft"
    ARROW_RIGHT = "ArrowRight"
    HOME = "Home"
    END = "End"
    PAGE_UP = "PageUp"
    PAGE_DOWN = "PageDown"
    CONTROL_A = "Control+A"
    CONTROL_Z = "Control+Z"
    CONTROL_SHIFT_Z = "Control+Shift+Z"
    META_A = "Meta+A"
    META_Z = "Meta+Z"
    META_SHIFT_Z = "Meta+Shift+Z"


class EmptyRequest(ClosedModel):
    api_version: ApiVersion = API_VERSION


class SessionRequest(EmptyRequest):
    session_id: UUID


class HealthResponse(ClosedModel):
    api_version: ApiVersion = API_VERSION
    status: Literal["ok"] = "ok"
    version: Literal["0.2.1"] = __version__
    browser_ready: bool
    session_active: bool
    slots_available: int = Field(ge=0, le=1)
    started_at: datetime

    _started_at_is_utc = field_validator("started_at")(_validate_utc)


class CreateSessionRequest(EmptyRequest):
    max_vision_steps: int = Field(default=DEFAULT_MAX_VISION_STEPS, ge=1, le=100)


class CreateSessionResponse(ClosedModel):
    api_version: ApiVersion = API_VERSION
    status: Literal["created"] = "created"
    session_id: UUID
    state: Literal[SessionState.ACTIVE] = SessionState.ACTIVE
    max_vision_steps: int = Field(ge=1, le=100)
    view_url: BoundedUrl
    created_at: datetime
    expires_at: datetime

    _timestamps_are_utc = field_validator("created_at", "expires_at")(_validate_utc)

    @model_validator(mode="after")
    def expiry_follows_creation(self) -> CreateSessionResponse:
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must follow created_at")
        return self


class NavigateRequest(SessionRequest):
    url: BoundedUrl


class AccessibilityNode(ClosedModel):
    role: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    name: Annotated[str, StringConstraints(max_length=1024)] = ""
    value: Annotated[str, StringConstraints(max_length=4096)] = ""
    focused: bool = False
    disabled: bool = False


class AccessibilitySnapshot(ClosedModel):
    nodes: list[AccessibilityNode] = Field(default_factory=list, max_length=MAX_ACCESSIBILITY_NODES)
    truncated: bool = False


class NavigateResponse(ClosedModel):
    api_version: ApiVersion = API_VERSION
    status: Literal["navigated"] = "navigated"
    session_id: UUID
    final_url: BoundedUrl
    title: BoundedTitle
    readable_text: BoundedReadableText
    accessibility: AccessibilitySnapshot
    navigated_at: datetime

    _navigated_at_is_utc = field_validator("navigated_at")(_validate_utc)


class SnapshotRequest(SessionRequest):
    pass


class Viewport(ClosedModel):
    width: int = Field(ge=1, le=4096)
    height: int = Field(ge=1, le=4096)
    device_scale_factor: float = Field(default=1.0, ge=0.25, le=4.0)


class SnapshotResponse(ClosedModel):
    api_version: ApiVersion = API_VERSION
    status: Literal["snapshot"] = "snapshot"
    session_id: UUID
    url: BoundedUrl
    title: BoundedTitle
    readable_text: BoundedReadableText
    accessibility: AccessibilitySnapshot
    screenshot_base64: Annotated[
        str,
        StringConstraints(min_length=1, max_length=MAX_SCREENSHOT_BASE64_CHARS),
    ]
    viewport: Viewport
    sequence: int = Field(ge=1, le=9_223_372_036_854_775_807)
    captured_at: datetime
    vision_steps_used: int = Field(ge=1, le=100)
    vision_steps_remaining: int = Field(ge=0, le=99)

    _captured_at_is_utc = field_validator("captured_at")(_validate_utc)


class InteractionTarget(ClosedModel):
    selector: BoundedSelector | None = None
    x: int | None = Field(default=None, ge=0, le=4095)
    y: int | None = Field(default=None, ge=0, le=4095)

    @model_validator(mode="after")
    def selector_or_coordinates(self) -> InteractionTarget:
        has_selector = self.selector is not None
        has_x = self.x is not None
        has_y = self.y is not None
        if has_x != has_y:
            raise ValueError("x and y must be supplied together")
        if has_selector == (has_x and has_y):
            raise ValueError("provide a selector or coordinates, not both")
        return self


class InteractRequest(SessionRequest):
    action: InteractionAction
    target: InteractionTarget | None = None
    text: BoundedTypedText | None = None
    key: AllowedKey | None = None
    delta_x: int | None = Field(default=None, ge=-10_000, le=10_000)
    delta_y: int | None = Field(default=None, ge=-10_000, le=10_000)

    @model_validator(mode="after")
    def validate_action_shape(self) -> InteractRequest:
        if self.action is InteractionAction.CLICK:
            if self.target is None:
                raise ValueError("click requires target")
            if any(
                value is not None for value in (self.text, self.key, self.delta_x, self.delta_y)
            ):
                raise ValueError("click accepts only target")
        elif self.action is InteractionAction.TYPE:
            if self.target is None or self.text is None:
                raise ValueError("type requires target and text")
            if any(value is not None for value in (self.key, self.delta_x, self.delta_y)):
                raise ValueError("type accepts only target and text")
        elif self.action is InteractionAction.SCROLL:
            if any(value is not None for value in (self.target, self.text, self.key)):
                raise ValueError("scroll accepts only delta_x and delta_y")
            if self.delta_x is None and self.delta_y is None:
                raise ValueError("scroll requires at least one delta")
            if (self.delta_x or 0) == 0 and (self.delta_y or 0) == 0:
                raise ValueError("scroll delta must be nonzero")
        elif self.action is InteractionAction.PRESS:
            if self.key is None:
                raise ValueError("press requires an allowlisted key")
            if any(
                value is not None for value in (self.target, self.text, self.delta_x, self.delta_y)
            ):
                raise ValueError("press accepts only key")
        return self


class InteractResponse(ClosedModel):
    api_version: ApiVersion = API_VERSION
    status: Literal["interacted"] = "interacted"
    session_id: UUID
    action: InteractionAction
    sequence: int = Field(ge=1, le=9_223_372_036_854_775_807)
    interacted_at: datetime

    _interacted_at_is_utc = field_validator("interacted_at")(_validate_utc)


class EndSessionRequest(SessionRequest):
    pass


class EndSessionResponse(ClosedModel):
    api_version: ApiVersion = API_VERSION
    status: Literal["ended", "already_ended"]
    session_id: UUID
    ended_at: datetime

    _ended_at_is_utc = field_validator("ended_at")(_validate_utc)


class ErrorDetail(ClosedModel):
    code: ErrorCode
    message: BoundedMessage
    retry_after_seconds: int | None = Field(default=None, ge=1, le=300)


class ErrorResponse(ClosedModel):
    api_version: ApiVersion = API_VERSION
    status: Literal["error"] = "error"
    error: ErrorDetail


__all__ = [
    "API_VERSION",
    "DEFAULT_MAX_VISION_STEPS",
    "AccessibilityNode",
    "AccessibilitySnapshot",
    "AllowedKey",
    "CreateSessionRequest",
    "CreateSessionResponse",
    "EndSessionRequest",
    "EndSessionResponse",
    "ErrorCode",
    "ErrorDetail",
    "ErrorResponse",
    "HealthResponse",
    "InteractRequest",
    "InteractResponse",
    "InteractionAction",
    "InteractionTarget",
    "NavigateRequest",
    "NavigateResponse",
    "SessionState",
    "SnapshotRequest",
    "SnapshotResponse",
    "Viewport",
]
