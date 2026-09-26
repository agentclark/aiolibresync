# Devices and discovery

What a LibreSync hub is, which products share the platform, and how a client finds and identifies one on the network.

The protocol facts here were established on one Platin Stereo Hub running firmware 1.52. The compatibility tables for other brands come from public evidence and have not been verified against their hardware.

## What the device is

The **Platin Stereo Hub** is a WiSA-certified wireless audio transmitter that acts as the central control unit for a set of active speakers. It carries the Platin badge, but it is not a Platin design:

- **Libre Wireless Technologies** (Irvine, CA) supplies the network module and the whole software stack, **LibreSync**. The hub is built around a Libre **LS9** module, part `LS9AC11DBT`, FCC ID `2ADBM-LS9-AC11DBT` (and `2ADBM-LS9ADAC11DBT` for the LS9AD variant).
- **Hansong (Nanjing) Technology** builds the reference hardware and sells it white-label to many brands.

That is why this library is named for LibreSync rather than Platin: the protocol belongs to the platform, and Platin is one of a dozen badges on it.

## Two protocols on one device

A hub exposes two control ports, and a full client needs both.

| Port | Protocol | Carries |
| --- | --- | --- |
| TCP 50006 | System Control, a binary length-prefixed frame format | power, source selection, DSP (manual EQ, EQ presets, room correction), and state reports for all of them plus volume, mute and audio activity |
| TCP 7777 | LUCI media session, a registered session with a 10-byte header | volume, transport (play, pause, stop, next, previous), now-playing metadata, play state and position |

The two ports are not independent implementations. Both are views of one internal bus of numbered message boxes. Port 50006 is a transparent tunnel to a single box, whose command language belongs to the host microcontroller behind the network module; on port 7777 the command field names a message box directly. That asymmetry is why port 7777 must never be swept: see [commands.md](commands.md).

Port 7777 mirrors client-to-device port 50006 frames in its `0x70` tunnel box, but not the device's replies or events, so neither port's event set is a superset of the other's. Keep both sockets open.

Port 50006 broadcasts every device-to-client frame to every connected client, so a passive listener sees state changes without polling. The exception is room correction, which is never pushed.

Port 7777 stays up while the hub is in standby, and its handshake is identical either way. A connected socket is not a powered-on hub; read power from port 50006.

The protocol references are [protocol-system-control.md](protocol-system-control.md) and [protocol-media.md](protocol-media.md). Every frame is listed in [commands.md](commands.md).

## Network surface

| Port | Proto | Role |
| --- | --- | --- |
| 50006 | TCP | System Control. The port whose presence confirms a LibreSync hub |
| 7777 | TCP | LUCI media session |
| 38400 | TCP (HTTP) | UPnP/DLNA MediaRenderer and its device description, served by `LibreDmr`. Bound to the hub's own address only |
| 1900 | UDP multicast | SSDP; the hub answers `M-SEARCH` for `MediaRenderer:1` while `LibreDmr` runs |
| 7000 | TCP | AirPlay. Not part of the control plane |
| 9095 | TCP | Spotify Connect helper. Not part of the control plane |
| 8800 | TCP | a Libre device service. Not analysed |
| ephemeral | TCP | a RAAT (Roon) endpoint; the port changes across reboots. Not analysed |
| 8008, 8009, 8443 and others | TCP (IPv6) | Chromecast stack. Not part of the control plane |

The documented unit has Wi-Fi only; it has no Ethernet interface.

Lithe Audio's installer documentation tells integrators to open 7777 and 9095 in a firewall, which is one of the fingerprints tying the ecosystem together, though 9095 is the Spotify helper and protects the streaming path, not control.

## Finding a hub

Discovery is implemented in [../src/aiolibresync/discovery.py](../src/aiolibresync/discovery.py). It takes three steps, in increasing order of cost:

1. An SSDP `M-SEARCH` for `urn:schemas-upnp-org:device:MediaRenderer:1` to `239.255.255.250:1900`, sent three times a second apart because UDP loses datagrams, collecting replies for 5 s.
2. An HTTP GET of the device description at the `LOCATION` header of each reply, for example `http://192.168.1.50:38400/description.xml`. This is the only place the manufacturer and the UDN appear.
3. A TCP connect to port 50006, which is what separates a LibreSync hub from every other MediaRenderer on the network.

The description filter accepts a `manufacturer` containing `LibreWireless` **or** a `modelDescription` containing `LibreSync`. Neither is guaranteed on a rebranded unit: a brand is free to write its own manufacturer and keep the stock DMR string, or the reverse. Step 3 is what confirms, so the filter can afford to be generous.

Details that matter to a client:

