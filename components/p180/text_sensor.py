import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import text_sensor

from . import CONF_P180_ID, REG_SOURCES, P180Component

DEPENDENCIES = ["p180"]

# Enum registers published as their name instead of a bare number.
#
# key -> (register, source, option names IN VALUE ORDER - index 0 is value 0)
#
# A value with no name publishes "Unknown (N)" rather than mapping to a
# neighbour, so an unmapped mode is visible rather than silently wrong.
ENUM_SENSORS = {
    # Reg 79. The station cycles Off -> On -> SOS -> Flash on the light button,
    # and the register steps by one each press.
    "light_mode": (79, "input", ["Off", "On", "SOS", "Flash"]),
}

CONFIG_SCHEMA = cv.Schema(
    {
        cv.GenerateID(CONF_P180_ID): cv.use_id(P180Component),
        **{
            cv.Optional(key): text_sensor.text_sensor_schema()
            for key in ENUM_SENSORS
        },
    }
)


async def to_code(config):
    parent = await cg.get_variable(config[CONF_P180_ID])

    for key, (register, source, options) in ENUM_SENSORS.items():
        if key not in config:
            continue
        sens = await text_sensor.new_text_sensor(config[key])
        cg.add(
            parent.add_register_text_sensor(register, REG_SOURCES[source], options, sens)
        )
