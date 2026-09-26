"""Port 7777: named operations only, and dispatch on the message box."""

import json

import pytest
from conftest import tap_log_frames
from fakes import device_frame

from aiolibresync import media
from aiolibresync.const import MID_SERIAL, MSG_COMMAND
from aiolibresync.exceptions import UnsafeCommandError
from aiolibresync.state import PlayState


def test_register_carries_our_own_ip_as_ascii():
    assert media.register("192.168.1.20") == bytes.fromhex("00000203000000000c00") + b"192.168.1.20"


def test_the_builders_match_the_command_reference():
    assert media.set_volume(30) == bytes.fromhex("00000240000000000200") + b"30"
    assert media.play() == bytes.fromhex("00000228000000000400") + b"PLAY"
    assert media.pause()[10:] == b"PAUSE"
    assert media.stop()[10:] == b"STOP"
    assert media.next_track()[10:] == b"NEXT"
    assert media.previous_track()[10:] == b"PREV"
    assert media.READ_MODEL[10:] == b"READ_Model"
    assert media.QUERY_VOLUME == bytes.fromhex("00000140000000000000")
    assert media.QUERY_PLAY_STATE == bytes.fromhex("00000133000000000000")
    assert media.QUERY_PLAYVIEW == bytes.fromhex("0000012a000000000000")
    assert media.QUERY_MUTE == bytes.fromhex("0000013f000000000000")


def test_there_is_no_way_to_set_mute():
    """Writing box 0x3f is accepted by the device and does nothing — tested
    2026-08-15. A setter here would ship a control that lies to the user."""
    assert not hasattr(media, "set_mute")


def test_the_raw_frame_builder_is_not_part_of_this_module_s_surface():
    """`build_media` takes an arbitrary message-box id. This module's whole
    purpose is that its own surface cannot address one."""
    assert not hasattr(media, "build_media")


@pytest.mark.parametrize("level", [-1, 101])
def test_set_volume_refuses_a_level_the_device_does_not_have(level):
    with pytest.raises(ValueError):
        media.set_volume(level)


def test_an_arbitrary_message_box_cannot_be_addressed():
    """`command` IS a message-box id, and the space contains factory reset."""
    with pytest.raises(UnsafeCommandError):
        media.build_query(0x00DB)


@pytest.mark.parametrize("mid", ["0x00db", None, 1.5, [0x0028], b"\x28", object()])
def test_a_message_box_of_the_wrong_type_is_refused_as_unsafe(mid):
    """The refusal is part of the public contract, so its type must hold.

    Every one of these used to escape as TypeError or ValueError instead:
    formatting a non-int as `0x{:04x}` raises, and an unhashable value makes the
    frozenset membership test itself raise. Nothing unsafe was built either way,
    but a caller told to catch UnsafeCommandError caught nothing.
    """
    with pytest.raises(UnsafeCommandError):
        media.build_query(mid)
    # `build_query` is the only public builder taking a caller's MID; the guard
    # itself is reached from the command direction too, so check it there.
    with pytest.raises(UnsafeCommandError):
        media._build(MSG_COMMAND, mid, b"x")


def test_decode_volume_and_mute():
    assert media.decode(_device(0x0040, b"30")) == {"volume": 30}
    assert media.decode(_device(0x003F, b"MUTE")) == {"muted": True}
    assert media.decode(_device(0x003F, b"UNMUTE")) == {"muted": False}


def test_decode_play_state():
    assert media.decode(_device(0x0033, b"0")) == {"play_state": PlayState.PLAYING}
    assert media.decode(_device(0x0033, b"2")) == {"play_state": PlayState.PAUSED}


def test_position_is_signed_and_minus_one_thousand_means_no_position():
    assert media.decode(_device(0x0031, b"32563")) == {"position_ms": 32563}
    assert media.decode(_device(0x0031, b"-1000")) == {"position_ms": None}


def test_speaker_state_is_the_stop_signal():
    assert media.decode(_device(0x0046, b"SPEAKER_ACTIVE,24")) == {"speakers_active": True}
    assert media.decode(_device(0x0046, b"SPEAKER_INACTIVE,24")) == {"speakers_active": False}


def test_decode_playview_gives_a_media_player_payload():
    frame = next(
        f
        for f in tap_log_frames("2026-08-15-remote-labelled-7777.log")
        if media.mid_of(f) == 0x002A and len(f) > 100
    )
    patch = media.decode(frame)
    track = patch["now_playing"]
    assert track.title and track.artist
    assert track.duration_ms and track.duration_ms > 0
    assert track.artwork_url is None or track.artwork_url.startswith("http")


