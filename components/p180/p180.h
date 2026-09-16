#pragma once

#include "esphome/core/component.h"
#include "esphome/core/helpers.h"
#include "esphome/components/ble_client/ble_client.h"
#include "esphome/components/esp32_ble_tracker/esp32_ble_tracker.h"
#include "esphome/components/sensor/sensor.h"
#include "esphome/components/binary_sensor/binary_sensor.h"
#include "esphome/components/button/button.h"

#include <esp_gattc_api.h>

#include <vector>

namespace esphome {
namespace p180 {

namespace espbt = esphome::esp32_ble_tracker;

// BrightEMS / SYD-power BLE service & characteristics
// Documented in Ylianst/ESP-FBot's internals/README.md (AFERIY P310 teardown).
// If your P180 doesn't respond, these UUIDs are the first thing to double check
// with nRF Connect - the register map is more likely to differ than these.
static const uint16_t P180_SERVICE_UUID_16 = 0xa002;      // 0000a002-0000-1000-8000-00805f9b34fb
static const uint16_t P180_WRITE_CHAR_UUID_16 = 0xc304;   // client -> device
static const uint16_t P180_NOTIFY_CHAR_UUID_16 = 0xc305;  // device -> client

// The station is a Modbus slave at address 0x11. This component is deliberately
// READ-ONLY: it only ever issues 0x03/0x04 reads. Function 0x06 (write) is not
// implemented on purpose - the closely related Sydpower reverse-engineering work
// (olofd/kraftverk) documents holding register 68 as permanently bricking the
// station when written 0, and registers 25/26 as toggling on *any* write
// regardless of the value sent. Don't add writes casually.
static const uint8_t P180_SLAVE_ADDR = 0x11;
static const uint8_t P180_FUNC_READ_HOLDING = 0x03;  // settings
static const uint8_t P180_FUNC_READ_INPUT = 0x04;    // live status

// The P180 volunteers 100 input registers (vs. the P310's 80). 160 is the upper
// bound the `probe_extended` button asks for - kraftverk reports 160 reachable
// on the Sydpower stack - and it sizes the buffers below.
static const uint16_t P180_INPUT_REG_COUNT = 100;
static const uint16_t P180_HOLDING_REG_COUNT = 80;
static const uint16_t P180_MAX_REGS = 160;

// Responses are NOT standard Modbus RTU framing. Instead of a single byte-count
// field, the device echoes the request's 4-byte start+count before the data:
//   addr(1) func(1) start(2) count(2) | count*2 data bytes | crc(2)
// which is why a 100-register reply is 6 + 200 + 2 = 208 bytes. The register
// count therefore lives at bytes 4..5, and the frame length is derived from it
// rather than hardcoded, so a longer probe reply still parses.
static const uint16_t P180_HEADER_LEN = 6;
static const uint16_t P180_CRC_LEN = 2;
static const uint16_t P180_MAX_FRAME_LEN = P180_HEADER_LEN + P180_MAX_REGS * 2 + P180_CRC_LEN;

// A partially assembled frame older than this is junk - drop it rather than
// letting it corrupt the next one.
static const uint32_t P180_FRAME_TIMEOUT_MS = 1000;
// Don't put two requests on the wire back to back; the station answers one at a
// time and a second request mid-reply just loses both.
static const uint32_t P180_MIN_REQUEST_GAP_MS = 250;

// Registers backing derived (non-raw) entities. Everything else is described in
// sensor.py / binary_sensor.py or configured from YAML.
static const uint16_t P180_REG_AC_IN_FREQUENCY = 9;
// AC input frequency reads ~6000 (60.00Hz) on grid power and exactly 0 on
// battery. Threshold well below nominal so a noisy sample can't flap the sensor.
static const uint16_t P180_GRID_PRESENT_THRESHOLD = 1000;

enum RegSource : uint8_t {
  REG_SOURCE_INPUT = 0,    // function 0x04 - live status
  REG_SOURCE_HOLDING = 1,  // function 0x03 - settings
  REG_SOURCE_COUNT = 2,
};

enum P180ButtonAction : uint8_t {
  P180_BUTTON_DUMP_INPUT = 0,
  P180_BUTTON_DUMP_HOLDING = 1,
  P180_BUTTON_PROBE_EXTENDED = 2,
  P180_BUTTON_RESET_BASELINE = 3,
};

// A sensor bound to one register. Named sensors and YAML `raw_registers:` entries
// both land here, so adding a newly identified register never needs new C++.
struct RegisterSensor {
  sensor::Sensor *sensor;
  uint16_t reg;
  float scale;
  RegSource source;
};

// A binary sensor bound to one bit (or bit group) of one register.
struct RegisterBitSensor {
  binary_sensor::BinarySensor *sensor;
  uint16_t reg;
  uint16_t mask;
  RegSource source;
};

class P180Component : public esphome::ble_client::BLEClientNode, public Component {
 public:
  void setup() override;
  void loop() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::AFTER_BLUETOOTH; }

