import logging

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import sensor

from . import (
    CONF_P180_ID,
    CONF_REGISTER,
    CONF_SCALE,
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

# Named sensors, each bound to one status (0x04) register.
# Offsets confirmed by diffing an AC-connected dump against an on-battery dump on
# a real P180 (Aug 2026) - NOT the same offsets as the P310/P280 maps.
# key -> (unit, accuracy_decimals, device_class, register, scale)
#
# To promote a newly identified register to a first-class sensor, add one line
# here. No C++ change is needed.
REGISTER_SENSORS = {
    "ac_in_voltage": ("V", 1, "voltage", 8, 0.1),
    "ac_in_frequency": ("Hz", 2, "frequency", 9, 0.01),
    "ac_out_voltage": ("V", 1, "voltage", 10, 0.1),
    "ac_out_frequency": ("Hz", 1, "frequency", 11, 0.1),
    "output_power": ("W", 0, "power", 12, 1.0),
    "battery_discharge_power": ("W", 0, "power", 13, 1.0),
    # Raw value IS the percent on this device - no scaling.
    "battery_percent": ("%", 0, "battery", 31, 1.0),
    # Light mode enum: 0 = off, then one value per mode as you cycle the button.
    # Confirmed on a P180 Pro. NOTE: it steps by 1, so it is invisible unless
    # `change_threshold` is 0.
    "light_mode": (None, 0, None, 79, 1.0),
    # USB output power. Tracked a USB-C PD load exactly on a P180 Pro, and is
    # separate from output_power (reg 12), which stays 0 for a USB-only load.
    "usb_output_power": ("W", 0, "power", 78, 1.0),
    # The station's OWN remaining-runtime estimate. Confirmed against the AFERIY
    # app: reg 72 read 950-1000 while the app showed 16h, at idle with ~11W of
    # USB draw. Needs no capacity or efficiency calibration.
    "remaining_time": ("min", 0, "duration", 72, 1.0),
}

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
            for key, (unit, accuracy, dclass, _register, _scale) in REGISTER_SENSORS.items()
        },
    }
)


async def to_code(config):
    parent = await cg.get_variable(config[CONF_P180_ID])

    for key, (_unit, _accuracy, _dclass, register, scale) in REGISTER_SENSORS.items():
        if key in config:
            sens = await sensor.new_sensor(config[key])
            cg.add(parent.add_register_sensor(register, scale, REG_SOURCES["input"], sens))

    for conf in config.get(CONF_RAW_REGISTERS, []):
        sens = await sensor.new_sensor(conf)
        cg.add(
            parent.add_register_sensor(
                conf[CONF_REGISTER], conf[CONF_SCALE], conf[CONF_SOURCE], sens
            )
        )
