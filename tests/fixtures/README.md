# Test fixtures

Real traffic between a Platin Stereo Hub (firmware 1.52), its vendor's app and a passive listener. The codec tests use it as golden data: real bytes in, expected objects out.

| File | What it is |
| --- | --- |
| `2026-01-18-port-50006-app-session.c` | a vendor-app session on port 50006, as a Wireshark C-array export; `peer0_*` is client to device, `peer1_*` device to client |
| `2026-01-18-port-7777-app-session.c` | the same session on port 7777 |
| `2026-08-15-remote-labelled-*.log` | both ports while the RF remote was used, one key at a time |
| `2026-08-15-app-dsp-*.log`, `2026-08-15-app-dsp2-*.log` | both ports while the app's DSP controls were used |
| `upnp-device-description.xml` | the UPnP device description the hub serves on port 38400 |

The `.log` files have one frame per line, all of them device to client: `time  port  hex  |ascii|`.

## What was changed

The captures were taken on a home network and carried personal data. The following were replaced before they were published, and nothing else:

- In PlayView JSON (message boxes `0x2a` and `0x2d`), the values of `TrackName`, `Artist`, `Album`, `CoverArtUrl` and `PlayUrl` became placeholders such as `Track 1` or `https://example.com/art/1/…`.
- IP addresses and the hub's UPnP UDN became stand-ins.

Every replacement has the same length as the original, so no length field and no packet boundary moved. Each device-to-client frame on port 7777 carries a CRC, and every CRC that was valid before is valid again: the affected frames were re-stamped. Equal values map to equal placeholders and different values to different ones, so track changes and the duplicated `0x2a`/`0x2d` pushes behave as they did on the wire.
