"""DeviceState is immutable, and stopping is derived from two fields."""

import pytest

from aiolibresync.state import DeviceState, NowPlaying, PlayState, Source


def test_device_state_is_frozen():
    state = DeviceState()
    with pytest.raises(AttributeError):
        state.power = True  # type: ignore[misc]


def test_evolve_returns_a_new_object_and_leaves_the_old_one_alone():
    first = DeviceState(volume=20)
    second = first.evolve(volume=30)
    assert first.volume == 20
    assert second.volume == 30
    assert first is not second


def test_equal_states_compare_equal_so_duplicates_can_be_collapsed():
    """The device sends 02 0e and 0x40 twice. Equality is the deduplication."""
    assert DeviceState(power=True).evolve(power=True) == DeviceState(power=True)


def test_stopped_needs_both_idle_and_silent_speakers():
    """Play state 1 appears mid-track-change too; SPEAKER_INACTIVE is the signal."""
    mid_change = DeviceState(play_state=PlayState.IDLE, speakers_active=True)
    stopped = DeviceState(play_state=PlayState.IDLE, speakers_active=False)
    playing = DeviceState(play_state=PlayState.PLAYING, speakers_active=True)
    assert not mid_change.is_stopped
    assert stopped.is_stopped
    assert not playing.is_stopped
    # None means "not yet known", not "falsy": a `not speakers_active` truthiness
    # check would wrongly read an unknown speaker state as stopped.
    unknown = DeviceState(play_state=PlayState.IDLE, speakers_active=None)
    assert not unknown.is_stopped
    assert not DeviceState().is_stopped


def test_playback_believes_the_audio_state_over_the_transport():
    """On a physical input 7777 answers PLAYING to a socket with nothing in it.

    Byte 8 is what knows. Measured 2026-08-16: selecting an empty USB port
    leaves `0x33` at PLAYING for as long as anyone queries it, while byte 8
    settles at IDLE within a second.
    """
    state = DeviceState(play_state=PlayState.PLAYING, audio_state=PlayState.IDLE)
    assert state.playback is PlayState.IDLE


def test_playback_falls_back_to_the_transport_before_the_first_report():
    """`02 21` is pushed on change only, so there is a window — shortened to the
    connect burst by `02 20`, not closed, because a hub can refuse a query."""
    state = DeviceState(play_state=PlayState.PAUSED, audio_state=None)
    assert state.playback is PlayState.PAUSED


def test_playback_is_unknown_when_neither_port_has_reported():
    assert DeviceState().playback is None
    assert DeviceState().audio_state is None


def test_sources_are_hashable_value_objects():
    assert Source(index=0, name="Streaming") == Source(index=0, name="Streaming")
    assert len({Source(index=0, name="Streaming"), Source(index=0, name="Streaming")}) == 1


def test_now_playing_holds_the_media_player_payload():
    track = NowPlaying(
        title="La Sentencia",
        artist="Melissa Aldana",
        album="La Sentencia",
        artwork_url="https://resources.tidal.com/images/x/640x640.jpg",
        app="TIDAL",
        duration_ms=277597,
    )
    assert track.duration_ms == 277597
