# P180 register map and discovery notes

Everything here was derived from captures on one **AFERIY P180 Pro** on a 230 V
/ 50 Hz grid, over a few hours. None of it has been tested across a firmware
update or on a second unit.

Rows are graded by how far the evidence actually goes:

- **measured** — observed directly and cross-checked
- **inferred** — fits the data, but rests on a model or an assumption
- **guess** — a label, not a finding

Only trust a row as far as its grade. [Corrections](#corrections) lists the
conclusions that turned out to be wrong, because a register map is only as good
as its worst-supported row.

---

## Contents

- [Status table (`0x04`)](#status-table-0x04)
  - [Status bitmask — register 75](#status-bitmask--register-75)
  - [Charging](#charging)
  - [Registers above 99 are not telemetry](#registers-above-99-are-not-telemetry)
- [Settings table (`0x03`)](#settings-table-0x03)
- [Register 13 is unresolved](#register-13-is-unresolved)
- [Remaining time and pack capacity](#remaining-time-and-pack-capacity)
- [Discovery workflow](#discovery-workflow)
- [Upstream maps](#upstream-maps)
- [Corrections](#corrections)

---

## Status table (`0x04`)

100 registers, polled every `polling_interval`. The table is sparse: idle on
battery with every output off, only 9 of the 100 are non-zero. Most of the zeros
are genuinely idle fields, not a parsing problem.

| Reg | Observed | Field | Evidence |
|---|---|---|---|
| 1 | `5` at 1000 W, `3` at 500 W | **AC charge-rate step** — `charge_rate_step`. Tracks the rear switch | **measured** both directions — a live flip moved it 3 → 5 with reg 2 following. Only two switch positions sampled, so don't extrapolate a formula |
| 2 | `501` at the 500 W setting; `1002`, then later only `751`, at 1000 W | **Charging power (W)** — `charging_power`. AC→battery only, *not* wall draw, and the *achieved* rate rather than the setting | **measured** — cross-checked against the front panel and an external meter. The 751 W sample was at 77 % SoC, so something limits the rate; mechanism **unconfirmed** |
| 8 | `2327` grid, `21` on battery | **AC input voltage** ×0.1 | **measured** |
| 9 | `5000` grid, `0` on battery | **AC input frequency** ×0.01 — drives `grid_power` | **measured** |
| 10 | `2316` with AC output on | **AC output voltage** ×0.1 = 231.6 V; sags to 208.9 V at ~1.8 kW | **measured** |
| 11 | `500` | **AC output frequency**, 50.0 Hz nominal even with output off | **inferred** — never seen change |
| 12 | tracks an AC load | **Output power (W)** — `output_power` | **measured** |
| 13 | `0` with all outputs off; an offset of 12–155 W with AC energised | **Output-attributed power (W)** — `battery_discharge_power`. See [below](#register-13-is-unresolved) | **measured** unit, additivity and 0-with-outputs-off. Idle behaviour **unknown** |
| 31 | `90` | **Battery %** — raw value is the percent, no scaling | **measured** |
| 37 | `0x4000` idle, `0x8000`/`0x8040` charging | charge status bitmask? | **guess** — only that it changes with charging |
| 53 | `0x10` AC out, `0x68` AC in, `0x78` both — but `0x38` with both, once | AC-side status bitmask | **guess** — the additive story is contradicted; see [Corrections](#corrections) |
| 54 | `0x0800` light, `0x0837` USB, `0x0980` DC | DC-side, unidentified | **guess** — only that it tracks DC-side output state |
| 66 / 67 | increment together under AC load | **AC output energy since power-on, 10 Wh per count.** [Details](#the-6667-counter) | **measured** — including the reset-on-restart |
| 71 | `34` at 1000 W, `67` at 500 W | **Time to full, minutes** — `time_to_full` | **measured** — both rates match to a few minutes assuming ~85 % charge efficiency |
| 72 | ~5400 all-off, ~1550 AC idle, 15 at 2.2 kW | **Remaining runtime, minutes** — `remaining_time` | **measured** — matches the station's own display |
| 75 | bitmask | **Status bits** — see [below](#status-bitmask--register-75) | **measured** |
| 78 | `11`→`15`→`33`→`37` under a USB-C PD load | **USB output power (W)** — `usb_output_power` | **measured** |
| 79 | `0`, then one value per mode | **Light mode** enum — `light_mode` | **measured** |
| 90 | `+1095` discharging, `−1002` charging | **Signed AC power (W)** — `ac_power`. [Details](#charging) | **measured** |
| 97–99 | `0x1901 0x0203 0x0405` | version/serial info? | **guess** — constant in every capture |

**Not found anywhere in registers 0–99:** per-port USB watts, temperatures, and
**fan state**. With the fans audibly cycling about three minutes after a
sustained 1.3 kW load, the only registers that moved in that window were 8, 10,
72 and 78 — all already identified.

### Status bitmask — register 75

**Register 75 is the status bitmask, not register 41.** Reg 41 stayed at
`0x0000` through every single toggle, so the Sydpower/P280 layout does not apply
here. The mask is additive: with USB and AC both on it reads
`0x0018` = `0x0008 | 0x0010`.

| Mask | Meaning | How it was confirmed |
|---|---|---|
| `0x0001` | *unidentified* | never seen set |
| `0x0002` | Light | 2 on/off cycles |
| `0x0004` | DC output | 2 on/off cycles |
| `0x0008` | USB output | 5 on/off cycles |
| `0x0010` | AC output | 3 on/off cycles |
| `0x0020` | DC input type — set = DC, clear = PV | set on PV → DC, cleared on DC → PV, with all 80 holding registers byte-identical each time |
| `0x0040` | Silent AC charging | `0x0010` → `0x0050` on enable, back on disable, with nothing else in the status table moving either time |

Not every bit is an output: `0x0020` and `0x0040` are modes. Every bit except
`0x0001` is a named binary sensor.

### Charging

Plugging in AC input brings a second set of registers to life. Regs 8 and 9 jump
to the real mains figures (233.0 V, 50.00 Hz), which is what drives
`grid_power`.

**Register 90 is signed.** Read unsigned it publishes ~65000 the moment the
charger is connected, so `ac_power` sets `signed: true`.

It is **not a net figure**. Switching AC output on *while the charger is still
connected* and then adding a load shows the two sides tracked separately:

| | reg 12 (AC out) | reg 2 (charging) | reg 90 |
|---|---|---|---|
| no load | 0 W | 502 | −502 |
| load ramping | 25 W | 502 | −502 |
| load settled | 46 W | 501 | −501 |

Reg 90 stays pinned to the input while the output climbs. So:

- on battery → reg 90 = **+** AC output power, and equals reg 12
- grid connected → reg 90 = **−** *charging* power, regardless of any AC output load

Reg 12 is therefore not a mirror of reg 90; that only holds while discharging.

#### There is no total-wall-draw register

Reg 2 is charging power, not what the station pulls from the socket. With the
front panel showing **530 W in / 29 W out**, reg 2 read **501** — and
530 − 29 = 501 exactly, with an external power meter agreeing with the panel.
The AC output load is drawn from the grid *on top of* charging:

```
wall draw  =  charging_power (reg 2)  +  output_power (reg 12)
```

The charge rate stays pinned at whatever the rear switch selects — 501 W with
both a 29 W and a 46 W output load — so the total moves with the load, not the
charging.

`remaining_time` (reg 72) reads **0** while charging, which is correct: time to
empty is meaningless then. Use `time_to_full` (reg 71).

#### The 66/67 counter

A controlled run — ~1092 W of AC load held for about two minutes — pins the
unit at **10 Wh per count**. With a 5 s poll you can only observe an interval as
a multiple of 5 s, and every observation lands on one of the two values that
allows:

| AC output | 10 Wh takes | 5 s-sampled | observed |
|---|---|---|---|
| ~1092 W | 33.0 s | 30 or 35 s | 30, 35, 30 |
| ~1320 W | 27.3 s | 25 or 30 s | 30, 25 |
| ~1568 W | 23.0 s | 20 or 25 s | 25 |

It counts **AC output** energy, not battery energy: a battery-side basis (÷0.85)
predicts 28.0 s and 23.2 s for the first two rows, consistently faster than
observed.

**It resets to 0 when the station restarts**, so it measures energy since
power-on, not lifetime. Don't build a `total_increasing` energy sensor on it
without handling the reset — and note that a jump *down* in the baseline between
captures is a power cycle, not a counter wrapping.

### Registers above 99 are not telemetry

The **Probe Extended Registers** button asks for 160 registers and the P180 Pro
**does answer** — the frame parses and the CRC passes. But the extra range is a
communications buffer read out of bounds:

| Register | Bytes | Meaning |
|---|---|---|
| 125–128 | `11 04 00 00 00 A0 E2 F2` | **our own Modbus probe request, echoed back verbatim** |
| 129–132 | `2C 31 2C 35 2C 2C 38 2C` | ASCII `,1,5,,8,` |
| 133–136 | `11 04 00 00 00 A0 E2 F2` | the same request again |
| 137 | `0D 00` | carriage return + NUL |
| everything else | `0000` | — |

The request bytes are byte-for-byte what the component transmitted, so nothing
above register 99 can be trusted as a measurement. Binding an entity up there
produces a config warning.

The probe's own log line is printed *before* the request goes out — it describes
what to look for, it is not a verdict. The dump that follows, and the
`input register count changed 100 -> 160` line, are the actual result.

---

## Settings table (`0x03`)

80 registers, polled every `settings_interval`. 12 of the 15 non-zero registers
are mapped. At rest:

```
holding regs 000-015: 0000 0000 0000 0000 0320 0000 0000 0001 0000 0000 0000 0000 0000 0000 0000 0000
holding regs 016-031: 0000 0000 0000 0000 0000 000F 0000 0001 01E0 012C 0064 0352 0005 0003 01E0 0000
holding regs 032-047: 0000 0000 0000 0000 0000 0000 0000 0000 0000 0000 0000 0000 0000 0000 0000 000B
holding regs 048-063: 0010 000C 000D 0000 0000 0000 0000 0000 0000 0000 0000 0000 0000 0000 0000 0000
holding regs 064-079: 0000 0000 0000 0000 0000 0000 0000 0000 0000 0000 0000 0000 0000 0000 0000 0000
```

Everything in this table was mapped the same way: change the value in the AFERIY
app, read the `holding reg NN` diff line. Every row has two data points and a
matching app label.

| Reg | Observed | Field | App label (German UI) |
|---|---|---|---|
| 23 | 1 → 5 | **Silent-charging current, A** — `silent_charge_current` | 1 A → 5 A |
| 24 | 480 → 960 | **AC idle standby, minutes** — `ac_standby_time` | AC-Leerlaufstandby-Zeit, 8 h → 16 h |
| 25 | 300 → 600 | **Screen off, seconds** — `screen_timeout` | 5 min → 10 min |
| 26 | 100 → 160 | **Discharge floor, ×0.1 %** — `discharge_limit` | 10 % → 16 % |
| 27 | 850 → 880 | **Charge ceiling, ×0.1 %** — `ac_charge_limit` | AC Ladelimit im ESP modus, 85 % → 88 % |
| 28 | 5 → 480 | **Whole-device shutdown, minutes** — `device_shutdown_time` | Gesamtgerät-Abschaltzeit, 5 min → 480 min |
| 29 | 3 → 10 | **USB idle standby, minutes** — `usb_standby_time` | USB-Leerlaufstandby-Zeit, 3 min → 10 min |
| 30 | 480 → 1440 | **DC idle standby, minutes** — `dc_standby_time` | DC-Leerlaufstandby-Zeit, 8 h → 24 h |
| 47–50 | 11, 16, 12, 13 | **Firmware versions, ×0.1** — `ac_firmware_version`, `bms_firmware_version`, `pv_firmware_version`, `panel_firmware_version` | AC v1.1, BMS-V1 v1.6, PV v1.2, Panel-V1 v1.3 |

Still unidentified: **4** (`800`), **7** (`1`) and **21** (`15`). Everything else
reads `0000`.

**The units are not uniform.** Screen-off is in seconds; all four standby timers
are in minutes. The two are adjacent — 24 and 25 sit next to each other — so do
not infer one from the other. At rest, 24 and 30 both read `480`, which is 8
*hours*.

**The charge ceiling has a second, behavioural confirmation**, stronger than a
matching UI label: the battery sat at 90 % (reg 31 = `0x5A`) with holding 27 at
`900` and mains present at 232.7 V, and `charging_power` read exactly `0`. The
station stops charging at the value in that register.

**The firmware versions were matched differently from everything else here.**
There is no before/after transition behind them — you cannot change a firmware
version to watch a register move. All four values were matched against the app's
Firmware-Version screen at once, and three are pinned by value alone: exactly one
listed component is v1.6, one v1.2, one v1.3, so 48, 49 and 50 need no assumption
about list order. Only 47 rests on ordering, since it reads `11` and *two*
components are v1.1 (AC and AC-V1-02). The app lists five components against four
registers, so one is not exposed.

**Holding 23 is the stored current, not an on/off state.** It held at `5`
straight through disabling silent charging, and read `1` before the feature was
ever touched. The enable flag is status reg 75 bit `0x0040`.

### Two "settings" are not in this table at all

Silent AC charging and the DC input type both move **status reg 75** and leave
all 80 holding registers byte-identical — four transitions, two each way.
Upstream puts DC input type at holding 15. So when a setting does not show up in
a holding diff, **check reg 75 before concluding the table does not carry it.**

### The rear 1000 W / 500 W switch writes nothing here

Flipping it 500 → 1000 W moved status reg 1 from 3 to 5 in the same poll, with
reg 2 following 501 → 760 W five seconds later — but dumps taken either side of
the flip are byte-identical across all 80 registers. It is a hardware switch, and
the settings table only carries what the app can set.

---

## Register 13 is unresolved

`battery_discharge_power` reads register 13, and it is the one register in the
status table whose behaviour is not explained.

**What is established:**

- **It is genuinely watts.** With AC output off and only a USB-C PD device
  attached, reg 13 equalled reg 78 exactly at 11, 15, 33 and 37 W, and adding an
  8 W USB draw moved it by exactly +8.
- **It is the battery side, not an AC mirror.** With a USB-C load and nothing
  else on, reg 13 tracked the draw (11 → 15 → 33 → 37 W) while reg 12 stayed at
  `0`. Under a ~1.8 kW AC load the split is unmistakable — reg 90 read 1755 W
  against reg 13's 2224 W.
- **It never includes station self-consumption.** Switching AC output off drops
  it to *exactly* 0 while the station is still plainly running its BMS, display
  and Bluetooth. So it reports output-attributed power only.
- **The error is additive, not an RMS artifact.** At 1757 W of AC output an
  additive model predicts 2204 against the observed 2222 (−18); combining the
  terms in quadrature, as a ripple- or RMS-current measurement would, predicts
  2072 (−150).
- **The offset is absent whenever the charger is connected.** With AC input
  plugged in and AC output supplying 46 W, reg 13 read `0`, correctly reflecting
  that the load came from mains.

**What is wrong:** with AC output energised on battery it carries an offset that
is steady *within* a session and wildly different *between* sessions:

| session | AC output | reg 13 | implied offset |
|---|---|---|---|
| 00:07 | 23 / 36 / 39 W | 35 / 51 / 54 | **12–15 W** |
| 00:24 | 1016 / 1552 / 1757 W | 1357 / 2016 / 2222 | ~148 W |
| 01:10 | 1088 / 1090 / 1103 W | 1404 / 1408 / 1422 | ~137 W |

In the 00:07 session a 137 W offset would have put those readings at
160/173/176. They were 35/51/54. Yet within each session it is rock steady — in
the 01:10 session, subtracting 137 gives an implied conversion efficiency of
0.858–0.859 across every sample.

**The high figures are refuted as real draw.** SoC sat at 48 % for **13
minutes**, with AC output energised for 12 of them, and never crossed a single
1 % boundary. At 137 W the pack would have given up ~27 Wh — two or three
crossings. Requiring zero crossings puts a hard ceiling on the true draw:

| Wh per 1 % | implied ceiling |
|---|---|
| 8.8 | < 44 W |
| 9.8 | < 49 W |

So reg 13's 137 W at AC idle is **wrong by roughly a factor of three**. The real
inverter idle cost is around **12 W**, from reg 72's own accounting (~5400 min
with everything off vs. ~1550 min with AC output idling).

> **Practical consequence:** `battery_discharge_power` over-reads whenever AC
> output is on, whether or not anything is plugged into it. Don't feed it into an
> energy dashboard without accounting for that. Under load the error is
> proportionally small; at idle it is the entire reading.

**Test worth running:** with the station idle, switch AC output off, wait a few
seconds, switch it back on, and read reg 13 before applying any load. If it comes
back at ~12 W rather than ~137 W, the high reading is a latched state — and
toggling AC output becomes a workaround.

---

## Remaining time and pack capacity

**Register 72 is the device's own remaining-runtime estimate, in minutes**,
confirmed against the AFERIY app, which showed **16 h** while reg 72 read
950–1000 (15.8–16.7 h). `remaining_time` reads it directly — no capacity or
efficiency calibration is involved.

Under a step load it collapses within two polls, then holds steady:

| battery draw | reg 72 |
|---|---|
| 150 W | 880 |
| 2016 W | 27 |
| 2224 W | 16 |
| 2225 W | 15 |

Multiply it by the battery draw and the two loaded points agree almost exactly,
implying ~554 Wh remaining:

| battery draw | reg 72 | product | = |
|---|---|---|---|
| 1660 W | 20 | 33 200 W·min | 553 Wh |
| 2222 W | 15 | 33 330 W·min | 556 Wh |
| 150 W (idle) | 950 | 142 500 W·min | **2375 Wh** ✗ |

**The idle point is 4.3× off**, which is the reg 13 problem above seen from the
other side: for reg 72 to read ~950 at idle the draw would have to be ~39 W, not
the 148 W reg 13 claims. Since the app agrees with reg 72, this is the station's
own accounting, and reg 13 is the outlier.

When the load is removed reg 72 climbs back over roughly 40 seconds
(`20 → 63 → 685 → 825 → 892 → 905`) and then **plateaus** at 860–1010,
oscillating there with no further trend across eight minutes of idle. The
smoothing window is short and the idle reading is a genuine steady-state value,
not a lagging one.

**Pack capacity, roughly.** Under a 1090 W load, SoC fell 50 % → 49 % → 48 % in
25 s per step — **8.8–9.8 Wh per 1 %**, so roughly **0.9–1.0 kWh**, consistent
with the 1024 Wh usually quoted for this model. Treat that as an
order-of-magnitude check: SoC has 1 % resolution, so each crossing carries up to
a full percent of timing error.

---

## Discovery workflow

The station volunteers all 100 status registers on every poll and answers the
80-register settings table on request. Everything needed to map them is in the
component — flash once, then run experiments from the Home Assistant UI and the
ESPHome log without recompiling per register.

Start from [`example-discovery.yaml`](example-discovery.yaml).

### Change logging

Set `log_changes: true` and the component logs only registers whose value moved
since the previous frame:

```
[D][p180:330]: input reg 75: 0x0010 -> 0x0050 (16 -> 80)
```

Press **Reset Baseline**, change exactly *one* thing on the station, and read off
which register moved. One change at a time is what makes the evidence
trustworthy. Reset Baseline also dumps the table the next diff is measured
against, so there is always a visible "before".

Use `ignore_registers:` to silence registers that move on their own (power, SoC)
and `change_threshold:` to filter jitter. `ignore_registers:` applies to the
**status table only** — the settings table is polled a minute apart and barely
moves, so muting the same offsets there would only risk hiding a real change.

**Set `change_threshold: 0` while mapping.** The light-mode enum steps by one, and
a threshold of 1 suppresses every step.

**Capture under load, not just idle.** Registers 78, 90 and the meaning of 72 are
all invisible on a resting unit. Toggling an output tells you which bit flips;
actually drawing power through it is what reveals the measurement registers.

### Probe buttons

All four are reads; none writes to the station.

| Action | What it does |
|---|---|
| `reset_baseline` | Clear the diff baseline before an experiment |
| `dump_input` | Request and dump the 100-register status table |
| `dump_holding` | Request and dump the 80-register settings table |
| `probe_extended` | Ask for 160 status registers, to find out whether more exist |

Dumps are chunked 16 registers per line and register-indexed, so values line up
with the map:

```
[D][p180:362]: input regs 000-015: 0000 0005 0000 0000 0000 0000 0000 0000 0917 1388 090C 01F4 000C 0000 0000 0000
[D][p180:362]: input regs 016-031: 0000 ...
```

### Exposing a register

The moment a diff line points at something interesting, add it to
`raw_registers:` — a YAML edit, no C++ change. For status bits, `raw_bits:` binds
a binary sensor to a single bit. See the configuration reference in the
[README](README.md#configuration-reference).

Once a register is confirmed, promote it to a named sensor by adding one line to
`REGISTER_SENSORS` in `components/p180/sensor.py` (or `BIT_SENSORS` in
`binary_sensor.py`). The C++ needs no change — named sensors and `raw_registers:`
share the same code path.

### Offline diffing

`tools/regdiff.py` diffs two saved captures, which is useful for slow or
un-repeatable events (an outage, a full charge cycle):

```console
$ esphome logs example-discovery.yaml --no-states > before.log   # press Dump, then stop
$ # ... change one thing ...
$ esphome logs example-discovery.yaml --no-states > after.log
$ tools/regdiff.py before.log after.log
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
| 5 | Light: off → on → SOS → flash | light bits + the mode enum |
| 6 | Known AC load, then add a DC load | splits reg 12 vs 13 |
| 7 | Single USB-C load only | per-port USB watt registers |
| 8 | Unplug AC (outage) | input regs → 0, inverter/charging bits flip |
| 9 | Cycle AC charge rate through its steps | charge-rate step + charging power |
| 10 | Solar/DC input, if available | DC input power/voltage |
| 11 | Let SoC move ≥1 % while discharging | SoC scaling, time-to-empty/full |
| 12 | Long idle / sustained high load | temperature registers |
| 13 | Press **Probe Extended Registers** | whether >100 registers exist |
| 14 | Change a setting in the AFERIY app | a `holding reg NN` line within `settings_interval` — this is how eight of the twelve mapped settings registers were found |
| 15 | Flip the rear 1000 W / 500 W input switch | status reg 1 (3 ↔ 5) and reg 2. *Not* the settings table |

---

## Upstream maps

These come from [`olofd/kraftverk`](https://github.com/olofd/kraftverk) (AFERIY
P280) and [`Ylianst/ESP-FBot`](https://github.com/Ylianst/ESP-FBot) (AFERIY
P310). **The P180's table is 100 registers and demonstrably reordered.** Treat
them as a source of candidate *field names and scalings* to match observed
changes against, never as a map to read directly.

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

**How well that transfers, now that the P180's settings table is mapped:** every
*field* looked for was found with the same units — standby timers, screen rest in
seconds, the floor/ceiling pair in tenths of a percent — and **not one was at the
upstream offset**. Holding 59-62 and 66/67 all read `0000` here. One field is not
even in the same table: DC input type is upstream's holding 15, and on the P180
it is a bit of status reg 75.

Good for field names and scalings; worthless as a set of offsets.

---

## Corrections

Conclusions that were recorded here and later turned out to be wrong.

- **~150 W inverter standby.** Stated as measured, from a four-point curve fit of
  the form `reg 13 ≈ AC_output / 0.85 + 150 W`. Refuted: the real figure is
  ~12 W. A two-parameter fit through idle-and-loaded points cannot separate a
  real standby draw from an offset in the idle reading. The 85 % conversion
  figure under load survives; the standby term does not.
- **Reg 72 is free-running noise.** It was in `ignore_registers`. Every early
  capture was at zero load, where a runtime estimate genuinely is huge and
  jittery. It is the single most useful register in the table.
- **The 66/67 counter survives reboots.** Invented; it resets to 0. No power
  cycle had been tested, and none was asked about.
- **Reg 90 is net AC power.** It is not — grid-connected it ignores the output
  load entirely.
- **Reg 2 is AC input power.** It is charging power only; wall draw is reg 2 plus
  reg 12.
- **Reg 54's high byte mirrors reg 75.** Holds for USB by coincidence, fails for
  DC and light.
- **The settings table never answers on this unit.** It does; twelve of its
  registers are mapped above.
- **Holding 28 is the charge-rate setting.** Graded *inferred* because holding
  28/29 read 5 and 3 — exactly the two values status reg 1 takes at the two
  switch positions. They are the device-shutdown and USB standby timers. Two
  registers matching two observed values is a coincidence, and "inferred" was too
  strong for it.
- **Holding 24 and 30 are 8-minute timeouts.** Extrapolated from 25 being
  seconds, the moment 25 was confirmed. They are minutes, so 480 is 8 hours. One
  register's units say nothing about its neighbour's.
- **Reg 53 is an additive bitmask** (`0x10` AC out + `0x68` AC in = `0x78`).
  Downgraded to *guess*: a later capture with AC input and output both on reads
  `0x38`. That sample had charging stopped at the ceiling, so the missing `0x40`
  may be a charging-active bit, but that is one observation.
