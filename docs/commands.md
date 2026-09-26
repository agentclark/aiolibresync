# Command and event reference

Every frame a client can send to a LibreSync hub, and every frame it can expect back, on both control ports: System Control on TCP 50006 and the media session on TCP 7777. This is the byte-level lookup table. The reasoning behind each rule lives in the protocol references: [protocol-system-control.md](protocol-system-control.md) for port 50006 and [protocol-media.md](protocol-media.md) for port 7777.

Everything here was established on one Platin Stereo Hub running firmware 1.52. Other firmware versions, and other brands on the same platform, may differ.

Byte sequences are hexadecimal. Offsets count from 0 at the first byte of the frame, including the length header.

## Evidence levels

Every row says where its confidence comes from.

- `tested` — sent to the device and confirmed by an effect of a different nature: a resulting event, or something audible.
- `capture` — seen on the wire, sent by the vendor's app or pushed by the device, but not confirmed by a deliberate test.
- `derived` — built from the frame rules of this reference and never sent to a device.
- `binary` — read out of the firmware itself (a string, a symbol or an instruction sequence). A name that suggests a meaning is not a confirmed meaning.
- `claimed` — asserted by an unverified source. No row currently carries this level.

A command is confirmed by its effect, not by reading back the value that was written. The mute section below shows why.

## Port 50006 — System Control

### Frame format

```
[length:2 BE][checksum][class][opcode][data...]
```

- `length` counts every byte after itself, so a frame is `length + 2` bytes long.
- `checksum = sum(bytes[3:]) & 0xFF` on client frames. The firmware does not verify it, so a wrong value is accepted, but generate it anyway: it is what the vendor's app does.
- Device-to-client frames carry `0x00` in byte 2. Byte 2 is not a direction marker: the firmware also accepts a client frame carrying `0x00`, so tell requests from reports by class, opcode and data length.

Two properties of the port shape every client:

- **It is a shared broadcast bus.** Every device-to-client frame goes to every connected client, not only the one that asked, and other clients' requests are visible too. A passive listener sees state changes without polling.
- **Frame on the length header.** Scanning the stream for byte patterns confuses one event for another; reading the 2-byte length and splitting on it does not.

The port is a transparent tunnel to a host microcontroller whose firmware is not on the network module, so `binary` evidence cannot exist for any row in this section. See [protocol-system-control.md](protocol-system-control.md).

### Power

