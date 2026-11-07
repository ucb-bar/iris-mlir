#pragma once

// Functional register/gateway helper for the audited Iris PLIC configuration.
// Registered fan-in/interrupt pipeline and TileLink transport latency are NOT
// modeled here. The adapter schedules MMIO completions and gateway clock edges.
#include "abstract_device.h"
#include "abstract_interrupt_controller.h"
#include <algorithm>
#include <array>
#include <functional>
#include <initializer_list>
#include <optional>
#include <stdexcept>
#include <string>

namespace iris_board {
class PlicRegisters : public abstract_device_t, public abstract_interrupt_controller_t {
 public:
  using Line = std::optional<bool>;
  using Interrupt = std::function<void(unsigned context, unsigned hart, bool supervisor, Line)>;
  static constexpr unsigned contexts = 6;
  static constexpr reg_t base = 0xc000000;
  static constexpr reg_t aperture = 0x4000000;
  static constexpr reg_t enable_base(unsigned context) { return 0x2000 + context * 0x80; }
  static constexpr reg_t control_base(unsigned context) { return 0x200000 + context * 0x1000; }

  reg_t size() override { return aperture; }
  bool pending() const { return pending_; }
  bool in_flight() const { return in_flight_; }
  bool source_level() const { return level_; }

  // Input changes alone do not clock the LevelGateway registers.
  void set_source_level(bool level) { level_ = level; }
  void advance_gateway() {
    const bool old_pending = pending_, old_in_flight = in_flight_;
    if (level_ && !old_pending) in_flight_ = true;
    if (level_ && !old_in_flight) pending_ = true;
    notify();
  }
  // Compatibility with Spike's interrupt-controller interface: one externally
  // delivered level event is sampled as a functional gateway edge. A timing
  // adapter should instead schedule set_source_level()/advance_gateway().
  void set_interrupt_level(uint32_t source, int level) override {
    if (source != 1) throw std::out_of_range("Iris PLIC has only interrupt source 1");
    set_source_level(level != 0);
    advance_gateway();
  }

  Line irq(unsigned context) const {
    check_context(context);
    // Max priority is one, so threshold=1 masks every source. Preserve known
    // zeros even when an unrelated, unobservable RTL register is unknown.
    return conjunction({Line(pending_), priority_, enable_[context], negate(threshold_[context])});
  }
  bool require_irq(unsigned context) const {
    const auto line = irq(context);
    if (!line) throw std::runtime_error("Iris PLIC interrupt line depends on uninitialized registers, context " + std::to_string(context));
    return *line;
  }
  void on_interrupt(Interrupt callback) {
    callback_ = std::move(callback);
    if (!callback_) return;
    for (unsigned context = 0; context < contexts; ++context) {
      if (!callback_) break;
      delivered_[context] = irq(context);
      auto copy = callback_;
      copy(context, context / 2, context % 2 != 0, delivered_[context]);
    }
  }

  bool load(reg_t address, size_t bytes, uint8_t* data) override {
    if (!valid(address, bytes)) return false;
    // A read of ANY byte of a claim RegField fires its read side effect.
    // Validate every returned field before committing the transaction.
    std::optional<unsigned> claimed_context;
    unsigned claimed_id = 0;
    for (unsigned context = 0; context < contexts; ++context) {
      const reg_t claim = control_base(context) + 4;
      if (address < claim + 4 && claim < address + bytes) {
        claimed_context = context;
        claimed_id = claim_id(context);
      }
    }
    std::array<uint8_t, 8> result{};
    for (size_t i = 0; i < bytes; ++i) {
      const reg_t offset = address + i;
      if (offset == 4) result[i] = known(priority_, "priority");
      else if (offset == 0x1000) result[i] = pending_ ? 2 : 0;
      for (unsigned context = 0; context < contexts; ++context) {
        if (offset == enable_base(context)) result[i] = known(enable_[context], "enable", context) ? 2 : 0;
        if (offset == control_base(context)) result[i] = known(threshold_[context], "threshold", context);
        if (offset == control_base(context) + 4) result[i] = uint8_t(claimed_id);
      }
    }
    if (claimed_context && claimed_id) pending_ = false;
    std::copy_n(result.data(), bytes, data);
    notify();
    return true;
  }

  bool store(reg_t address, size_t bytes, const uint8_t* data) override {
    if (!valid(address, bytes)) return false;
    bool complete = false;
    // A 32-bit completion RegField only fires when ALL its bytes are written.
    // Its RTL destination width is one bit: values such as 3 alias source 1.
    for (unsigned context = 0; context < contexts; ++context) {
      const reg_t claim = control_base(context) + 4;
      if (address <= claim && claim + 4 <= address + bytes) {
        const bool source_one = data[claim - address] & 1;
        if (source_one) complete = known(enable_[context], "completion enable", context);
      }
    }
    // No state mutates before unknown-dependent completion is checked.
    for (size_t i = 0; i < bytes; ++i) {
      const reg_t offset = address + i;
      if (offset == 4) priority_ = (data[i] & 1) != 0;
      for (unsigned context = 0; context < contexts; ++context) {
        if (offset == enable_base(context)) enable_[context] = (data[i] & 2) != 0;
        if (offset == control_base(context)) threshold_[context] = (data[i] & 1) != 0;
      }
    }
    // Any enabled context may complete, regardless of which one claimed.
    if (complete) in_flight_ = false;
    notify();
    return true;
  }

 private:
  // Plic.scala uses Reg, not RegInit, for these one-bit registers.
  Line priority_;
  std::array<Line, contexts> enable_{};
  std::array<Line, contexts> threshold_{};
  bool level_ = false;
  bool pending_ = false;
  bool in_flight_ = false;
  std::array<Line, contexts> delivered_{};
  Interrupt callback_;

  static Line negate(Line value) { return value ? Line(!*value) : std::nullopt; }
  static Line conjunction(std::initializer_list<Line> values) {
    bool unknown = false;
    for (auto value : values) {
      if (value && !*value) return false;
      unknown |= !value.has_value();
    }
    return unknown ? std::nullopt : Line(true);
  }
  static bool known(Line value, const char* name, unsigned context = contexts) {
    if (!value) throw std::runtime_error(std::string("Iris PLIC read/effect depends on uninitialized ") + name +
                                         (context < contexts ? ", context " + std::to_string(context) : ""));
    return *value;
  }
  unsigned claim_id(unsigned context) const {
    // PLICFanIn selects by priority and enable/pending; threshold only masks
    // the interrupt line and does not participate in maxDevs/claim selection.
    return known(conjunction({Line(pending_), priority_, enable_[context]}), "claim selection", context) ? 1 : 0;
  }
  static void check_context(unsigned context) {
    if (context >= contexts) throw std::out_of_range("Iris PLIC context out of range");
  }
  static bool valid(reg_t address, size_t bytes) {
    return bytes && bytes <= 8 && !(bytes & (bytes - 1)) && address % bytes == 0 &&
           address < aperture && bytes <= aperture - address;
  }
  void notify() {
    if (!callback_) return;
    for (unsigned context = 0; context < contexts; ++context) {
      if (!callback_) break;
      const auto line = irq(context);
      if (line != delivered_[context]) {
        delivered_[context] = line;
        auto copy = callback_;
        copy(context, context / 2, context % 2 != 0, line);
      }
    }
  }
};
}  // namespace iris_board
