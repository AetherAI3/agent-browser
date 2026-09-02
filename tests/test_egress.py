# ruff: noqa: S104 -- nonloopback binds are deliberate fail-closed inputs.

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import Awaitable, Callable

import pytest

from agent_browser.egress import PinnedSocks5Proxy
from agent_browser.policy import ConnectionPlan, NavigationPolicy

PUBLIC_ADDRESS = ipaddress.IPv4Address("93.184.216.34")


async def _open_client(
    proxy: PinnedSocks5Proxy,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection(proxy.host, proxy.port)


async def _negotiate(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    writer.write(b"\x05\x01\x00")
    await writer.drain()
    assert await reader.readexactly(2) == b"\x05\x00"


def _domain_request(hostname: str, port: int) -> bytes:
    encoded = hostname.encode("ascii")
    return b"\x05\x01\x00\x03" + bytes((len(encoded),)) + encoded + port.to_bytes(2, "big")


async def _close_client(writer: asyncio.StreamWriter) -> None:
    writer.close()
    # Refusal tests intentionally provoke a peer reset.  On Linux the reset is
    # retained by StreamReader and re-raised by wait_closed(), even after the
    # transport is already closed; yielding once is sufficient for test cleanup.
    await asyncio.sleep(0)


async def _echo_target(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    try:
        while data := await reader.read(4096):
            writer.write(data)
            await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_fragmented_socks_frames_use_only_the_published_numeric_pin() -> None:
    target = await asyncio.start_server(_echo_target, "127.0.0.1", 0)
    target_port = int(target.sockets[0].getsockname()[1])
    dialed: list[tuple[str, int]] = []

    async def planner(hostname: str, port: int) -> ConnectionPlan:
        assert hostname == "allowed.example.org"
        return ConnectionPlan(hostname, port, (PUBLIC_ADDRESS,))

    async def dialer(
        address: ipaddress.IPv4Address | ipaddress.IPv6Address,
        port: int,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        dialed.append((str(address), port))
        return await asyncio.open_connection("127.0.0.1", target_port)

    proxy = PinnedSocks5Proxy(planner, dialer=dialer)
    await proxy.start()
    reader, writer = await _open_client(proxy)
    try:
        for byte in b"\x05\x02\x02\x00":
            writer.write(bytes((byte,)))
            await writer.drain()
        assert await reader.readexactly(2) == b"\x05\x00"

        request = _domain_request("allowed.example.org", 443)
        for byte in request:
            writer.write(bytes((byte,)))
            await writer.drain()
        assert (await reader.readexactly(10))[1] == 0

        writer.write(b"through-the-pin")
        await writer.drain()
        assert await reader.readexactly(15) == b"through-the-pin"
        assert dialed == [(str(PUBLIC_ADDRESS), 443)]
    finally:
        await _close_client(writer)
        await proxy.close()
        target.close()
        await target.wait_closed()


@pytest.mark.asyncio
async def test_unknown_hostname_and_wrong_port_are_refused_without_dial() -> None:
    resolver_calls: list[str] = []

    async def resolver(hostname: str) -> list[str]:
        resolver_calls.append(hostname)
        return [str(PUBLIC_ADDRESS)]

    guard = NavigationPolicy(resolver).new_guard()
    await guard.authorize_request("https://known.example.org/")
    dialed = False

    async def dialer(
        _address: ipaddress.IPv4Address | ipaddress.IPv6Address,
        _port: int,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        nonlocal dialed
        dialed = True
        raise AssertionError("a refused endpoint must not be dialed")

    proxy = PinnedSocks5Proxy(guard.connection_plan, dialer=dialer)
    refusal_generation = proxy.planner_refusal_generation
    await proxy.start()
    try:
        for hostname, port in (
            ("unknown.example.org", 443),
            ("known.example.org", 8443),
        ):
            reader, writer = await _open_client(proxy)
            try:
                await _negotiate(reader, writer)
                writer.write(_domain_request(hostname, port))
                await writer.drain()
                assert (await reader.readexactly(10))[1] == 2
            finally:
                await _close_client(writer)
    finally:
        await proxy.close()

    assert not dialed
    assert resolver_calls == ["known.example.org"]
    assert proxy.planner_refusal_generation == refusal_generation + 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("preamble", "expected"),
    [
        (b"\x04\x01\x00", b"\x05\xff"),
        (b"\x05\x01\x02", b"\x05\xff"),
    ],
)
async def test_malformed_or_non_no_auth_negotiation_fails_closed(
    preamble: bytes,
    expected: bytes,
) -> None:
    async def planner(_hostname: str, _port: int) -> ConnectionPlan:
        raise AssertionError("malformed negotiation reached the planner")

    proxy = PinnedSocks5Proxy(planner)
    await proxy.start()
    reader, writer = await _open_client(proxy)
    try:
        writer.write(preamble)
        await writer.drain()
        assert await reader.readexactly(2) == expected
        assert await reader.read() == b""
    finally:
        await _close_client(writer)
        await proxy.close()


@pytest.mark.asyncio
async def test_unsupported_command_and_oversized_domain_are_bounded() -> None:
    async def planner(_hostname: str, _port: int) -> ConnectionPlan:
        raise AssertionError("malformed request reached the planner")

    proxy = PinnedSocks5Proxy(planner)
    await proxy.start()
    try:
        reader, writer = await _open_client(proxy)
        await _negotiate(reader, writer)
        writer.write(b"\x05\x02\x00\x01\x08\x08\x08\x08\x00\x50")
        await writer.drain()
        assert (await reader.readexactly(10))[1] == 7
        await _close_client(writer)

        reader, writer = await _open_client(proxy)
        await _negotiate(reader, writer)
        writer.write(b"\x05\x01\x00\x03\xfe")
        await writer.drain()
        assert (await reader.readexactly(10))[1] == 8
        await _close_client(writer)
    finally:
        await proxy.close()


@pytest.mark.asyncio
async def test_handshake_connect_and_idle_timeouts_close_connections() -> None:
    never = asyncio.Event()

    async def planner(hostname: str, port: int) -> ConnectionPlan:
        return ConnectionPlan(hostname, port, (PUBLIC_ADDRESS,))

    async def blocked_dialer(
        _address: ipaddress.IPv4Address | ipaddress.IPv6Address,
        _port: int,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        await never.wait()
        raise AssertionError("unreachable")

    proxy = PinnedSocks5Proxy(
        planner,
        dialer=blocked_dialer,
        handshake_timeout_seconds=0.02,
        connect_timeout_seconds=0.02,
        idle_timeout_seconds=0.02,
    )
    await proxy.start()
    try:
        reader, writer = await _open_client(proxy)
        assert await asyncio.wait_for(reader.read(), timeout=1.0) == b""
        await _close_client(writer)

        reader, writer = await _open_client(proxy)
        await _negotiate(reader, writer)
        writer.write(_domain_request("timeout.example.org", 443))
        await writer.drain()
        assert (await reader.readexactly(10))[1] == 5
        await _close_client(writer)
    finally:
        await proxy.close()

    target = await asyncio.start_server(_echo_target, "127.0.0.1", 0)
    target_port = int(target.sockets[0].getsockname()[1])

    async def connected_dialer(
        _address: ipaddress.IPv4Address | ipaddress.IPv6Address,
        _port: int,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        return await asyncio.open_connection("127.0.0.1", target_port)

    idle_proxy = PinnedSocks5Proxy(
        planner,
        dialer=connected_dialer,
        idle_timeout_seconds=0.02,
    )
    await idle_proxy.start()
    reader, writer = await _open_client(idle_proxy)
    try:
        await _negotiate(reader, writer)
        writer.write(_domain_request("idle.example.org", 443))
        await writer.drain()
        assert (await reader.readexactly(10))[1] == 0
        assert await asyncio.wait_for(reader.read(), timeout=1.0) == b""
    finally:
        await _close_client(writer)
        await idle_proxy.close()
        target.close()
        await target.wait_closed()


@pytest.mark.asyncio
async def test_concurrency_limit_rejects_excess_connection_without_wait_queue() -> None:
    dial_started = asyncio.Event()
    release_dial = asyncio.Event()

    async def planner(hostname: str, port: int) -> ConnectionPlan:
        return ConnectionPlan(hostname, port, (PUBLIC_ADDRESS,))

    async def dialer(
        _address: ipaddress.IPv4Address | ipaddress.IPv6Address,
        _port: int,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        dial_started.set()
        await release_dial.wait()
        raise ConnectionError

    proxy = PinnedSocks5Proxy(
        planner,
        dialer=dialer,
        max_concurrent_connections=1,
    )
    await proxy.start()
    first_reader, first_writer = await _open_client(proxy)
    second_writer: asyncio.StreamWriter | None = None
    try:
        await _negotiate(first_reader, first_writer)
        first_writer.write(_domain_request("first.example.org", 443))
        await first_writer.drain()
        await asyncio.wait_for(dial_started.wait(), timeout=1.0)

        second_reader, second_writer = await _open_client(proxy)
        second_writer.write(b"\x05\x01\x00")
        await second_writer.drain()
        try:
            refusal = await asyncio.wait_for(second_reader.read(), timeout=1.0)
        except ConnectionResetError:
            refusal = b""
        assert refusal == b""
    finally:
        release_dial.set()
        await _close_client(first_writer)
        if second_writer is not None:
            await _close_client(second_writer)
        await proxy.close()


@pytest.mark.asyncio
async def test_repeated_close_cancellation_still_drains_owned_connections() -> None:
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()

    async def planner(hostname: str, port: int) -> ConnectionPlan:
        return ConnectionPlan(hostname, port, (PUBLIC_ADDRESS,))

    class BlockingCleanupProxy(PinnedSocks5Proxy):
        async def _close_connection_writers(
            self,
            client_writer: asyncio.StreamWriter,
            remote_writer: asyncio.StreamWriter | None,
        ) -> None:
            cleanup_started.set()
            await release_cleanup.wait()
            await super()._close_connection_writers(client_writer, remote_writer)

    proxy = BlockingCleanupProxy(planner)
    await proxy.start()
    _reader, writer = await _open_client(proxy)
    closing = asyncio.create_task(proxy.close())
    await asyncio.wait_for(cleanup_started.wait(), timeout=1.0)
    closing.cancel()
    await asyncio.sleep(0)
    closing.cancel()
    await asyncio.sleep(0)
    assert not closing.done()

    release_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert not proxy.is_running
    assert not proxy._connections
    await _close_client(writer)


@pytest.mark.parametrize(
    "factory",
    [
        lambda planner: PinnedSocks5Proxy(planner, listen_host="0.0.0.0"),
        lambda planner: PinnedSocks5Proxy(planner, listen_host="localhost"),
        lambda planner: PinnedSocks5Proxy(planner, max_concurrent_connections=0),
        lambda planner: PinnedSocks5Proxy(planner, stream_limit=128),
        lambda planner: PinnedSocks5Proxy(planner, handshake_timeout_seconds=float("nan")),
    ],
)
def test_proxy_configuration_limits_fail_closed(
    factory: Callable[[Callable[[str, int], Awaitable[ConnectionPlan]]], PinnedSocks5Proxy],
) -> None:
    async def planner(hostname: str, port: int) -> ConnectionPlan:
        return ConnectionPlan(hostname, port, (PUBLIC_ADDRESS,))

    with pytest.raises(ValueError):
        factory(planner)
