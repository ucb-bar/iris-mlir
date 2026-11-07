#pragma once
#include "scalar_l2_hit_service.hpp"

namespace iris::empirical {
// Independent ordinary scalar DRAM response surcharge. It changes neither the
// shared outer launch interval nor vector/atomic/store/instruction service.
class ScalarOuterResponseService {
 public:
  void configure(double coefficient,uint64_t horizon) {
    if(enabled_||!std::isfinite(coefficient)||coefficient<0||!horizon||coefficient>horizon)
      throw std::runtime_error("IRIS_SCALAR_OUTER_CONFIGURATION");
    enabled_=true;coefficient_=coefficient;horizon_=horizon;
  }
  uint64_t charge(const Delta&delta,L2ServiceActor actor) {
    if(!enabled_||actor!=L2ServiceActor::ScalarLoad||delta.path!=Path::DRAM)return 0;
    if(delta.l1_misses<delta.l2_misses||delta.l1_misses-delta.l2_misses!=delta.l2_hits)
      throw std::runtime_error("IRIS_SCALAR_OUTER_CLASSIFICATION");
    const double next=fraction_+coefficient_*delta.l2_misses;
    if(!std::isfinite(next)||next>horizon_)throw std::runtime_error("IRIS_SCALAR_OUTER_HORIZON");
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
