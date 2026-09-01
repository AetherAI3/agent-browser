"""Loopback-only SOCKS5 egress bound to per-session endpoint pins."""

from __future__ import annotations

import asyncio
import inspect
import ipaddress
import math
import socket
from collections.abc import Awaitable, Callable
from typing import Final, Protocol

DEFAULT_HANDSHAKE_TIMEOUT_SECONDS: Final = 3.0
DEFAULT_CONNECT_TIMEOUT_SECONDS: Final = 10.0
DEFAULT_IDLE_TIMEOUT_SECONDS: Final = 60.0
DEFAULT_CLEANUP_TIMEOUT_SECONDS: Final = 3.0
DEFAULT_MAX_CONCURRENT_CONNECTIONS: Final = 64
DEFAULT_STREAM_LIMIT: Final = 64 * 1024
DEFAULT_RELAY_CHUNK_SIZE: Final = 16 * 1024

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class NumericConnectionPlan(Protocol):
    """Structural plan interface that keeps the proxy independent of policy."""

    @property
    def port(self) -> int: ...

    @property
    def addresses(self) -> tuple[IPAddress, ...]: ...


ConnectionPlanner = Callable[
    [str, int],
    NumericConnectionPlan | Awaitable[NumericConnectionPlan],
]
NumericDialer = Callable[
    [IPAddress, int],
    Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]],
]


class EgressProxyError(RuntimeError):
    """The owned egress proxy could not start or shut down safely."""


class _SocksRefusal(Exception):
    __slots__ = ("reply",)

    def __init__(self, reply: int) -> None:
        self.reply = reply


class _NegotiationRefusal(Exception):
    pass


async def _await_plan(
    value: NumericConnectionPlan | Awaitable[NumericConnectionPlan],
) -> NumericConnectionPlan:
    if inspect.isawaitable(value):
        return await value
    return value


