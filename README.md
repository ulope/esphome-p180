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

Confirmed since on a P180 Pro (50Hz grid), by toggling each output on its own
and diffing:

**Register 75 is the output status bitmask** — *not* register 41. Reg 41 stayed
at `0x0000` through every single toggle, so the Sydpower/P280 layout does not
apply to this device.

| Mask | Output | How it was confirmed |
|---|---|---|
| `0x0002` | Light | 2 on/off cycles |
| `0x0004` | DC output | 2 on/off cycles |
| `0x0008` | USB output | 5 on/off cycles |
| `0x0010` | AC output | 3 on/off cycles |
| `0x0001` | *unidentified* | never seen set |

All four are named binary sensors: `light`, `dc_output`, `usb_output`,
`ac_output`.

Everything below is graded by how far the evidence actually goes. **Measured**
means observed directly and cross-checked; **inferred** means it fits the data
but rests on a model or an assumption; **guess** means it is a label, not a
finding. Only trust a row as far as its grade.

| Register | Observed | Field | Evidence |
|---|---|---|---|
| 72 | ~5400 all-off, ~1550 AC idle, 15 at 2.2kW | **Remaining runtime, minutes** — read by `remaining_time` | **measured** — matches the figure on the station's own display |
| 78 | `11`→`15`→`33`→`37` under a USB-C PD load | **USB output power (W)** — `usb_output_power` | **measured** |
| 90 | `23`→`36`→`39` under an AC load | **AC output power (W)** — mirrors reg 12 | **measured** |
| 10 | `2316` with AC output on | **AC output voltage** ×0.1 = 231.6 V on a 230 V grid; sags to 208.9 V at ~1.8 kW | **measured** |
| 79 | `0`, then one value per mode | **Light mode** enum — `light_mode` | **measured** |
| 66 / 67 | increment together under AC load | **AC output energy since power-on, 10 Wh per count.** Resets to 0 on restart — not a lifetime meter | **measured** — reset seen directly in a post-reboot capture (8 → 0); *inferred* that it is AC rather than total output |
| 11 | `500` | AC output frequency, 50.0 Hz nominal even with output off | **inferred** — never seen change |
| 13 | 137 W at AC idle, `0` with all outputs off | **Output-attributed power (W)** — carries a ~137 W artifact whenever AC output is energised, and never includes station self-consumption | **measured** — exact under load and for USB steps; reads exactly 0 with outputs off; the idle figure is refuted by a 13-minute SoC hold |
| 53 | `0x0010` while AC output on | AC-side, unidentified | **guess** — only that it tracks AC output state |
| 54 | `0x0800` light, `0x0837` USB, `0x0980` DC | DC-side, unidentified | **guess** — only that it tracks DC-side output state |
| 1 | `5` | charge-rate step? | **guess** — constant in every capture |
| 97–99 | `0x1901 0x0203 0x0405` | version/serial info? | **guess** — constant in every capture |

Nothing here has been tested across a firmware update, a charge cycle, or a
second unit. "Constant in every capture" means constant across a few hours on
one P180 Pro.

**Set `change_threshold: 0` while mapping.** The light mode enum steps by one
(0→1→2→3), and a threshold of 1 suppresses every step — the mode register only
became visible on the 3→0 transition back to off.

**Capture under load, not just idle.** Registers 78, 90 and the meaning of 72
are all invisible on a resting unit. Toggling an output tells you which bit
flips; actually drawing power through it is what reveals the measurement
registers.

The status bitmask is additive: with USB and AC both on, reg 75 reads
`0x0018` = `0x0008 | 0x0010`.

### Registers above 99 are not telemetry

The **Probe Extended Registers** button asks for 160 registers, and the P180 Pro
**does answer** — the frame parses and the CRC passes. But the extra range is
not more telemetry. Decoding it:

| Register | Bytes | Meaning |
|---|---|---|
| 125–128 | `11 04 00 00 00 A0 E2 F2` | **our own Modbus probe request, echoed back verbatim** |
| 129–132 | `2C 31 2C 35 2C 2C 38 2C` | ASCII `,1,5,,8,` |
| 133–136 | `11 04 00 00 00 A0 E2 F2` | the same request again |
| 137 | `0D 00` | carriage return + NUL |
| everything else | `0000` | — |

