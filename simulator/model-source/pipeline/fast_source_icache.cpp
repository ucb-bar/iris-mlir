#include "fast_source_icache.hpp"
#include <bit>
#include <stdexcept>

namespace iris::frontend {
ShuttleCachePipeline::Inputs FastSourceICache::control_inputs(const Inputs& in) {
  return {in.reset,in.request_valid,in.request_vaddr,in.s1_paddr,in.s1_kill,in.s2_kill,
          in.invalidate,in.memory_a_ready,bool(in.data_beat)};
}
FastSourceICache::Outputs FastSourceICache::evaluate(const Inputs& in) const {
  Outputs out;out.control=metadata_.outputs(control_inputs(in));
  if(s2_mask_) {
    uint64_t word=0;bool known=true;
    for(unsigned way=0;way<2;++way)if(*s2_mask_&(1u<<way)) {
      if(s2_data_[way])word|=*s2_data_[way];else known=false;
    }
    if(known)out.data_word=word; // A zero one-hot mask annihilates unknown RAM.
  }
  if(out.control.response_valid&&!out.data_word)
    throw std::logic_error("FAST_SOURCE_ICACHE_REQUIRED_DATA_UNDEFINED");
  return out;
}
void FastSourceICache::commit(const Inputs& in) {
  if(in.reset) {
    metadata_.clock(control_inputs(in));data_={};tag_data_={};sram_output_={};
    tag_output_={};s2_data_={};s2_mask_.reset();invalidated_=false;return;
  }
  const auto out=evaluate(in);const auto pending=metadata_.tags().pending();
  const bool done=in.data_beat&&pending&&pending->beats==7;
  auto next_read=sram_output_;auto next_tags=tag_output_;
  // Native-memory read outputs update only on a real S0 SRAM read.
  if(out.control.request_fire) {
    const unsigned row=(in.request_vaddr>>3)&511,set=(in.request_vaddr>>6)&63;
    for(unsigned way=0;way<2;++way) {
      next_read[way]=data_[way][row];
      if(!done)next_tags[way]=tag_data_[set][way];
    }
  }
  if(metadata_.state().s1_valid) {
    const auto probe=metadata_.tags().probe(in.s1_paddr);
    unsigned mask=0;bool known=true;
    for(unsigned way=0;way<2;++way)if(probe.state[way].valid) {
      if(!tag_output_[way])known=false;
      else if(*tag_output_[way]==probe.tag)mask|=1u<<way;
    }
    if(std::popcount(mask)>1)throw std::logic_error("FAST_SOURCE_ICACHE_MULTIPLE_TAG_HITS");
    s2_mask_=known?std::optional<unsigned>(mask):std::nullopt;
    s2_data_=sram_output_;
  }
  if(in.data_beat) {
    if(!pending)throw std::logic_error("FAST_SOURCE_ICACHE_D_WITHOUT_ACCEPTED_A");
    const unsigned row=pending->set*8+pending->beats;
    if(!invalidated_)data_[pending->way][row]=*in.data_beat;
    if(done)tag_data_[pending->set][pending->way]=pending->tag;
  }
  metadata_.clock(control_inputs(in));
  invalidated_=bool(pending)&&(in.invalidate||invalidated_);
  sram_output_=next_read;tag_output_=next_tags;
}
}
