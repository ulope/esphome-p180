import logging

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import sensor

from . import (
    CONF_P180_ID,
    CONF_REGISTER,
    CONF_SCALE,
    CONF_SIGNED,
    CONF_SOURCE,
    MAX_REGISTER,
    REAL_REGISTER_COUNT,
    REG_SOURCES,
    P180Component,
)

_LOGGER = logging.getLogger(__name__)


def _register_number(value):
    """Validate a register number, warning if it is past the real table.

    The probe button can read up to MAX_REGISTER, but only the first
    REAL_REGISTER_COUNT are telemetry - above that the station returns
    comms-buffer memory. Binding an entity there is almost always a mistake,
    so warn rather than silently publishing garbage.
    """
    value = cv.int_range(min=0, max=MAX_REGISTER)(value)
    if value >= REAL_REGISTER_COUNT:
        _LOGGER.warning(
            "p180: register %d is above the station's %d real registers. The extended "
            "range is answered but holds comms-buffer memory, not telemetry, so this "
            "entity will publish meaningless values. See the README.",
            value, REAL_REGISTER_COUNT,
        )
    return value


CONF_RAW_REGISTERS = "raw_registers"

# Named sensors, each bound to one register.
# Offsets confirmed by diffing an AC-connected dump against an on-battery dump on
# a real P180 (Aug 2026) - NOT the same offsets as the P310/P280 maps.
# key -> (unit, accuracy_decimals, device_class, register, scale, signed[, source])
#
# `source` defaults to "input" (the 0x04 status table). The two settings
# registers below are the only ones confirmed in the 0x03 table so far.
#
# To promote a newly identified register to a first-class sensor, add one line
# here. No C++ change is needed.
REGISTER_SENSORS = {
    "ac_in_voltage": ("V", 1, "voltage", 8, 0.1, False),
    "ac_in_frequency": ("Hz", 2, "frequency", 9, 0.01, False),
    "ac_out_voltage": ("V", 1, "voltage", 10, 0.1, False),
    "ac_out_frequency": ("Hz", 1, "frequency", 11, 0.1, False),
    "output_power": ("W", 0, "power", 12, 1.0, False),
    # WARNING: reads 0 with all outputs off, so it never includes the station's
    # own consumption. With AC output energised it carries an offset that is
    # steady within a session but varies wildly BETWEEN them - 12-15W in one
    # capture, 137-155W in others - and the high values are refuted as real draw
    # by a 13-minute SoC hold. It is genuinely watts and the error is additive,
    # but do not feed this into an energy dashboard unadjusted.
    "battery_discharge_power": ("W", 0, "power", 13, 1.0, False),
    # Raw value IS the percent on this device - no scaling.
    "battery_percent": ("%", 0, "battery", 31, 1.0, False),
    # Light mode enum: 0 = off, then one value per mode as you cycle the button.
    # Confirmed on a P180 Pro. NOTE: it steps by 1, so it is invisible unless
    # `change_threshold` is 0.
    "light_mode": (None, 0, None, 79, 1.0, False),
    # USB output power. Tracked a USB-C PD load exactly on a P180 Pro, and is
    # separate from output_power (reg 12), which stays 0 for a USB-only load.
    "usb_output_power": ("W", 0, "power", 78, 1.0, False),
    # The station's OWN remaining-runtime estimate. Confirmed against the AFERIY
    # app: reg 72 read 950-1000 while the app showed 16h, at idle with ~11W of
    # USB draw. Needs no capacity or efficiency calibration.
    "remaining_time": ("min", 0, "duration", 72, 1.0, False),
    # CHARGING power (AC -> battery), not the AC input port. Confirmed against
    # the front panel and an external meter: panel showed 530 W in / 29 W out
    # while this read 501, and 530 - 29 = 501. Total wall draw is this plus
    # output_power; no register reports it directly.
    # This is the ACHIEVED rate, not the switch position: it read 501 W at the
    # 500 W position, but only 751 W at the 1000 W position with the battery at
    # 77%. An earlier capture saw 1002 W at the same switch position with a
    # lower SoC, so something (taper, thermal) limits it - do not read this as
    # a setting.
    "charging_power": ("W", 0, "power", 2, 1.0, False),
    # Time to full. Matched both charge rates to within a few minutes assuming
    # ~85% charge efficiency.
    "time_to_full": ("min", 0, "duration", 71, 1.0, False),
    # Charge-rate step, and it does track the rear switch: flipping 500 -> 1000 W
    # moved it 3 -> 5 in the same poll, with reg 2 following 501 -> 760 W five
    # seconds later. Confirmed in both directions. The switch writes NOTHING
    # into the settings table - see the README.
    "charge_rate_step": (None, 0, None, 1, 1.0, False),
    # SIGNED, and not a net figure. On battery it reads AC output power and
    # equals reg 12. Grid-connected it reads MINUS the CHARGING power (reg 2)
    # and stays pinned there even when the AC output is supplying a load -
    # reg 12 went 0 -> 25 -> 46 W while this held at -501. It is NOT minus the
    # wall draw. Read unsigned it publishes ~65000.
    "ac_power": ("W", 0, "power", 90, 1.0, True),
    # --- settings table (0x03) ---
    # --- the timeout family ---
    # THE UNITS ARE NOT UNIFORM. Screen-off is seconds; the four standby timers
    # are minutes. Each was measured by changing it in the app's "Standby-Zeit"
    # screen and reading the holding diff:
    #
    #   25  300 ->  600   screen off          5 min -> 10 min   => SECONDS
    #   24  480 ->  960   AC idle standby     8 h   -> 16 h     => minutes
    #   30  480 -> 1440   DC idle standby     8 h   -> 24 h     => minutes
    #   29    3 ->   10   USB idle standby    3 min -> 10 min   => minutes
    #   28    5 ->  480   whole-device off    5 min -> 480 min  => minutes
    #
    # 24 and 30 both read 480 at rest, which is 8 HOURS, not 8 minutes - do not
    # carry 25's seconds across to them.
    "screen_timeout": ("s", 0, "duration", 25, 1.0, False, "holding"),
    "ac_standby_time": ("min", 0, "duration", 24, 1.0, False, "holding"),
    "dc_standby_time": ("min", 0, "duration", 30, 1.0, False, "holding"),
    "usb_standby_time": ("min", 0, "duration", 29, 1.0, False, "holding"),
    "device_shutdown_time": ("min", 0, "duration", 28, 1.0, False, "holding"),
    # Silent-AC-charging current, amps. Measured: 1 A -> 5 A in the app moved
    # holding 23 from 1 to 5. NOTE it already read 1 before the feature was ever
    # touched, so it is the stored current, not an on/off state - the enable
    # flag is most likely status reg 75 bit 0x0040 (see the README, inferred).
    "silent_charge_current": ("A", 0, "current", 23, 1.0, False, "holding"),
    # Discharge floor, tenths of a percent. Measured: changing the app's
    # discharge limit from 10% to 16% moved holding 26 from 100 to 160.
    "discharge_limit": ("%", 0, "battery", 26, 0.1, False, "holding"),
    # Charge ceiling, tenths of a percent. Measured: 85% -> 88% in the app moved
    # holding 27 from 850 to 880. The German app labels this "AC Ladelimit im
    # ESP modus"; the mode name is most likely EPS/UPS (the unit stays plugged
    # in and holds a reserve), but that reading is not confirmed, only the
    # register and its scaling are.
    "ac_charge_limit": ("%", 0, "battery", 27, 0.1, False, "holding"),
}


