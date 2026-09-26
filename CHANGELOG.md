# Changelog

## 0.4.0 — unreleased

- The factory serial, read from message box 231: `DeviceState.serial` on a connected client, and `async_read_serial(host)` for a hub that is not connected. It is the preferred stable identifier, because it does not depend on the UPnP daemon. `None` when the unit has no serial marked valid.
- `diagnostics()` redacts the serial.
- The connect-time queries include box 231.

## 0.3.0 — 2026-09-26

First public release.

- `LibreSyncClient`: both ports, push-driven state, reconnection, confirmation of every command, a slow poll for power and room correction, and `diagnostics()`.
- `DeviceState.playback`: the play state to believe, from `audio_state` (whether sound is coming out) where it is known.
- `async_discover()` and `async_probe()`: SSDP discovery, device description, and a control-port check that sends nothing.
- `DeviceState.eq_preset` can be `0` while the vendor app's EQ editor is open.
