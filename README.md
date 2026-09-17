# P180 ESPHome Component

Monitor an **AFERIY P180 / P180 Pro** power station over Bluetooth from
ESPHome and Home Assistant — battery level, input and output power, runtime
estimates, which outputs are on, and the station's own settings.

The component is **read-only**. It issues Modbus function `0x04` (live status)
and `0x03` (settings) reads and never writes — see
[Why there are no writes](#why-there-are-no-writes).

> **Register map and reverse-engineering notes** live in
> **[REGISTERS.md](REGISTERS.md)**: what each register means, how much evidence
> is behind it, and the workflow for mapping the ones that are still unknown.
> You don't need any of it to use the component.

## What it does

An ESP32 connects to the P180 over BLE and polls it. Every entity below is a
plain ESPHome entity, so it shows up in Home Assistant with no extra glue:

- **Battery** — level, discharge power, remaining runtime, time to full
- **AC** — input/output voltage and frequency, output power, charging power
- **Outputs** — AC, DC, USB and light on/off, light mode, USB watts
- **Grid** — an outage binary sensor that flips when mains is lost
- **Settings** — standby timers, screen timeout, charge/discharge limits,
  firmware versions, read from the station's settings table
- **Anything else** — any register or status bit can be exposed straight from
  YAML, with no C++ change

## Why

The AFERIY app is the only supported way to see any of this, and it is
phone-only, cloud-attached and has no automation hooks. This puts the same data
on your own network, in Home Assistant, where you can graph it, alert on it and
automate against it — in particular `grid_power`, which is a genuine outage
trigger.

## How it works

The P180 speaks **Modbus RTU wrapped in BLE GATT notifications**: the component
writes a request to characteristic `c304` on service `a002` and the station
answers on `c305`. Framing is standard Modbus with a CRC-16, with one quirk —
replies echo the request's start address and count, so a frame is
`addr | func | start | count | data | crc` rather than carrying the usual
byte-count header.

Two register tables are polled: the 100-register **status** table (`0x04`) every
`polling_interval`, and the 80-register **settings** table (`0x03`) every
`settings_interval`. The station also **pushes a status frame when something
changes**, so toggling an output shows up in about 100 ms rather than at the
next poll.

Everything else — the register offsets, and what evidence backs each one — is in
[REGISTERS.md](REGISTERS.md).

## Requirements

- An ESP32 within Bluetooth range of the station
- **ESPHome 2025.7.0 or newer**, for the `devices:` sub-device support used
  below — see [Sub-device vs. flat node](#sub-device-vs-flat-node) to run on
  older versions
- The station's BLE MAC address — scan with nRF Connect or any BLE scanner app
  and look for a device named `FOSSIBOT` or `POWER`

## Example configuration

A complete, working config exposing everything the component currently names.
Drop whatever you don't want; every entity is optional.

```yaml
esphome:
  # This names the ESP32 itself: its hostname and OTA identity, not the battery.
  name: esp32-ble-gateway
  friendly_name: ESP32 BLE Gateway
  min_version: 2025.7.0
  # The battery is a sub-device, so its entities group under "AFERIY P180" in
  # Home Assistant instead of taking over the whole node.
  devices:
    - id: p180_device
      name: "AFERIY P180"

esp32:
  board: esp32dev
  framework:
    type: esp-idf

api:
  encryption:
    key: "your_api_encryption_key_here"

wifi:
  ssid: !secret wifi_ssid
  password: !secret wifi_password

logger:
  level: INFO

external_components:
  - source:
      type: git
      url: https://github.com/ulope/esphome-p180
      ref: main
    components: [p180]

ble_client:
  - mac_address: "AA:BB:CC:DD:EE:FF"   # your P180's BLE MAC
    id: p180_ble

p180:
  id: p180_main
  ble_client_id: p180_ble
  polling_interval: 5s
  settings_interval: 60s

sensor:
  - platform: p180
    p180_id: p180_main

    # --- battery ---
    battery_percent:
      name: "Battery"
      device_id: p180_device
    # The station's own runtime estimate. Reads 0 while charging, when time to
    # empty is meaningless - use time_to_full then.
    remaining_time:
      name: "Remaining Time"
      device_id: p180_device
    time_to_full:
      name: "Time To Full"
      device_id: p180_device
    # Read REGISTERS.md before using this for energy accounting: it over-reads
    # while AC output is on, by an amount that is steady within a session but
    # varies between sessions.
    battery_discharge_power:
      name: "Battery Discharge Power"
      device_id: p180_device

    # --- AC ---
    ac_in_voltage:
      name: "AC Input Voltage"
      device_id: p180_device
    ac_in_frequency:
      name: "AC Input Frequency"
      device_id: p180_device
    ac_out_voltage:
      name: "AC Output Voltage"
      device_id: p180_device
    ac_out_frequency:
      name: "AC Output Frequency"
      device_id: p180_device
    output_power:
      name: "Output Power"
      device_id: p180_device
    # AC -> battery only. Total wall draw is this plus output_power; no single
    # register reports it.
    charging_power:
      name: "Charging Power"
      device_id: p180_device
    # Signed: + is AC output on battery, - is charging when grid-connected.
    ac_power:
      name: "AC Power"
      device_id: p180_device
    # Tracks the rear 1000 W / 500 W input switch.
    charge_rate_step:
      name: "Charge Rate Step"
      device_id: p180_device

    # --- outputs ---
    usb_output_power:
      name: "USB Output Power"
      device_id: p180_device
    light_mode:
      name: "Light Mode"
      device_id: p180_device

    # --- settings, read from the 0x03 table ---
    discharge_limit:
      name: "Discharge Limit"
      device_id: p180_device
    ac_charge_limit:
      name: "AC Charge Limit"
      device_id: p180_device
    silent_charge_current:
      name: "Silent Charge Current"
      device_id: p180_device
    screen_timeout:
      name: "Screen Timeout"
      device_id: p180_device
    ac_standby_time:
      name: "AC Standby Time"
      device_id: p180_device
    dc_standby_time:
      name: "DC Standby Time"
      device_id: p180_device
    usb_standby_time:
      name: "USB Standby Time"
      device_id: p180_device
    device_shutdown_time:
      name: "Device Shutdown Time"
      device_id: p180_device

    # --- firmware versions, constants ---
    ac_firmware_version:
      name: "AC Firmware"
      entity_category: diagnostic
      device_id: p180_device
    bms_firmware_version:
      name: "BMS Firmware"
      entity_category: diagnostic
      device_id: p180_device
    pv_firmware_version:
      name: "PV Firmware"
      entity_category: diagnostic
      device_id: p180_device
    panel_firmware_version:
      name: "Panel Firmware"
      entity_category: diagnostic
      device_id: p180_device

binary_sensor:
  - platform: p180
    p180_id: p180_main

    connected:
      name: "P180 Connected"
      device_id: p180_device
    # Your outage sensor: off means mains was lost and the station switched to
    # battery.
    grid_power:
      name: "Grid Power"
      device_id: p180_device

    # --- outputs ---
    ac_output:
      name: "AC Output"
      device_id: p180_device
    dc_output:
      name: "DC Output"
      device_id: p180_device
    usb_output:
      name: "USB Output"
      device_id: p180_device
    light:
      name: "Light"
      device_id: p180_device

    # --- modes ---
    silent_charging:
      name: "Silent AC Charging"
      device_id: p180_device
    # On = DC, off = PV.
    dc_input_type:
      name: "DC Input Type is DC"
      device_id: p180_device
```

In Home Assistant, `binary_sensor.grid_power` going `off` means the P180 lost
AC input and switched to battery — that is the automation trigger you want for
outage alerts.

### Sub-device vs. flat node

The ESP32 and the battery are two different things, so the config keeps them
apart: the `esphome:` block names the ESP32, and the battery is a sub-device
that entities attach to with `device_id:`.

You get an "ESP32 BLE Gateway" device in Home Assistant with an "AFERIY P180"
nested under it, and the ESP32 stays free for anything else you put on it —
another BLE client, a second power station — each as its own sub-device, without
one of them claiming the node's identity. Each sub-device also accepts
`area_id:`.

Sub-devices need **ESPHome 2025.7.0+**, which is what `min_version:` pins. On
older versions `devices:` is not a valid key and the config is rejected — drop
the `devices:` block and every `device_id:` line for a flat node.

**Migrating an existing install:** adding `device_id:` re-parents entities onto
the sub-device. Entity IDs are preserved, but anything referencing the *device*
(device triggers, device conditions, area assignment) changes, so check those.
Changing `name:` also changes the hostname — update your DNS/OTA target.

## Configuration reference

### `p180:` component

| Option | Default | Meaning |
|---|---|---|
| `ble_client_id` | *required* | The `ble_client:` to talk over |
| `polling_interval` | `5s` | How often to read the status table (`0x04`) |
| `settings_interval` | `60s` | How often to read the settings table (`0x03`). `0s` disables |
| `log_changes` | `false` | Log registers that changed since the previous frame |
| `debug_dump` | `false` | Dump the whole table every poll (very noisy) |
| `ignore_registers` | `[]` | Status-table registers to exclude from change logging. Does not affect the settings table |
| `change_threshold` | `0` | Suppress changes of this magnitude or less |

The last four are for [register discovery](REGISTERS.md) and can be ignored for
normal use.

### `sensor:` platform

From the status table (`0x04`):

| Key | Unit | Key | Unit |
|---|---|---|---|
| `battery_percent` | % | `output_power` | W |
| `remaining_time` | min | `charging_power` | W |
| `time_to_full` | min | `ac_power` | W, signed |
| `battery_discharge_power` | W | `usb_output_power` | W |
| `ac_in_voltage` | V | `ac_out_voltage` | V |
| `ac_in_frequency` | Hz | `ac_out_frequency` | Hz |
| `charge_rate_step` | — | `light_mode` | — |

From the settings table (`0x03`):

| Key | Unit | Key | Unit |
|---|---|---|---|
| `discharge_limit` | % | `screen_timeout` | s |
| `ac_charge_limit` | % | `ac_standby_time` | min |
| `silent_charge_current` | A | `dc_standby_time` | min |
| `ac_firmware_version` | — | `usb_standby_time` | min |
| `bms_firmware_version` | — | `device_shutdown_time` | min |
| `pv_firmware_version` | — | `panel_firmware_version` | — |

Generic — expose any register without touching C++:

```yaml
raw_registers:
  - register: 56          # 0-159
    name: "Reg 56"
    source: holding       # `input` (0x04, default) or `holding` (0x03)
    scale: 0.1            # default 1.0
    signed: true          # default false; two's-complement
```

### `binary_sensor:` platform

| Key | Meaning |
|---|---|
| `connected` | BLE link state |
| `grid_power` | Mains present — **the outage sensor** |
| `ac_output` / `dc_output` / `usb_output` / `light` | Output on/off |
| `silent_charging` | Silent AC charging mode |
| `dc_input_type` | On = DC, off = PV |

Generic:

```yaml
raw_bits:
  - register: 75
    bitmask: 0x0040
    name: "Some Bit"
    source: input         # default
```

### `button:` platform

Diagnostic, all reads — none of them writes to the station.

| `action:` | What it does |
|---|---|
| `reset_baseline` | Clear the change-logging baseline and dump the new reference |
| `dump_input` | Dump the 100-register status table |
| `dump_holding` | Dump the 80-register settings table |
| `probe_extended` | Ask for 160 status registers instead of 100 |

Every entity on every platform also accepts the standard ESPHome `device_id:`,
used above to attach it to the battery sub-device.

## Why there are no writes

Function `0x06` (write) is deliberately not implemented. The upstream P280
reverse-engineering work documents two hazards on the settings table:

- **Holding register 68 permanently bricks the station when written `0`.** The
  vendor app omits the value entirely, which independently corroborates it.
- **Registers 25/26 toggle on *any* write**, regardless of the value sent.

Adding control is not hard — same framing, different function code — but it
should be a deliberate, opt-in change with a hard (register, value) whitelist,
not something that arrives by accident.

## Troubleshooting

**Nothing connects / no sensors populate.** Raise the log level:

```yaml
logger:
  level: DEBUG
  logs:
    p180: DEBUG
```

Set `debug_dump: true` and you should see a full set of `input regs 000-015:` …
`input regs 096-099:` lines every polling interval. Nothing at all usually means
the service/characteristic UUIDs (`a002`/`c304`/`c305`) don't match your unit —
check with nRF Connect what it actually exposes.

A per-tag level may not be *more* verbose than the global `level:`; ESPHome
rejects the config outright if it is. Logger settings are compiled in, so
changing them needs a rebuild and upload, not a restart.

**Values look wrong** — battery reads 500 %, power spikes to absurd numbers. The
register offsets may differ on your firmware. Use the discovery workflow in
[REGISTERS.md](REGISTERS.md) to re-derive them; that is exactly what it is for.

**The log is a wall of `[S][sensor]` lines.** Those are *not* device logs and no
`logger:` setting will suppress them — `aioesphomeapi` renders them host-side
from the API state stream. Tell them apart by the missing source line:

```
[D][p180:330]: input reg 72: 0x1196 -> 0x12C2   <- device, filterable
[S][sensor]: 'Battery' >> 64 %                  <- host-side
```

Use `esphome logs your-device.yaml --no-states`, or set
`ESPHOME_LOG_STATES=false`. The ESPHome dashboard and the Home Assistant
**ESPHome Device Builder** add-on pass no flag and default to showing them, so
run the CLI for a clean log. Entities *also* log on-device at `VERBOSE`; those
the logger does control, via `sensor: WARN` and friends.

**CRC mismatch warnings.** Frames are arriving but getting corrupted. A full
status frame is 208 bytes; below the negotiated MTU it arrives split across
notifications and is reassembled. Persistent failures after a clean reassembly
usually mean interference or range problems.

**`Frame declares N registers, outside 1..160`.** Something other than a
status/settings reply is arriving on the notify characteristic. Harmless — it is
discarded — but worth a look at `VERBOSE`.

## Status

- ✅ Battery, AC input/output, output power, runtime estimates, charging
- ✅ Outage detection (`grid_power`) — confirmed by direct test
- ✅ Output and mode flags: AC/DC/USB/light, silent charging, DC input type
- ✅ Settings table read, with 12 of its 15 non-zero registers named
- ✅ Any register or status bit exposed from YAML, no C++ change
- ✅ Discovery tooling: change logging, chunked dumps, probe buttons, and
  offline diffing (`tools/regdiff.py`)
- ⬜ Per-port USB watts, temperatures, fan state — no register found for these
  yet; see [REGISTERS.md](REGISTERS.md)
- ❌ Output control and settings writes — deliberately omitted, see
  [Why there are no writes](#why-there-are-no-writes)

## Credits

Protocol framing and the initial register hypotheses come from
[`Ylianst/ESP-FBot`](https://github.com/Ylianst/ESP-FBot) (AFERIY P310) and
[`olofd/kraftverk`](https://github.com/olofd/kraftverk) (AFERIY P280). The P180
shares their BLE UUIDs and Modbus framing but has its own, larger register
table — see [REGISTERS.md](REGISTERS.md) for how far the upstream maps do and do
not carry over.
