# P180 ESPHome Component (custom, monitoring-focused)

Implements the BrightEMS Modbus-over-BLE protocol documented in
`Ylianst/ESP-FBot`'s `internals/README.md` (reverse-engineered from an
AFERIY P310 teardown). The P180 shares the exact same BLE UUIDs and
Modbus/CRC framing as the P310, **but uses a different, larger register
table (100 registers vs. 80)**.

The component is **read-only**. It issues Modbus function `0x04` (live status)
and `0x03` (settings) reads and never writes — see [Why there are no writes](#why-there-are-no-writes).

## Confirmed register map

These offsets were confirmed by capturing real data from a live P180 twice —
once running on grid power, once running on battery — and diffing which
registers changed:

| Register | AC-connected | On battery | Field |
|---|---|---|---|
| 8  | 1204 (120.4V) | 20 (2.0V)   | AC input voltage |
| 9  | 6000 (60.00Hz) | 0 (0.00Hz) | AC input frequency — **outage sensor** |
| 10 | 1182 (118.2V) | 1189 (118.9V) | AC output voltage (stable) |
| 11 | 600 (60.0Hz)  | 600 (60.0Hz)  | AC output frequency (stable) |
| 12 | 250 | 256 | Output power (W) |
| 13 | 0   | 256 | Battery discharge power (W) — *see caveat below* |
| 31 | 90  | 90  | Battery % (raw value = %, no scaling) |

That is 7 registers out of 100. The other 93 arrive on every poll and are
available to expose from YAML — see [Register discovery](#register-discovery).

### Remaining time is computed, not read

Register 75 looked plausible at first (144 matched a rough runtime estimate) but
stayed fixed across multiple real captures while the AFERIY app's own number
changed. The component instead computes it:
`(battery% / 100 × capacity_wh × efficiency) / discharge_power_w × 60` minutes.

The `efficiency` factor exists because a naive calc without it runs noticeably
optimistic versus the app (observed: 198 min calculated vs. 168 min shown
in-app, a ~15% gap). Both `battery_capacity_wh` and `battery_efficiency` are
live-adjustable via `number:` entities, no reflash needed to recalibrate.

**Two open questions worth re-testing**, because both would let you delete the
efficiency fudge factor entirely:

1. **Registers 12 and 13 read *identical* values.** The original guess was that
   reg 13 reports AC-side rather than DC-side power. A simpler explanation fits
   the upstream maps: they may be *AC output power* and *total output power*
   (two separate registers that read the same whenever AC is the only load).
   Experiment 6 in the matrix below distinguishes these.
2. **A real time-to-empty register may exist after all.** The upstream maps have
   genuine time-to-empty and time-to-full registers. Reg 75 sitting still rules
   out reg 75, not the value. Note also that ~19 registers were *invisible* to
   those early captures: the old debug dump was hardcoded to 168 bytes while a
   frame is 208, so registers ~81-99 never appeared in a log at all.

## Register discovery

The station volunteers all 100 status registers on every poll, and has a second
80-register settings table behind function `0x03`. Everything needed to map them
is in the component — flash once, then run experiments from the Home Assistant
UI and the ESPHome log without recompiling per register.

Start from [`example-discovery.yaml`](example-discovery.yaml).

### How it works

Set `log_changes: true` and the component logs only registers whose value moved
since the previous frame:

```
[D][p180]: input reg 41: 0x0E00 -> 0x0A00 (3584 -> 2560)
```

Press the **Reset Baseline** button, change exactly *one* thing on the station,
and read off which register moved. One change at a time is what makes the
evidence trustworthy — this is the same snapshot-and-diff method that produced
the P280 map.

Use `ignore_registers:` to silence registers that move on their own (power,
SoC) so a deliberate change stands out, and `change_threshold: 1` to filter
±1 jitter on analog readings.

### Keeping the log readable

Every ESPHome entity logs its state on **every publish** — once per poll, per
entity, whether or not the value changed. At a 5s interval that is a wall of

```
[S][sensor]: 'AC Input Voltage' >> 0.0 V
[S][sensor]: 'Battery' >> 64 %
```

scrolling past continuously, which buries the p180 lines you are trying to
read.

Those state lines are `VERBOSE`, and every bit of discovery output — register
dumps, change lines — is `DEBUG`. So the fix is simply **don't run the logger
at `VERBOSE`**:

```yaml
logger:
  level: DEBUG
  logs:
    p180: DEBUG
```

At `DEBUG` the entity state logging compiles out entirely and the log contains
only p180 output. Nothing you need for discovery is lost.

If you do need the p180 frame-level diagnostics (reassembly, stray
notifications, CRC detail), raising the global level to `VERBOSE` un-mutes
*every* component — `ble_client` and `esp32_ble_tracker` are far noisier than
the sensors were. A per-tag level can only ever be **less** verbose than the
global one, so mute the loud ones explicitly:

```yaml
logger:
  level: VERBOSE
  logs:
    p180: VERBOSE
    sensor: INFO          # per-publish entity state - the main offender
    binary_sensor: INFO
    button: INFO
    number: INFO
    ble_client: INFO
    esp32_ble_tracker: INFO
    api: INFO
    wifi: INFO
```

**Reset Baseline** prints a banner and dumps the table the next diff will be
measured against, so there is always a visible "before" to compare against:

```
[I][p180]: ===== Baseline cleared - dumping the new reference, then change ONE thing =====
[D][p180]: input regs 000-015: ...
```

### Probe buttons

All four are reads; none of them writes to the station.

| Action | What it does |
|---|---|
| `reset_baseline` | Clear the diff baseline before an experiment |
| `dump_input` | Request and dump the 100-register status table |
| `dump_holding` | Request and dump the 80-register settings table |
| `probe_extended` | Ask for 160 status registers instead of 100, to find out whether more exist |

Dumps are logged chunked, 16 registers per line, register-indexed so values line
up with the map:

```
[D][p180]: input regs 000-015: 0000 0000 0000 0000 0000 0000 0000 0000 04B4 1770 049E 0258 00FA 0000 0000 0000
[D][p180]: input regs 016-031: 0000 ... 
```

### Exposing a register

The moment a diff line points at something interesting, add it to
`raw_registers:` — a YAML edit, no C++ change:

```yaml
sensor:
  - platform: p180
    p180_id: p180_main
    raw_registers:
      - register: 41
        name: "Reg 41"
      - register: 56
        name: "Reg 56"
        scale: 0.1
      - register: 66
        source: holding        # `input` (0x04, default) or `holding` (0x03)
        name: "Holding 66"
```

For status bits, `raw_bits:` binds a binary sensor to a single bit:

```yaml
binary_sensor:
  - platform: p180
    p180_id: p180_main
    raw_bits:
      - register: 41
        bitmask: 0x0800
        name: "AC Output Active"
```

Once a register is confirmed, promote it to a first-class named sensor by
adding one line to `REGISTER_SENSORS` in `components/p180/sensor.py`. The C++
needs no change — named sensors and `raw_registers:` share the same code path.

### Offline diffing

`tools/regdiff.py` diffs two saved log captures, which is useful for slow or
un-repeatable events (an outage, a full charge cycle) where watching live isn't
practical:

```console
$ esphome logs example-discovery.yaml > before.log      # press Dump, then stop
$ # ... change one thing ...
$ esphome logs example-discovery.yaml > after.log
$ tools/regdiff.py before.log after.log
== before.log -> after.log
-- input: 3 of 100 shared registers changed
   reg  12: 0x00FA -> 0x0005  (  250 ->     5, -245)  <- output power (W) [confirmed on P180]
   reg  31: 0x005A -> 0x0059  (   90 ->    89, -1)  <- battery percent [confirmed on P180]
   reg  41: 0x0A00 -> 0x0200  ( 2560 ->   512, -2048)  <- status bitmask? 0x200=USB 0x400=DC 0x800=AC 0x1000=light
```

Run it with a single file to decode one capture instead of diffing.

### Experiment matrix

Run with `log_changes: true` and `ignore_registers: [12, 13, 31]`, pressing
**Reset Baseline** before each row.

| # | Action | Expect to reveal |
|---|---|---|
| 1 | Idle baseline, AC in, all outputs off | reference snapshot |
| 2 | AC output on → off | AC bit in the status bitmask |
| 3 | DC/car output on → off | DC bit |
| 4 | USB output on → off | USB bit |
| 5 | Light: off → on → SOS → flash | light bits + a 0-3 enum register |
| 6 | Known AC load (~100 W), then add a DC load | **splits reg 12 vs 13** — which is AC-output vs total-output power |
| 7 | Single USB-C load only | per-port USB watt registers (tenths) |
| 8 | Unplug AC (outage) | input regs → 0, inverter/charging bits flip |
| 9 | Cycle AC charge rate through its steps | charge-rate step + charging power |
| 10 | Solar/DC input, if available | DC input power/voltage, input-type flag |
| 11 | Let SoC move ≥1% while discharging | SoC scaling, time-to-empty / time-to-full |
| 12 | Long idle / sustained high load | temperature registers |
| 13 | Press **Probe Extended Registers** | whether >100 registers exist |
| 14 | Press **Dump Settings Registers**, change a setting in the app, dump again | settings table semantics |

### Upstream maps — hypotheses, not answers

These come from [`olofd/kraftverk`](https://github.com/olofd/kraftverk) (AFERIY
P280) and [`Ylianst/ESP-FBot`](https://github.com/Ylianst/ESP-FBot) (AFERIY
P310). **The P180's table is 100 registers and demonstrably reordered** — our
regs 8/9 correspond to their 21/22, but our regs 10/11 do not fit that same
shift. Treat the table below as a source of candidate *field names and
scalings* to match observed changes against, never as a map to read directly.

Status (`0x04`): 3 charging power · 4 DC/solar input W · 6 total input W ·
18 AC out V (÷10) · 19 AC out Hz · 20 AC out W · 21 AC in V (÷10) ·
22 AC in Hz (÷100) · 30-37 per-port USB W (÷10) · 39 total output W ·
41 status bitmask · 47-50 firmware versions · 48 AC charging state ·
56 SoC (÷10) · 58 time to full (min) · 59 time to empty (min).

Status bitmask (reg 41): `0x0200` USB · `0x0400` DC · `0x0800` AC ·
`0x1000` light · `0x0080` DC converter · `0x0010` charging from AC ·
`0x0004` inverter active.

Settings (`0x03`): 13 AC charge rate step · 15 DC input type · 24/25/26
USB/DC/AC output toggles · 27 light mode · 56 key sound · 57 AC silent
charging · 59-61 standby timers · 62 screen rest (seconds) ·
66 discharge floor (÷10 %) · 67 AC charge ceiling (÷10 %) · 68 sleep minutes.

## Why there are no writes

Function `0x06` (write) is deliberately not implemented. The upstream P280 work
documents two hazards on the settings table:

- **Holding register 68 permanently bricks the station when written `0`.** The
  vendor app omits the value entirely, which independently corroborates it.
- **Registers 25/26 toggle on *any* write**, regardless of the value sent.

Adding control is not hard — it is the same framing with a different function
code — but it should be a deliberate, opt-in change with a hard (register,
value) whitelist, not something that arrives by accident.

## Installation

1. Reference this as an external component (see example below)
2. Get your battery's BLE MAC address (search for a device starting with
   "FOSSIBOT" or "POWER" using a BLE scanner app or nRF Connect)

## Example configuration

```yaml
esphome:
  # The ESP32 itself - this is the hostname and OTA identity, not the battery.
  name: esp32-ble-gateway
  friendly_name: ESP32 BLE Gateway
  min_version: 2025.7.0
  # The battery is a sub-device of the ESP32, so its entities group under
  # "AFERIY P180" in Home Assistant instead of taking over the whole node.
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
  # NOTE: a per-tag level may not be more verbose than the global `level:`.
  # To debug the P180, raise both (see Troubleshooting).
  level: INFO

external_components:
  - source:
      type: git
      url: https://github.com/enelson493/esphome-p180
      ref: main
    components: [p180]
    refresh: 0s   # while iterating - forces a fresh pull every compile

ble_client:
  - mac_address: "AA:BB:CC:DD:EE:FF"   # your P180's BLE MAC
    id: p180_ble

p180:
  id: p180_main
  ble_client_id: p180_ble
  polling_interval: 5s
  battery_capacity_wh: 1024      # compiled-in starting value
  battery_efficiency: 0.85       # compiled-in starting value - tune against the app

# Adjustable at runtime from Home Assistant (e.g. after adding an expansion
# battery, or after calibrating against the AFERIY app's own estimate) without
# reflashing - persists across reboots via restore_value.
number:
  - platform: template
    name: "Battery Capacity"
    id: battery_capacity_wh
    device_id: p180_device
    icon: mdi:battery-high
    unit_of_measurement: "Wh"
    min_value: 1024
    max_value: 5120
    step: 1024
    initial_value: 1024
    optimistic: true
    restore_value: true
    on_value:
      then:
        - lambda: |-
            id(p180_main).set_battery_capacity_wh(x);

  - platform: template
    name: "Battery Runtime Efficiency"
    id: battery_efficiency
    device_id: p180_device
    icon: mdi:lightning-bolt
    unit_of_measurement: "%"
    min_value: 50
    max_value: 100
    step: 1
    initial_value: 85
    optimistic: true
    restore_value: true
    on_value:
      then:
        - lambda: |-
            id(p180_main).set_battery_efficiency(x / 100.0f);

sensor:
  - platform: p180
    p180_id: p180_main
    ac_in_voltage:
      name: "AC Input Voltage"
      device_id: p180_device
    ac_in_frequency:
      name: "AC Input Frequency"
      device_id: p180_device
    battery_percent:
      name: "Battery"
      device_id: p180_device
    output_power:
      name: "Output Power"
      device_id: p180_device
    battery_discharge_power:
      name: "Battery Discharge Power"
      device_id: p180_device
    remaining_time:
      name: "Remaining Minutes"
      device_id: p180_device

binary_sensor:
  - platform: p180
    p180_id: p180_main
    connected:
      name: "P180 Connected"
      device_id: p180_device
    grid_power:
      name: "Grid Power"        # <-- this is your outage sensor
      device_id: p180_device
```

In Home Assistant, `binary_sensor.grid_power` going `off` means the P180
detected loss of AC input (a real outage) and switched to battery — that's
the automation trigger you want for alerts/notifications.

### The battery is a sub-device, not the node

The ESP32 and the battery are two different things, so the config keeps them
separate: the `esphome:` block names the ESP32 (that's its hostname and OTA
identity), and the battery is declared as a sub-device that the entities attach
to with `device_id:`.

```yaml
esphome:
  name: esp32-ble-gateway
  devices:
    - id: p180_device
      name: "AFERIY P180"
```

In Home Assistant you get an "ESP32 BLE Gateway" device with an "AFERIY P180"
device nested under it, and all the battery entities live on the battery. The
ESP32 stays free for anything else you put on it — another BLE client, a sensor,
a second power station — each as its own sub-device, without one of them
claiming the node's identity.

Sub-devices need **ESPHome 2025.7.0 or newer**, which is what the `min_version:`
line pins. On older versions `devices:` is not a valid key and the config is
rejected; drop both the `devices:` block and every `device_id:` line to go back
to a flat node.

Each sub-device can also take an `area_id:` if you use ESPHome areas.

**Migrating an existing install:** adding `device_id:` re-parents entities onto
the new sub-device in Home Assistant. Entity IDs are preserved, but the device
your automations and dashboards reference by *device* (device triggers, device
conditions, area assignment) changes, so check anything wired up that way. If
you also change `name:`, the node gets a new hostname — update your DNS/OTA
target and expect Home Assistant to discover it as a new device.

## Configuration reference

### `p180:` component

| Option | Default | Meaning |
|---|---|---|
| `polling_interval` | `5s` | How often to read the status table (`0x04`) |
| `settings_interval` | `60s` | How often to read the settings table (`0x03`). `0s` disables |
| `battery_capacity_wh` | `1024` | Used to compute `remaining_time` |
| `battery_efficiency` | `0.85` | Lumped derate factor for `remaining_time` |
| `log_changes` | `false` | Log registers that changed since the previous frame |
| `debug_dump` | `false` | Dump the whole table every poll (very noisy) |
| `ignore_registers` | `[]` | Registers to exclude from change logging |
| `change_threshold` | `0` | Suppress changes of this magnitude or less |

### `sensor:` platform

Named: `ac_in_voltage`, `ac_in_frequency`, `ac_out_voltage`, `ac_out_frequency`,
`output_power`, `battery_discharge_power`, `battery_percent`, `remaining_time`.

Generic: `raw_registers:` — a list of `register:` (0-159), optional
`source:` (`input`/`holding`, default `input`) and `scale:` (default `1.0`),
plus the usual sensor options including `filters:` and `device_id:`.

### `binary_sensor:` platform

Named: `connected`, `grid_power`.

Generic: `raw_bits:` — a list of `register:`, `bitmask:` and optional `source:`.

Every entity on every platform above also accepts the standard ESPHome
`device_id:`, used here to attach it to the battery sub-device.

### `button:` platform

`action:` is one of `reset_baseline`, `dump_input`, `dump_holding`,
`probe_extended`.

## Troubleshooting

**Nothing connects / no sensors populate:**
Raise the log level and watch the ESPHome logs:

```yaml
logger:
  level: DEBUG
  logs:
    p180: DEBUG
```

A per-tag level may not be *more* verbose than the global `level:` — ESPHome
rejects the config outright if it is. Set `debug_dump: true` and you should see
a full set of `input regs 000-015:` … `input regs 096-099:` lines every polling
interval. If you see nothing at all, the service/characteristic UUIDs
(`a002`/`c304`/`c305`) likely don't match your P180 — confirm with nRF Connect
what it actually exposes and we'll update `p180.h`.

**It connects and logs raw bytes, but values look wrong (e.g. battery %
reads 500% or power spikes to absurd numbers):**
The register *offsets* may differ on your P180's firmware. Use the discovery
workflow above to re-derive them — that is exactly what it is for.

**CRC mismatch warnings in the log:**
Frames are arriving but getting corrupted. The component logs the negotiated
MTU at connect; a full status frame is 208 bytes, and below that the frame
arrives split across notifications and is reassembled. Persistent CRC failures
after a clean reassembly usually mean genuine interference or range problems.

**`Frame declares N registers, outside 1..160`:**
Something other than a status/settings reply is arriving on the notify
characteristic. Harmless — it's discarded — but worth a look at `VERBOSE` level.

## What's implemented vs. not

- ✅ Battery %, AC input/output voltage & frequency, output power,
  battery discharge power, remaining time, connection state
- ✅ Derived grid-power (outage) binary sensor — confirmed by direct test
- ✅ Any register or status bit exposed from YAML, no C++ change
- ✅ Settings table (`0x03`) read alongside the status table
- ✅ Changed-register diff logging, chunked full dumps, extended-range probe
- ✅ Offline diff tooling (`tools/regdiff.py`)
- ⬜ Named sensors for USB/DC/AC/light status, per-port USB watts, temperatures,
  charge state — reachable today via `raw_registers:` / `raw_bits:`; run the
  experiment matrix to confirm offsets, then promote them
- ❌ Output control and settings writes — deliberately omitted, see
  [Why there are no writes](#why-there-are-no-writes)
