#pragma once
#include "frontend_cache.hpp"
#include <array>
#include <optional>

namespace iris::frontend {
// Saved Shuttle ICache.scala:49-76,90-120,167-175,207-209.
// Only source interface inputs are supplied. No s2 hit, miss, request, or
// accepted refill output may be injected. TileLink timing is a peer input.
class ShuttleCachePipeline {
 public:
  struct Inputs {
    bool reset=false,request_valid=false;
    uint64_t request_vaddr=0,s1_paddr=0;
    bool s1_kill=false,s2_kill=false,invalidate=false;
    bool memory_a_ready=false,memory_data_beat=false;
  };
  struct Outputs {
    bool request_ready=false,response_valid=false,miss=false;
    bool memory_request_valid=false;
    std::optional<uint64_t> memory_request_address;
    bool request_fire=false,memory_request_fire=false;
  };
  struct State {
    bool s1_valid=false,s2_valid=false;
    // These source registers/SRAM outputs are not reset. Unknown bits are
    // never turned into architectural tag state or a timing decision.
    std::optional<bool> s2_hit,previous_can_request;
    std::optional<uint64_t> refill_address;
  };
  const State& state() const {return state_;}
  const ShuttleCache& tags() const {return tags_;}
  Outputs outputs(const Inputs& in) const;
  Outputs clock(const Inputs& in);
 private:
  State state_{};
  ShuttleCache tags_{};
  std::array<std::array<bool,2>,64> tag_initialized_{};
  std::array<std::optional<uint32_t>,2> synchronous_tag_data_{};
  std::optional<bool> s1_hit(const Inputs& in) const;
};
}
