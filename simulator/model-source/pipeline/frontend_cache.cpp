#include "frontend_cache.hpp"
#include <stdexcept>

namespace iris::frontend {
uint16_t ShuttleCache::next_lfsr(uint16_t s) {
  return uint16_t((s<<1)|(((s>>10)^(s>>12)^(s>>13)^(s>>15))&1));
}
void ShuttleCache::reset() {
  state_={};lfsr_=1;invalidated_=false;pending_.reset();
}
ShuttleCache::Probe ShuttleCache::probe(uint64_t address) const {
  if (address>>35) throw std::invalid_argument("SHUTTLE_ICACHE_PADDR_OUTSIDE_SOURCE_WIDTH");
  Probe p;p.set=(address>>6)&63;p.tag=address>>12;p.state=state_[p.set];
  for (const auto& way:p.state) p.hit|=way.valid && way.tag==p.tag;
  return p;
}
ShuttleCache::Transition ShuttleCache::clock(const Edge& edge) {
  if (edge.reset) {reset();return {};}
  if ((edge.accepted_refill && pending_) || (edge.accepted_refill && edge.data_beat))
    throw std::logic_error("SHUTTLE_ICACHE_OVERLAPPING_OR_ZERO_LATENCY_REFILL");
  if (edge.data_beat && !pending_) throw std::logic_error("SHUTTLE_ICACHE_DATA_WITHOUT_REQUEST");
  const bool was_active=bool(pending_),was_invalidated=invalidated_;
  Transition result;
  if (edge.accepted_refill) {
    const auto address=*edge.accepted_refill;auto p=probe(address);
    lfsr_=next_lfsr(lfsr_);
    // Source LFSR advances at A.fire; the later D beats use the CURRENT
    // output, so the selected way is this post-increment low bit.
    const auto way=unsigned(lfsr_&1);const auto old=state_[p.set][way];
    if (old.valid) result.selected_victim_line=(uint64_t(old.tag)<<12)|(uint64_t(p.set)<<6);
    pending_=Refill{address,p.set,way,0,p.tag};result.accepted=true;
  }
  if (edge.data_beat) {
    auto& request=*pending_;auto& way=state_[request.set][request.way];
    if (++request.beats>line_bytes/beat_bytes) throw std::logic_error("SHUTTLE_ICACHE_TOO_MANY_DATA_BEATS");
    const bool last=request.beats==line_bytes/beat_bytes;
    way.valid=last && !was_invalidated;
    if (last) {way.tag=request.tag;pending_.reset();result.completed=true;}
  }
  // Source vb_array invalidate assignment has priority over a concurrent
  // refill beat. Tag RAM may still receive the final tag, but it is invalid.
  if (edge.invalidate) for (auto& set:state_) for (auto& way:set) way.valid=false;
  // The later !refill_valid assignment uses OLD activity, including when a
  // new A request or the final D beat occurs on this same edge.
  invalidated_=was_active && (edge.invalidate || was_invalidated);
  return result;
}
}
