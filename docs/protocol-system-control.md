# Port 50006: System Control protocol

Port 50006 is a raw binary TCP protocol with no handshake and no authentication. It carries power, source selection, DSP settings (manual EQ, EQ presets, room correction) and the asynchronous state feedback that goes with them. Playback, metadata and the media session live on port 7777, documented in [protocol-media.md](protocol-media.md). The consolidated byte tables for both ports are in [commands.md](commands.md), and what the device is and how to find it on the network is in [devices.md](devices.md).

Everything here was established on one Platin Stereo Hub running firmware 1.52. Other firmware versions, and other brands built on the same LibreSync platform, may behave differently; the library is written to tolerate that where it can (see [Known frames](#known-frames)).

## Evidence levels

Rows in the tables below carry one of these labels:

- `tested`: sent to a device and confirmed by the resulting event or reply.
- `capture`: observed on the wire from the vendor's app or the device, not sent independently.
- `derived`: reconstructed from the frame rules, never sent to a device.
- `binary`: established by static analysis of the device firmware.
- `claimed`: asserted by prior third-party documentation or code and not independently confirmed. No frame in this document rests on this level.

No opcode on this port can be `binary`: as the next section explains, the opcode table is not in the firmware that handles the port. `binary` applies here only to how the port itself works.

## What the port is: a transparent byte tunnel

Static analysis of the device firmware shows that nothing in the LibreSync stack parses the frames documented below. The TCP server on port 50006 is a tunnel:

- **Client to device.** The server reads up to 1 KiB from the socket and hands the buffer, unmodified, to the platform's internal message bus as message box 112. The box number is a hardcoded constant; there is no field a client can use to address anything else. `binary`
- **Device to client.** Every change of message box 112 is sent, unmodified, to every connected client in turn. `binary`, and confirmed by experiment: a connection that has sent nothing receives the replies to another client's queries. `tested`

The `[length][checksum][class][opcode]` grammar belongs to a separate host microcontroller in the product, which the LibreSync module reaches over a serial link. A 50006 frame travels there as the payload of an internal message and comes back the same way. Three consequences run through the rest of this document:

- The checksum is not validated, the direction convention in byte 2 is not enforced, and every frame is broadcast to every client, because the software in the middle does not look at any of it.
- The opcode table cannot be recovered from the device firmware. Every opcode below rests on observation, on a successful test, or on the get/report/set pattern described later.
- A client on this port can only reach message box 112. Platform functions such as factory reset and firmware upgrade live in other message boxes and are unreachable from here. They are reachable from port 7777, where the client chooses the message box; see [protocol-media.md](protocol-media.md).

The listening port is not a compile-time constant: the host microcontroller announces it at startup through a separate message box (111), as ASCII. The tunnel is also registered conditionally on the device's customer identification, so a unit of another brand on the same platform may have no tunnel behind port 50006 at all. `binary`

## Frame format

```
+--------+--------+----------+---------+--------+-----------+
| len_hi | len_lo | checksum |  class  | opcode |  data...  |
+--------+--------+----------+---------+--------+-----------+
   0        1          2          3         4       5..N
```

| Field | Size | Notes |
| --- | --- | --- |
| `length` | 2 bytes, big endian | number of bytes that follow, **including** the checksum byte. Total frame size is `length + 2`. |
| `checksum` | 1 byte | `sum(bytes[3:]) & 0xFF` on client-to-device frames. Always `0x00` on device-to-client frames. |
| `class` | 1 byte | command namespace: `0x02` system, `0x03` manual EQ, `0x07` room correction, `0x08` EQ presets |
| `opcode` | 1 byte | operation within the class |
| `data` | 0..N bytes | operands |

Frame the stream on the 2-byte length header. Scanning for byte patterns confuses one event for another. The library's reader in [../src/aiolibresync/frames.py](../src/aiolibresync/frames.py) also resynchronises past a header whose length is implausible, one byte at a time, rather than dropping the connection.

### Byte 2 is a checksum

Byte 2 is the low byte of the sum of everything from byte 3 (class) onward. It is not a fixed "command class" byte, a mistake some older third-party notes make. The EQ presets show it most clearly: the three frames differ in one data byte, and byte 2 tracks that byte exactly.

| Frame | byte 2 | `sum(bytes[3:]) & 0xFF` |
| --- | --- | --- |
| `00 03 03 02 01` get source list | `03` | `03` |
| `00 03 0f 02 0d` get power | `0f` | `0f` |
| `00 04 11 02 0f 00` power toggle | `11` | `11` |
| `00 04 08 03 04 01` manual EQ on | `08` | `08` |
| `00 07 0f 08 02 01 01 01 02` EQ preset 1 | `0f` | `0f` |
| `00 07 10 08 02 01 02 01 02` EQ preset 2 | `10` | `10` |
| `00 07 11 08 02 01 03 01 02` EQ preset 3 | `11` | `11` |
| `00 04 09 07 01 01` room correction on | `09` | `09` |

> **Caution: source-select constants in older notes are wrong.** Some older notes and prototype clients hardcode byte 2 as `0x0a` for every source-select frame. That is correct only for index 5 (`0x02 + 0x03 + 0x05 = 0x0a`). The correct form is `00 04 <0x05 + index> 02 03 <index>`.

### The firmware does not validate the checksum

Frames with a wrong byte 2 are accepted and acted on. `tested`

| Frame | Byte 2 sent | Correct value | Result |
| --- | --- | --- | --- |
| `00 04 0a 02 03 06` HDMI, hardcoded constant | `0a` | `0b` | accepted, switched to HDMI |
| `00 04 0b 02 03 06` HDMI, correct | `0b` | `0b` | accepted |
| `00 04 ff 02 03 00` Streaming | `ff` | `05` | accepted, switched to Streaming |
| `00 04 00 02 0f 00` power toggle | `00` | `11` | accepted, powered off |

So clients that hardcode the wrong constant still work. **Send the correct checksum anyway.** It costs one addition, it is what the device itself does, and a firmware revision that starts validating would break every frame that guessed. The library always computes it; see `build_control` in [../src/aiolibresync/frames.py](../src/aiolibresync/frames.py).

### Byte 2 is not a direction marker

Every device-to-client frame carries `0x00` in byte 2 instead of a checksum, so the byte looks like it marks direction. It does not. A client frame carrying `0x00`, shaped exactly like a device event (`00 04 00 02 03 08`), was accepted and acted on. `tested` And because the port is a broadcast bus, other clients' commands are visible to every connection anyway.

A parser that tells a command from an event by byte 2 is trusting a convention nothing enforces. Match on class, opcode and data length instead, and decode only the report forms. [../src/aiolibresync/systemcontrol.py](../src/aiolibresync/systemcontrol.py) does exactly this: a `02 0f` sent by another client is recognised and ignored, so it cannot be mistaken for a power state.

## Status queries

The device answers status queries. A client can read its state on connect rather than reconstructing it by sending commands and watching for side effects. The vendor's app does this: it opens the socket, sends a burst of queries, and the hub answers every one.

Within class `0x02` the opcodes mostly form a triplet per property: `N` get, `N+1` report, `N+2` set.

| Property | get | report | set | Evidence |
| --- | --- | --- | --- | --- |
| Source list and current source | `02 01` | `02 02` + JSON | `02 03 <index>` | tested |
| Power | `02 0d` | `02 0e <0\|1>` | `02 0f 00` (toggle) | tested |
| Source, volume, mute, audio activity | `02 20` | `02 21` + 4 bytes | `02 22`? | get and report tested; set derived |
| unknown (`0x4a`) | `02 4a` | `02 4b <byte>` | `02 4c`? | get and report tested; set derived |
| unknown (`0x4d`) | `02 4d` | `02 4e <byte>` | `02 4f`? | get and report tested; set derived |
| unknown (`0x8b`) | `02 8b` | `02 8b <byte>` (**not** `8c`) | none | tested |

Every cell marked `?` is inference from the pattern and has never been sent to a device. Do not send them: `02 22`, `02 4c` and `02 4f` would write properties nobody has identified, on a port whose command language is defined by a microcontroller whose firmware is not available. A get is a small bet; a set on an unknown property is not. The library deliberately does not define these opcodes.

`02 20` (`00 03 22 02 20`) answers with a full `02 21` frame in about 84 ms. It is the only way to learn volume, mute and audio activity on connect, because `02 21` is otherwise sent only on change.

### The triplet is a pattern, not a rule

`02 8b` answers on its own opcode (`02 8b 3c`), not on `02 8c`. `02 4a` and `02 4d` do increment, answering `02 4b 00` and `02 4e 00`, so both behaviours coexist in the same class. Do not assume an unknown property's get or set opcode from its report; confirm it first with a get.

### Configuration queries

The vendor's app sends `02 4a`, `02 4d` and `02 8b` on every connect. They are read-only: sending them changes nothing, and each reply reaches both the sending connection and passive listeners. `tested` Their answers are the same with the hub on or off and on any source, and `0x8b`'s value (`0x3c`, 60) has been stable over months. They report configuration, not state; what they mean cannot be determined from this side of the serial link. Do not poll them. The library does not send them.

## Pushed events

### The port is a broadcast bus

Every device-to-client frame goes to every connected client, not only the one that asked. A client therefore sees replies to other clients' queries, and must be prepared for state changes it did not request. `tested`

### What is pushed and what is not

| Property | Pushed? | Frame |
| --- | --- | --- |
| Volume, mute, source, audio activity | yes, on change only | `02 21` |
| Source list and current source | yes, on every source change | `02 02` |
| Power | yes, including changes from the RF remote | `02 0e` |
| Manual EQ | yes, about 200 ms after the set | `03 05` |
| EQ preset | yes | `08 05` |
| Room correction | **never**, whoever changes it | none |

Power changed over the network produces **two** identical `02 0e` frames, broadcast to every client; deduplicate them. Power changed from the RF remote also produces `02 0e`, confirmed on firmware 1.52. Earlier observations on this hardware saw no event for an RF-remote power change, so on other firmware a client should not rely on the push alone; see [What a client must poll](#what-a-client-must-poll).

### `02 21`: source, volume, mute and audio activity

The most useful event on the port: four properties in one 9-byte frame.

```
00 07 00 02 21 <source> <volume> <mute> <audio>
                  5         6       7       8
```

| Byte | Meaning | Values |
| --- | --- | --- |
| 5 | source, in its own numbering, **not** the JSON `ix` | 1..10, see [Byte 5: source numbering](#byte-5-source-numbering) |
| 6 | volume | `0x00`–`0x64` (0–100), the same value as the volume on port 7777 |
| 7 | mute | `01` muted, `00` not muted |
| 8 | audio activity on the selected input | `00` audio, `01` none, `02` paused, `05` starting; see [Byte 8](#byte-8-audio-activity) |

Example: `00 07 00 02 21 0a 1c 01 01` is source 10 (Streaming), volume 28, muted, no audio. Unmuting from the RF remote produces the same frame with byte 7 at `00` and nothing else changed.

Mute is read-only over the network on both ports: there is no command that changes it. See [commands.md](commands.md).

#### Sent on change, and only on change

A volume set on port 7777 produces one `02 21`, 51 to 155 ms after port 7777's own volume echo and never more than 192 ms after the write itself (seven sets, with the hub off, on an idle physical input, and playing on Streaming).

A volume set to the level the hub **already holds** produces no `02 21` at all, and a single volume echo on 7777 instead of the usual two. A client therefore cannot use `02 21` to confirm a volume set: a no-op set would never be confirmed. Confirm volume on port 7777 instead.

#### Byte 8: audio activity

Byte 8 reports **whether audio is actually flowing on the selected input**. It uses the same values as port 7777's play state (message box `0x33`), but it is a different property.

> **Caution: byte 8 is neither a signal flag nor the play state.** No boolean produces `02` and `05`, so it is not a "signal present" flag. And it matches the 7777 play state only on Streaming; on every physical input the two disagree.

On Streaming, the renderer's transport is what makes audio flow, so byte 8 tracks the 7777 play state exactly. Seven transitions, covering every value and both ways of driving playback:

| 7777 play state | byte 8 | lag | how it was driven |
| --- | --- | --- | --- |
| `LOADING` (5) | `05` | 1 ms | playback started on a hub that was off |
| `PLAYING` (0) | `00` | 0.12 s | the same wake, once it settled |
| `PAUSED` (2) | `02` | 0.19 s | `PAUSE` on 7777 |
| `PLAYING` (0) | `00` | 0.37 s | `PLAY` on 7777 |
| `IDLE` (1) | `01` | 1.0 s | `STOP` on 7777 |
| `PAUSED` (2) | `02` | 0.12 s | paused inside the TIDAL app |
| `IDLE` (1) | `01` | 0.07 s | cast session ended from the TIDAL app |

A stop gives `01`, whether it comes from the protocol or from the cast session being torn down.

On a physical input there is no transport, and it is the 7777 play state that is wrong. Selecting an input makes the device push `PLAYING` and `SPEAKER_ACTIVE` on port 7777 whether or not anything is connected, and it answers `PLAYING` to every query afterwards. Byte 8 meanwhile behaves like a signal detector:

| Input | byte 8 | 7777 play state |
| --- | --- | --- |
| USB, nothing plugged in | `01` | `PLAYING` |
| HDMI, television off | `01` | `PLAYING` |
| HDMI, television on | `00`, for as long as it plays | `PLAYING` |

On a physical input, byte 8 is the only field on either port that knows whether sound is playing.

**It is fast in both directions.** Arriving on an input that is already playing, byte 8 reads `00` within 1.53 to 1.78 s of the select. With no source change at all, a television paused and resumed was followed in 3.09 s and 2.23 s.

**Polling adds nothing.** `02 20` polled at 1 Hz across a transition returned the old value until the moment the push arrived, 105 ms apart. What the device pushes is what it knows. Read byte 8 once on connect with `02 20`, then follow the pushes.

#### The transient on source change belongs to the source being left

When the source being left was producing audio, byte 8 briefly keeps reporting `00` after the switch, even though the sound stops instantly. The value is the previous source's, held for a moment; it is not a reading of the new input. When the source being left was idle, there is no transient: selecting an empty USB input from an idle Streaming gives `01` at once.

| Leaving | Transitions measured | Stale `00` lasts |
| --- | --- | --- |
| a playing physical input | 4 | 0.40 to 0.58 s |
| a playing cast session | 5 | 2.56 to 2.98 s, plus one outlier of 19.05 s |
| an idle source of any kind | 7 | no transient |

Isolated tails of 16 to 19 s have been observed, but they are the edge of the distribution, not its centre. **Do not debounce byte 8.** A window wide enough to hide a three-second transient would also hide a real change for as long.

#### Keep audio activity separate from the play state

Do not fold byte 8 into the 7777 play state. Two ports feeding one field is how they end up disagreeing. The library exposes it as its own field, `DeviceState.audio_state`, next to `play_state` from port 7777, and `DeviceState.playback` names which one to believe (audio activity wherever it is known). See [../src/aiolibresync/state.py](../src/aiolibresync/state.py).

#### Byte 5: source numbering

Byte 5 numbers the sources independently of the JSON source list:

| Byte 5 | Source | JSON `ix` |
| --- | --- | --- |
| 1 | Bluetooth | 9 |
| 2 | USB | 1 |
| 3 | COAX | 2 |
| 4 | OPT3 | 3 |
| 5 | OPT2 | 4 |
| 6 | OPT1 | 5 |
| 7 | HDMI | 6 |
| 8 | AUX | 7 |
| 9 | Line | 8 |
| 10 | Streaming | 0 |

It is `ix + 1` for the eight physical inputs and differs on the two wireless ones: the JSON list puts Streaming at 0, this numbering puts Bluetooth at 1. These are two independent enumerations, so **do not compute one from the other**. The `02 02` JSON is self-describing and is the one to trust; treat byte 5 as informational. The library ignores it and takes the current source from the source list.

Naming differs too: the RF remote labels source 10 "Wi-Fi", the device's JSON calls it "Streaming".

### `02 02`: the source list, pushed

Changing source, from any client or from the remote, pushes the **full source-list JSON**, identical in form to the reply to a `02 01` query, with `sts: 1` on the new source. A `02 21` follows within about 5 ms, carrying the new source in byte 5. A client never has to poll for the source: query once on connect, then listen.

## The source list JSON

A `02 01` query (`00 03 03 02 01`) is answered by a `02 02` frame whose data is a JSON document. On this hub the frame starts `01 4a 00 02 02`; the length varies with the list.

```json
{"src":[{"ix":8,"name":"Line","sts":0},
        {"ix":7,"name":"AUX","sts":0},
        {"ix":6,"name":"HDMI","sts":0},
        {"ix":5,"name":"OPT1","sts":0},
        {"ix":4,"name":"OPT2","sts":0},
        {"ix":3,"name":"OPT3","sts":1},
        {"ix":2,"name":"COAX","sts":0},
        {"ix":1,"name":"USB","sts":0},
        {"ix":9,"name":"Bluetooth","sts":0},
        {"ix":0,"name":"Streaming","sts":0}]}
```

| Key | Meaning |
| --- | --- |
| `ix` | source index, the operand of the source-select command `02 03 <ix>` |
| `name` | display name as reported by the device |
| `sts` | `1` on the currently selected source, `0` otherwise |

The list is authoritative and device-reported. Build the source list from this JSON rather than from a hardcoded table: that is also what makes a client portable to rebranded devices that expose a different set of inputs. Keep the device's order. If no entry carries `sts: 1`, the frame does not say which source is current; keep the one already known rather than clearing it.

## Selecting a source

`00 04 <0x05 + ix> 02 03 <ix>`, where `ix` comes from the JSON. The device confirms with a pushed `02 02` and `02 21`. Per-input frames and their evidence are in [commands.md](commands.md).

Switching **away from Streaming terminates the cast session**, and switching back does not resume it. A client offering source selection should expect the media session to end, not merely pause.

## Power

### Power is a toggle

`02 0f` takes a data byte, but the firmware ignores it: both `00` and `01` toggle. Idempotent control therefore needs a read-then-act sequence:

1. Query `02 0d` (`00 03 0f 02 0d`), or use the last `02 0e` event.
2. If the reported state already matches the target, do nothing.
3. Otherwise send the toggle (`00 04 11 02 0f 00`) and wait for the `02 0e` confirmation.

Toggling first and correcting afterwards audibly cycles the speakers whenever the state was already correct. Reading first also makes a retry safe: a call whose confirmation was lost re-reads and does nothing, where a bare toggle would undo itself. The library's `async_set_power` follows this order, and raises rather than toggling blind if the read goes unanswered and no state is cached.

### There is no discrete power opcode

Other products on the same platform expose discrete power commands (see [commands.md](commands.md)), and the vendor's Android app appears to use discrete frames too. It encodes the intent in the data byte, with a correct checksum for each:

```
power off:  00 04 11 02 0f 00
power on:   00 04 12 02 0f 01
```

It is still a toggle. The app's own "power on" frame, `00 04 12 02 0f 01`, sent to a hub that was **already on**, turns it off. `tested`

> **Warning: the vendor's app has a double-toggle race.** Its "power on" button turns a powered-on hub off whenever the app's cached state is stale. Any client that sends a "discrete" power frame without reading the current state first has the same bug.

### What "power" actually is

It is not mains power, and not a power state in the ordinary sense. While "off", the hub answers on both ports, keeps its metadata, and replies to every query: a client connecting to a powered-down hub gets a fully populated state in about 0.05 s. Three measurements describe what the property does.

**Switching off is a stop.** The whole transition takes about 100 ms, in causal order: `02 0e` off first, then play state `IDLE` on port 7777, then `SPEAKER_INACTIVE`, then position `-1000`, the same sentinel an ordinary stop produces. Playback is terminated, not suspended.

**Switching on restores nothing.** After a power-on, no play state, position or speaker event follows. It only permits playback again.

**On the wake path, power arrives last.** Starting playback on a hub that is off produced `SPEAKER_ACTIVE` at t = 0, metadata at +1.0 s, `LOADING` at +1.3 s, and `02 0e` on at **+1.4 s**. Here power-on is a consequence of the session starting, not its cause.

So `02 0e` is a stop/permit flag that also tracks whether a session is running. Two consequences for anything built on top:

- **Do not map it onto a media player's on/off state.** An entity driven by power shows "off" while music plays, and on the wake path power is the last thing to arrive, so that window covers the whole start of a track.
- **Expect power transitions nobody requested.** The hub wakes itself when a source starts sending, so the property changes with no command behind it. Port 7777's play state and speaker events describe playback (the library's `is_stopped` and `speakers_active`); power describes whether the hub will allow it.

## Room correction is never pushed

Setting room correction produces **no frame on either port**, on the sending connection or on any other: three attempts in isolation, with waits of up to 8 s, drew nothing. The command works, and a query afterwards reports the new value; it simply announces nothing. Manual EQ, set in the same conditions, pushes `00 04 00 03 05 <0|1>` about 200 ms later, so this is a property of room correction specifically.

The vendor's app hides this by re-reading after every set, which makes the reply look like an event:

```
app: set    00 04 08 07 01 00
app: query  00 03 09 07 02
     reply  00 04 00 07 03 00     <- the reply to the query, not an event
```

The app also re-reads when its audio screen opens, so it shows a stale value for as long as that screen stays open while something else changes the setting.

The device applies a room-correction set with a delay: 0.21, 0.29, 0.57 and 0.76 s over four writes. A client confirming a set by re-reading must retry until the value changes, not read once. The library first re-reads after 0.3 s, then every 0.15 s, for up to 5 s.

## What a client must poll

| Property | Pushed? | Strategy |
| --- | --- | --- |
| volume, mute, source, audio activity | yes, on change | read once on connect with `02 20`, then listen |
| source list | yes | read once on connect with `02 01`, then listen |
| manual EQ, EQ preset | yes | read once on connect, then listen |
| power | yes on firmware 1.52, including from the RF remote | listen, and poll `02 0d` as a bound on staleness |
| room correction | **never** | poll `07 02` (`00 03 09 07 02`) |

Room correction alone justifies a periodic poll. Power is included because it has been seen unreported from the RF remote on this hardware before, and a poll is the only bound on how stale it gets if that recurs on another firmware. Writing the poll also has a side benefit: a connection that is written to periodically notices a peer that disappeared without closing the socket, which a listen-only connection never does. The library polls power and room correction every 30 s; see [../src/aiolibresync/client.py](../src/aiolibresync/client.py).

## Known frames

The frames the library sends and decodes. [commands.md](commands.md) has the complete table for both ports, including every source-select frame, the EQ preset and filter frames, and their exact layouts.

### Client to device

| Action | Frame | Evidence |
| --- | --- | --- |
| Get source list and current source | `00 03 03 02 01` | tested |
| Select source `ix` | `00 04 <05+ix> 02 03 <ix>` | tested (Streaming, HDMI, Line); derived for the other inputs |
| Get power | `00 03 0f 02 0d` | tested |
| Toggle power | `00 04 11 02 0f 00` | tested |
| Get source, volume, mute, audio activity | `00 03 22 02 20` | tested |
| Manual EQ on / off | `00 04 08 03 04 01` / `00 04 07 03 04 00` | tested |
| Get manual EQ | `00 03 08 03 05` | tested |
| Select EQ preset `n` (1 to 3) | `00 07 <0e+n> 08 02 01 <n> 01 02` | tested |
| Get active EQ preset | `00 04 0d 08 05 00` | capture |
| Room correction on / off | `00 04 09 07 01 01` / `00 04 08 07 01 00` | tested |
| Get room correction | `00 03 09 07 02` | tested |

### Device to client

| Event | Frame | Meaning | Evidence |
| --- | --- | --- | --- |
| Power | `00 04 00 02 0e <0\|1>` | off / on; sent twice when changed over the network | tested |
| Source list | `<len> 00 02 02` + JSON | full list, `sts: 1` marks current | tested |
| Source, volume, mute, audio activity | `00 07 00 02 21 <src> <vol> <mute> <audio>` | on change only, or in reply to `02 20` | tested |
| Manual EQ | `00 04 00 03 05 <0\|1>` | off / on, about 200 ms after a set | tested |
| EQ preset | `00 09 00 08 05 02 04 <n> 01 01 01` | active preset, id at offset 7 | tested |
| Room correction | `00 04 00 07 03 <0\|1>` | off / on; only ever a reply to `07 02`, never pushed | tested |
| unknown `0x4b` | `00 04 00 02 4b 00` | configuration | capture |
| unknown `0x4e` | `00 04 00 02 4e 00` | configuration | capture |
| unknown `0x8b` | `00 04 00 02 8b 3c` | configuration, `0x3c` = 60 | capture |

The library recognises a further set of frames (other clients' requests, the configuration replies above, the room calibration curve and per-preset filter parameters) and decodes them to no state. Any frame outside both sets is recorded for diagnostics rather than treated as an error, so a firmware that emits something new does not break the connection.