That is a **communications buffer**, read out of bounds. The request bytes are
byte-for-byte what the component transmitted, so nothing above register 99 can
be trusted as a measurement. Binding an entity up there now produces a config
warning.

Note the probe's own log line is printed *before* the request goes out — it
describes what to look for, it is not a verdict. The dump that follows, and the
`input register count changed 100 -> 160` line, are the actual result.

### The 66/67 counter is 10 Wh per count

A controlled run — ~1092 W of AC load held for about two minutes — pins the
unit. With a 5 s poll interval you can only observe an interval as a multiple of
5 s, and every observation lands on one of the two values that allows:

| run | AC output | 10 Wh takes | 5 s-sampled | observed |
|---|---|---|---|---|
| ~1092 W | 33.0 s | 30 or 35 s | 30, 35, 30 |
| ~1320 W | 27.3 s | 25 or 30 s | 30, 25 |
| ~1568 W | 23.0 s | 20 or 25 s | 25 |

It counts **AC output** energy, not battery energy: a battery-side basis
(÷0.85) predicts 28.0 s and 23.2 s for the first two rows, consistently faster
than observed.

**It resets to 0 when the station restarts**, so it measures energy since
power-on, not lifetime. Don't build a lifetime `total_increasing` energy sensor
on it without handling the reset — and note that a restart between captures also
means a jump *down* in the baseline is a power cycle, not a counter wrapping.

**Not found: any fan indicator.** With the fans audibly cycling on and off
about three minutes after a sustained 1.3kW load, the only registers that moved
in that window were 8, 10, 72 and 78 — all already identified. Fan state does
not appear anywhere in registers 0–99.

The table is sparse: idle on battery with every output off, only 9 of the 100
registers are non-zero. Most of the zeros are genuinely idle fields, not a
parsing problem.

The table is also very sparse: idle on battery with every output off, only 9 of
the 100 registers are non-zero. Most of the zeros are genuinely idle fields
(AC in/out, power), not a parsing problem.

