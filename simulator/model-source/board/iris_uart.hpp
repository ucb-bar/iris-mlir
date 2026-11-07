#pragma once

// Functional register/FIFO and UARTTx state for the audited 8-bit, no-parity,
// no-four-wire UART. Bus pipeline and UARTRx sampling remain adapter work.
#include "abstract_device.h"
#include <algorithm>
#include <array>
#include <cstdint>
#include <functional>
#include <optional>
#include <stdexcept>
#include <utility>
#include <vector>

namespace iris_board {
class UartRegisters : public abstract_device_t {
 public:
  using Interrupt = std::function<void(bool)>;
  using Transmitted = std::function<void(uint8_t)>;
  struct Parameters {
    unsigned tx_entries;
    unsigned rx_entries;
    uint16_t divisor_init;
    unsigned bus_beat_bytes;
  };
  explicit UartRegisters(Parameters parameters)
      : parameters_(parameters), tx_(parameters.tx_entries), rx_(parameters.rx_entries),
        divisor_(parameters.divisor_init) {
    tx_watermark_bits_ = watermark_bits(parameters.tx_entries);
    rx_watermark_bits_ = watermark_bits(parameters.rx_entries);
    if (!parameters.divisor_init || (parameters.bus_beat_bytes != 4 && parameters.bus_beat_bytes != 8))
      throw std::invalid_argument("UART requires explicit source divisor and bus beat width");
  }
  reg_t size() override { return 0x1000; }
  unsigned tx_count() const { return tx_.count; }
  unsigned rx_count() const { return rx_.count; }
  bool tx_enabled() const { return tx_enable_; }
  bool rx_enabled() const { return rx_enable_; }
  bool tx_busy() const { return tx_bits_remaining_ != 0; }
  bool tx_output() const { return tx_output_; }
  uint16_t divisor() const { return divisor_; }
  bool irq() const { return (pending() & interrupt_enable_) != 0; }
  uint32_t pending() const {
    return (tx_.count < tx_watermark_ ? 1u : 0u) |
           (rx_.count > rx_watermark_ ? 2u : 0u);
  }
  void on_interrupt(Interrupt callback) {
    interrupt_ = std::move(callback);
    delivered_ = irq();
    if (interrupt_) { auto copy = interrupt_; copy(delivered_); }
  }
  void on_transmitted(Transmitted callback) { transmitted_ = std::move(callback); }

  bool load(reg_t address, size_t bytes, uint8_t* data) override {
    if (!valid(address, bytes)) return false;
    std::array<uint8_t, 8> result{};
    const bool data_read = address <= 4 && 4 < address + bytes;
    // RX queue RAM is cmem, not reset storage. Do not silently invent its byte
    // when software reads the empty data field. The status byte is still known.
    if (data_read && !rx_.front())
      throw std::runtime_error("IRIS_UART_UNKNOWN_RX_DATA: uninitialized FIFO cell; read empty status byte separately");
    for (size_t i = 0; i < bytes; ++i) {
      const reg_t offset = address + i;
      if (offset == 3) result[i] = tx_.full() ? 0x80 : 0;
      if (offset == 4) result[i] = *rx_.front();
      if (offset == 7) result[i] = rx_.count ? 0 : 0x80;
      if (offset == 8) result[i] = uint8_t(tx_enable_ | (two_stop_bits_ << 1));
      if (offset == 0xc) result[i] = rx_enable_;
      if (offset == 0xa || offset == 0xb) result[i] = uint8_t(tx_watermark_ >> ((offset - 0xa) * 8));
      if (offset == 0xe || offset == 0xf) result[i] = uint8_t(rx_watermark_ >> ((offset - 0xe) * 8));
      if (offset == 0x10) result[i] = interrupt_enable_;
      if (offset == 0x14) result[i] = uint8_t(pending());
      if (offset == 0x18 || offset == 0x19) result[i] = uint8_t(divisor_ >> ((offset - 0x18) * 8));
    }
    if (data_read && rx_.count) rx_.pop();
    std::copy_n(result.data(), bytes, data);
    notify();
    return true;
  }

  bool store(reg_t address, size_t bytes, const uint8_t* data) override {
    if (!valid(address, bytes)) return false;
    auto touched = [&](reg_t byte) { return address <= byte && byte < address + bytes; };
    auto field = [&](reg_t start, unsigned width) -> std::optional<uint32_t> {
      const unsigned field_bytes = (width + 7) / 8;
      // RegMapper writes require every bit of the RegField to be strobed. A
      // nine-bit FireSim watermark therefore needs both of its bytes.
      if (address > start || start + field_bytes > address + bytes) return std::nullopt;
      uint32_t value = 0;
      for (unsigned byte=0; byte<field_bytes; ++byte)
        value |= uint32_t(data[start + byte - address]) << (byte * 8);
      return value & ((1u << width) - 1);
    };
    if (touched(0)) {
      const bool quash = touched(3) && (data[3 - address] & 0x80);
      if (!quash) tx_.push(data[0 - address]); // Full nonblocking writes drop.
    }
    if (touched(8)) {
      tx_enable_ = data[8 - address] & 1;
      two_stop_bits_ = (data[8 - address] & 2) != 0;
    }
    if (touched(0xc)) rx_enable_ = data[0xc - address] & 1;
    if (auto value = field(0xa, tx_watermark_bits_)) tx_watermark_ = *value;
    if (auto value = field(0xe, rx_watermark_bits_)) rx_watermark_ = *value;
    if (touched(0x10)) interrupt_enable_ = data[0x10 - address] & 3;
    if (auto value = field(0x18, 16)) divisor_ = uint16_t(*value);
    notify();
    return true;
  }