def _entry(spec):
    """Unpack a table row, defaulting the register source to the status table.

    Most named sensors live in the 0x04 table, so rows carry a source only when
    they do not - which keeps the common case to six fields.
    """
    unit, accuracy, dclass, register, scale, signed = spec[:6]
    return unit, accuracy, dclass, register, scale, signed, spec[6] if len(spec) > 6 else "input"

# Expose any register without touching C++ - the point of the discovery workflow.
# Scaling stays in YAML via `scale:` or ESPHome `filters:`.

def _sensor_kwargs(unit, accuracy, dclass):
    """Build sensor_schema kwargs, omitting the ones a register does not have.

    An enum register (light mode) has no unit and no device class, and passing
    None for those is not the same as leaving them out.
    """
    kwargs = {"accuracy_decimals": accuracy, "state_class": "measurement"}
    if unit is not None:
        kwargs["unit_of_measurement"] = unit
    if dclass is not None:
        kwargs["device_class"] = dclass
    return kwargs


RAW_REGISTER_SCHEMA = sensor.sensor_schema(
    accuracy_decimals=0,
    state_class="measurement",
).extend(
    {
        cv.Required(CONF_REGISTER): _register_number,
        cv.Optional(CONF_SOURCE, default="input"): cv.enum(REG_SOURCES, lower=True),
        cv.Optional(CONF_SCALE, default=1.0): cv.float_,
        cv.Optional(CONF_SIGNED, default=False): cv.boolean,
    }
)

CONFIG_SCHEMA = cv.Schema(
    {
        cv.GenerateID(CONF_P180_ID): cv.use_id(P180Component),
        cv.Optional(CONF_RAW_REGISTERS): cv.ensure_list(RAW_REGISTER_SCHEMA),
        **{
            cv.Optional(key): sensor.sensor_schema(
                **_sensor_kwargs(unit, accuracy, dclass)
            )
            for key, (unit, accuracy, dclass, *_) in (
                (k, _entry(v)) for k, v in REGISTER_SENSORS.items()
            )
        },
    }
)


async def to_code(config):
    parent = await cg.get_variable(config[CONF_P180_ID])

    for key, spec in REGISTER_SENSORS.items():
        if key not in config:
            continue
        _unit, _accuracy, _dclass, register, scale, signed, source = _entry(spec)
        sens = await sensor.new_sensor(config[key])
        cg.add(
            parent.add_register_sensor(
                register, scale, REG_SOURCES[source], signed, sens
            )
        )

    for conf in config.get(CONF_RAW_REGISTERS, []):
        sens = await sensor.new_sensor(conf)
        cg.add(
            parent.add_register_sensor(
                conf[CONF_REGISTER],
                conf[CONF_SCALE],
                conf[CONF_SOURCE],
                conf[CONF_SIGNED],
                sens,
            )
        )