def test_parse_playview_survives_malformed_input():
    """The exact defect class that already bit `parse_source_list` once
    (it crashed on `{"src": null}`). Never raise, always None or a
    sane patch."""
    assert media.parse_playview(b"not json") is None
    assert media.parse_playview(b'"just a string"') is None
    assert media.parse_playview(b"[1, 2, 3]") is None
    assert media.parse_playview(b'{"Window CONTENTS": null}') is None
    patch = media.parse_playview(
        b'{"Window CONTENTS": {"TrackName": "T", "PlayState": "not an int"}}'
    )
    assert patch is not None
    assert patch["now_playing"].title == "T"
    assert "play_state" not in patch


def test_decode_refuses_a_frame_shorter_than_the_header():
    assert media.decode(b"\x00" * 9) is None


def test_the_playview_twin_produces_an_equal_patch():
    """The client's whole deduplication story rests on 0x2a and 0x2d decoding the
    same payload into equal patches."""
    payload = json.dumps(
        {
            "Window CONTENTS": {
                "TrackName": "T",
                "Artist": "A",
                "TotalTime": 1000,
                "PlayState": 0,
            }
        }
    ).encode()
    assert media.decode(_device(0x002A, payload)) == media.decode(_device(0x002D, payload))


def test_a_refused_request_is_not_a_value():
    """status 0x02 comes back with an empty payload; so does a real empty reply."""
    assert media.decode(_device(0x0040, b"", status=0x02)) == {}
    assert media.is_refused(_device(0x0040, b"", status=0x02))


def test_an_unregistered_session_is_detected_by_status_not_by_the_box():
    frame = _device(0x0000, b"", status=0x03)
    assert media.is_not_registered(frame)
    assert media.decode(frame) == {}


def test_status_one_is_not_a_reply_flag():
    """The unsolicited 0x01ee track-change push carries status 0x01 too."""
    frame = _device(0x01EE, b"", status=0x01)
    assert media.mid_of(frame) == 0x01EE
    assert media.decode(frame) == {}


def test_every_media_frame_in_every_capture_is_understood():
    """0x00e0 is the one box nobody has identified; everything else must decode.

    A new MID appearing here is a finding. Decode it or document it in
    docs/protocol-media.md; do not widen the allowlist silently.
    """
    unknown = set()
    for log in (
        "2026-08-15-remote-labelled-7777.log",
        "2026-08-15-app-dsp-7777.log",
        "2026-08-15-app-dsp2-7777.log",
    ):
        for frame in tap_log_frames(log):
            if media.decode(frame) is None:
                unknown.add(media.mid_of(frame))
    assert unknown <= {0x00E0}


def _device(mid: int, payload: bytes, status: int = 0x01) -> bytes:
    """Build a device->client frame: 16-bit fields big-endian, real CRC."""
    from aiolibresync.frames import media_crc

    frame = (
        b"\x00\x00"
        + bytes([0x02])
        + mid.to_bytes(2, "big")
        + bytes([status])
        + b"\x00\x00"
        + len(payload).to_bytes(2, "big")
        + payload
    )
    return frame[:6] + media_crc(frame).to_bytes(2, "big") + frame[8:]


@pytest.mark.parametrize(
    ("payload", "serial"),
    [
        # The shape measured on the hub: 20 characters, then the validity flag.
        (b"AbCdEf0123456789XyZw,VALID", "AbCdEf0123456789XyZw"),
        (b"AbCdEf0123456789XyZw,VALID\x00", "AbCdEf0123456789XyZw"),
        # The firmware keeps a validity flag, so a unit without one is expected.
        (b"AbCdEf0123456789XyZw,INVALID", None),
        (b",VALID", None),
        (b"", None),
        (b"AbCdEf0123456789XyZw", None),
        (b"Ab/Cd,VALID", None),
    ],
)
def test_the_serial_is_believed_only_when_the_firmware_marks_it_valid(payload, serial):
    assert media.parse_serial(payload) == serial


def test_box_231_decodes_to_the_serial_or_to_none_but_never_to_unknown():
    """No serial is a state of the unit, not a frame nobody has classified."""
    valid = device_frame(MID_SERIAL, b"AbCdEf0123456789XyZw,VALID")
    assert media.decode(valid) == {"serial": "AbCdEf0123456789XyZw"}
    assert media.decode(device_frame(MID_SERIAL)) == {"serial": None}


def test_the_serial_query_is_a_query_and_nothing_else():
    assert media.QUERY_SERIAL == bytes.fromhex("000001e7000000000000")