  // Advance UART TARGET/PERIPHERAL clock edges, never host time or retired
  // instructions. This matches the source UARTTx counter/prescaler/shifter.
  // The global adapter must explicitly map its clock domain before use.
  void advance_tx_cycles(uint64_t cycles) {
    while (cycles) {
      if (!tx_busy()) {
        if (!tx_enable_ || !tx_.count) return;
        current_tx_ = *tx_.front(); tx_.pop();
        tx_shifter_ = uint16_t(current_tx_) << 1;
        tx_bits_remaining_ = 10 + unsigned(two_stop_bits_);
        --cycles; notify(); // Enqueue-to-TX accept is its own target edge.
        continue;
      }
      const uint64_t until_pulse = uint64_t(tx_prescaler_) + 1;
      if (cycles < until_pulse) {
        tx_prescaler_ -= uint16_t(cycles); return;
      }
      cycles -= until_pulse;
      tx_prescaler_ = divisor_;
      tx_output_ = tx_shifter_ & 1;
      tx_shifter_ = uint16_t((tx_shifter_ >> 1) | 0x100);
      --tx_bits_remaining_;
      if (!tx_busy() && transmitted_) { auto copy = transmitted_; copy(current_tx_); }
    }
  }

  // Read-only scheduler query; original register/state behavior is unchanged.
  std::optional<uint64_t> next_tx_event_cycles() const {
    if (!tx_busy()) return tx_enable_ && tx_.count ? std::optional<uint64_t>(1) : std::nullopt;
    return uint64_t(tx_prescaler_) + 1 +
      uint64_t(tx_bits_remaining_ - 1) * (uint64_t(divisor_) + 1);
  }

  // RX decoding/sampling is intentionally separate. A frame that started
  // while enabled may finish after rxen clears, as in the source state machine.
  bool begin_rx_frame() {
    if (!rx_enable_ || rx_frame_active_) return false;
    rx_frame_active_ = true; return true;
  }
  bool complete_rx_frame(uint8_t value) {
    if (!rx_frame_active_) throw std::logic_error("UART RX completion without a started frame");
    rx_frame_active_ = false;
    const bool accepted = rx_.push(value); // Valid-only input drops at full.
    notify(); return accepted;
  }

 private:
  struct Fifo {
    explicit Fifo(unsigned entries) : cells(entries && entries <= 256 && !(entries & (entries - 1)) ? entries :
      throw std::invalid_argument("UART requires explicit power-of-two FIFO depth through 256")) {}
    std::vector<std::optional<uint8_t>> cells;
    unsigned head = 0, tail = 0, count = 0;
    bool full() const { return count == cells.size(); }
    std::optional<uint8_t> front() const { return cells.at(head); }
    bool push(uint8_t value) {
      if (full()) return false;
      cells[tail] = value; tail = (tail + 1) % cells.size(); ++count; return true;
    }
    void pop() {
      if (!count) throw std::logic_error("empty UART FIFO pop");
      head = (head + 1) % cells.size(); --count;
    }
  };
  static unsigned watermark_bits(unsigned entries) {
    if (!entries || entries > 256 || (entries & (entries - 1)))
      throw std::invalid_argument("UART requires explicit power-of-two FIFO depth through 256");
    unsigned bits = 1;
    while (entries >>= 1) ++bits;
    return bits;
  }
  bool valid(reg_t address, size_t bytes) const {
    return bytes && bytes <= parameters_.bus_beat_bytes && !(bytes & (bytes - 1)) &&
           address % bytes == 0 && address < 0x1000 && bytes <= 0x1000 - address;
  }
  void notify() {
    const bool value = irq();
    if (value != delivered_) {
      delivered_ = value;
      if (interrupt_) { auto copy = interrupt_; copy(value); }
    }
  }
  Parameters parameters_;
  Fifo tx_, rx_;
  unsigned tx_watermark_bits_, rx_watermark_bits_;
  uint16_t tx_watermark_ = 0, rx_watermark_ = 0;
  bool tx_enable_ = false, rx_enable_ = false, two_stop_bits_ = false;
  uint16_t divisor_;
  uint8_t interrupt_enable_ = 0;
  bool delivered_ = false, rx_frame_active_ = false;
  unsigned tx_bits_remaining_ = 0;
  uint16_t tx_prescaler_ = 0, tx_shifter_ = 0;
  uint8_t current_tx_ = 0;
  bool tx_output_ = true;
  Interrupt interrupt_;
  Transmitted transmitted_;
};
}  // namespace iris_board
