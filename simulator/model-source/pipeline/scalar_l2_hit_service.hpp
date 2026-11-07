#pragma once
// Optional effective service for modeled ordinary scalar DRAM loads only.
// This neither selects cache victims nor supplies functional memory values.
#include "../timing/empirical_cache.hpp"
#include <cmath>
#include <cstdint>
#include <limits>
#include <stdexcept>

namespace iris::empirical {
enum class L2ServiceActor { ScalarLoad, ScalarStore, VectorMemory, Atomic, Instruction, Other };
struct ScalarL2HitStats {
  uint64_t accepted_loads=0, l1_hits=0, l1_misses=0, l2_hits=0, l2_misses=0, charged_cycles=0;
};
class ScalarL2HitService {
 public:
  void configure(double coefficient,uint64_t horizon) {
    if(enabled_||!std::isfinite(coefficient)||coefficient<0||!horizon||coefficient>horizon)
      throw std::runtime_error("IRIS_SCALAR_L2_HIT_CONFIGURATION");
    enabled_=true;coefficient_=coefficient;horizon_=horizon;
  }
  uint64_t charge(const Delta&delta,L2ServiceActor actor) {
    if(!enabled_||actor!=L2ServiceActor::ScalarLoad||delta.path!=Path::DRAM)return 0;
    // In this model each ordinary D miss performs exactly one shared-L2 probe.
    // Fail rather than substituting this algebra for a changed cache model.
    if(delta.l1_misses<delta.l2_misses||delta.l1_misses-delta.l2_misses!=delta.l2_hits)
      throw std::runtime_error("IRIS_SCALAR_L2_HIT_CLASSIFICATION");
    const double next=fraction_+coefficient_*delta.l2_hits;
    if(!std::isfinite(next)||next>horizon_)
      throw std::runtime_error("IRIS_SCALAR_L2_HIT_HORIZON");
    const auto cycles=uint64_t(std::floor(next));fraction_=next-cycles;
    ++stats_.accepted_loads;stats_.l1_hits+=delta.l1_hits;stats_.l1_misses+=delta.l1_misses;
    stats_.l2_hits+=delta.l2_hits;stats_.l2_misses+=delta.l2_misses;stats_.charged_cycles+=cycles;
    return cycles;
  }
  const ScalarL2HitStats&stats()const{return stats_;}
  bool enabled()const{return enabled_;}
 private:
  bool enabled_=false;double coefficient_=0,fraction_=0;uint64_t horizon_=0;
  ScalarL2HitStats stats_{};
};
}