- **`LOCATION` is the only URL to trust.** The description's `URLBase` is written at boot and can carry an address the hub no longer has. The library follows a `LOCATION` only when it names the address the reply came from, which is what the hub sends (`http://<its IP>:38400/description.xml`, captured); a reply pointing at a hostname or at another machine is ignored.
- **The confirmation probe sends nothing.** It connects to 50006 and closes. At that point the host has only served a description of itself, and 50006 is a shared bus that every connected client hears.
- **Manual setup has no `LOCATION` header**, so a client that knows the address assumes `http://<host>:38400/description.xml`. Port 50006 answering is what makes an address usable; a missing description only means the identity is unknown.
- Each SSDP reply carries a `BOOTID.UPNP.ORG` header that changes on every reboot. It is present in the reply and not in the description.

## Identity

### What the hub reports

| Source | Field | Value on the documented unit |
| --- | --- | --- |
| UPnP description | `friendlyName` | `Stereo` |
| UPnP description | `manufacturer` | `LibreWireless` |
| UPnP description | `modelName` | `LibreWireless` |
| UPnP description | `modelDescription` | `LibreSyncDMR` |
| UPnP description | `UDN` | `uuid:00000000-0000-0000-0000-000000000000` (per unit) |
| UPnP description | `serialNumber` | `1234567890`, a vendor placeholder |
| UPnP description | services | `RenderingControl`, `AVTransport`, `ConnectionManager`, and Tencent `QPlay:1` |
| SSDP reply | `SERVER` | `Platform 1.0 CyberLinkC/3.0 UPnP/1.0 DLNADOC/1.50` |
| SSDP reply | `USN` | carries the UDN |
| Port 7777 | `READ_Model` | `Model:Stereo Hub` |
| Port 7777 | box `0x00e7` query | `<serial>,VALID`: the factory serial, 20 characters |

### Two stable identifiers, in order of preference

**The factory serial, from message box 231.** At boot the LUCI service reads the unit's serial from the factory partition, checks it, and stores it in box `0x00e7` as `<serial>,VALID`. A registered port-7777 session reads it with a plain query (`01 00e7`, empty payload). It does not depend on the UPnP daemon, so it is readable whenever the control ports are, and it comes from the factory partition rather than from anything a reset regenerates. Believe it only when the flag says `VALID`: the firmware keeps that flag because a unit without a valid serial is possible. The mechanism is in Libre's generic LUCI service rather than in anything brand-specific, but it has only been confirmed on one Platin unit.

**The UPnP UDN.** It survives reboots, mains power cycles, a change of subnet and months of operation, and it is what SSDP carries in its `USN`, so discovery sees it without any connection. A factory reset mints a new one. It is served by `LibreDmr`, and it is unreadable whenever that daemon is down (below).

A client that has to recognise the same hub across paths should record both when it can. Discovery only ever sees the UDN, and a unit may lack a valid serial. The library exposes the serial as `DeviceState.serial` and `async_read_serial(host)`, and the UDN as `DiscoveredDevice.udn`.

Do not use the description's `serialNumber`: it is the placeholder `1234567890` on this hardware, unrelated to the factory serial. `friendlyName` and `modelName` are generic. The Wi-Fi MAC is not readable by a client: the firmware only uses it internally.

### When the UDN is unavailable

The UDN is stable but not always readable. It is served by the UPnP daemon `LibreDmr`, which answers both the SSDP search and the description on port 38400, and that daemon can die on an otherwise healthy hub. Nothing on the device restarts it. It has been found gone within three hours of a boot, and alive after eight days on another. What stops it is unknown.

While `LibreDmr` is down:

- SSDP discovery does not find the hub, and port 38400 refuses connections.
- **Both control ports keep working.** The hub is fully controllable; only its identity is missing. A client must not treat "no UDN" as "no device".
- A mains power cycle brings the daemon back, and discovery works again.

A hub on a physical input stays visible to discovery; selecting HDMI or another input does not stop the renderer.

## Compatible devices

Only the Platin Stereo Hub has been verified. The rest of this section maps the platform from public evidence, so treat every entry below Tier 1 as a lead rather than a guarantee.

### How to recognise the platform

Two fingerprints identify a device built on this platform:

1. **FCC grantee codes**: `2ADBM` (Libre Wireless modules) and `XCO` (Hansong). Lithe Audio's filing `2AQOB-LWF1V2` is a "Change in ID" against Libre's `2ADBM-LS9ADAC11DBT` grant, which is about as direct as evidence gets.
2. **The `App_development` Google Play publisher account**, which is Hansong's software arm. Any brand whose official app ships from that account runs Hansong firmware.

A softer third signal is the "Zen microphone" room-correction workflow, a Hansong DSP feature reskinned per brand.

The corporate link between the two names is also public: an executive of Hansong is President of Platin Gate Technology.

### Tier 1: direct hardware rebrands

