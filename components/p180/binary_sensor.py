import logging

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import binary_sensor

from . import (
    CONF_BITMASK,
    CONF_P180_ID,
    CONF_REGISTER,
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
            "entity will publish meaningless values. See REGISTERS.md.",
            value, REAL_REGISTER_COUNT,
        )
    return value


CONF_RAW_BITS = "raw_bits"

# Derived binary sensors - not a straight register read.
# key -> (device_class, cpp_setter)
BINARY_SENSORS = {
    "connected": ("connectivity", "set_connected_binary_sensor"),
    # This is the one you actually want for outage detection - confirmed by
    # direct test (AC input frequency register reads 0 with no grid power)
    "grid_power": ("power", "set_grid_power_binary_sensor"),
}

# Output flags, each one bit of the status register. Confirmed on a P180 Pro by
# toggling each output on its own and diffing - see the register map in
# REGISTERS.md.
# Reg 75 is the status register on this device, NOT reg 41 as on the P280/P310.
# key -> (device_class, register, bitmask)
BIT_SENSORS = {
    "light": ("light", 75, 0x0002),
    "dc_output": ("power", 75, 0x0004),
    "usb_output": ("power", 75, 0x0008),
    "ac_output": ("power", 75, 0x0010),
    # Not an output either: the DC input type. ON = DC, OFF = PV. Confirmed in
    # both directions - PV -> DC took reg 75 from 0x0010 to 0x0030 and DC -> PV
    # took it back, with all 80 holding registers byte-identical each time. The
    # P280/P310 map has this as a SETTING at holding 15; on the P180 it is a
    # status bit, so check reg 75 before concluding the settings table lacks
    # something.
    "dc_input_type": (None, 75, 0x0020),
    # Not an output: the silent-AC-charging mode flag. Confirmed in BOTH
    # directions - enabling it in the app took reg 75 from 0x0010 to 0x0050 and
    # disabling it took 0x0050 back to 0x0010, with nothing else in the status
    # table moving either time. The current that mode uses is a separate
    # setting, holding 23, which held at 5 straight through the off-toggle.
    "silent_charging": (None, 75, 0x0040),
}

# Expose any single bit of any register. Once the status bitmask register is
# located, the USB/DC/AC/light flags are pure YAML - no C++ change.
RAW_BIT_SCHEMA = binary_sensor.binary_sensor_schema().extend(
    {
        cv.Required(CONF_REGISTER): _register_number,
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
        **{
            # A mode flag has no meaningful device class, and passing None is
            # not the same as leaving the argument out.
            cv.Optional(key): binary_sensor.binary_sensor_schema(
                **({"device_class": dclass} if dclass is not None else {})
            )
            for key, (dclass, _register, _mask) in BIT_SENSORS.items()
        },
    }
)


async def to_code(config):
    parent = await cg.get_variable(config[CONF_P180_ID])

    for key, (_dclass, setter) in BINARY_SENSORS.items():
        if key in config:
            bsens = await binary_sensor.new_binary_sensor(config[key])
            cg.add(getattr(parent, setter)(bsens))

    for key, (_dclass, register, mask) in BIT_SENSORS.items():
        if key in config:
            bsens = await binary_sensor.new_binary_sensor(config[key])
            cg.add(
                parent.add_register_bit_sensor(
                    register, mask, REG_SOURCES["input"], bsens
                )
            )

    for conf in config.get(CONF_RAW_BITS, []):
        bsens = await binary_sensor.new_binary_sensor(conf)
        cg.add(
            parent.add_register_bit_sensor(
                conf[CONF_REGISTER], conf[CONF_BITMASK], conf[CONF_SOURCE], bsens
            )
        )
