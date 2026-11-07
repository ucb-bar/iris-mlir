#pragma once
#include <array>
#include <cstdint>
#include <optional>

namespace iris::frontend {
// Saved ShuttleICache geometry; this contract is not the Rocket cache.
// Timing is external: accepted A requests and actual D data beats are inputs.
class ShuttleCache {
 public:
  static constexpr unsigned sets=64,ways=2,line_bytes=64,beat_bytes=8,tag_bits=23;
  struct Way {bool valid=false;uint32_t tag=0;};
  struct Probe {unsigned set=0;uint32_t tag=0;std::array<Way,2> state{};bool hit=false;};
  struct Edge {bool reset=false,invalidate=false,data_beat=false;std::optional<uint64_t> accepted_refill;};
  struct Refill {uint64_t address=0;unsigned set=0,way=0,beats=0;uint32_t tag=0;};
  struct Transition {bool accepted=false,completed=false;std::optional<uint64_t> selected_victim_line;};
  ShuttleCache() {reset();}
  void reset();
  Probe probe(uint64_t address) const;
  Transition clock(const Edge& edge);
  uint16_t replacement_state() const {return lfsr_;}
  const std::optional<Refill>& pending() const {return pending_;}
 private:
  std::array<std::array<Way,ways>,sets> state_{};
  uint16_t lfsr_=1;
  bool invalidated_=false;
  std::optional<Refill> pending_;
  static uint16_t next_lfsr(uint16_t value);
};
}