  void gattc_event_handler(esp_gattc_cb_event_t event, esp_gatt_if_t gattc_if,
                            esp_ble_gattc_cb_param_t *param) override;

  void set_polling_interval(uint32_t ms) { this->polling_interval_ = ms; }
  // Settings (0x03) change rarely, so they're polled far slower than status. 0 disables.
  void set_settings_interval(uint32_t ms) { this->settings_interval_ = ms; }

  // --- Register discovery options ---------------------------------------
  // Log the full register block every poll, chunked 16 registers per line.
  void set_debug_dump(bool enabled) { this->debug_dump_ = enabled; }
  // Log only registers whose value changed since the previous frame. This is the
  // workhorse for mapping the table: reset the baseline, change exactly one
  // thing on the station, and read off which register moved.
  void set_log_changes(bool enabled) { this->log_changes_ = enabled; }
  // Suppress changes of this magnitude or less, to filter sensor jitter.
  void set_change_threshold(uint16_t threshold) { this->change_threshold_ = threshold; }
  // Silence registers that move constantly (power, SoC) so a deliberate change stands out.
  void add_ignored_register(uint16_t reg) { this->ignored_registers_.push_back(reg); }

  // --- Generic register -> entity binding -------------------------------
  void add_register_sensor(uint16_t reg, float scale, RegSource source, sensor::Sensor *s) {
    this->register_sensors_.push_back(RegisterSensor{s, reg, scale, source});
  }
  void add_register_bit_sensor(uint16_t reg, uint16_t mask, RegSource source, binary_sensor::BinarySensor *s) {
    this->register_bit_sensors_.push_back(RegisterBitSensor{s, reg, mask, source});
  }

  // --- Derived entities (computed, not a straight register read) ---------
  void set_connected_binary_sensor(binary_sensor::BinarySensor *s) { this->connected_binary_sensor_ = s; }
  // Is grid/AC power actually present at the input (the outage sensor) -
  // confirmed by direct test against real AC loss on this device.
  void set_grid_power_binary_sensor(binary_sensor::BinarySensor *s) { this->grid_power_binary_sensor_ = s; }


  // --- Probe actions (all reads - no 0x06 writes anywhere) ---------------
  void dump_input_registers();
  void dump_holding_registers();
  void probe_extended_registers();
  void reset_baseline();

 protected:
  void send_read_request_(uint8_t func, uint16_t start, uint16_t count);
  void on_notify_(const uint8_t *data, uint16_t len);
  void handle_frame_(const uint8_t *frame, uint16_t len, uint8_t func, uint16_t count);
  void store_and_diff_(RegSource source, const uint8_t *frame, uint16_t count);
  void dump_registers_(RegSource source);
  void publish_(RegSource source);
  bool is_ignored_(uint16_t reg) const;
  bool ready_();
  static const char *source_name_(RegSource source);
  static uint16_t crc16_modbus_(const uint8_t *data, uint16_t len);

  uint32_t polling_interval_{5000};
  uint32_t settings_interval_{60000};
  uint32_t last_poll_{0};
  uint32_t last_settings_poll_{0};
  uint32_t last_request_{0};
  uint32_t last_rx_{0};
  bool service_ready_{false};
  uint16_t write_handle_{0};
  uint16_t notify_handle_{0};
  esp_bd_addr_t remote_bda_{};  // captured from ESP_GATTC_OPEN_EVT - parent's copy is protected
  uint16_t conn_id_{0};         // captured from ESP_GATTC_OPEN_EVT - parent's copy is protected

  // Notifications are not guaranteed to carry a whole frame; if MTU negotiation
  // ever returns less than the frame size they arrive split, so reassemble.
  std::vector<uint8_t> rx_buf_;

  uint16_t regs_[REG_SOURCE_COUNT][P180_MAX_REGS]{};
  uint16_t reg_count_[REG_SOURCE_COUNT]{};
  bool have_baseline_[REG_SOURCE_COUNT]{};
  bool dump_pending_[REG_SOURCE_COUNT]{};

  bool debug_dump_{false};
  bool log_changes_{false};
  uint16_t change_threshold_{0};
  std::vector<uint16_t> ignored_registers_;

  std::vector<RegisterSensor> register_sensors_;
  std::vector<RegisterBitSensor> register_bit_sensors_;


  binary_sensor::BinarySensor *connected_binary_sensor_{nullptr};
  binary_sensor::BinarySensor *grid_power_binary_sensor_{nullptr};
};

// Probe buttons. Every action is a read; none of them writes to the station.
class P180Button : public button::Button, public Parented<P180Component> {
 public:
  void set_action(P180ButtonAction action) { this->action_ = action; }

 protected:
  void press_action() override;
  P180ButtonAction action_{P180_BUTTON_DUMP_INPUT};
};

}  // namespace p180
}  // namespace esphome
