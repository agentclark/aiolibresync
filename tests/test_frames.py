"""Frame splitting for both LibreSync ports, checked against real captures."""

from conftest import capture_arrays, tap_log_frames

from aiolibresync.frames import (
    ControlFrameReader,
    MediaFrameReader,
    build_control,
    build_media,
    checksum,
    crc16_ccitt,
    media_command,
    media_crc,
    split_control,
    split_media,
)


def test_checksum_matches_client_frames_in_capture():
    """Client->device 50006 frames carry sum(payload) & 0xFF in byte 2."""
    for name, data in capture_arrays("2026-01-18-port-50006-app-session.c"):
        if not name.startswith("peer0"):
            continue
        assert data[2] == checksum(data[3:]), name


def test_device_frames_carry_zero_checksum():
    """Device->client 50006 frames use 0x00 in byte 2, not a real checksum."""
    for name, data in capture_arrays("2026-01-18-port-50006-app-session.c"):
        if not name.startswith("peer1"):
            continue
        assert data[2] == 0x00, name


def test_split_control_handles_concatenated_frames():
    """peer1_1 in the capture is two 6-byte frames in one TCP segment."""
    # The split literal is the point, not an accident: it marks where one frame
    # ends and the next begins. `# fmt: skip` keeps the formatter from merging
    # the grouping away here and at the two sites below, where the same trick
    # marks field boundaries.
    blob = bytes.fromhex("000400020e00" "000400024b00")  # fmt: skip
    frames, rest = split_control(blob)
    assert frames == [bytes.fromhex("000400020e00"), bytes.fromhex("000400024b00")]
    assert rest == b""


def test_split_control_keeps_partial_frame_as_remainder():
    frames, rest = split_control(bytes.fromhex("000400020e"))
    assert frames == []
    assert rest == bytes.fromhex("000400020e")


def test_split_control_splits_the_json_source_list():
    """The 332-byte source-list frame must come out whole."""
    arrays = dict(capture_arrays("2026-01-18-port-50006-app-session.c"))
    frames, rest = split_control(arrays["peer1_0"])
    assert len(frames) == 1
    assert rest == b""
    assert b'"src"' in frames[0]


def test_split_media_reads_big_endian_length():
    """Device->client 7777 frames: 10-byte header, payload length BE at [8:10]."""
    arrays = dict(capture_arrays("2026-01-18-port-7777-app-session.c"))
    frames, rest = split_media(arrays["peer1_1"])
    assert len(frames) == 1
    assert frames[0][10:] == b"Model:Stereo Hub"
    assert rest == b""


def test_split_media_handles_two_frames_in_one_segment():
    """peer1_9 is a 0x70 tunnel frame followed by a READ_Model reply."""
    arrays = dict(capture_arrays("2026-01-18-port-7777-app-session.c"))
    frames, rest = split_media(arrays["peer1_9"])
    assert len(frames) == 2
    assert frames[0][4] == 0x70
    assert frames[1][10:] == b"Model:Stereo Hub"
    assert rest == b""


def test_tunnel_payload_contains_control_frames():
    """The 0x70 tunnel carries verbatim port-50006 frames."""
    arrays = dict(capture_arrays("2026-01-18-port-7777-app-session.c"))
    frames, _ = split_media(arrays["peer1_9"])
    inner, rest = split_control(frames[0][10:])
    assert rest == b""
    assert inner == [
        bytes.fromhex("0003030201"),
        bytes.fromhex("00030f020d"),
        bytes.fromhex("00034c024a"),
    ]


def test_media_crc_matches_every_device_frame_in_the_labelled_capture():
    """Bytes 6..7 of a 7777 device->client frame are CRC-16/XMODEM."""
    frames = list(tap_log_frames("2026-08-15-remote-labelled-7777.log"))
    assert len(frames) > 100, "capture should not be nearly empty"
    for frame in frames:
        assert int.from_bytes(frame[6:8], "big") == media_crc(frame), frame.hex(" ")


def test_media_crc_also_matches_the_january_capture():
    """Independent capture, seven months earlier, same rule."""
    checked = 0
    for name, data in capture_arrays("2026-01-18-port-7777-app-session.c"):
        if not name.startswith("peer1"):
            continue
        frames, _ = split_media(data)
        for frame in frames:
            assert int.from_bytes(frame[6:8], "big") == media_crc(frame), name
            checked += 1
    assert checked >= 15


def test_client_frames_send_a_zero_crc():
    """The app never computes the CRC; it sends 0x0000 and the device accepts it."""
    for name, data in capture_arrays("2026-01-18-port-7777-app-session.c"):
        if not name.startswith("peer0"):
            continue
        assert data[6:8] == b"\x00\x00", name


def test_crc16_ccitt_against_the_standard_check_vector():
    """CRC-16/XMODEM of "123456789" is 0x31C3."""
    assert crc16_ccitt(b"123456789") == 0x31C3


def test_build_control_round_trips_every_client_frame_in_the_capture():
    """Rebuilding each client->device 50006 frame from its fields is byte-identical."""
    checked = 0
    for name, data in capture_arrays("2026-01-18-port-50006-app-session.c"):
        if not name.startswith("peer0"):
            continue
        for frame in split_control(data)[0]:
            assert build_control(frame[3], frame[4], frame[5:]) == frame, name
            checked += 1
    assert checked >= 5


