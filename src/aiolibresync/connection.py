"""One TCP socket to the hub, framed, and reconnecting for ever.

Exponential backoff with jitter and **unlimited retries**: the hub reboots,
powers off and changes IP, and an integration that gives up after N attempts is
one the user has to restart by hand.

The two ports share this class but not a codec — the framings genuinely differ,
and pretending otherwise costs more than it saves.
"""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable

from .exceptions import NotConnectedError
from .frames import FrameReader

_LOGGER = logging.getLogger(__name__)


def backoff_delay(
    attempt: int,
    base: float,
    maximum: float,
    rng: Callable[[], float] = random.random,
) -> float:
    """Exponential backoff with half jitter, capped at `maximum`.

    Jitter matters because both sockets reconnect together: without it they
    retry in lockstep for ever.
    """
    ceiling: float = min(maximum, base * 2 ** min(attempt, 16))
    return ceiling * (0.5 + 0.5 * rng())


async def cancel_and_wait(task: asyncio.Task[None]) -> None:
    """Cancel `task` and wait for it to end, without swallowing our own cancellation.

    The CancelledError that `task` ends with is expected and absorbed. One
    aimed at the caller, which arrives at the same `await`, is re-raised, so a
    caller cancelled while tearing down still learns it was cancelled.
    """
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            raise


class Connection:
    """A reconnecting reader/writer pair for one port."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        reader_factory: Callable[[], FrameReader],
        on_frame: Callable[[bytes], None],
        on_availability: Callable[[bool], None],
        on_connect: Callable[["Connection"], Awaitable[None]] | None = None,
        base_delay: float,
        max_delay: float,
    ) -> None:
        self.host = host
        self.port = port
        self._reader_factory = reader_factory
        self._on_frame = on_frame
        self._on_availability = on_availability
        self._on_connect = on_connect
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._writer: asyncio.StreamWriter | None = None
        self._task: asyncio.Task[None] | None = None
        self._connected = False
        self._ready = False
        self.resyncs = 0

    @property
    def connected(self) -> bool:
        """Open, and through the connect hook: ready for the caller's commands.

        The hook itself sends before this is true; `send` only needs the
        socket.
        """
        return self._ready

    @property
    def local_ip(self) -> str | None:
        """The address the kernel chose for this connection.

        Exact by construction, which is what the 7777 registration wants —
        better than guessing with a UDP socket, and it cannot fall back to
        127.0.0.1.

        Never None during `on_connect`: `_writer` is assigned with no
        intervening await, so a hook's None check is defensive-by-invariant.
        """
        if self._writer is None:
            return None
        sockname = self._writer.get_extra_info("sockname")
        return str(sockname[0]) if sockname else None

    async def start(self) -> None:
        """Start the reconnect loop, or do nothing if it is already running.

        Guarded because callers retry — Home Assistant re-runs
        `async_setup_entry`, and connecting never raises. A second
        `create_task` would orphan the first loop, and the two would then share
        `_writer` and `_connected` on this one instance: the orphan closes the
        live socket and goes on driving callbacks into a discarded client.
        """
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name=f"aiolibresync-{self.port}")

    async def stop(self) -> None:
        task, self._task = self._task, None
        try:
            if task is not None:
                await cancel_and_wait(task)
        finally:
            self._close_writer()

    def drop(self) -> None:
        """Force the current socket down; `_run` reconnects and re-runs on_connect.

        Protocol-agnostic on purpose. The caller decides *when* a session is
        beyond saving — this only knows how to make one start over.
        """
        self._close_writer()

    async def send(self, *frames: bytes) -> None:
        """Write each frame in turn to the socket, or raise NotConnectedError.

        One write and one drain per frame, never coalesced: whether the
        firmware accepts two frames in one segment has not been measured.

        A drop between the check and the drain raises OSError, and normalising
        it here makes NotConnectedError the one signal every caller has to know.
        """
        for frame in frames:
            writer = self._writer
            if writer is None or not self._connected:
                raise NotConnectedError(f"{self.host}:{self.port} is not connected")
            try:
                writer.write(frame)
                await writer.drain()
            except OSError as err:
                raise NotConnectedError(f"{self.host}:{self.port} dropped mid-write") from err

    async def _run(self) -> None:
        attempt = 0
        while True:
            try:
                reader, writer = await asyncio.open_connection(self.host, self.port)
            except OSError as err:
                delay = backoff_delay(attempt, self._base_delay, self._max_delay)
                _LOGGER.debug(
                    "%s:%s unreachable (%s); retrying in %.1fs", self.host, self.port, err, delay
                )
                attempt += 1
                await asyncio.sleep(delay)
                continue

            self._writer = writer
            self._connected = True
            _LOGGER.debug("connected to %s:%s", self.host, self.port)
            proved = False
            try:
                if self._on_connect is not None:
                    await self._on_connect(self)
                # Only now: on 7777 the hook is the session registration, and a
                # command before it would be refused.
                self._ready = True
                self._notify_availability(True)
                proved = await self._read_loop(reader)
            except (OSError, asyncio.IncompleteReadError) as err:
                _LOGGER.debug("%s:%s dropped: %s", self.host, self.port, err)
            # A caller's bug must not end the reconnect loop, which is the whole
            # point of this class. CancelledError is not an Exception, so stop()
            # still cancels.
            except Exception:
                _LOGGER.exception("connect hook or read loop failed on %s:%s", self.host, self.port)
            finally:
                self._connected = self._ready = False
                self._close_writer()
                self._notify_availability(False)
            # A hub that accepts and drops without ever sending has not proved
            # the connection usable, so keep escalating instead of retrying at
            # the floor for ever and churning `available` on every cycle.
            attempt = 0 if proved else attempt + 1
            await asyncio.sleep(backoff_delay(attempt, self._base_delay, self._max_delay))

    def _notify_availability(self, available: bool) -> None:
        """Tell the caller the socket came up or went down.

        Guarded for the same reason `on_frame` is: a raising callback must not
        take the reconnect loop down with it.
        """
        try:
            self._on_availability(available)
        # A caller's bug must not kill the socket.
        except Exception:
            _LOGGER.exception("availability handler failed on %s:%s", self.host, self.port)

    async def _read_loop(self, reader: asyncio.StreamReader) -> bool:
        """Read until EOF, and report whether the peer ever sent anything.

        That answer is what tells the backoff the connection had proved itself.
        """
        framer = self._reader_factory()
        received = False
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                return received
            received = True
            for frame in framer.feed(chunk):
                try:
                    self._on_frame(frame)
                # A decoder bug must not kill the socket.
                except Exception:
                    _LOGGER.exception("frame handler failed on %s", frame.hex(" "))
            if framer.dropped:
                self.resyncs += framer.dropped
                _LOGGER.debug("resynced past %d byte(s) on %s", framer.dropped, self.port)
                framer.dropped = 0

    def _close_writer(self) -> None:
        if self._writer is not None:
            self._writer.close()
            self._writer = None
