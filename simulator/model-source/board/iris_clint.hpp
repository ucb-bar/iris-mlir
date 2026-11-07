#pragma once

#include "abstract_device.h"
#include <functional>
#include <optional>
#include <stdexcept>
#include <vector>

namespace iris_board {
// Call advance_rtc() with modeled 500-kHz RTC ticks. No host clock or instruction
// count is consulted. Unknown mtimecmp state is visible rather than reset to zero.
class TimerRegisters : public abstract_device_t {
 public:
  using TimerLine = std::optional<bool>;
  using Interrupt = std::function<void(unsigned, bool, TimerLine)>;
  explicit TimerRegisters(unsigned harts = 3)
      : software_(harts, false), compare_(harts, 0), initialized_(harts, 0),
        last_software_(harts, false), last_timer_(harts) {
    if (!harts) throw std::invalid_argument("CLINT requires a nonzero hart count");
  }
  reg_t size() override { return 0x10000; }
  uint64_t mtime() const { return time_; }
  std::optional<uint64_t> compare(unsigned hart) const {
    return initialized_.at(hart) == 0xff ? std::optional<uint64_t>(compare_.at(hart)) : std::nullopt;
  }
  bool msip(unsigned hart) const { return software_.at(hart); }
  TimerLine mtip(unsigned hart) const {
    return initialized_.at(hart) == 0xff ? TimerLine(time_ >= compare_[hart]) : std::nullopt;
  }
  void on_interrupt(Interrupt callback) {
    callback_ = std::move(callback);
    if (!callback_) return;
    for (unsigned h = 0; h < software_.size(); ++h) {
      if (!callback_) break;
      last_software_[h] = software_[h];
      last_timer_[h] = mtip(h);
      auto copy = callback_;
      copy(h, software_[h], last_timer_[h]);
    }
  }
  void advance_rtc(uint64_t ticks) {
    time_ += ticks;  // Architectural 64-bit mtime wraps unsigned.
    update();
  }
  bool load(reg_t address, size_t bytes, uint8_t* data) override {
    if (!valid(address, bytes)) return false;
    for (size_t i = 0; i < bytes; ++i) {
      reg_t offset = address + i;
      if (offset < 0x4000) {
        unsigned hart = unsigned(offset / 4), byte = unsigned(offset % 4);
        data[i] = hart < software_.size() && byte == 0 ? software_[hart] : 0;
      } else if (offset < 0xbff8) {
        unsigned hart = unsigned((offset - 0x4000) / 8), byte = unsigned(offset % 8);
        if (hart < compare_.size()) {
          if (!(initialized_[hart] & (1u << byte)))
            throw std::runtime_error("Iris CLINT read of uninitialized mtimecmp byte");
          data[i] = uint8_t(compare_[hart] >> (byte * 8));
        } else data[i] = 0;
      } else if (offset < 0xc000) {
        data[i] = uint8_t(time_ >> (unsigned(offset % 8) * 8));
      } else data[i] = 0;
    }
    return true;
  }
  bool store(reg_t address, size_t bytes, const uint8_t* data) override {
    if (!valid(address, bytes)) return false;
    for (size_t i = 0; i < bytes; ++i) {
      reg_t offset = address + i;
      if (offset < 0x4000) {
        unsigned hart = unsigned(offset / 4), byte = unsigned(offset % 4);
        if (hart < software_.size() && byte == 0) software_[hart] = data[i] & 1;
      } else if (offset < 0xbff8) {
        unsigned hart = unsigned((offset - 0x4000) / 8), byte = unsigned(offset % 8);
        if (hart < compare_.size()) {
          unsigned shift = byte * 8;
          compare_[hart] = (compare_[hart] & ~(uint64_t(0xff) << shift)) | (uint64_t(data[i]) << shift);
          initialized_[hart] |= 1u << byte;
        }
      } else if (offset < 0xc000) {
        unsigned shift = unsigned(offset % 8) * 8;
        time_ = (time_ & ~(uint64_t(0xff) << shift)) | (uint64_t(data[i]) << shift);
      }
    }
    update();
    return true;
  }
 private:
  uint64_t time_ = 0;
  std::vector<bool> software_;
  std::vector<uint64_t> compare_;
  std::vector<uint8_t> initialized_;
  std::vector<bool> last_software_;
  std::vector<TimerLine> last_timer_;
  Interrupt callback_;
  static bool valid(reg_t address, size_t bytes) {
    return bytes && bytes <= 8 && !(bytes & (bytes - 1)) && address % bytes == 0 &&
           address < 0x10000 && bytes <= 0x10000 - address;
  }
  void update() {
    if (!callback_) return;
    for (unsigned hart = 0; hart < software_.size(); ++hart) {
      if (!callback_) break;
      auto timer = mtip(hart);
      if (last_software_[hart] != software_[hart] || last_timer_[hart] != timer) {
        last_software_[hart] = software_[hart];
        last_timer_[hart] = timer;
        auto copy = callback_;
        copy(hart, software_[hart], timer);
      }
    }
  }
};
}  // namespace iris_board
