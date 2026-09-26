"""Port 7777 semantics: named operations, and a dispatch that trusts only the box.

The `command` field is a **16-bit LUCI message-box id**, not a port-local
opcode. A client on this port can therefore address any box on the internal bus,
including factory reset and firmware upgrade. This module is the whole answer to
that: every outbound frame goes through `_build`, which refuses any MID outside
`const.ALLOWED_MIDS`, and there is no "send raw command" entry point. Port 50006
has no equivalent hazard because MessageBoxHandler hardcodes its box.

Two decoding rules, both won the hard way:

- **Dispatch on the message box, never on `status`.** `0x01` appears on replies
  *and* on the unsolicited `0x01ee` track-change push.
- **An empty payload is not a value.** A refused request (`status 0x02`) is
  empty too, and boxes 107 and 219 answer that way on this model.
"""

import json
import logging
from typing import Any

from . import frames
from .const import (
    ALLOWED_MIDS,
    KNOWN_UNDECODED_MIDS,
    MID_MODEL,
    MID_MUTE,
    MID_PLAY_STATE,
    MID_PLAYVIEW,
    MID_PLAYVIEW_DUPLICATE,
    MID_POSITION,
    MID_REGISTER,
    MID_SERIAL,
    MID_SPEAKER,
    MID_TRANSPORT,
    MID_VOLUME,
    MSG_COMMAND,
    MSG_QUERY,
    READ_MODEL_PAYLOAD,
    STATUS_NOT_REGISTERED,
    STATUS_REFUSED,
    STATUS_REPLY,
    TRANSPORT_PAYLOADS,
    VOLUME_MAX,
    VOLUME_MIN,
)
from .exceptions import UnsafeCommandError
from .frames import MEDIA_HEADER
from .state import NowPlaying, PlayState

_LOGGER = logging.getLogger(__name__)


def _build(msg_type: int, mid: int, payload: bytes = b"") -> bytes:
    # The type check comes first and must stay first. `ALLOWED_MIDS` is a
    # frozenset, so an unhashable `mid` makes the membership test itself raise
    # TypeError, and formatting a non-int as `0x{:04x}` raises too — either way
    # the caller would get an exception that is not the one this module
    # promises. Nothing unsafe was ever built, but `UnsafeCommandError` is
    # exported and documented as the refusal, so it has to be what comes out.
    if not isinstance(mid, int) or mid not in ALLOWED_MIDS:
        shown = f"0x{mid:04x}" if isinstance(mid, int) else repr(mid)
        raise UnsafeCommandError(
            f"message box {shown} is not in the closed set this library may "
            "address; the field is a LUCI message-box id, not an opcode"
        )
    return frames.build_media(msg_type, mid, payload)


def build_query(mid: int) -> bytes:
    """A query for one of the boxes in ALLOWED_MIDS. Raises otherwise."""
    return _build(MSG_QUERY, mid)


QUERY_VOLUME = build_query(MID_VOLUME)
QUERY_PLAY_STATE = build_query(MID_PLAY_STATE)
QUERY_PLAYVIEW = build_query(MID_PLAYVIEW)
#: Mute is readable on demand — the one thing the 2026-08-15 write test added.
#: There is no setter, deliberately: writing this box is accepted and changes
#: nothing but the value that reads back.
QUERY_MUTE = build_query(MID_MUTE)
READ_MODEL = _build(MSG_COMMAND, MID_MODEL, READ_MODEL_PAYLOAD)
#: The factory serial. Read-only, and there is deliberately no way to write it.
QUERY_SERIAL = build_query(MID_SERIAL)

#: What to ask once the session is registered.
CONNECT_QUERIES = (
    QUERY_PLAYVIEW,
    QUERY_VOLUME,
    QUERY_PLAY_STATE,
    QUERY_MUTE,
    READ_MODEL,
    QUERY_SERIAL,
)


def register(ip: str) -> bytes:
    """Register the session, announcing the address pushes should go to.

    Mandatory: an unregistered connection is answered `mid 0x0000 status 0x03`
    and its request is dropped. Send the real local address — the prototypes'
    fallback to 127.0.0.1 is likely to cost pushed events.
    """
    return _build(MSG_COMMAND, MID_REGISTER, ip.encode("ascii"))


def set_volume(level: int) -> bytes:
    if not VOLUME_MIN <= level <= VOLUME_MAX:
        raise ValueError(f"volume must be {VOLUME_MIN}..{VOLUME_MAX}, got {level}")
    return _build(MSG_COMMAND, MID_VOLUME, str(level).encode("ascii"))


def _transport(payload: bytes) -> bytes:
    if payload not in TRANSPORT_PAYLOADS:  # the set is closed and internal
        raise ValueError(f"{payload!r} is not one of the closed transport payloads")
    return _build(MSG_COMMAND, MID_TRANSPORT, payload)


def play() -> bytes:
    return _transport(b"PLAY")


def pause() -> bytes:
    return _transport(b"PAUSE")


def stop() -> bytes:
    return _transport(b"STOP")