async def _numeric_dialer(
    address: IPAddress,
    port: int,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Connect a numeric address without invoking a name resolver."""

    family = socket.AF_INET if address.version == 4 else socket.AF_INET6
    target_socket = socket.socket(family, socket.SOCK_STREAM)
    target_socket.setblocking(False)
    try:
        await asyncio.get_running_loop().sock_connect(
            target_socket,
            (address.compressed, port),
        )
        return await asyncio.open_connection(sock=target_socket)
    except BaseException:
        target_socket.close()
        raise


async def _drain_owned_task(
    task: asyncio.Future[None],
    cancellation: asyncio.CancelledError | None = None,
) -> asyncio.CancelledError | None:
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            cancellation = cancellation or error
        except Exception:
            break
    return cancellation


class PinnedSocks5Proxy:
    """A bounded no-auth CONNECT proxy that can dial only published pins."""

    def __init__(
        self,
        connection_planner: ConnectionPlanner,
        *,
        listen_host: str = "127.0.0.1",
        listen_port: int = 0,
        handshake_timeout_seconds: float = DEFAULT_HANDSHAKE_TIMEOUT_SECONDS,
        connect_timeout_seconds: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
        idle_timeout_seconds: float = DEFAULT_IDLE_TIMEOUT_SECONDS,
        cleanup_timeout_seconds: float = DEFAULT_CLEANUP_TIMEOUT_SECONDS,
        max_concurrent_connections: int = DEFAULT_MAX_CONCURRENT_CONNECTIONS,
        stream_limit: int = DEFAULT_STREAM_LIMIT,
        relay_chunk_size: int = DEFAULT_RELAY_CHUNK_SIZE,
        dialer: NumericDialer | None = None,
    ) -> None:
        try:
            address = ipaddress.ip_address(listen_host)
        except ValueError:
            raise ValueError("SOCKS listener host must be a numeric loopback address") from None
        if not address.is_loopback or address.ipv4_mapped is not None:
            raise ValueError("SOCKS listener host must be a numeric loopback address")
        if not callable(connection_planner) or (dialer is not None and not callable(dialer)):
            raise ValueError("SOCKS callbacks must be callable")
        if type(listen_port) is not int or not 0 <= listen_port <= 65_535:
            raise ValueError("SOCKS listener port is outside the supported range")
        for timeout in (
            handshake_timeout_seconds,
            connect_timeout_seconds,
            idle_timeout_seconds,
            cleanup_timeout_seconds,
        ):
            if (
                isinstance(timeout, bool)
                or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout)
                or timeout <= 0
            ):
                raise ValueError("SOCKS timeouts must be positive and finite")
        if (
            type(max_concurrent_connections) is not int
            or not 1 <= max_concurrent_connections <= 1_024
            or type(stream_limit) is not int
            or not 1_024 <= stream_limit <= 1024 * 1024
            or type(relay_chunk_size) is not int
            or not 1_024 <= relay_chunk_size <= stream_limit
        ):
            raise ValueError("SOCKS resource bounds are outside the supported range")

        self._planner = connection_planner
        self._listen_address = address
        self._requested_port = listen_port
        self._handshake_timeout = float(handshake_timeout_seconds)
        self._connect_timeout = float(connect_timeout_seconds)
        self._idle_timeout = float(idle_timeout_seconds)
        self._cleanup_timeout = float(cleanup_timeout_seconds)
        self._stream_limit = stream_limit
        self._relay_chunk_size = relay_chunk_size
        self._max_concurrent_connections = max_concurrent_connections
        self._dialer = dialer or _numeric_dialer
        self._capacity = asyncio.Semaphore(max_concurrent_connections)
        self._connections: set[asyncio.Task[None]] = set()
        self._server: asyncio.AbstractServer | None = None
        self._bound_port: int | None = None
        self._closing = False

    @property
    def is_running(self) -> bool:
        return self._server is not None and self._bound_port is not None and not self._closing

    @property
    def host(self) -> str:
        return self._listen_address.compressed

    @property
    def port(self) -> int:
        if self._bound_port is None:
            raise EgressProxyError("The SOCKS proxy is not running.")
        return self._bound_port

    @property
    def server_url(self) -> str:
        host = f"[{self.host}]" if self._listen_address.version == 6 else self.host
        return f"socks5://{host}:{self.port}"

    async def start(self) -> None:
        if self._server is not None or self._closing:
            raise EgressProxyError("The SOCKS proxy is already active.")
        family = socket.AF_INET if self._listen_address.version == 4 else socket.AF_INET6
        try:
            async with asyncio.timeout(self._connect_timeout):
                server = await asyncio.start_server(
                    self._handle_client,
                    host=self.host,
                    port=self._requested_port,
                    family=family,
                    backlog=self._max_concurrent_connections,
                    limit=self._stream_limit,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            raise EgressProxyError("The SOCKS proxy could not be started.") from None

        sockets = tuple(server.sockets or ())
        if len(sockets) != 1:
            server.close()
            await server.wait_closed()
            raise EgressProxyError("The SOCKS proxy listener is ambiguous.")
        bound = sockets[0].getsockname()
        try:
            bound_address = ipaddress.ip_address(str(bound[0]))
            bound_port = int(bound[1])
        except (IndexError, TypeError, ValueError):
            server.close()
            await server.wait_closed()
            raise EgressProxyError("The SOCKS proxy listener is invalid.") from None
        if bound_address != self._listen_address or not 1 <= bound_port <= 65_535:
            server.close()
            await server.wait_closed()
            raise EgressProxyError("The SOCKS proxy listener is invalid.")
        self._server = server
        self._bound_port = bound_port

    async def close(self) -> None:
        if self._server is None and not self._connections:
            self._bound_port = None
            return
        if self._closing:
            raise EgressProxyError("SOCKS proxy cleanup is already in progress.")
        self._closing = True
        cleanup = asyncio.create_task(
            self._close_owned_resources(),
            name="aether-socks-cleanup",
        )
        try:
            cancellation = await _drain_owned_task(cleanup)
            failure: EgressProxyError | None = None
            try:
                cleanup.result()
            except asyncio.CancelledError as error:
                cancellation = cancellation or error
            except EgressProxyError as error:
                failure = error
            except Exception:
                failure = EgressProxyError("The SOCKS proxy did not shut down safely.")
            if cancellation is not None:
                raise cancellation
            if failure is not None:
                raise failure
        finally:
            self._closing = False

    async def _close_owned_resources(self) -> None:
        server = self._server
        if server is not None:
            server.close()
            try:
                async with asyncio.timeout(self._cleanup_timeout):
                    await server.wait_closed()
            except asyncio.CancelledError:
                raise
            except Exception:
                raise EgressProxyError("The SOCKS proxy listener did not close.") from None
            self._server = None
            self._bound_port = None

        # Let accepted callbacks register their current tasks before taking the
        # ownership snapshot, then cancel and drain every live tunnel.
        await asyncio.sleep(0)
        current = asyncio.current_task()
        tasks = tuple(task for task in self._connections if task is not current)
        for task in tasks:
            task.cancel()
        if tasks:
            try:
                async with asyncio.timeout(self._cleanup_timeout):
                    await asyncio.gather(*tasks, return_exceptions=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                raise EgressProxyError("SOCKS connections did not close.") from None
        self._connections.difference_update(tasks)

    async def _handle_client(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._connections.add(task)
        acquired = False
        remote_writer: asyncio.StreamWriter | None = None
        cancellation: asyncio.CancelledError | None = None
        try:
            # Callback entry is serialized until the first await, making this
            # a strict cap rather than an unbounded waiter queue.
            if self._capacity.locked():
                return
            await self._capacity.acquire()
            acquired = True
            try:
                async with asyncio.timeout(self._handshake_timeout):
                    await self._negotiate_no_auth(client_reader, client_writer)
                    host, port = await self._read_connect_request(client_reader)
                    try:
                        plan = await _await_plan(self._planner(host, port))
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        await self._send_reply(client_writer, 0x02)
                        return
                    if plan.port != port or not plan.addresses or len(plan.addresses) > 64:
                        await self._send_reply(client_writer, 0x02)
                        return
            except _SocksRefusal as refusal:
                await self._send_reply(client_writer, refusal.reply)
                return
            except _NegotiationRefusal:
                return

            try:
                remote_reader, remote_writer = await self._dial_plan(plan)
            except asyncio.CancelledError:
                raise
            except Exception:
                await self._send_reply(client_writer, 0x05)
                return
            await self._send_reply(client_writer, 0x00)
            await self._relay_bidirectionally(
                client_reader,
                client_writer,
                remote_reader,
                remote_writer,
            )
        except (asyncio.IncompleteReadError, ConnectionError, TimeoutError):
            return
        except asyncio.CancelledError as error:
            cancellation = error
        except Exception:
            return
        finally:
            cleanup = asyncio.create_task(
                self._close_connection_writers(client_writer, remote_writer),
                name="aether-socks-connection-cleanup",
            )
            cancellation = await _drain_owned_task(cleanup, cancellation)
            try:
                cleanup.result()
            except asyncio.CancelledError as error:
                cancellation = cancellation or error
            except Exception:
                client_writer.close()
            if acquired:
                self._capacity.release()
            if task is not None:
                self._connections.discard(task)
            if cancellation is not None:
                raise cancellation

    async def _negotiate_no_auth(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        version, method_count = await reader.readexactly(2)
        if version != 5 or method_count == 0:
            writer.write(b"\x05\xff")
            await writer.drain()
            raise _NegotiationRefusal
        methods = await reader.readexactly(method_count)
        if 0x00 not in methods:
            writer.write(b"\x05\xff")
            await writer.drain()
            raise _NegotiationRefusal
        writer.write(b"\x05\x00")
        await writer.drain()

    async def _read_connect_request(
        self,
        reader: asyncio.StreamReader,
    ) -> tuple[str, int]:
        version, command, reserved, address_type = await reader.readexactly(4)
        if version != 5 or reserved != 0:
            raise _SocksRefusal(0x01)
        if command != 1:
            raise _SocksRefusal(0x07)
        if address_type == 1:
            host = str(ipaddress.IPv4Address(await reader.readexactly(4)))
        elif address_type == 4:
            host = str(ipaddress.IPv6Address(await reader.readexactly(16)))
        elif address_type == 3:
            length = (await reader.readexactly(1))[0]
            if length == 0 or length > 253:
                raise _SocksRefusal(0x08)
            try:
                host = (await reader.readexactly(length)).decode("ascii", "strict")
            except UnicodeDecodeError:
                raise _SocksRefusal(0x08) from None
        else:
            raise _SocksRefusal(0x08)
        port = int.from_bytes(await reader.readexactly(2), "big")
        if port == 0:
            raise _SocksRefusal(0x02)
        return host, port

    async def _dial_plan(
        self,
        plan: NumericConnectionPlan,
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._connect_timeout
        for address in plan.addresses:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                async with asyncio.timeout(remaining):
                    return await self._dialer(address, plan.port)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: S112 -- try the next already-authorized numeric pin.
                continue
        raise ConnectionError("No pinned address accepted the connection")

    async def _relay_bidirectionally(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        remote_reader: asyncio.StreamReader,
        remote_writer: asyncio.StreamWriter,
    ) -> None:
        activity = asyncio.Event()

        async def relay(
            source: asyncio.StreamReader,
            destination: asyncio.StreamWriter,
        ) -> None:
            while True:
                chunk = await source.read(self._relay_chunk_size)
                if not chunk:
                    return
                destination.write(chunk)
                await destination.drain()
                activity.set()

        upstream = asyncio.create_task(relay(client_reader, remote_writer))
        downstream = asyncio.create_task(relay(remote_reader, client_writer))
        relays = {upstream, downstream}
        try:
            while not any(task.done() for task in relays):
                activity.clear()
                wake = asyncio.create_task(activity.wait())
                done, _pending = await asyncio.wait(
                    {*relays, wake},
                    timeout=self._idle_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if not done:
                    wake.cancel()
                    await asyncio.gather(wake, return_exceptions=True)
                    return
                if wake not in done:
                    wake.cancel()
                    await asyncio.gather(wake, return_exceptions=True)
                    return
        finally:
            for relay_task in relays:
                relay_task.cancel()
            await asyncio.gather(*relays, return_exceptions=True)

    async def _send_reply(self, writer: asyncio.StreamWriter, reply: int) -> None:
        async with asyncio.timeout(self._handshake_timeout):
            writer.write(bytes((5, reply, 0, 1, 0, 0, 0, 0, 0, 0)))
            await writer.drain()

    async def _close_writer(self, writer: asyncio.StreamWriter) -> None:
        writer.close()
        try:
            async with asyncio.timeout(self._cleanup_timeout):
                await writer.wait_closed()
        except asyncio.CancelledError:
            raise
        except Exception:
            return

    async def _close_connection_writers(
        self,
        client_writer: asyncio.StreamWriter,
        remote_writer: asyncio.StreamWriter | None,
    ) -> None:
        if remote_writer is not None:
            await self._close_writer(remote_writer)
        await self._close_writer(client_writer)


__all__ = [
    "ConnectionPlanner",
    "DEFAULT_CLEANUP_TIMEOUT_SECONDS",
    "DEFAULT_CONNECT_TIMEOUT_SECONDS",
    "DEFAULT_HANDSHAKE_TIMEOUT_SECONDS",
    "DEFAULT_IDLE_TIMEOUT_SECONDS",
    "DEFAULT_MAX_CONCURRENT_CONNECTIONS",
    "EgressProxyError",
    "NumericDialer",
    "NumericConnectionPlan",
    "PinnedSocks5Proxy",
]
