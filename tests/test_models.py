from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from aether_browser import __version__
from aether_browser.models import (
    API_VERSION,
    AllowedKey,
    CreateSessionRequest,
    CreateSessionResponse,
    HealthResponse,
    InteractRequest,
    InteractionAction,
    InteractionTarget,
    NavigateRequest,
)


def test_version_contract_is_consistent() -> None:
    response = HealthResponse(
        browser_ready=True,
        session_active=False,
        slots_available=1,
        started_at=datetime.now(UTC),
    )
    assert __version__ == "0.1.0"
    assert response.version == __version__
    assert response.api_version == API_VERSION == "v1"


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        CreateSessionRequest.model_validate({"max_vision_steps": 25, "credential_token": "no"})


@pytest.mark.parametrize("value", [0, 101])
def test_vision_budget_is_bounded(value: int) -> None:
    with pytest.raises(ValidationError):
        CreateSessionRequest(max_vision_steps=value)


def test_create_timestamps_are_ordered_and_aware() -> None:
    created = datetime.now(UTC)
    response = CreateSessionResponse(
        session_id=uuid4(),
        max_vision_steps=25,
        view_url="http://127.0.0.1:6080/vnc.html",
        created_at=created,
        expires_at=created + timedelta(hours=1),
    )
    assert response.expires_at > response.created_at

    with pytest.raises(ValidationError):
        CreateSessionResponse(
            session_id=uuid4(),
            max_vision_steps=25,
            view_url="http://127.0.0.1:6080/vnc.html",
            created_at=created,
            expires_at=created,
        )


def test_url_and_selector_bounds() -> None:
    with pytest.raises(ValidationError):
        NavigateRequest(session_id=uuid4(), url="h" * 2049)
    with pytest.raises(ValidationError):
        InteractionTarget(selector="x" * 2049)


def test_target_requires_selector_or_complete_coordinates() -> None:
    assert InteractionTarget(selector="#submit").selector == "#submit"
    assert InteractionTarget(x=10, y=20).x == 10
    with pytest.raises(ValidationError):
        InteractionTarget(x=10)
    with pytest.raises(ValidationError):
        InteractionTarget(selector="#submit", x=10, y=20)


def test_interaction_shapes_are_closed() -> None:
    session_id = uuid4()
    click = InteractRequest(
        session_id=session_id,
        action=InteractionAction.CLICK,
        target=InteractionTarget(selector="button"),
    )
    assert click.action is InteractionAction.CLICK

    with pytest.raises(ValidationError):
        InteractRequest(session_id=session_id, action="click")
    with pytest.raises(ValidationError):
        InteractRequest(session_id=session_id, action="scroll", delta_y=0)
    with pytest.raises(ValidationError):
        InteractRequest(session_id=session_id, action="press", key="F13")
    with pytest.raises(ValidationError):
        InteractRequest(
            session_id=session_id,
            action="type",
            target={"selector": "input"},
            text="x" * 16_385,
        )
    with pytest.raises(ValidationError):
        InteractRequest(
            session_id=session_id,
            action="click",
            target={"selector": "button"},
            javascript="alert(1)",
        )


def test_allowlisted_key_combination() -> None:
    request = InteractRequest(
        session_id=uuid4(),
        action=InteractionAction.PRESS,
        key=AllowedKey.CONTROL_A,
    )
    assert request.key is AllowedKey.CONTROL_A
