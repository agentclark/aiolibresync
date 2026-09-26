# Changelog

## 0.5.0 — unreleased

- `async_connect(timeout=...)` waits until both ports are open and raises `NotConnectedError` if they are not in time. On that error, and on cancellation, it disconnects again first, so a failed attempt leaves no reconnect loop or poll running. Without `timeout` it behaves as before.
- `state.available` and `Connection.connected` turn true only once the connect-time sequence has run, which on 7777 is the session registration. Before, they turned true at the TCP connect, and a command sent at once could be refused as unregistered.
- `async_disconnect()` no longer swallows a cancellation aimed at its caller while it waits for the poll loop or a socket task to end.
- `__version__` falls back to `0+unknown` when the package metadata is not installed.
- A command's confirmation waiter is armed before the command is sent, so a reply that arrives while the write is still draining is no longer missed.
- `async_probe_control` is exported: the control-port check on its own, for a caller that already has the device description.
- `__version__` is read from the installed package metadata. It had been left at `0.2.0`.
- A 7777 session the device has forgotten is dropped directly, without a background task. The cooldown still limits recovery to one per `REREGISTER_COOLDOWN`.
- `frames.crc16_ccitt` uses `binascii.crc_hqx`. The result is unchanged.

## 0.4.0 — 2026-09-26

- The factory serial, read from message box 231: `DeviceState.serial` on a connected client, and `async_read_serial(host)` for a hub that is not connected. It is the preferred stable identifier, because it does not depend on the UPnP daemon. `None` when the unit has no serial marked valid.
- `diagnostics()` redacts the serial.
- The connect-time queries include box 231.

## 0.3.0 — 2026-09-26

First public release.

- `LibreSyncClient`: both ports, push-driven state, reconnection, confirmation of every command, a slow poll for power and room correction, and `diagnostics()`.
- `DeviceState.playback`: the play state to believe, from `audio_state` (whether sound is coming out) where it is known.
- `async_discover()` and `async_probe()`: SSDP discovery, device description, and a control-port check that sends nothing.
- `DeviceState.eq_preset` can be `0` while the vendor app's EQ editor is open.
