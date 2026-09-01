from __future__ import annotations

import pytest

from examples import demo


class _FakeResponse:
    def __init__(self, status: int, reason: str, body: bytes) -> None:
        self.status = status
        self.reason = reason
        self.body = body

    def read(self) -> bytes:
        return self.body


class _FakeConnection:
    def __init__(self, host: str, port: int, timeout: int, response: _FakeResponse) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.response = response
        self.requests: list[tuple[str, str, bytes | None, dict[str, str]]] = []
        self.closed = False

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None,
        headers: dict[str, str],
    ) -> None:
        self.requests.append((method, path, body, headers.copy()))

    def getresponse(self) -> _FakeResponse:
        return self.response

    def close(self) -> None:
        self.closed = True


def _install_fake_connection(
    monkeypatch: pytest.MonkeyPatch,
    response: _FakeResponse,
) -> list[_FakeConnection]:
    connections: list[_FakeConnection] = []

    def factory(host: str, port: int, *, timeout: int) -> _FakeConnection:
        connection = _FakeConnection(host, port, timeout, response)
        connections.append(connection)
        return connection

    monkeypatch.setattr(demo.http.client, "HTTPConnection", factory)
    return connections


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
    assert demo.validate_api_base(value) == expected


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
        demo.validate_api_base(value)


def test_request_json_ignores_inherited_proxy_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://192.0.2.1:3128")
    monkeypatch.setenv("NO_PROXY", "")
    connections = _install_fake_connection(
        monkeypatch,
        _FakeResponse(200, "OK", b'{"status":"ok"}'),
    )

    assert demo.request_json("http://127.0.0.1:8092", "/browser/health") == {
        "status": "ok"
    }
    assert len(connections) == 1
    connection = connections[0]
    assert (connection.host, connection.port, connection.timeout) == ("127.0.0.1", 8092, 30)
    assert connection.requests == [
        ("GET", "/browser/health", None, {"Accept": "application/json"})
    ]
    assert connection.closed


def test_request_json_refuses_redirect_without_a_second_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connections = _install_fake_connection(
        monkeypatch,
        _FakeResponse(302, "Found", b""),
    )

    with pytest.raises(RuntimeError, match="refused HTTP redirect 302"):
        demo.request_json("http://127.0.0.1:8092", "/browser/health")

    assert len(connections) == 1
    assert len(connections[0].requests) == 1
    assert connections[0].closed