def test_build_media_round_trips_every_client_frame_in_the_capture():
    """Same for 7777, reading the command as the 16-bit LE field at 3..4."""
    checked = 0
    for name, data in capture_arrays("2026-01-18-port-7777-app-session.c"):
        if not name.startswith("peer0"):
            continue
        length = int.from_bytes(data[8:10], "little")
        payload = data[10 : 10 + length]
        command = media_command(data, from_device=False)
        assert build_media(data[2], command, payload) == data, name
        checked += 1
    assert checked >= 5


def test_media_command_reads_a_two_byte_mid():
    """`00 00 02 01 ee 01 ...` is command 0x01ee, not status 1 / command 0xee.

    This is the frame that disproves the single-byte reading: 0x01ee is a
    message box MessageBoxHandler registers for by number, and reading it as
    one byte invents a `flags` field to hold the other half.
    """
    # header | mid | status | checksum | length
    frame = bytes.fromhex("000002" "01ee" "01" "3091" "0000")  # fmt: skip
    assert media_command(frame) == 0x01EE
    assert frame[5] == 0x01  # status, non-zero because this one is a reply


def test_media_command_survives_the_round_trip_above_255():
    built = build_media(0x02, 0x01EE)
    assert media_command(built, from_device=False) == 0x01EE
    assert built[3:5] == b"\xee\x01"  # little-endian on the way out


def test_every_mid_in_the_labelled_capture_is_plausible():
    """Big-endian at 3..4 keeps every device MID inside the bus's own range.

    MID_LENGTH_MAX bounds a message box at 0x1400 bytes and luci_service's
    dispatch tops out at MID 0x1f7; reading the field the other way round would
    put MIDs like 0xee01 far outside both.
    """
    frames = list(tap_log_frames("2026-08-15-remote-labelled-7777.log"))
    assert frames
    for frame in frames:
        assert media_command(frame) <= 0x1F7, frame.hex(" ")


def test_build_control_computes_the_checksum_the_prototypes_hardcoded():
    """Source select: the checksum tracks the index, it is not a fixed 0x0a."""
    assert build_control(0x02, 0x03, b"\x06") == bytes.fromhex("00040b020306")
    assert build_control(0x02, 0x03, b"\x00") == bytes.fromhex("000405020300")
    assert build_control(0x02, 0x03, b"\x05") == bytes.fromhex("00040a020305")


def test_build_media_leaves_the_crc_field_zero():
    """The device accepts zero and its own app sends zero."""
    assert build_media(0x02, 0x03, b"1.2.3.4")[6:8] == b"\x00\x00"


def test_control_reader_reassembles_a_frame_split_across_chunks():
    reader = ControlFrameReader()
    assert reader.feed(bytes.fromhex("000400")) == []
    assert reader.feed(bytes.fromhex("020e00")) == [bytes.fromhex("000400020e00")]


def test_control_reader_yields_both_frames_from_one_chunk():
    reader = ControlFrameReader()
    frames = reader.feed(bytes.fromhex("000400020e00" "000400024b00"))  # fmt: skip
    assert frames == [bytes.fromhex("000400020e00"), bytes.fromhex("000400024b00")]


def test_control_reader_resyncs_past_garbage():
    """Different firmware will emit frames we do not know. Survive them."""
    reader = ControlFrameReader()
    frames = reader.feed(b"\xff\xff\xff\xff" + bytes.fromhex("000400020e00"))
    assert frames == [bytes.fromhex("000400020e00")]
    assert reader.dropped == 4


def test_control_reader_rejects_an_impossibly_short_frame():
    """A body of two bytes cannot hold checksum, class and opcode."""
    reader = ControlFrameReader()
    assert reader.feed(bytes.fromhex("0002000200")) == []
    assert reader.dropped > 0


def test_media_reader_reassembles_a_frame_split_across_chunks():
    reader = MediaFrameReader()
    whole = bytes.fromhex("00000200400000000002") + b"28"
    assert reader.feed(whole[:7]) == []
    assert reader.feed(whole[7:]) == [whole]


def test_media_reader_resyncs_on_an_impossible_msg_type():
    reader = MediaFrameReader()
    whole = bytes.fromhex("00000200400000000002") + b"28"
    frames = reader.feed(b"\x99\x99\x99" + whole)
    assert frames == [whole]
    assert reader.dropped == 3


def test_readers_replay_the_whole_capture_byte_by_byte():
    """One byte at a time is the worst case a TCP stream can produce."""
    log = list(tap_log_frames("2026-08-15-remote-labelled-7777.log"))
    reader = MediaFrameReader()
    out = []
    for frame in log:
        for byte in frame:
            out += reader.feed(bytes([byte]))
    assert out == log
    assert reader.dropped == 0


def test_control_reader_replays_the_whole_capture_byte_by_byte():
    """One byte at a time is the worst case a TCP stream can produce."""
    log = list(tap_log_frames("2026-08-15-remote-labelled-50006.log"))
    reader = ControlFrameReader()
    out = []
    for frame in log:
        for byte in frame:
            out += reader.feed(bytes([byte]))
    assert out == log
    assert reader.dropped == 0


def test_dropped_accumulates_across_feed_calls_until_reset():
    """The connection's read loop calls feed() once per socket chunk and only clears
    the counter after logging, so it must be additive across calls."""
    reader = ControlFrameReader()
    reader.feed(b"\xff\xff\xff\xff")
    assert reader.dropped == 3
    reader.feed(b"\xff\xff\xff\xff")
    assert reader.dropped == 7  # accumulated, not reset by the second call
    reader.dropped = 0
    assert reader.dropped == 0