| Action | Frame | Evidence |
| --- | --- | --- |
| Get power state | `00 03 0f 02 0d` | tested |
| Toggle power | `00 04 11 02 0f 00` | tested |
| Toggle power (the vendor app's "on" form) | `00 04 12 02 0f 01` | tested |

> **Power is a toggle.** The last two rows are the same command. The vendor's app sends `0f 00` for off and `0f 01` for on, with a correct checksum for each, but the firmware ignores the data byte: `0f 01` sent to a powered-on hub turns it off. There is no discrete power opcode. Query the state first, then toggle only if it differs; the vendor's own app does not, and races. See [protocol-system-control.md](protocol-system-control.md).

The toggle answers with **two** identical `02 0e` frames, broadcast to every connected client. Deduplicate them.

### Source selection

Frame: `00 04 <05+ix> 02 03 <ix>`, where `ix` is the source index from the device's own source list.

| Input | Index | Frame | Evidence |
| --- | --- | --- | --- |
| Streaming / Wi-Fi | 0 | `00 04 05 02 03 00` | tested |
| USB | 1 | `00 04 06 02 03 01` | derived |
| COAX | 2 | `00 04 07 02 03 02` | derived |
| OPT3 | 3 | `00 04 08 02 03 03` | derived |
| OPT2 | 4 | `00 04 09 02 03 04` | derived |
| OPT1 | 5 | `00 04 0a 02 03 05` | derived |
| HDMI ARC | 6 | `00 04 0b 02 03 06` | tested |
| AUX | 7 | `00 04 0c 02 03 07` | derived |
| Line In | 8 | `00 04 0d 02 03 08` | tested |
| Bluetooth | 9 | `00 04 0e 02 03 09` | derived |

| Action | Frame | Evidence |
| --- | --- | --- |
| Get source list and current source | `00 03 03 02 01` | tested |

Take the index-to-name mapping from the device's JSON source list (the `02 02` report), not from this table. The names above are the ones this hub reports, and a rebranded device can expose a different input set.

Frames with a wrong checksum byte are accepted: `0x0a`, `0xff` and `0x00` in byte 2 all select the source. Send the correct checksum regardless.

Switching **away from Streaming ends the Chromecast session**, and switching back does not resume it. A client that offers source selection should expect the media session to end, not merely pause.

### Volume, mute and source in one read

| Action | Frame | Response | Evidence |
| --- | --- | --- | --- |
| Get source, volume, mute and audio activity | `00 03 22 02 20` | `00 07 00 02 21 <src> <vol> <mute> <audio>` | tested |

The hub answers in about 84 ms with the same `02 21` report it pushes on change. It is the only way to read those four fields on connect, because `02 21` is otherwise emitted on change only. Read it once on connect; there is nothing to gain from polling it.

The volume and transport *commands* live on port 7777. Mute has no command on either port.

### DSP

| Action | Frame | Evidence |
| --- | --- | --- |
| Manual EQ on | `00 04 08 03 04 01` | tested |
| Manual EQ off (flat) | `00 04 07 03 04 00` | tested |
| Get manual EQ state | `00 03 08 03 05` | tested |
| Room correction on | `00 04 09 07 01 01` | tested |
| Room correction off | `00 04 08 07 01 00` | tested |
| Get room correction state | `00 03 09 07 02` | tested |

`03 04` is the same switch the vendor's app shows as the manual EQ master switch.

Room correction is a single frame. The set alone takes effect; the query that the vendor's app sends after it is an ordinary read, not a second half of the command. The hub takes between 0.21 s and 0.76 s to apply a change (four measured writes), so a read-back immediately after the set can still return the old value. Re-read until the value changes or a timeout expires.

### EQ presets

| Action | Frame | Evidence |
| --- | --- | --- |
| Select EQ preset 1 | `00 07 0f 08 02 01 01 01 02` | tested |
| Select EQ preset 2 | `00 07 10 08 02 01 02 01 02` | tested |
| Select EQ preset 3 | `00 07 11 08 02 01 03 01 02` | tested |
| Get active preset | `00 04 0d 08 05 00` | capture |

The data field is `01 <n> 01 <action>`. The preset id sits at offset 6 and is the only byte that varies between the three selects.

> **Never send `08 02` with a last byte other than `02`.** The last byte is an action, not a constant: `02` selects, while `00` and `01` store the editor's working curve into preset `<n>` and **overwrite** whatever filters the owner built there. A client that means to select must send `02` and nothing else. The library's `select_eq_preset` range-checks the id to 1–3 and appends a fixed `01 02`, so it cannot store by accident.

#### Preset 0 and the editor frames

The hub has a fourth preset slot, **preset 0**, which is the vendor app's EQ editor working slot. It holds the last curve edited. The app selects it with the ordinary select command when its editor opens, and from then on the hub reports preset 0 until another preset is selected. A client must accept 0 as a legitimate active preset, not treat it as an error.

The editor uses these frames. They are listed so a client can recognise them on the shared bus, not so it can send them.

| Frame | Meaning | Evidence |
| --- | --- | --- |
| `00 07 0e 08 02 01 00 01 02` | select preset 0, the editor's working slot | capture |
| `00 23 <cs> 08 01 …`, 35 bytes | write one filter into the working slot. Not decoded | capture |
| `00 07 <0c+n> 08 02 01 <n> 01 00`, then `00 07 <0d+n> 08 02 01 <n> 01 01` | store the working curve into preset `<n>`, **overwriting** it. Each draws an `08 05` report whose last byte echoes `00` or `01` | capture |

#### Filter definitions

Selecting a preset is supported; defining one is not, and users build presets in the vendor's app. Two queries return the underlying data, and neither response is decoded.

| Query | Response | Contents |
| --- | --- | --- |
| `00 03 10 07 09` | `00 8f 00 07 0a …`, 143 bytes | the room calibration curve |
| `00 05 <0c+n> 08 04 00 <n>` | `00 0f 00 08 04 02 <i> …`, 17 bytes, one frame per filter | filter parameters for preset `<n>` |

The filter read answers with **one 17-byte frame per filter**, with the filter index `<i>` in the second data byte (`02 00 …`, `02 01 …`). A preset with no filters gets no answer at all, so silence means an empty preset, not a failure. The blocks differ per preset, which is where the filter definitions live.

### Connect-time configuration queries

The vendor's app sends these on every connect. They are read-only: sending them changes nothing, and each answer goes to every connected client.

| Frame | Response | Evidence |
| --- | --- | --- |
| `00 03 4c 02 4a` | `00 04 00 02 4b 00` | tested |
| `00 03 4f 02 4d` | `00 04 00 02 4e 00` | tested |
| `00 03 8d 02 8b` | `00 04 00 02 8b 3c` | tested |

All three answers are invariant across power state and source. They are configuration, not state, and what they mean cannot be determined from the network side. **Do not poll them**; read them once on connect if at all.

> **The get/report/set pattern is not a rule.** In class `02` the opcodes usually come in triplets, a get at `N`, a report at `N+1` and a set at `N+2` (`01/02/03`, `0d/0e/0f`, `20/21`). `02 8b` breaks it by answering with its own opcode. Do not send the inferred set forms `02 22`, `02 4c` or `02 4f`: they would write properties nobody has identified.

### Events (device to client)

Byte 2 is always `0x00` in this direction, and every frame goes to every connected client.

| Event | Frame | Meaning | Evidence |
| --- | --- | --- | --- |
| Power state | `00 04 00 02 0e 01` / `… 00` | on / off. Sent **twice** when changed over the network | tested |
| Source list | `01 4a 00 02 02` + JSON | the full source list, `sts:1` marks the current one. Pushed on every source change | tested |
| Volume / mute / source | `00 07 00 02 21 <src> <vol> <mute> <audio>`, 9 bytes | all four in one frame, **on change only** | tested |
| Room correction | `00 04 00 07 03 01` / `… 00` | on / off. **Never pushed**: it only appears as the answer to a `07 02` query | tested |
| Manual EQ | `00 04 00 03 05 01` / `… 00` | on / off, about 200 ms after the set | tested |
| EQ preset | `00 09 00 08 05 02 04 <n> 01 01 <a>`, 11 bytes | active preset, id at offset 7 (0 is the editor slot). The last byte `<a>` is `01` after a select, and echoes the action byte (`00` or `01`) after a store | tested |
| unknown `0x4b` | `00 04 00 02 4b 00` | | capture |
| unknown `0x4e` | `00 04 00 02 4e 00` | | capture |
| unknown `0x8b` | `00 04 00 02 8b 3c` | `0x3c` = 60 | capture |

Notes on the `02 21` report:

- `<src>` at offset 5 is a source index in a **second numbering** that disagrees with the JSON `ix` on the wireless inputs. Take the current source from the `02 02` list instead.
- `<vol>` is at offset 6 and `<mute>` at offset 7. The mute byte is the dependable way to read mute state, since the port 7777 mute query can be refused.
- `<audio>` at offset 8 is **audio activity on the selected input**, in the play-state values `00`, `01`, `02`, `05`. It is neither a signal flag nor the streaming play state, and on a physical input it is the only field on either port that knows whether sound is coming out.

Field-by-field detail is in [protocol-system-control.md](protocol-system-control.md).

**Room correction is the one property with no event at all**, whoever changes it. A client must poll `00 03 09 07 02`. The vendor's app re-reads after each of its own sets and shows a stale value if something else changes the setting while its screen is open.

Power changes are pushed on firmware 1.52, including a press on the RF remote. Whether every power path is pushed on every firmware is not established, so a periodic `02 0d` query is a sensible backstop.

### Observed, unidentified

| Frame | Notes | Evidence |
| --- | --- | --- |
| `00 07 00 08 06 02 00 01 00` | class `08` (presets), opcode `06`. Seen twice, changed no observed state, meaning unknown | capture |

## Port 7777 — media session

### Frame format

Both directions share one 10-byte header followed by the payload:

```
<RemoteID:2> <msg_type> <cmd:2> <status> <CRC:2> <len:2> <payload>
```

Every 16-bit field is **little-endian from the client** and **big-endian from the device**. For the commands below a request is therefore `00 00 <msg_type> <cmd_lo> <cmd_hi> 00 00 00 <len_lo> <len_hi> <payload>`.

- `msg_type`: `01` query, `02` command or pushed event.
- `cmd` is a **16-bit LUCI message-box id**, not a port-local opcode. The same box is read one way and written the other.
- `status`: `00` unsolicited, `01` reply, `02` refused, `03` session not registered. Dispatch on the message box, not on this byte.
- **Leave the CRC at `00 00` on requests.** The device accepts zero, which is what its own app sends, and also accepts a wrong non-zero value. On device frames the field is CRC-16/XMODEM and can be verified.

The full layout is in [protocol-media.md](protocol-media.md).

### Commands and queries

| Action | msg_type | cmd | Payload | Evidence |
| --- | --- | --- | --- | --- |
| Register session | `02` | `0003` | client IP as ASCII, e.g. `192.168.1.20` | tested |
| Read model | `02` | `00d0` | `READ_Model` | capture |
| Set volume | `02` | `0040` | `"0"`–`"100"` | tested |
| Play / Pause / Stop / Next / Prev | `02` | `0028` | `PLAY` `PAUSE` `STOP` `NEXT` `PREV` | tested |
| Get now-playing | `01` | `002a` | *(empty)* | capture |
| Get volume | `01` | `0040` | *(empty)* | tested |
| Get mute | `01` | `003f` | *(empty)*, answered `MUTE` / `UNMUTE` or refused | tested |
| Get firmware build | `01` | `0005` | *(empty)*, answered e.g. `15262` | tested |
| Get serial | `01` | `00e7` | *(empty)*, answered `<serial>,VALID`. Query only: never write this box | tested |
| ~~Set mute~~ | `02` | `003f` | `MUTE` / `UNMUTE`, **accepted and ineffective** | tested |

**Register the session before anything else.** An unregistered connection is answered `cmd 0000 status 03` and the request is dropped, which looks like a malformed frame and is not. The vendor's app waits about 100 ms after registering before it sends anything.

What the transport commands produce:

- `PAUSE`: the `0028` ack (`status 01`), then `0033 "2"`.
- `PLAY`: the same ack, then `0033 "0"`.
- `NEXT` and `PREV`: byte-identical sequences, the `0028` ack, `0033 "1"`, `0033 "5"`, a position reset and `01ee`. The two cannot be told apart on the wire. The RF remote produces the same sequence.
- `STOP`: the position field goes to `-1000`, which means "no position" and makes the field **signed**, and the speakers report `SPEAKER_INACTIVE`. See [protocol-media.md](protocol-media.md).
- Set volume: the `0040` event arrives **twice**, as it does when the volume changes from the remote.

The mute query answered `MUTE` / `UNMUTE` in one session and was refused with `status 02` in later sessions on the same firmware, with the hub both off and playing. Read mute from the port 50006 `02 21` report instead, fetched on demand with `02 20`.

### There is no mute command

Neither the RF remote nor the vendor's app sends a mute command over the network: the remote's mute button puts nothing on the wire, and the app has no mute control. Writing `02 003f "MUTE"` is **accepted**, without the refusal other boxes give, and a re-read then returns `MUTE`. **The audio does not change**, confirmed by ear and by the playback position, which kept tracking wall-clock time to within 800 ms.

Two rules follow:

- **Mute is read-only.** Box `0x3f` is a mirror the host microcontroller updates; writing it desynchronises the mirror and reaches nothing. Volume behaves differently because its box is backed by a stored setting with a handler that propagates it. "Same box, read one way and written the other" is a property of some boxes, not of the bus.
- **Writing a box is not commanding the device.** Reading back the value just written confirms nothing. A command is confirmed by an effect of a different nature: an event on the other port, or something audible.

### Message boxes named from the firmware

| cmd | Meaning | Evidence |
| --- | --- | --- |
| `0005` | firmware version | binary |
| `0040` | volume, backed by the stored `current_volume` setting | binary |
| `005a` | model/brand string, `LibreX` on this build | binary |
| `006f` | tunnel init; the payload is the TCP port as ASCII | binary |
| `0070` | tunnel data. **This is port 50006** | binary + capture |

> **Do not sweep this port, and never address factory reset.** Because `cmd` names a message box directly, an unknown value reaches whatever the internal bus attaches to that box, and somewhere in that space are factory reset and firmware upgrade. Port 50006 cannot reach those; port 7777 can. A client should address only a closed set of known boxes, never one taken from user input. Boxes 107 (`0x6b`) and 219 (`0xdb`) refuse requests with `status 02` on this model.

### Pushed events (device to client)

| cmd | Payload | Meaning | Evidence |
| --- | --- | --- | --- |
| `0031` | milliseconds, ASCII | playback position, once per second while playing. Signed: `-1000` means no position | capture |
| `0033` | `0` `1` `2` `5` | play state: playing / stopped or idle / paused / loading | capture |
| `003f` | `MUTE` / `UNMUTE` | mute changed | capture |
| `0040` | `0`–`100` | volume changed, sent **twice** | capture |
| `002a`, `002d` | PlayView JSON | now-playing metadata, sent as an identical pair | capture |
| `0046` | `SPEAKER_ACTIVE,<n>` / `SPEAKER_INACTIVE,<n>` | WiSA speakers active or released | capture |
| `00e6` | e.g. `48000` | sample rate of the new track | capture |
| `01ee` | *(empty)* | part of the track-change sequence. A two-byte id: read `01 ee` at offsets 3..4, not `status 01` / `cmd ee` | capture |
| `000a`, `0032` | e.g. `24` / `25` | internal source id | capture |
| `0070` | binary | tunnel: carries client-to-device port 50006 frames. The same box is also backed by UART and BLE | capture + binary |

The tunnel carries port 50006 **commands** only, not the device's replies or events, so port 7777 alone is not enough. A client needs both sockets.

### Multiroom

`SETMASTER` `SETSLAVE` `SETFREE` `JOINALL` `JOINTO` `JOINNEXT` `JOINNEXTLEFT` `JOINNEXTRIGHT` `DROPME` `DROPALL` exist as command names in the firmware (`binary` only). No wire format and no port is known for them.

## Not this protocol: Primare's ASCII API

Primare publishes a TCP/IP and RS232 API for its Prisma range, built on the same Libre LS9 module, on **the same port 50006**. It is not this protocol. Primare's API is line-oriented ASCII:

```
!1pow.?   -> get power        !1vol.25  -> set volume 25
!1pow.1   -> power on         !1mut.t   -> toggle mute
!1inp.6   -> select input 6   terminated by <CR><LF>
```

Same port number, same module, a completely different wire format: Primare layers its own integrator-facing API over the LibreSync stack. The document is useful as a map of which properties the platform models (power, input, volume, mute, DSP and surround mode), not as a decoder for the binary protocol. In particular, Primare's discrete power on/off does not mean the binary protocol has one: `02 0f` toggles and ignores its data byte.
