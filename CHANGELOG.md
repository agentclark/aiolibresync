# Changelog

## 0.3.0 — unreleased

First public release.

- `LibreSyncClient`: both ports, push-driven state, reconnection, confirmation of every command, a slow poll for power and room correction, and `diagnostics()`.
- `DeviceState.playback`: the play state to believe, from `audio_state` (whether sound is coming out) where it is known.
- `async_discover()` and `async_probe()`: SSDP discovery, device description, and a control-port check that sends nothing.
- `DeviceState.eq_preset` can be `0` while the vendor app's EQ editor is open.
