import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import button

from . import CONF_P180_ID, P180Component, p180_ns

DEPENDENCIES = ["p180"]

P180Button = p180_ns.class_("P180Button", button.Button, cg.Parented.template(P180Component))
P180ButtonAction = p180_ns.enum("P180ButtonAction")

CONF_ACTION = "action"

# Every action is a read. This component never issues Modbus writes (0x06).
ACTIONS = {
    # Request and dump the live status table (0x04).
    "dump_input": P180ButtonAction.P180_BUTTON_DUMP_INPUT,
    # Request and dump the settings table (0x03).
    "dump_holding": P180ButtonAction.P180_BUTTON_DUMP_HOLDING,
    # Ask for 160 input registers instead of the 100 the station volunteers, to
    # find out whether more exist.
    "probe_extended": P180ButtonAction.P180_BUTTON_PROBE_EXTENDED,
    # Clear the diff baseline before starting an experiment.
    "reset_baseline": P180ButtonAction.P180_BUTTON_RESET_BASELINE,
}

CONFIG_SCHEMA = button.button_schema(P180Button).extend(
    {
        cv.GenerateID(CONF_P180_ID): cv.use_id(P180Component),
        cv.Required(CONF_ACTION): cv.enum(ACTIONS, lower=True, space="_"),
    }
)


async def to_code(config):
    var = await button.new_button(config)
    await cg.register_parented(var, config[CONF_P180_ID])
    cg.add(var.set_action(config[CONF_ACTION]))
