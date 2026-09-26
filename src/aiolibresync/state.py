"""The device's state, as immutable value objects.

Nothing here mutates. The client builds a new DeviceState per change and hands
it to subscribers, so two tasks can never observe a half-applied update, and two
identical frames — which both protocols send routinely — produce the same object
and therefore no callback at all.
"""

from dataclasses import dataclass, replace
from enum import IntEnum
from typing import Any


class PlayState(IntEnum):
    """Values of the 7777 `0x33` push and of PlayView's `PlayState`.

    `IDLE` is both "stopped" and "between tracks" — see DeviceState.is_stopped.
    """

    PLAYING = 0
    IDLE = 1
    PAUSED = 2
    LOADING = 5


@dataclass(frozen=True, slots=True)
class Source:
    """One entry of the device's own source list. `index` is the JSON `ix`.

    Never compute this from the `02 21` source byte: that is a second numbering
    which disagrees with `ix` on the two wireless inputs.
    """

    index: int
    name: str


@dataclass(frozen=True, slots=True)
class NowPlaying:
    """The useful half of the PlayView JSON."""

    title: str | None = None
    artist: str | None = None
    album: str | None = None
    artwork_url: str | None = None
    app: str | None = None
    duration_ms: int | None = None


@dataclass(frozen=True, slots=True)
class DeviceState:
    """Everything the library knows about the hub.

    `available` is the TCP connection; `power` is the hub's own power state.
    They are different things and conflating them is the single most likely bug
    in this project — port 7777 answers happily while the hub is powered down.

    `play_state` is the streaming renderer's and nothing more. Selecting any
    physical input puts it at `PLAYING`, connected or not — an empty USB port
    and a switched-off television both do it — and it stays there afterwards,
    across a return to Streaming, until a real cast session moves it. It answers
    "what is the renderer doing", never "is sound coming out".

    `audio_state` answers the second question, from `02 21` byte 8, and it is
    right on all ten sources: on the renderer it mirrors the transport, and on a
    physical input it reports whether the input is carrying sound. **Read
    `playback` rather than either of them** — it names which one to believe. See
    docs/protocol-system-control.md.
    """

    available: bool = False
    power: bool | None = None
    volume: int | None = None
    muted: bool | None = None
    source: Source | None = None
    sources: tuple[Source, ...] = ()
    play_state: PlayState | None = None
    audio_state: PlayState | None = None
    position_ms: int | None = None
    speakers_active: bool | None = None
    now_playing: NowPlaying | None = None
    room_correction: bool | None = None
    manual_eq: bool | None = None
    eq_preset: int | None = None  # 1..3, or 0 while the vendor app's EQ editor is open
    model: str | None = None

    @property
    def playback(self) -> PlayState | None:
        """What the device is doing, from the field that does not lie.

        `audio_state` is right on every source, so it wins wherever it is known.
        `play_state` is the fallback for the window before the first `02 21`,
        which the `02 20` query in the connect burst normally closes at once.
        """
        return self.audio_state if self.audio_state is not None else self.play_state

    @property
    def is_stopped(self) -> bool:
        """True only when idle *and* the speakers have been released.

        Play state `1` also appears in the middle of a track change, so it
        cannot separate stopped from transitioning on its own.
        """
        return self.play_state is PlayState.IDLE and self.speakers_active is False

    def evolve(self, **changes: Any) -> "DeviceState":
        """A copy with `changes` applied."""
        return replace(self, **changes)
