import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import button
from esphome.const import CONF_DISABLED_BY_DEFAULT

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
    # Clear the diff baseline before starting an experiment.
    "reset_baseline": P180ButtonAction.P180_BUTTON_RESET_BASELINE,
}

CONFIG_SCHEMA = button.button_schema(P180Button, entity_category="diagnostic").extend(
    {
        cv.GenerateID(CONF_P180_ID): cv.use_id(P180Component),
        cv.Required(CONF_ACTION): cv.enum(ACTIONS, lower=True, space="_"),
        # These are register-discovery tools, not everyday controls: a dump
        # prints ~7 lines of hex and Reset Baseline is meaningless unless you
        # are mid-experiment. Ship them disabled so they stay out of the way,
        # and enable the one you want in Home Assistant when you need it
        # (Settings -> Devices -> the battery -> +N disabled entities).
        #
        # This only overrides the DEFAULT; `disabled_by_default: false` on an
        # individual button still wins, which is what example-discovery.yaml
        # does since discovery is exactly when you want them close at hand.
        cv.Optional(CONF_DISABLED_BY_DEFAULT, default=True): cv.boolean,
    }
)


async def to_code(config):
    var = await button.new_button(config)
    await cg.register_parented(var, config[CONF_P180_ID])
    cg.add(var.set_action(config[CONF_ACTION]))
