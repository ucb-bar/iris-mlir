#include "frontend_cache_pipeline.hpp"
#include <stdexcept>

namespace iris::frontend {
std::optional<bool> ShuttleCachePipeline::s1_hit(const Inputs& in) const {
  const auto probe=tags_.probe(in.s1_paddr);
  bool unknown=false;
  for(unsigned way=0;way<2;++way) if(probe.state[way].valid) {
    if(!synchronous_tag_data_[way])unknown=true;
    else if(*synchronous_tag_data_[way]==probe.tag)return true;
  }
  if(unknown)return std::nullopt;
  return false;
}
ShuttleCachePipeline::Outputs ShuttleCachePipeline::outputs(const Inputs& in) const {
  Outputs o;o.request_ready=!in.memory_data_beat;
  o.request_fire=in.request_valid && o.request_ready;
  if(state_.s2_valid) {
    if(!state_.s2_hit)throw std::logic_error("SHUTTLE_ICACHE_UNKNOWN_REQUIRED_S2_HIT");
    o.response_valid=*state_.s2_hit;
    o.miss=!*state_.s2_hit && !in.s2_kill;
  }
  if(o.miss) {
    if(!state_.previous_can_request)throw std::logic_error("SHUTTLE_ICACHE_UNKNOWN_REQUEST_HISTORY");
    o.memory_request_valid=*state_.previous_can_request;
  }
  if(o.memory_request_valid) {
    if(!state_.refill_address)throw std::logic_error("SHUTTLE_ICACHE_UNKNOWN_REQUIRED_REFILL_ADDRESS");
    o.memory_request_address=*state_.refill_address & ~uint64_t(63);
  }
  o.memory_request_fire=o.memory_request_valid && in.memory_a_ready;
  return o;
}
ShuttleCachePipeline::Outputs ShuttleCachePipeline::clock(const Inputs& in) {
  // Source non-reset registers still evaluate during reset. Model reset
  // suppresses their unobservable randomized values until first defined use.
  if(in.reset) {
    state_={};state_.previous_can_request=true;
    tag_initialized_={};synchronous_tag_data_={};tags_.reset();
    return {};
  }
  const auto o=outputs(in);
  const bool can_request=!(o.miss || tags_.pending());
  const auto hit=s1_hit(in);
  const auto old_pending=tags_.pending();
  const auto accepted_address=state_.refill_address;
  const bool done=in.memory_data_beat && old_pending && old_pending->beats==7;
  auto next_tags=synchronous_tag_data_;
  // The single tag SRAM port reads synchronously at S0. D beats gate S0,
  // and final D writes the tag RAM. Valid bits are consulted later at S1.
  if(o.request_fire && !done) {
    const auto p=tags_.probe(in.request_vaddr & 4095);
    for(unsigned way=0;way<2;++way)
      next_tags[way]=tag_initialized_[p.set][way]?std::optional<uint32_t>(p.state[way].tag):std::nullopt;
  }
  if(state_.s1_valid && can_request)state_.refill_address=in.s1_paddr;
  state_.s2_valid=state_.s1_valid && !in.s1_kill;
  state_.s1_valid=o.request_fire;
  state_.s2_hit=hit;
  state_.previous_can_request=can_request;
  tags_.clock({false,in.invalidate,in.memory_data_beat,
               o.memory_request_fire?accepted_address:std::nullopt});
  if(done)tag_initialized_[old_pending->set][old_pending->way]=true;
  synchronous_tag_data_=next_tags;
  return o;
}
}
