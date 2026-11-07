#pragma once
#include "iris_uart.hpp"
#include "iris_clocked_device.h"
#include <limits>

namespace iris_board {
class ClockedUart final : public abstract_device_t, public iris_clocked_device_t {
 public:
  struct Clock { uint64_t uart_hz, rtc_hz; };
  ClockedUart(UartRegisters::Parameters parameters, Clock clock)
      : registers_(parameters), clock_(clock) {
    if (!clock_.uart_hz || !clock_.rtc_hz)
      throw std::invalid_argument("IRIS_UART_CLOCK_UNKNOWN: explicit source clocks required");
  }
  UartRegisters& registers() { return registers_; }
  const UartRegisters& registers() const { return registers_; }
  uint64_t uart_cycles() const { return uart_cycles_; }
  bool live_clocked() const { return live_; }
  // Deterministic frame-boundary stimulus, not an invented RX pin sampler.
  void schedule_rx_frame(uint64_t start,uint64_t complete,uint8_t value) {
    if (live_ || functional_rtc_ || !start || complete<=start ||
        (!rx_frames_.empty() && start<=rx_frames_.back().complete))
      throw std::invalid_argument("IRIS_UART_RX_SCRIPT: ordered nonoverlapping future frame boundaries required");
    rx_frames_.push_back({start,complete,value});
  }
  bool load(reg_t addr, size_t bytes, uint8_t* data) override {
    return registers_.load(addr,bytes,data);
  }
  bool store(reg_t addr, size_t bytes, const uint8_t* data) override {
    return registers_.store(addr,bytes,data);
  }
  reg_t size() override { return registers_.size(); }
  void tick(reg_t rtc_ticks) override {
    if (live_) return; // The exact model hook has already advanced this interval.
    if (rtc_ticks > UINT64_MAX - functional_rtc_)
      throw std::overflow_error("IRIS_UART_CLOCK_OVERFLOW");
    functional_rtc_ += rtc_ticks;
    advance_to(scale(functional_rtc_,clock_.uart_hz,clock_.rtc_hz,false));
  }
  void iris_advance_model_clock(uint64_t before,uint64_t after,uint64_t base_hz) override {
    if (!base_hz || after < before || (live_ && (before != model_tick_ || base_hz != base_hz_)) ||
        (!live_ && (before || functional_rtc_)))
      throw std::runtime_error("IRIS_UART_CLOCK_DISCONTINUITY");
    live_=true;base_hz_=base_hz;model_tick_=after;
    advance_to(scale(after,clock_.uart_hz,base_hz,false));
  }
  std::optional<uint64_t> iris_next_model_event(uint64_t now,uint64_t base_hz) const override {
    if (!base_hz || (live_ && (base_hz != base_hz_ || now != model_tick_)))
      throw std::runtime_error("IRIS_UART_CLOCK_DISCONTINUITY");
    auto distance=registers_.next_tx_event_cycles();
    if (distance && *distance > UINT64_MAX-uart_cycles_)
      throw std::overflow_error("IRIS_UART_CLOCK_OVERFLOW");
    bool has_target=distance.has_value();
    uint64_t target=has_target ? uart_cycles_+*distance : 0;
    if (rx_index_<rx_frames_.size()) {
      uint64_t rx_edge=rx_started_ ? rx_frames_[rx_index_].complete : rx_frames_[rx_index_].start;
      if (!has_target || rx_edge<target) {target=rx_edge;has_target=true;}
    }
    if (!has_target) return std::nullopt;
    uint64_t edge=scale(target,base_hz,clock_.uart_hz,true);
    if (edge <= now) {
      if (now==UINT64_MAX) throw std::overflow_error("IRIS_UART_CLOCK_OVERFLOW");
      edge=now+1;
    }
    return edge;
  }
 private:
  static uint64_t scale(uint64_t ticks,uint64_t numerator,uint64_t denominator,bool ceil) {
    const __uint128_t product=__uint128_t(ticks)*numerator;
    const __uint128_t value=product/denominator+(ceil && product%denominator!=0);
    if (value > UINT64_MAX) throw std::overflow_error("IRIS_UART_CLOCK_OVERFLOW");
    return uint64_t(value);
  }
  void advance_to(uint64_t target) {
    if (target < uart_cycles_) throw std::runtime_error("IRIS_UART_CLOCK_REWIND");
    while (rx_index_<rx_frames_.size()) {
      const auto& frame=rx_frames_[rx_index_];
      uint64_t edge=rx_started_ ? frame.complete : frame.start;
      if (edge>target) break;
      registers_.advance_tx_cycles(edge-uart_cycles_);uart_cycles_=edge;
      if (!rx_started_) { rx_accepted_=registers_.begin_rx_frame();rx_started_=true; }
      else {
        if (rx_accepted_) registers_.complete_rx_frame(frame.value);
        rx_started_=false;rx_accepted_=false;++rx_index_;
      }
    }
    registers_.advance_tx_cycles(target-uart_cycles_);uart_cycles_=target;
  }
  UartRegisters registers_;
  Clock clock_;
  bool live_=false;
  uint64_t uart_cycles_=0,functional_rtc_=0,model_tick_=0,base_hz_=0;
  struct RxFrame {uint64_t start,complete;uint8_t value;};
  std::vector<RxFrame> rx_frames_;
  size_t rx_index_=0;
  bool rx_started_=false,rx_accepted_=false;
};
}
