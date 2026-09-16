import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import binary_sensor

from . import (
    CONF_BITMASK,
    CONF_P180_ID,
    CONF_REGISTER,
    CONF_SOURCE,
    MAX_REGISTER,
    REG_SOURCES,
    P180Component,
)

CONF_RAW_BITS = "raw_bits"

# Derived binary sensors - not a straight register read.
# key -> (device_class, cpp_setter)
BINARY_SENSORS = {
    "connected": ("connectivity", "set_connected_binary_sensor"),
    # This is the one you actually want for outage detection - confirmed by
    # direct test (AC input frequency register reads 0 with no grid power)
    "grid_power": ("power", "set_grid_power_binary_sensor"),
}

# Expose any single bit of any register. Once the status bitmask register is
# located, the USB/DC/AC/light flags are pure YAML - no C++ change.
RAW_BIT_SCHEMA = binary_sensor.binary_sensor_schema().extend(
    {
        cv.Required(CONF_REGISTER): cv.int_range(min=0, max=MAX_REGISTER),
        cv.Required(CONF_BITMASK): cv.hex_uint16_t,
        cv.Optional(CONF_SOURCE, default="input"): cv.enum(REG_SOURCES, lower=True),
    }
)

CONFIG_SCHEMA = cv.Schema(
    {
        cv.GenerateID(CONF_P180_ID): cv.use_id(P180Component),
        cv.Optional(CONF_RAW_BITS): cv.ensure_list(RAW_BIT_SCHEMA),
        **{
            cv.Optional(key): binary_sensor.binary_sensor_schema(device_class=dclass)
            for key, (dclass, _setter) in BINARY_SENSORS.items()
        },
    }
)


async def to_code(config):
    parent = await cg.get_variable(config[CONF_P180_ID])

    for key, (_dclass, setter) in BINARY_SENSORS.items():
        if key in config:
            bsens = await binary_sensor.new_binary_sensor(config[key])
            cg.add(getattr(parent, setter)(bsens))

    for conf in config.get(CONF_RAW_BITS, []):
        bsens = await binary_sensor.new_binary_sensor(conf)
        cg.add(
            parent.add_register_bit_sensor(
                conf[CONF_REGISTER], conf[CONF_BITMASK], conf[CONF_SOURCE], bsens
            )
        )
