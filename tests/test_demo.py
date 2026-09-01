from __future__ import annotations

import pytest

from examples.demo import validate_api_base


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://127.0.0.1:8092", "http://127.0.0.1:8092"),
        ("http://127.0.0.2:80/", "http://127.0.0.2:80"),
        ("http://[::1]:8092", "http://[::1]:8092"),
    ],
)
def test_validate_api_base_accepts_only_canonicalizable_loopback_origins(
    value: str,
    expected: str,
) -> None:
    assert validate_api_base(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "https://127.0.0.1:8092",
        "http://localhost:8092",
        "http://192.0.2.1:8092",
        "http://127.0.0.1",
        "http://127.0.0.1:8092/path",
        "http://user@127.0.0.1:8092",
        "http://127.0.0.1:8092?next=https://example.com",
        "http://127.0.0.1:8092#fragment",
        "http://[::ffff:127.0.0.1]:8092",
        "http://[::1%25loopback]:8092",
        "not-a-url",
    ],
)
def test_validate_api_base_rejects_non_loopback_or_non_origin_values(value: str) -> None:
    with pytest.raises(ValueError, match="numeric-loopback root origin"):
        validate_api_base(value)