def next_track() -> bytes:
    """Next track. The device reports the resulting track, never the button —
    NEXT and PREV produce byte-identical sequences."""
    return _transport(b"NEXT")


def previous_track() -> bytes:
    return _transport(b"PREV")


def mid_of(frame: bytes) -> int:
    """The message box a device frame names."""
    return frames.media_command(frame, from_device=True)


def is_refused(frame: bytes) -> bool:
    """status 0x02 — the device declined. Boxes 107 and 219 do this."""
    return len(frame) > 5 and frame[5] == STATUS_REFUSED


def is_transport_ack(frame: bytes) -> bool:
    """The device's acknowledgement of a transport command: an empty `0x0028`, status 0x01.

    It arrives for every transport command, including one that changes nothing,
    such as a stop on a hub that is already stopped, which produces no play
    state at all. Measured on hardware on 2026-09-26: two stops in a row were
    both acknowledged, the second within 11 ms and with nothing else.
    """
    return len(frame) > 5 and mid_of(frame) == MID_TRANSPORT and frame[5] == STATUS_REPLY


def is_not_registered(frame: bytes) -> bool:
    """status 0x03 — register the session and try again."""
    return len(frame) > 5 and frame[5] == STATUS_NOT_REGISTERED


def parse_playview(payload: bytes) -> dict[str, Any] | None:
    """Decode the PlayView JSON into NowPlaying plus, if present, the play state."""
    try:
        contents = json.loads(payload)["Window CONTENTS"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(contents, dict):
        return None

    def text(key: str) -> str | None:
        value = contents.get(key)
        return str(value) if isinstance(value, str) and value else None

    duration = contents.get("TotalTime")
    track = NowPlaying(
        title=text("TrackName"),
        artist=text("Artist"),
        album=text("Album"),
        artwork_url=text("CoverArtUrl"),
        app=text("CastContentApp"),
        # Current_time is -1 in every frame ever observed: unimplemented. The
        # live position arrives on the 0x31 push instead.
        duration_ms=int(duration) if isinstance(duration, int) and duration > 0 else None,
    )
    patch: dict[str, Any] = {"now_playing": track}
    state = contents.get("PlayState")
    if isinstance(state, int) and state in PlayState:
        patch["play_state"] = PlayState(state)
    return patch


def decode(frame: bytes) -> dict[str, Any] | None:
    """Decode one port-7777 frame into a partial DeviceState update.

    Empty patch = understood, no state. None = unclassified, recorded for
    diagnostics.
    """
    if len(frame) < MEDIA_HEADER:
        return None
    mid = mid_of(frame)
    payload = frame[MEDIA_HEADER:]

    if is_refused(frame) or is_not_registered(frame):
        return {}
    if mid == MID_SERIAL:
        # A unit with no valid factory serial is expected to exist: the
        # firmware stores a validity flag next to it. No serial is a state,
        # not an unclassified frame.
        return {"serial": parse_serial(payload)}
    if not payload:
        return {} if mid in KNOWN_UNDECODED_MIDS else None

    if mid in (MID_PLAYVIEW, MID_PLAYVIEW_DUPLICATE):
        # The two arrive as a byte-identical pair. Decoding both is harmless:
        # the second produces an equal state and therefore no callback.
        return parse_playview(payload)

    text = payload.decode("ascii", "replace").strip("\x00").strip()

    if mid == MID_VOLUME:
        level = _as_int(text)
        if level is None or not VOLUME_MIN <= level <= VOLUME_MAX:
            return None
        return {"volume": level}
    if mid == MID_MUTE:
        if text in ("MUTE", "UNMUTE"):
            return {"muted": text == "MUTE"}
        return None
    if mid == MID_POSITION:
        position = _as_int(text)
        if position is None:
            return None
        # Signed. -1000 is the sentinel the device sends on STOP; parsed
        # unsigned it becomes a huge number and any seek UI misbehaves.
        return {"position_ms": None if position < 0 else position}
    if mid == MID_PLAY_STATE:
        value = _as_int(text)
        if value is None or value not in PlayState:
            return None
        return {"play_state": PlayState(value)}
    if mid == MID_SPEAKER:
        if text.startswith("SPEAKER_ACTIVE"):
            return {"speakers_active": True}
        if text.startswith("SPEAKER_INACTIVE"):
            return {"speakers_active": False}
        return None
    if mid == MID_MODEL:
        return {"model": text.removeprefix("Model:").strip() or None}

    if mid in KNOWN_UNDECODED_MIDS:
        return {}
    return None


def parse_serial(payload: bytes) -> str | None:
    """`<serial>,VALID` -> the serial. Anything else, including an empty box, -> None."""
    text = payload.decode("ascii", "replace").strip("\x00").strip()
    serial, _, flag = text.partition(",")
    serial = serial.strip()
    if flag.strip() != "VALID" or not serial or not serial.isascii() or not serial.isalnum():
        return None
    return serial


def _as_int(text: str) -> int | None:
    try:
        return int(text)
    except ValueError:
        return None
