import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.const import CONF_ID
from esphome.components import ble_client

CODEOWNERS = ["@you"]
DEPENDENCIES = ["ble_client"]
AUTO_LOAD = ["sensor", "binary_sensor", "button"]
MULTI_CONF = True

p180_ns = cg.esphome_ns.namespace("p180")
P180Component = p180_ns.class_("P180Component", cg.Component, ble_client.BLEClientNode)

# Which of the station's two register tables an entity reads from.
#   input   -> function 0x04, live status
#   holding -> function 0x03, settings
RegSource = p180_ns.enum("RegSource")
REG_SOURCES = {
    "input": RegSource.REG_SOURCE_INPUT,
    "holding": RegSource.REG_SOURCE_HOLDING,
}

# Upper bound baked into the C++ buffers (P180_MAX_REGS). The probe button can
# read this far, but see REAL_REGISTER_COUNT below before binding an entity here.
MAX_REGISTER = 159

# The station only has this many real input registers. A 160-register probe IS
# answered, but on a P180 Pro everything above this is comms-buffer memory - it
# contained our own Modbus request echoed back verbatim, twice, plus ASCII
# fragments. Binding an entity up there reads out-of-bounds memory, not telemetry.
REAL_REGISTER_COUNT = 100

CONF_P180_ID = "p180_id"
CONF_POLLING_INTERVAL = "polling_interval"
CONF_SETTINGS_INTERVAL = "settings_interval"
CONF_BATTERY_CAPACITY_WH = "battery_capacity_wh"
CONF_BATTERY_EFFICIENCY = "battery_efficiency"
CONF_DEBUG_DUMP = "debug_dump"
CONF_LOG_CHANGES = "log_changes"
CONF_IGNORE_REGISTERS = "ignore_registers"
CONF_CHANGE_THRESHOLD = "change_threshold"

# Shared by sensor.py / binary_sensor.py for the generic raw-register entities.
CONF_REGISTER = "register"
CONF_SOURCE = "source"
CONF_SCALE = "scale"
CONF_BITMASK = "bitmask"

CONFIG_SCHEMA = (
    cv.Schema(
        {
            cv.GenerateID(): cv.declare_id(P180Component),
            cv.Optional(CONF_POLLING_INTERVAL, default="5s"): cv.positive_time_period_milliseconds,
            # Settings change rarely; poll them far slower than status. 0 disables.
            cv.Optional(CONF_SETTINGS_INTERVAL, default="60s"): cv.positive_time_period_milliseconds,
            cv.Optional(CONF_BATTERY_CAPACITY_WH, default=1024.0): cv.float_range(min=1.0),
            cv.Optional(CONF_BATTERY_EFFICIENCY, default=0.85): cv.percentage,
            # --- register discovery -------------------------------------
            cv.Optional(CONF_DEBUG_DUMP, default=False): cv.boolean,
            cv.Optional(CONF_LOG_CHANGES, default=False): cv.boolean,
            cv.Optional(CONF_CHANGE_THRESHOLD, default=0): cv.int_range(min=0, max=65535),
            cv.Optional(CONF_IGNORE_REGISTERS, default=[]): cv.ensure_list(
                cv.int_range(min=0, max=MAX_REGISTER)
            ),
        }
    )
    .extend(cv.COMPONENT_SCHEMA)
    .extend(ble_client.BLE_CLIENT_SCHEMA)
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await ble_client.register_ble_node(var, config)
    cg.add(var.set_polling_interval(config[CONF_POLLING_INTERVAL]))
    cg.add(var.set_settings_interval(config[CONF_SETTINGS_INTERVAL]))
    cg.add(var.set_battery_capacity_wh(config[CONF_BATTERY_CAPACITY_WH]))
    cg.add(var.set_battery_efficiency(config[CONF_BATTERY_EFFICIENCY]))
    cg.add(var.set_debug_dump(config[CONF_DEBUG_DUMP]))
    cg.add(var.set_log_changes(config[CONF_LOG_CHANGES]))
    cg.add(var.set_change_threshold(config[CONF_CHANGE_THRESHOLD]))
    for reg in config[CONF_IGNORE_REGISTERS]:
        cg.add(var.add_ignored_register(reg))
