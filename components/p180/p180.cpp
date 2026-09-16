#include "p180.h"
#include "esphome/core/log.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>

namespace esphome {
namespace p180 {

static const char *const TAG = "p180";

void P180Component::setup() { this->rx_buf_.reserve(P180_MAX_FRAME_LEN); }

void P180Component::dump_config() {
  ESP_LOGCONFIG(TAG, "AFERIY / BrightEMS P180 Battery:");
  ESP_LOGCONFIG(TAG, "  Status poll interval: %ums", static_cast<unsigned>(this->polling_interval_));
  if (this->settings_interval_ > 0) {
    ESP_LOGCONFIG(TAG, "  Settings poll interval: %ums", static_cast<unsigned>(this->settings_interval_));
  } else {
    ESP_LOGCONFIG(TAG, "  Settings poll: disabled");
  }
  ESP_LOGCONFIG(TAG, "  Full register dump: %s", YESNO(this->debug_dump_));
  ESP_LOGCONFIG(TAG, "  Changed-register logging: %s", YESNO(this->log_changes_));
  if (this->log_changes_) {
    ESP_LOGCONFIG(TAG, "    Change threshold: %u", static_cast<unsigned>(this->change_threshold_));
    ESP_LOGCONFIG(TAG, "    Ignored registers: %u", static_cast<unsigned>(this->ignored_registers_.size()));
  }
  ESP_LOGCONFIG(TAG, "  Register-bound sensors: %u", static_cast<unsigned>(this->register_sensors_.size()));
  ESP_LOGCONFIG(TAG, "  Register-bound binary sensors: %u",
                static_cast<unsigned>(this->register_bit_sensors_.size()));
  ESP_LOGCONFIG(TAG, "  Read-only: this component never issues Modbus writes (0x06)");
}

const char *P180Component::source_name_(RegSource source) {
  return source == REG_SOURCE_HOLDING ? "holding" : "input";
}

bool P180Component::ready_() {
  return this->parent_->state() == espbt::ClientState::ESTABLISHED && this->service_ready_;
}

void P180Component::loop() {
  if (!this->ready_()) {
    return;
  }
  const uint32_t now = millis();

  // Drop a partially assembled frame that never completed, so it can't corrupt
  // the next one.
  if (!this->rx_buf_.empty() && now - this->last_rx_ > P180_FRAME_TIMEOUT_MS) {
    ESP_LOGW(TAG, "Discarding %u-byte partial frame (no continuation within %ums)",
             static_cast<unsigned>(this->rx_buf_.size()), static_cast<unsigned>(P180_FRAME_TIMEOUT_MS));
    this->rx_buf_.clear();
  }

  // One request at a time - the station answers serially.
  if (now - this->last_request_ < P180_MIN_REQUEST_GAP_MS) {
    return;
  }

  if (now - this->last_poll_ >= this->polling_interval_) {
    this->last_poll_ = now;
    this->send_read_request_(P180_FUNC_READ_INPUT, 0, P180_INPUT_REG_COUNT);
  } else if (this->settings_interval_ > 0 && now - this->last_settings_poll_ >= this->settings_interval_) {
    this->last_settings_poll_ = now;
    this->send_read_request_(P180_FUNC_READ_HOLDING, 0, P180_HOLDING_REG_COUNT);
  }
}

void P180Component::gattc_event_handler(esp_gattc_cb_event_t event, esp_gatt_if_t gattc_if,
                                         esp_ble_gattc_cb_param_t *param) {
  switch (event) {
    case ESP_GATTC_OPEN_EVT: {
      if (param->open.status == ESP_GATT_OK) {
        memcpy(this->remote_bda_, param->open.remote_bda, sizeof(this->remote_bda_));
        this->conn_id_ = param->open.conn_id;
      }
      break;
    }

    case ESP_GATTC_CFG_MTU_EVT: {
      // Worth knowing: below ~211 bytes a status frame arrives split across
      // several notifications and relies on the reassembly in on_notify_().
      ESP_LOGI(TAG, "Negotiated MTU: %u (a full 100-register frame is %u bytes)",
               static_cast<unsigned>(param->cfg_mtu.mtu),
               static_cast<unsigned>(P180_HEADER_LEN + P180_INPUT_REG_COUNT * 2 + P180_CRC_LEN));
      break;
    }

    case ESP_GATTC_SEARCH_CMPL_EVT: {
      auto *write_char = this->parent_->get_characteristic(
          espbt::ESPBTUUID::from_uint16(P180_SERVICE_UUID_16),
          espbt::ESPBTUUID::from_uint16(P180_WRITE_CHAR_UUID_16));
      auto *notify_char = this->parent_->get_characteristic(
          espbt::ESPBTUUID::from_uint16(P180_SERVICE_UUID_16),
          espbt::ESPBTUUID::from_uint16(P180_NOTIFY_CHAR_UUID_16));

      if (write_char == nullptr || notify_char == nullptr) {
        ESP_LOGW(TAG,
                 "Could not find the expected BrightEMS service (a002) / characteristics "
                 "(c304/c305) on this device. Your P180 may expose different UUIDs - "
                 "connect with nRF Connect to check its actual GATT table.");
        break;
      }

      this->write_handle_ = write_char->handle;
      this->notify_handle_ = notify_char->handle;

      auto status = esp_ble_gattc_register_for_notify(this->parent_->get_gattc_if(), this->remote_bda_,
                                                        this->notify_handle_);
      if (status != ESP_OK) {
        ESP_LOGW(TAG, "esp_ble_gattc_register_for_notify failed, status=%d", status);
      }
      break;
    }

    case ESP_GATTC_REG_FOR_NOTIFY_EVT: {
      // Enable notifications on the peer by writing the CCCD (0x2902)
      auto *descr = this->parent_->get_descriptor(
          espbt::ESPBTUUID::from_uint16(P180_SERVICE_UUID_16),
          espbt::ESPBTUUID::from_uint16(P180_NOTIFY_CHAR_UUID_16),
          espbt::ESPBTUUID::from_uint16(ESP_GATT_UUID_CHAR_CLIENT_CONFIG));
      if (descr != nullptr) {
        uint8_t notify_en[2] = {0x01, 0x00};
        esp_ble_gattc_write_char_descr(this->parent_->get_gattc_if(), this->conn_id_, descr->handle,
                                        sizeof(notify_en), notify_en, ESP_GATT_WRITE_TYPE_RSP,
                                        ESP_GATT_AUTH_REQ_NONE);
      }
      this->service_ready_ = true;
      this->last_poll_ = 0;  // poll right away instead of waiting a full interval
      this->last_settings_poll_ = 0;
      ESP_LOGI(TAG, "Notifications enabled, starting polling");
      break;
    }

    case ESP_GATTC_NOTIFY_EVT: {
      if (param->notify.handle == this->notify_handle_) {
        this->on_notify_(param->notify.value, param->notify.value_len);
      }
      break;
    }

    case ESP_GATTC_DISCONNECT_EVT: {
      this->service_ready_ = false;
      this->write_handle_ = 0;
      this->notify_handle_ = 0;
      this->rx_buf_.clear();
      // Values across a reconnect aren't a meaningful "change" - start fresh.
      this->have_baseline_[REG_SOURCE_INPUT] = false;
      this->have_baseline_[REG_SOURCE_HOLDING] = false;
      if (this->connected_binary_sensor_ != nullptr)
        this->connected_binary_sensor_->publish_state(false);
      break;
    }

    default:
      break;
  }
}

void P180Component::send_read_request_(uint8_t func, uint16_t start, uint16_t count) {
  // Modbus RTU style request: 11 <func> <start_hi> <start_lo> <count_hi> <count_lo> <crc_hi> <crc_lo>
  uint8_t cmd[8] = {P180_SLAVE_ADDR,
                    func,
                    static_cast<uint8_t>(start >> 8),
                    static_cast<uint8_t>(start & 0xFF),
                    static_cast<uint8_t>(count >> 8),
                    static_cast<uint8_t>(count & 0xFF),
                    0x00,
                    0x00};
  const uint16_t crc = crc16_modbus_(cmd, 6);
  // This device sends/expects the CRC high byte first, which is the opposite of
  // stock Modbus RTU. Stock libraries fail silently against it.
  cmd[6] = (crc >> 8) & 0xFF;
  cmd[7] = crc & 0xFF;

  this->last_request_ = millis();
  esp_ble_gattc_write_char(this->parent_->get_gattc_if(), this->conn_id_, this->write_handle_, sizeof(cmd), cmd,
                            ESP_GATT_WRITE_TYPE_NO_RSP, ESP_GATT_AUTH_REQ_NONE);
}

uint16_t P180Component::crc16_modbus_(const uint8_t *data, uint16_t len) {
  uint16_t crc = 0xFFFF;
  for (uint16_t i = 0; i < len; i++) {
    crc ^= data[i];
    for (uint8_t j = 0; j < 8; j++) {
      if (crc & 0x0001) {
        crc = (crc >> 1) ^ 0xA001;
      } else {
        crc >>= 1;
      }
    }
  }
  return crc;
}

void P180Component::on_notify_(const uint8_t *data, uint16_t len) {
  if (len == 0) {
    return;
  }
  const uint32_t now = millis();
  if (!this->rx_buf_.empty() && now - this->last_rx_ > P180_FRAME_TIMEOUT_MS) {
    this->rx_buf_.clear();
  }
  this->last_rx_ = now;

  // Only start a frame on a plausible header, so a stray notification can't
  // poison the buffer and desync every frame after it.
  if (this->rx_buf_.empty() && data[0] != P180_SLAVE_ADDR) {
    ESP_LOGV(TAG, "Ignoring %u-byte notification starting with 0x%02X (expected 0x%02X)",
             static_cast<unsigned>(len), static_cast<unsigned>(data[0]), static_cast<unsigned>(P180_SLAVE_ADDR));
    return;
  }

  if (this->rx_buf_.size() + len > P180_MAX_FRAME_LEN) {
    ESP_LOGW(TAG, "Frame would exceed %u bytes, resetting reassembly buffer",
             static_cast<unsigned>(P180_MAX_FRAME_LEN));
    this->rx_buf_.clear();
    return;
  }
  this->rx_buf_.insert(this->rx_buf_.end(), data, data + len);

  // Identify the frame type as soon as the function code lands, before waiting
  // for a full header. A Modbus exception reply (function | 0x80) is only 5
  // bytes, so it never reaches the 6-byte header length - if it were left in
  // the buffer it would corrupt the next frame appended after it. The station
  // can legitimately send one if it rejects the 0x03 settings read.
  if (this->rx_buf_.size() < 2) {
    return;
  }

  const uint8_t func = this->rx_buf_[1];
  if (func != P180_FUNC_READ_INPUT && func != P180_FUNC_READ_HOLDING) {
    if (func & 0x80) {
      ESP_LOGD(TAG, "Station rejected function 0x%02X with a Modbus exception",
               static_cast<unsigned>(func & 0x7F));
    } else {
      ESP_LOGV(TAG, "Ignoring frame with unsupported function 0x%02X", static_cast<unsigned>(func));
    }
    this->rx_buf_.clear();
    return;
  }

  // The echoed 6-byte header carries the register count, so the total length
  // isn't known until it has arrived.
  if (this->rx_buf_.size() < P180_HEADER_LEN) {
    return;
  }

  const uint16_t count = (static_cast<uint16_t>(this->rx_buf_[4]) << 8) | this->rx_buf_[5];
  if (count == 0 || count > P180_MAX_REGS) {
    // Either a corrupt frame, or this firmware doesn't use the echoed
    // start+count header this component assumes. Log the header so the framing
    // can actually be diagnosed rather than leaving a silent dead end.
    ESP_LOGW(TAG,
             "Frame declares %u registers, outside 1..%u - discarding. "
             "Header: %02X %02X %02X %02X %02X %02X (expected addr func start_hi start_lo count_hi count_lo)",
             static_cast<unsigned>(count), static_cast<unsigned>(P180_MAX_REGS),
             static_cast<unsigned>(this->rx_buf_[0]), static_cast<unsigned>(this->rx_buf_[1]),
             static_cast<unsigned>(this->rx_buf_[2]), static_cast<unsigned>(this->rx_buf_[3]),
             static_cast<unsigned>(this->rx_buf_[4]), static_cast<unsigned>(this->rx_buf_[5]));
    this->rx_buf_.clear();
    return;
  }

  const uint16_t expected = P180_HEADER_LEN + count * 2 + P180_CRC_LEN;
  if (this->rx_buf_.size() < expected) {
    ESP_LOGV(TAG, "Frame incomplete: %u/%u bytes", static_cast<unsigned>(this->rx_buf_.size()),
             static_cast<unsigned>(expected));
    return;
  }
  if (this->rx_buf_.size() > expected) {
    ESP_LOGV(TAG, "Frame carries %u trailing bytes, ignoring them",
             static_cast<unsigned>(this->rx_buf_.size() - expected));
  }

  this->handle_frame_(this->rx_buf_.data(), expected, func, count);
  this->rx_buf_.clear();
}

void P180Component::handle_frame_(const uint8_t *frame, uint16_t len, uint8_t func, uint16_t count) {
  const uint16_t crc_calc = crc16_modbus_(frame, len - P180_CRC_LEN);
  const uint16_t crc_recv = (static_cast<uint16_t>(frame[len - 2]) << 8) | frame[len - 1];
  if (crc_calc != crc_recv) {
    ESP_LOGW(TAG, "CRC mismatch on %u-byte frame (calc=%04X recv=%04X), discarding", static_cast<unsigned>(len),
             static_cast<unsigned>(crc_calc), static_cast<unsigned>(crc_recv));
    return;
  }

  const RegSource source = (func == P180_FUNC_READ_INPUT) ? REG_SOURCE_INPUT : REG_SOURCE_HOLDING;

  // A probe that changes the register count invalidates the diff baseline -
  // otherwise the extra registers all read as "changed" against stale zeros.
  if (this->reg_count_[source] != count) {
    if (this->have_baseline_[source]) {
      ESP_LOGI(TAG, "%s register count changed %u -> %u, resetting diff baseline", source_name_(source),
               static_cast<unsigned>(this->reg_count_[source]), static_cast<unsigned>(count));
    }
    this->have_baseline_[source] = false;
    this->reg_count_[source] = count;
  }

  this->store_and_diff_(source, frame, count);

  if (this->debug_dump_ || this->dump_pending_[source]) {
    this->dump_pending_[source] = false;
    this->dump_registers_(source);
  }

  this->publish_(source);
}

bool P180Component::is_ignored_(uint16_t reg) const {
  return std::find(this->ignored_registers_.begin(), this->ignored_registers_.end(), reg) !=
         this->ignored_registers_.end();
}

void P180Component::store_and_diff_(RegSource source, const uint8_t *frame, uint16_t count) {
  uint16_t *regs = this->regs_[source];
  const bool diff = this->log_changes_ && this->have_baseline_[source];

  for (uint16_t i = 0; i < count; i++) {
    const uint16_t value =
        (static_cast<uint16_t>(frame[P180_HEADER_LEN + i * 2]) << 8) | frame[P180_HEADER_LEN + i * 2 + 1];
    if (diff && value != regs[i] && !this->is_ignored_(i)) {
      const uint16_t delta = value > regs[i] ? value - regs[i] : regs[i] - value;
      if (delta > this->change_threshold_) {
        ESP_LOGD(TAG, "%s reg %u: 0x%04X -> 0x%04X (%u -> %u)", source_name_(source), static_cast<unsigned>(i),
                 static_cast<unsigned>(regs[i]), static_cast<unsigned>(value), static_cast<unsigned>(regs[i]),
                 static_cast<unsigned>(value));
      }
    }
    regs[i] = value;
  }
  this->have_baseline_[source] = true;
}

void P180Component::dump_registers_(RegSource source) {
  const uint16_t count = this->reg_count_[source];
  const uint16_t *regs = this->regs_[source];
  if (count == 0) {
    ESP_LOGW(TAG, "No %s registers captured yet", source_name_(source));
    return;
  }

  // 16 registers per line keeps each line around 100 characters, well inside the
  // logger's default 512-byte tx buffer. The old single-line dump was both
  // truncated below the frame size and long enough to overrun that buffer.
  // Register-indexed (not byte-indexed) so values line up with the register map.
  char line[16 * 5 + 1];
  for (uint16_t start = 0; start < count; start += 16) {
    const uint16_t end = std::min<uint16_t>(start + 16, count);
    int pos = 0;
    for (uint16_t i = start; i < end && pos >= 0 && pos < static_cast<int>(sizeof(line)); i++) {
      pos += snprintf(line + pos, sizeof(line) - pos, "%04X ", regs[i]);
    }
    if (pos > 0) {
      line[pos - 1] = '\0';  // strip the trailing space
    }
    ESP_LOGD(TAG, "%s regs %03u-%03u: %s", source_name_(source), static_cast<unsigned>(start),
             static_cast<unsigned>(end - 1), line);
  }
}

void P180Component::publish_(RegSource source) {
  const uint16_t count = this->reg_count_[source];
  const uint16_t *regs = this->regs_[source];

  for (auto &entry : this->register_sensors_) {
    if (entry.source != source) {
      continue;
    }
    if (entry.reg >= count) {
      ESP_LOGV(TAG, "Sensor bound to %s reg %u, but only %u registers were returned", source_name_(source),
               static_cast<unsigned>(entry.reg), static_cast<unsigned>(count));
      continue;
    }
    entry.sensor->publish_state(regs[entry.reg] * entry.scale);
  }

  for (auto &entry : this->register_bit_sensors_) {
    if (entry.source != source || entry.reg >= count) {
      continue;
    }
    entry.sensor->publish_state((regs[entry.reg] & entry.mask) != 0);
  }

  // Derived entities are all driven by the live status table.
  if (source != REG_SOURCE_INPUT) {
    return;
  }

  if (this->connected_binary_sensor_ != nullptr) {
    this->connected_binary_sensor_->publish_state(true);
  }

  if (this->grid_power_binary_sensor_ != nullptr && P180_REG_AC_IN_FREQUENCY < count) {
    this->grid_power_binary_sensor_->publish_state(regs[P180_REG_AC_IN_FREQUENCY] > P180_GRID_PRESENT_THRESHOLD);
  }

  // "Remaining time" is computed rather than read. Register 75 looked plausible
  // but stayed fixed across captures while the app's own estimate moved. Note
  // that ~19 registers were invisible to those early captures because the old
  // hex dump was truncated below the frame size, so a genuine time-to-empty
  // register may still turn up - see the README's discovery notes.
  if (this->remaining_time_sensor_ != nullptr && P180_REG_BATTERY_PERCENT < count) {
    const float discharge_w = regs[P180_REG_BATTERY_DISCHARGE_POWER];
    if (discharge_w > 0.0f) {
      const float battery_pct = regs[P180_REG_BATTERY_PERCENT];
      const float minutes =
          (battery_pct / 100.0f * this->battery_capacity_wh_ * this->battery_efficiency_) / discharge_w * 60.0f;
      this->remaining_time_sensor_->publish_state(minutes);
    } else {
      // Not discharging (on AC passthrough, or idle) - "remaining time on
      // battery" isn't a meaningful number right now.
      this->remaining_time_sensor_->publish_state(NAN);
    }
  }
}

void P180Component::dump_input_registers() {
  if (!this->ready_()) {
    ESP_LOGW(TAG, "Not connected - cannot request input registers");
    return;
  }
  this->dump_pending_[REG_SOURCE_INPUT] = true;
  this->send_read_request_(P180_FUNC_READ_INPUT, 0, P180_INPUT_REG_COUNT);
}

void P180Component::dump_holding_registers() {
  if (!this->ready_()) {
    ESP_LOGW(TAG, "Not connected - cannot request holding registers");
    return;
  }
  this->dump_pending_[REG_SOURCE_HOLDING] = true;
  this->send_read_request_(P180_FUNC_READ_HOLDING, 0, P180_HOLDING_REG_COUNT);
}

void P180Component::probe_extended_registers() {
  if (!this->ready_()) {
    ESP_LOGW(TAG, "Not connected - cannot probe");
    return;
  }
  ESP_LOGI(TAG,
           "Probing %u input registers (the station volunteers %u). No reply, or a CRC "
           "failure, means the extended range isn't supported - polling continues normally.",
           static_cast<unsigned>(P180_MAX_REGS), static_cast<unsigned>(P180_INPUT_REG_COUNT));
  this->dump_pending_[REG_SOURCE_INPUT] = true;
  this->send_read_request_(P180_FUNC_READ_INPUT, 0, P180_MAX_REGS);
}

void P180Component::reset_baseline() {
  this->have_baseline_[REG_SOURCE_INPUT] = false;
  this->have_baseline_[REG_SOURCE_HOLDING] = false;
  ESP_LOGI(TAG, "Diff baseline cleared - the next frame becomes the new reference");
}

void P180Button::press_action() {
  switch (this->action_) {
    case P180_BUTTON_DUMP_INPUT:
      this->parent_->dump_input_registers();
      break;
    case P180_BUTTON_DUMP_HOLDING:
      this->parent_->dump_holding_registers();
      break;
    case P180_BUTTON_PROBE_EXTENDED:
      this->parent_->probe_extended_registers();
      break;
    case P180_BUTTON_RESET_BASELINE:
      this->parent_->reset_baseline();
      break;
  }
}

}  // namespace p180
}  // namespace esphome
