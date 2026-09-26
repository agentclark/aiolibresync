"""Backoff arithmetic, and a socket that comes back after the server drops it."""

import asyncio
import socket
import time
from itertools import pairwise

import pytest
from conftest import until

from aiolibresync.connection import Connection, backoff_delay, cancel_and_wait
from aiolibresync.exceptions import NotConnectedError
from aiolibresync.frames import ControlFrameReader

FRAME = bytes.fromhex("000400020e01")


def _connection(port: int, **kwargs) -> Connection:
    """A Connection with the boring arguments filled in."""
    kwargs.setdefault("reader_factory", ControlFrameReader)
    kwargs.setdefault("on_frame", lambda _: None)
    kwargs.setdefault("on_availability", lambda _: None)
    kwargs.setdefault("base_delay", 0.01)
    kwargs.setdefault("max_delay", 0.02)
    return Connection("127.0.0.1", port, **kwargs)


def test_backoff_grows_and_is_capped():
    ceilings = [backoff_delay(n, base=1.0, maximum=60.0, rng=lambda: 1.0) for n in range(10)]
    assert ceilings[0] == 1.0
    assert ceilings[1] == 2.0
    assert ceilings == sorted(ceilings)
    assert max(ceilings) == 60.0


def test_backoff_jitter_stays_between_half_and_full():
    assert backoff_delay(3, base=1.0, maximum=60.0, rng=lambda: 0.0) == 4.0
    assert backoff_delay(3, base=1.0, maximum=60.0, rng=lambda: 1.0) == 8.0


async def test_connection_reads_frames_and_reconnects_after_a_drop():
    seen: list[bytes] = []
    up: list[bool] = []
    server_writers: list[asyncio.StreamWriter] = []

    async def handle(reader, writer):
        server_writers.append(writer)
        writer.write(bytes.fromhex("000400020e01"))
        await writer.drain()
        await reader.read(1024)
        writer.close()  # or wait_closed() blocks on a transport still attached

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    conn = Connection(
        "127.0.0.1",
        port,
        reader_factory=ControlFrameReader,
        on_frame=seen.append,
        on_availability=up.append,
        base_delay=0.01,
        max_delay=0.02,
    )
    await conn.start()
    await until(lambda: len(seen) == 1)

    server_writers[0].close()  # the hub reboots
    await until(lambda: len(seen) == 2, timeout=5.0)

    assert seen == [bytes.fromhex("000400020e01")] * 2
    assert up[:3] == [True, False, True]

    await conn.stop()
    server.close()
    await server.wait_closed()


async def test_connection_runs_the_connect_hook_before_anything_else():
    """The 7777 session must register before it may command anything."""
    got: list[bytes] = []

    async def handle(reader, writer):
        got.append(await reader.read(5))
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    async def on_connect(conn):
        await conn.send(b"hello")

    conn = Connection(
        "127.0.0.1",
        port,
        reader_factory=ControlFrameReader,
        on_frame=lambda _: None,
        on_availability=lambda _: None,
        on_connect=on_connect,
        base_delay=0.01,
        max_delay=0.02,
    )
    await conn.start()
    # `got[:1]`, not `got`: the socket keeps reconnecting and the hook keeps
    # firing, so the list grows between polls.
    await until(lambda: got[:1] == [b"hello"])
    await conn.stop()
    server.close()
    await server.wait_closed()


async def test_send_on_a_socket_that_never_came_up_raises():
    with pytest.raises(NotConnectedError):
        await _connection(1).send(b"hello")


async def test_send_reports_a_drop_as_not_connected_never_as_an_oserror():
    """NotConnectedError is the one failure signal every caller has to know."""
    server_writers: list[asyncio.StreamWriter] = []

    async def handle(reader, writer):
        server_writers.append(writer)
        await reader.read(65536)
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    conn = _connection(server.sockets[0].getsockname()[1])
    await conn.start()
    await until(lambda: conn.connected)

    server_writers[0].close()  # the hub goes away mid-conversation
    with pytest.raises(NotConnectedError):
        for _ in range(100):
            await conn.send(b"x" * 65536)

    await conn.stop()
    server.close()
    await server.wait_closed()


async def test_connection_retries_a_refused_port_until_a_server_appears():
    """The hub is often off when Home Assistant starts, and comes up later."""
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()  # nothing listens here, so connect() is refused

    up: list[bool] = []
    conn = _connection(port, on_availability=up.append)
    await conn.start()
    await asyncio.sleep(0.05)  # several trips through the OSError branch
    assert up == []

    async def handle(reader, writer):
        writer.write(FRAME)
        await writer.drain()
        await reader.read(1024)
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", port)
    await until(lambda: up[:1] == [True])

    await conn.stop()
    server.close()
    await server.wait_closed()


async def test_backoff_escalates_when_the_hub_accepts_and_drops():
    """Accepting and closing without sending is not proof of a usable link.

    A hub stuck this way mid-reboot would otherwise be retried at the floor for
    ever, churning `available` on every cycle.
    """
    accepted: list[float] = []

    async def handle(reader, writer):
        accepted.append(time.monotonic())
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    conn = _connection(server.sockets[0].getsockname()[1], base_delay=0.02, max_delay=1.0)
    await conn.start()
    await until(lambda: len(accepted) == 5, timeout=5.0)
    await conn.stop()

    gaps = [b - a for a, b in pairwise(accepted)]
    # Gap n lies in [0.5, 1] x base x 2**(n+1), so gaps three apart cannot
    # overlap however the jitter falls.
    assert gaps[3] > gaps[0]

    server.close()
    await server.wait_closed()


async def test_a_raising_frame_handler_does_not_stop_later_frames():
    seen: list[bytes] = []

    def on_frame(frame: bytes) -> None:
        seen.append(frame)
        if len(seen) == 1:
            raise ValueError("a decoder bug")

    async def handle(reader, writer):
        writer.write(FRAME * 2)
        await writer.drain()
        await reader.read(1024)
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    conn = _connection(server.sockets[0].getsockname()[1], on_frame=on_frame)
    await conn.start()
    await until(lambda: seen == [FRAME, FRAME])

    await conn.stop()
    server.close()
    await server.wait_closed()


async def test_a_raising_availability_handler_does_not_stop_reconnection():
    """An integration bug in a callback must not silently end the retry loop."""
    seen: list[bytes] = []
    up: list[bool] = []

    def on_availability(available: bool) -> None:
        up.append(available)
        raise RuntimeError("an integration bug")

    async def handle(reader, writer):
        writer.write(FRAME)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    conn = _connection(
        server.sockets[0].getsockname()[1],
        on_frame=seen.append,
        on_availability=on_availability,
    )
    await conn.start()
    await until(lambda: len(seen) >= 3, timeout=5.0)
    assert up[:3] == [True, False, True]

    await conn.stop()
    server.close()
    await server.wait_closed()


async def test_cancel_and_wait_keeps_the_callers_own_cancellation():
    """A teardown that is itself cancelled must still end cancelled."""

    async def slow_to_stop() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            await asyncio.sleep(0.1)  # winding down takes a moment
            raise

    inner = asyncio.create_task(slow_to_stop())
    await asyncio.sleep(0)
    outer = asyncio.create_task(cancel_and_wait(inner))
    await asyncio.sleep(0.01)
    outer.cancel()

    with pytest.raises(asyncio.CancelledError):
        await outer


async def test_cancel_and_wait_absorbs_the_tasks_cancellation():
    inner = asyncio.create_task(asyncio.sleep(10))
    await asyncio.sleep(0)
    await cancel_and_wait(inner)
    assert inner.cancelled()