Physically the same box off the same line. The protocol should be identical.

| Brand | Model | Evidence |
| --- | --- | --- |
| Platin | Stereo Hub / Stereo Hub HT | the device documented here |
| System Audio | Stereo Hub, Stereo Hub HT, Surround Hub | a SoundStage! review states it is identical to Buchardt's, both by Hansong; the `SA Cockpit` app is published as `com.hansong.systemaudio` |
| Buchardt Audio | Stereo Hub (with A500 / A700 / Anniversary 10) | the company's CEO confirmed Hansong manufacture publicly |
| Mesanovic | bundled with CDM65 monitors | sold openly as "Platin Stereo Hub", stock firmware |
| Econik | Stereo Hub, Surround Hub X | identical enclosure and remote; Hansong app |
| Triangle Hi-Fi | Compact Stereo Hub (Capella system) | matching I/O and remote; Zen microphone support |

### Tier 2: same stack, different chassis

Libre modules and the same software stack, but each brand's own hardware around them. Command values may differ (a given input index will not mean the same thing), while the framing should hold. The library reads the source list from the device rather than assuming Platin's inputs, which is what lets it work on a different input set.

| Brand | Models | Module | Evidence |
| --- | --- | --- | --- |
| Primare | Prisma range: SC15, PRE35, I15/I25/I35, CD15/CD35, NP5, SPA25 | LS9AD-AC11DBT | teardowns identify the module; Primare publishes a TCP/IP API on port 50006, **but see the caveat below** |
| Rotel | S14, RAS-5000 | LS10 | FCC `2ADBM-LS10` + `XCO-HSBT075`; app published by `App_development` |
| Lithe Audio | Wi-Fi Ceiling Speaker V2, Pro Series, Cinema Hub | LS9AD | FCC Change-in-ID against the Libre grant; support documentation tells installers to open TCP 7777 and 9095 |
| Sonoro | Maestro, Meisterstück Gen 2, Grand Orchestra | Hansong ODM | "sonoro Room Correction" app published by Hansong directly |
| Riva Audio | Concert, Stadium, Festival, Arena | MAVID | Libre launch partner; app by `App_development`; named in CVE-2020-35755 |
| Vanguard Dynamics | Vail Cast | Hansong ODM | app by `App_development` |
| Harman Kardon | Citation 100/300/500, Bar, Tower | LS9 | named in CVE-2020-35755; built around Google Home, so 50006 may be locked down |
| Canton | Smart Soundbar 9/10, Smart Amp 5.1, Smart Connect 5.1 | Hansong ODM | listed as a Hansong customer; the weakest evidence in this table |

CVE-2020-35755 and CVE-2020-35756 concern the LS9 platform's port 7777 service, so their affected-device list doubles as a compatibility list.

### The Primare caveat

Primare's publicly downloadable TCP/IP / RS232 API document describes a service on port 50006, but it is **ASCII, not the binary protocol** this library speaks: commands such as `!1pow.?` and `!1vol.25`, terminated by `<CR><LF>`. Primare layers its own integrator-facing API over the LibreSync stack. The document does not prove that 50006 carries a shared wire format across brands; it remains useful as a list of which properties the platform models. See [commands.md](commands.md) for the comparison.

### Not compatible

| Device | Why |
| --- | --- |
| WiSA SoundSend | built by Hansong, but has no Wi-Fi control path: its app connects over BLE/GATT. The command semantics are probably the same; the transport is not |
| Bang & Olufsen Beolab | its own Mozart / BeoNet control plane. Often paired with a Platin hub as the WiSA transmitter, in which case the hub is the controllable part |
| Dynaudio Focus (current) | speaker-centric, no hub role. The legacy Xeo 2 was Hansong-built (`XCO-XEO2`) and may be worth probing |
| Generic WiSA speakers | receivers only, with no network control surface |

### The new-generation hub (PTX3)

In 2024 Platin released a successor, the *WiSA Stereo Hub HT (New Generation)*, model PTX3. Platin describes it as having a "3rd generation" streaming chipset, but no public source names that chipset: neither the user manual nor the CE declaration does, and no FCC filing under `2ADBM` or `XCO` has been found. **Whether the PTX3 runs LibreSync is unknown.** An owner can check cheaply: a UPnP description with `manufacturer` `LibreWireless` or `modelDescription` `LibreSyncDMR`, and TCP 50006 and 7777 open, would put it on the platform.

## Why discovery does not assume Platin

Because the protocol is the platform's, discovery looks for the platform. An SSDP search for a `LibreWireless` or `LibreSync` MediaRenderer, confirmed by a TCP connect to 50006, should find the whole Tier 1 set and probably much of Tier 2, whatever the badge on the box. See the [README](../README.md) for how to use it.
