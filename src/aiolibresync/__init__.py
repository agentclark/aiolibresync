"""Async client for Libre Wireless LibreSync audio devices.

    import asyncio
    from aiolibresync import DeviceState, LibreSyncClient

    async def main() -> None:
        client = LibreSyncClient("192.168.1.50")
        ready = asyncio.Event()

        def on_state(state: DeviceState) -> None:
            print(state)
            if state.available:
                ready.set()

        client.subscribe(on_state)
        await client.async_connect()
        await ready.wait()
        await client.async_set_power(True)
        await asyncio.sleep(60)
        await client.async_disconnect()

    asyncio.run(main())

`async_connect()` returns as soon as the two sockets have been *scheduled*, not
once they are open, so a command issued on the next line raises
`NotConnectedError`. `state.available` is the readiness signal — it turns true
when both ports are connected — and waiting on it is what the example's
`asyncio.Event` is for. It goes false again on any disconnection, so a long-
running program should treat it as a condition, not a one-off.

`DeviceState` reports playback twice, because the hub does. `play_state` is the
streaming renderer's transport and answers `PLAYING` to a physical input with
nothing connected to it; `audio_state` is whether sound is coming out, from
`02 21` byte 8, and is right on all ten sources. **Read `state.playback`**,
which is the second wherever it is known and the first otherwise.

Finding a hub, when its address is not already known:

    from aiolibresync import async_discover

    for device in await async_discover():
        print(device.host, device.udn, device.name)

`async_discover()` sends a multicast search and waits the full timeout — it does
not return early on the first answer — and `async_probe(host)` performs the same
identification for an address the user already has. Both are read-only: a
search, an HTTP GET of the device description, and a TCP connect to 50006 that
sends nothing.

The codec is importable on its own — `aiolibresync.frames`,
`aiolibresync.systemcontrol`, `aiolibresync.media` — and does no I/O.
`frames.build_media` is deliberately not re-exported here: its `command`
argument is a LUCI message-box id, and the box space includes factory reset.
Use the named operations in `aiolibresync.media` instead.
"""

from .client import LibreSyncClient
from .const import CONTROL_PORT, MEDIA_PORT, POLL_INTERVAL
from .discovery import DiscoveredDevice, async_discover, async_probe
from .exceptions import (
    ConfirmationTimeout,
    LibreSyncError,
    NotConnectedError,
    UnsafeCommandError,
)
from .state import DeviceState, NowPlaying, PlayState, Source

__version__ = "0.2.0"

__all__ = [
    "CONTROL_PORT",
    "MEDIA_PORT",
    "POLL_INTERVAL",
    "ConfirmationTimeout",
    "DeviceState",
    "DiscoveredDevice",
    "LibreSyncClient",
    "LibreSyncError",
    "NotConnectedError",
    "NowPlaying",
    "PlayState",
    "Source",
    "UnsafeCommandError",
    "async_discover",
    "async_probe",
]
