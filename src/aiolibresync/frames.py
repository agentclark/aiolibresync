"""Framing for the two LibreSync TCP protocols: split a stream, build a frame.

Pure functions and one accumulator class. No I/O, nothing async — this module
must stay importable and testable without a device and without an event loop,
which is what Home Assistant requires of an external library.
"""

from .const import (
    MAX_CONTROL_BODY,
    MAX_PAYLOAD,
    MIN_CONTROL_BODY,
    MSG_COMMAND,
    MSG_QUERY,
)

CONTROL_HEADER = 2
MEDIA_HEADER = 10


def checksum(payload: bytes) -> int:
    """Byte 2 of a client->device port-50006 frame.

    The firmware does not verify this — see docs/protocol-system-control.md.
    Generate it anyway: it is what the device does, and a firmware that started
    checking would break anything that guessed.
    """
    return sum(payload) & 0xFF


def crc16_ccitt(data: bytes) -> int:
    """CRC-16/XMODEM: poly 0x1021, init 0x0000, no reflection, no final xor."""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def media_crc(frame: bytes) -> int:
    """The value bytes 6..7 of a port-7777 frame should carry.

    Computed over the frame with those two bytes removed, not zeroed — so
    `header[0:6] || length || payload`.

    Device->client frames always carry it. Client->device frames send 0x0000
    instead, and the device accepts that: `luci_service` contains the string
    "CRC EQUAL OR ZERO".
    """
    return crc16_ccitt(frame[0:6] + frame[8:])


def split_control(buf: bytes) -> tuple[list[bytes], bytes]:
    """Split a port-50006 byte stream into complete frames plus a remainder.

    Frame layout: [length: 2 bytes BE][checksum][class][opcode][data...]
    `length` counts everything after itself, so the frame is length + 2 bytes.
    """
    frames = []
    while len(buf) >= CONTROL_HEADER:
        total = int.from_bytes(buf[0:2], "big") + CONTROL_HEADER
        if total > len(buf):
            break
        frames.append(buf[:total])
        buf = buf[total:]
    return frames, buf


def media_command(frame: bytes, from_device: bool = True) -> int:
    """The 16-bit LUCI message-box id a port-7777 frame names.

    It lives at offsets 3..4 in both directions. The device writes its 16-bit
    fields big-endian and clients write them little-endian, which is the same
    rule that flips the length field at 8..9 — see docs/protocol-media.md.

    Reading it as one byte plus a `status`/`flags` byte works right up until a
    MID above 255 arrives, and then it invents a field: `00 00 02 01 ee 01 ...`
    is command 0x01ee, not status 1 / command 0xee.
    """
    return int.from_bytes(frame[3:5], "big" if from_device else "little")


def split_media(buf: bytes) -> tuple[list[bytes], bytes]:
    """Split a port-7777 byte stream into complete frames plus a remainder.

    Device->client layout:
        [RemoteID: 2][msg_type][command: 2 BE][status][CRC: 2][length: 2 BE]
    followed by `length` payload bytes.
    """
    frames = []
    while len(buf) >= MEDIA_HEADER:
        total = MEDIA_HEADER + int.from_bytes(buf[8:10], "big")
        if total > len(buf):
            break
        frames.append(buf[:total])
        buf = buf[total:]
    return frames, buf


def build_control(cls: int, opcode: int, data: bytes = b"") -> bytes:
    """Build a client->device port-50006 frame.

    Layout: [length: 2 BE][checksum][class][opcode][data...], where `length`
    counts everything after itself.

    The checksum is computed rather than hardcoded. The firmware ignores it, but
    every archived prototype hardcoded `0x0a` for the whole source-select family
    and got nine of ten frames wrong on paper — computing it costs one addition
    and makes that class of bug impossible.
    """
    body = bytes([cls, opcode]) + data
    return (len(body) + 1).to_bytes(2, "big") + bytes([checksum(body)]) + body


def build_media(msg_type: int, command: int, payload: bytes = b"") -> bytes:
    """Build a client->device port-7777 frame.

    Layout: [RemoteID: 2][msg_type][command: 2 LE][status][CRC: 2][length: 2 LE]
    followed by the payload.

    `command` is a 16-bit LUCI message-box id, so it is encoded as two bytes.
    For every command under 256 that produces exactly the bytes the vendor's app
    sends, which is why the single-byte reading survived as long as it did.

    The CRC field is left at zero. The device accepts that — `luci_service`
    contains "CRC EQUAL OR ZERO" — and the vendor's own app never computes it.
    A deliberately wrong non-zero value was accepted too, so this path is not
    checked at all.

    Note the length is little-endian here and big-endian on the way back. The
    command field flips the same way; it is one rule, not two. See
    docs/protocol-media.md.
    """
    return (
        b"\x00\x00"
        + bytes([msg_type])
        + command.to_bytes(2, "little")
        + b"\x00"
        + b"\x00\x00"
        + len(payload).to_bytes(2, "little")
        + payload
    )


class FrameReader:
    """Accumulate bytes, yield whole frames, resync past anything else.

    Malformed input is not fatal: a header that cannot be a frame costs one
    byte, and the reader tries again from the next one. Firmware we have not
    seen will emit frames we do not know, and the integration has to survive
    that rather than wedge the connection.
    """

    header = 0

    def __init__(self) -> None:
        self._buf = bytearray()
        self.dropped = 0

    def feed(self, data: bytes) -> list[bytes]:
        """Add bytes; return every complete frame they finished."""
        self._buf += data
        out: list[bytes] = []
        while len(self._buf) >= self.header:
            total = self._total(bytes(self._buf[: self.header]))
            if total is None:
                del self._buf[0]
                self.dropped += 1
                continue
            if total > len(self._buf):
                break
            out.append(bytes(self._buf[:total]))
            del self._buf[:total]
        return out

    def _total(self, header: bytes) -> int | None:
        """Total frame length, or None if this cannot be a header."""
        raise NotImplementedError


class ControlFrameReader(FrameReader):
    """Port 50006: [length: 2 BE] counting everything after itself."""

    header = CONTROL_HEADER

    def _total(self, header: bytes) -> int | None:
        length = int.from_bytes(header[0:2], "big")
        if not MIN_CONTROL_BODY <= length <= MAX_CONTROL_BODY:
            return None
        return length + CONTROL_HEADER


class MediaFrameReader(FrameReader):
    """Port 7777: a 10-byte header with the payload length big-endian at 8..9."""

    header = MEDIA_HEADER

    def _total(self, header: bytes) -> int | None:
        if header[2] not in (MSG_QUERY, MSG_COMMAND):
            return None
        length = int.from_bytes(header[8:10], "big")
        if length > MAX_PAYLOAD:
            return None
        return MEDIA_HEADER + length