That is a handful of registers out of 100. The rest arrive on every poll and are
available to expose from YAML — see [Register discovery](#register-discovery).

The device also **pushes a status frame of its own when state changes**, not
only when polled: a USB toggle produces a frame within ~100ms rather than
waiting up to `polling_interval`.

### Remaining time is read, not computed

`remaining_time` reads **register 72**, the station's own estimate, confirmed
against the AFERIY app. There is no capacity or efficiency calibration: the
earlier computed formula and its `battery_capacity_wh` / `battery_efficiency`
knobs have been removed.

**Both of the original open questions are now answered**, by capturing with a
real load attached rather than idle:

**Registers 12 and 13 are not duplicates.** With a USB-C PD load and nothing
else on, reg 13 tracked the draw (11 → 15 → 33 → 37 W) while reg 12 stayed at
`0` throughout. With an AC load, reg 12 read the AC output power and reg 13 sat
consistently 12–15 W above it:

| reg 90 / reg 12 (AC out W) | reg 13 (battery W) | gap |
|---|---|---|
| 23 | 35 | 12 |
| 36 | 51 | 15 |
| 39 | 54 | 15 |

That gap is the inverter plus standby overhead, so **reg 13 is the genuine
DC-side battery draw** — not an AC-side mirror as originally suspected. The
earlier "identical values" reading came from testing with an AC-only load,
where the two naturally track.

A ~1.8kW load makes the split unmistakable:

| reg 90 (AC out) | reg 78 (USB) | reg 13 (battery) | loss | efficiency |
|---|---|---|---|---|
| 1552 W | 16 W | 2016 W | 448 W | 77.8% |
| 1755 W | 15 W | 2224 W | 454 W | 79.6% |
| 1757 W | 15 W | 2222 W | 450 W | 79.7% |
| 1016 W | 13 W | 1357 W | 328 W | 75.8% |

Reg 13 is unambiguously the battery side. The 78–80% in that last column is
*overall* efficiency, not conversion efficiency — separating the two gives a
model that fits idle and full load alike:

```
reg 13  ≈  AC_output / 0.85  +  150 W standby
```

| AC out | predicted reg 13 | actual | error |
|---|---|---|---|
| 0 W | 150 | 148 | +2 |
| 1016 W | 1345 | 1357 | −12 |
| 1552 W | 1976 | 2016 | −40 |
| 1757 W | 2217 | 2222 | −5 |

So the inverter converts at about **85%** under load.

**The standby term is unresolved.** The fit implies a ~150 W draw whenever AC
output is enabled, and reg 13 does read ~148 W while AC idles. But that does not
survive observation: with AC output idling and only ~11 W of USB draw, the
station's remaining-time estimate *rose* from 16 h to 17 h over seven minutes. A
real 148 W draw would take roughly 1.7% of this pack in that time and push the
estimate down, not up.

So reg 13's idle reading is almost certainly **not** sustained battery draw.
Either it reports something else when the inverter is energised but unloaded
(apparent rather than real power is one candidate), or the measurement has an
offset that only appears with AC output on. Note reg 13 matches USB power
*exactly* with AC output off, so the register is sound — it is specifically the
AC-idle case that misbehaves.

**Reg 72 checked against itself** is the strongest evidence, because it needs no
assumption about pack size. At the same state of charge (48–50%), the station
reported:

| condition | reg 72 |
|---|---|
| idle, AC output on | ~1150 min |
| 1090 W AC load | ~21.5 min |

The remaining energy is the same in both cases, so the two draws must differ by
a factor of 1150 ÷ 21.5 = **53.5**. Working backwards from the loaded draw:

| loaded draw used | implied idle draw |
|---|---|
| 1405 W (reg 13 as-is) | 26.3 W |
| 1268 W (reg 13 minus the offset) | 23.7 W |

Reg 13 reports **137 W** at idle. The station's own estimate is self-consistent
only at roughly **24–26 W** — about a fifth of that.

Reg 13 is otherwise well-behaved: switching USB on added exactly its 8 W to the
reading, and switching it off removed exactly 8 W again. So it is a real power
measurement carrying a constant offset that appears only when AC output is
energised, not a broken register.

**Settled by observation.** SoC sat at 48% for **13 minutes** — with AC output
energised for 12 of them — and never crossed a single 1% boundary. At the
claimed 137 W the pack would have given up ~27 Wh, about 3%, so two or three
crossings.

Requiring *zero* crossings puts a hard ceiling on the real draw:

| Wh per 1% | implied ceiling |
|---|---|
| 8.8 | < 44 W |
| 9.8 | < 49 W |

So **reg 13's 137 W at AC idle is wrong by roughly a factor of three**.

**Reg 13 does not include station self-consumption at all.** Switching AC output
off drops it to *exactly* 0, while the station is still plainly running its BMS,
display and Bluetooth. So it reports output-attributed power only, and its
AC-idle figure is an accounting artifact rather than a measurement of anything
leaving the battery.

What the station itself believes, from reg 72 (assuming a ~940 Wh pack at 48%):

| state | reg 72 | implied draw |
|---|---|---|
| everything off | ~5400 min (90 h) | ~5 W |
| AC output on, idle | ~1550 min (26 h) | ~18 W |

The difference — about **12 W** — is the inverter's real idle cost. Not 137 W.
Those two rows are *inferred*: they need a pack-size assumption, unlike the
ceiling above.

It is not a broken register: under load it is consistent, and USB draw adds to
it exactly.

> **Practical consequence:** `battery_discharge_power` over-reads by roughly
> 137 W whenever AC output is on, whether or not anything is plugged into it.
> Don't feed it into an energy dashboard without accounting for that. Under
> load the error is proportionally small; at idle it is the entire reading.

### Pack capacity, roughly

Under a 1090 W load, SoC fell 50% → 49% → 48% in 25 s per step. At the loaded
draw that is **8.8–9.8 Wh per 1%**, so a pack of roughly **0.9–1.0 kWh**,
consistent with the 1024 Wh figure usually quoted for this model.

Treat that as an order-of-magnitude check, not a measurement: SoC has 1%
resolution, so each crossing carries up to a full percent of timing error.

**Register 72 is the device's own remaining-runtime estimate, in minutes** —
confirmed against the AFERIY app, which showed **16 h** while reg 72 read
950–1000 (15.8–16.7 h), at idle with ~11 W of USB draw.

The `remaining_time` sensor reads it directly — no calibration needed.

Under a step load it collapses within two polls, then holds rock steady:

| battery draw | reg 72 |
|---|---|
| 150 W | 880 |
| 2016 W | 27 |
| 2224 W | 16 |
| 2225 W | 15 |

Multiply it by the battery draw and the two load points agree almost exactly,
implying ~554 Wh remaining — which at 60% SoC is a ~920 Wh pack, close to the
component's 1024 Wh default:

| battery draw | reg 72 | product | = |
|---|---|---|---|
| 1660 W | 20 | 33 200 W·min | 553 Wh |
| 2222 W | 15 | 33 330 W·min | 556 Wh |
| 150 W (idle) | 950 | 142 500 W·min | **2375 Wh** ✗ |

**The idle point is 4.3× off.** For reg 72 to read ~950 at idle, the draw would
have to be ~39 W, but reg 13 reports 148 W. Since the app agrees with reg 72,
this is the *station's* accounting, not a misread: its runtime estimate does not
charge the ~150 W inverter standby against the battery. Treat `remaining_time`
as optimistic whenever AC output is idling — the pack will not actually last
16 h while burning 148 W.

With every output switched off it reads ~5400 (90 h), which is why the earliest
captures — all taken with the station idle — showed it wandering between 4502
and 7203 and looked like noise. At a ~5 W draw a runtime estimate really is that
large, and its jitter is amplified in proportion. It was never noise.

When the load is removed it climbs back over roughly 40 seconds
(`20 → 63 → 685 → 825 → 892 → 905`) and then **plateaus** at 860–1010,
oscillating there with no further trend across eight minutes of idle. So the
smoothing window is short, and the idle reading is a genuine steady-state value
rather than a lagging one — an earlier guess that a long averaging window
explained the idle figure does not survive this capture.

The oscillation at idle tracks small changes in USB draw inversely, which is
consistent with a live estimate; it is the *scale* at idle that is unexplained,
not the behaviour.

**Confirm it against the AFERIY app under a steady load.** If it matches, it
replaces the computed `remaining_time` and the `efficiency` knob outright.

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

If your log is a wall of entity states like this, once per poll, forever:

```
[23:50:16.063][S][sensor]: 'AC Input Voltage' >> 0.0 V
[23:50:16.063][S][sensor]: 'Battery' >> 64 %
```

**no `logger:` setting will suppress them.** Those lines never came from the
device. They are rendered host-side by `aioesphomeapi`'s state log formatter
from the API state stream — `[S]` is not an ESPHome log level, which is why
they carry no `tag:line` the way real device logs do (`[D][p180:330]`).

The switch is a flag on the `logs` command:

```console
$ esphome logs your-device.yaml --no-states
```

or the environment variable `ESPHOME_LOG_STATES=false`. The ESPHome dashboard
and the Home Assistant **ESPHome Device Builder** add-on pass no flag, so they
fall through to the default, which is *on*. Run `esphome logs ... --no-states`
from a terminal to get a clean log there.

Telling the two apart: a real device log line always has a source line number.

```
[D][p180:330]: input reg 72: 0x1196 -> 0x12C2 (4502 -> 4802)   <- device, filterable
[S][sensor]: 'Battery' >> 64 %                                 <- host-side, --no-states
```

#### Device-side log levels

Separately, entities *do* log their own state on the device at `VERBOSE`. That
is a different set of lines, and those the logger does control. The example
mutes them by name rather than relying on the global level, since which level
they land on has moved between ESPHome versions:

```yaml
logger:
  level: DEBUG
  logs:
    p180: DEBUG
    sensor: WARN
    binary_sensor: WARN
    button: WARN
    number: WARN
```

All discovery output — register dumps, change lines — is `DEBUG`, so nothing
you need is lost. If you raise the global level to `VERBOSE` for the p180
frame diagnostics, mute `ble_client` and `esp32_ble_tracker` too; on a node
also running `bluetooth_proxy` they are far louder than the sensors were.

Logger settings are **compiled in, not runtime** — changing them needs a
rebuild and upload, not a restart. The boot banner reports what is running:

```
[C][logger:216]:   Max Level: DEBUG
[C][logger:238]:   Level for 'sensor': WARN
```

**Reset Baseline** prints a banner and dumps the table the next diff is
measured against, so there is always a visible "before":

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
