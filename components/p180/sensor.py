import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import sensor

from . import (
    CONF_P180_ID,
    CONF_REGISTER,
    CONF_SCALE,
    CONF_SOURCE,
    MAX_REGISTER,
    REG_SOURCES,
    P180Component,
)

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
}

# Computed rather than read straight from a register.
# key -> (unit, accuracy_decimals, device_class, cpp_setter)
DERIVED_SENSORS = {
    "remaining_time": ("min", 0, "duration", "set_remaining_time_sensor"),
}

# Expose any register without touching C++ - the point of the discovery workflow.
# Scaling stays in YAML via `scale:` or ESPHome `filters:`.
RAW_REGISTER_SCHEMA = sensor.sensor_schema(
    accuracy_decimals=0,
    state_class="measurement",
).extend(
    {
        cv.Required(CONF_REGISTER): cv.int_range(min=0, max=MAX_REGISTER),
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
                unit_of_measurement=unit,
                accuracy_decimals=accuracy,
                device_class=dclass,
                state_class="measurement",
            )
            for key, (unit, accuracy, dclass, _register, _scale) in REGISTER_SENSORS.items()
        },
        **{
            cv.Optional(key): sensor.sensor_schema(
                unit_of_measurement=unit,
                accuracy_decimals=accuracy,
                device_class=dclass,
                state_class="measurement",
            )
            for key, (unit, accuracy, dclass, _setter) in DERIVED_SENSORS.items()
        },
    }
)


async def to_code(config):
    parent = await cg.get_variable(config[CONF_P180_ID])

    for key, (_unit, _accuracy, _dclass, register, scale) in REGISTER_SENSORS.items():
        if key in config:
            sens = await sensor.new_sensor(config[key])
            cg.add(parent.add_register_sensor(register, scale, REG_SOURCES["input"], sens))

    for key, (_unit, _accuracy, _dclass, setter) in DERIVED_SENSORS.items():
        if key in config:
            sens = await sensor.new_sensor(config[key])
            cg.add(getattr(parent, setter)(sens))

    for conf in config.get(CONF_RAW_REGISTERS, []):
        sens = await sensor.new_sensor(conf)
        cg.add(
            parent.add_register_sensor(
                conf[CONF_REGISTER], conf[CONF_SCALE], conf[CONF_SOURCE], sens
            )
        )
