#include "fast_source_frontend.hpp"
#include "fast_source_icache.hpp"
#include "fast_shuttle_predictor.hpp"
#include "fast_source_rvc_table.hpp"
#include "frontend_parcels.hpp"
#include "frontend_queue.hpp"
#include <array>
#include <bit>
#include <optional>
#include <stdexcept>

namespace iris::frontend {
namespace {
constexpr uint64_t mask40=(1ULL<<40)-1,mask39=(1ULL<<39)-1,mask35=(1ULL<<35)-1;
bool within(uint64_t address,uint64_t base,uint64_t bytes) {return address>=base&&address-base<bytes;}
// Exact union after simplifying saved PMAChecker.io_resp_x and M-mode
// ITLB.deny_access_to_debug. Permission is not a claim of external RAM.
bool fetch_access_error(uint64_t address) {
  return !(within(address,0x3000,0x1000)||within(address,0x10000,0x10000)||
    within(address,0x70000000,0x20000)||within(address,0x70020000,0x20000)||
    within(address,0x70080000,0x80000)||within(address,0x80000000,0x100000000ULL)||
    within(address,0x580000000ULL,0x20000));
}
int branch_immediate(uint32_t x) {
  uint32_t v=((x>>31)<<12)|(((x>>7)&1)<<11)|(((x>>25)&63)<<5)|(((x>>8)&15)<<1);
  return int(v&4096?v|0xffffe000u:v);
}
unsigned rotate7(unsigned value,unsigned amount) {amount%=7;return((value<<amount)|(value>>(7-amount)))&127;}
unsigned onehot7(unsigned value) {return std::countr_zero(value);}
FetchParcels source_parcels(bool valid,uint64_t pc,uint64_t word,bool previous,uint16_t half,
                           const FastShuttlePredictor::Prediction& prediction,std::array<uint32_t,4>& expanded) {
  auto out=decode_parcels(false,pc,word,previous,half);
  out.call_mask=out.return_mask=out.npc_plus4_mask=0;
  bool redirected=false;
  for(unsigned i=0;i<4;++i) {
    expanded[i]=expand_source_rvc(out.raw[i]);out.control[i]=fetch_control(expanded[i]);
    bool fits=true;
    if(i==1)fits=previous||!((out.mask&1)&&((out.raw[0]&3)==3));
    if(i==2)fits=!((out.mask&2)&&((out.raw[1]&3)==3));
    if(i==3)fits=!(((out.mask&4)&&((out.raw[2]&3)==3))||((out.raw[3]&3)==3));
    if(valid&&i>=((pc>>1)&3)&&fits&&!redirected)out.mask|=1u<<i;
    if(out.control[i].call)out.call_mask|=1u<<i;
    if(out.control[i].ret)out.return_mask|=1u<<i;
    if((out.raw[i]&3)==3&&!(i==0&&previous))out.npc_plus4_mask|=1u<<i;
    const bool branch=out.control[i].branch&&prediction.valid&&prediction.index==i&&prediction.taken&&(prediction.value&1);
    if((out.mask&(1u<<i))&&(out.control[i].jump||out.control[i].jalr||branch)) {
      out.redirect=true;out.redirect_index=i;redirected=true;
    }
  }
  out.end_half_valid=!((out.mask&4)&&((out.raw[2]&3)==3))&&((out.end_half&3)==3);
  return out;
}
}

struct FastSourceFrontend::Impl {
  struct Stage1 {bool valid=false,replay=false;uint64_t pc=0,ppc=0;bool fault=false;unsigned ras_head=5;};
  struct Stage2 {bool valid=false,replay=false,fault=false;uint64_t pc=0,ppc=0,next_fetch=0,f1_target=0;
    unsigned ras_head=5;FastShuttlePredictor::Prediction prediction;};
  Stage1 s1;Stage2 s2;
  FastSourceICache cache;FastShuttlePredictor predictor;ShuttleFetchQueue queue;
  std::array<std::optional<Uop>,7> payload;
  std::array<std::optional<uint64_t>,6> ras;
  bool jump_to_reset=true,previous_half_valid=false,ras_pending=false,evaluated=false;
  uint16_t previous_half=0;unsigned ras_pending_index=0;uint64_t ras_pending_address=0,edges=0;
  Inputs input;Outputs output;
  struct Comb {
    FastSourceICache::Inputs cache;
    FastShuttlePredictor::Inputs predictor;
    FetchParcels parcels;
    bool f1_clear=false,clear=false,enq_valid=false,enq_ready=false,s0_valid=false,s0_replay=false;
    bool s1_fault=false,ras_activity=false,ras_write=false;
    uint64_t s0_pc=0,s1_ppc=0,f1_next=0,f1_target=0,f2_target=0,ras_address=0;
    unsigned s0_ras=0,s1_ras=0,ras_next=0,ras_index=0;
    std::array<Uop,4> incoming;
  } comb;

  Impl(uint64_t seed,unsigned reset_edges) {
    if(!seed||seed>uint64_t(INT32_MAX)||reset_edges<2)
      throw std::invalid_argument("FAST_SOURCE_FRONTEND_EXPLICIT_RESET_SEED_REQUIRED");
    // Seed names the reference's unreset exploration. This implementation
    // keeps required unreset payload unknown rather than silently zeroing it.
    for(unsigned i=0;i<reset_edges;++i) {
      FastSourceICache::Inputs ci;ci.reset=true;cache.commit(ci);
      FastShuttlePredictor::Inputs pi;pi.reset=true;predictor.commit(pi);
    }
    s1.pc=0x10000;s1.ras_head=5;
  }
  void validate(const Inputs& in) const {
    if(in.ready1&&!in.ready0)throw std::invalid_argument("FAST_SOURCE_FRONTEND_READY_NOT_PREFIX");
    if(in.redirect_pc>>40||in.redirect_ras_head>7||in.ras.head>7||in.ras.address>>40||
       in.btb.pc>>39||in.btb.target>>39||in.btb.branch_pc>>39||in.btb.prediction_entry>63||
       in.btb.cfi_type>3||in.bht.pc>>39||in.bht.history>255||in.bht.value>3)
      throw std::invalid_argument("FAST_SOURCE_FRONTEND_PORT_WIDTH_EXCEEDED");
  }
  Outputs evaluate(const Inputs& in) {
    evaluated=false;validate(in);input=in;comb={};auto& c=comb;
    c.s1_ppc=s1.replay?s1.ppc:(s1.pc&mask35);
    c.s1_fault=s1.replay?s1.fault:fetch_access_error(s1.pc);
    c.cache.s1_paddr=c.s1_ppc;c.cache.s2_kill=s2.valid&&s2.fault&&!s2.replay;
    c.cache.invalidate=in.invalidate_icache;c.cache.memory_a_ready=in.memory_request_ready;
    if(in.memory_data)c.cache.data_beat=in.memory_data->value;
    const auto cached=cache.evaluate(c.cache);
    const auto word=cached.data_word.value_or(0);
    std::array<uint32_t,4> expanded;
    c.parcels=source_parcels(s2.valid,s2.pc,word,previous_half_valid,previous_half,s2.prediction,expanded);
    std::array<bool,4> calls{},returns{};
    bool do_call=false,do_ret=false;
    for(unsigned i=0;i<4;++i) {
      const auto control=fetch_control(expanded[i]);calls[i]=control.call;returns[i]=control.ret;
      if(c.parcels.mask&(1u<<i)){do_call|=calls[i];do_ret|=returns[i];}
    }
    // Source flush is not gated by s2_valid. Preserve the actual source
    // instruction-length predicates even on a killed/idle F2 payload.
    const std::array<bool,4> parcel_valid={true,
      previous_half_valid||!((c.parcels.mask&1)&&((c.parcels.raw[0]&3)==3)),
      !((c.parcels.mask&2)&&((c.parcels.raw[1]&3)==3)),
      !(((c.parcels.mask&4)&&((c.parcels.raw[2]&3)==3))||((c.parcels.raw[3]&3)==3))};
    if(!cached.data_word&&s2.prediction.valid&&s2.prediction.index)
      throw std::logic_error("FAST_SOURCE_FRONTEND_UNRESET_PAYLOAD_CONTROLS_BTB_FLUSH");
    c.predictor.pc=(s1.pc&~uint64_t(7))&mask39;
    c.predictor.btb=in.btb;c.predictor.bht=in.bht;c.predictor.advance=s1.valid;
    c.predictor.flush=s2.prediction.valid&&!parcel_valid[s2.prediction.index];
    const auto predicted=predictor.evaluate(c.predictor);
    c.f1_next=((s1.pc&~uint64_t(7))+8)&mask40;
    c.f1_target=predicted.valid&&predicted.taken?
      (predicted.target|((predicted.target&(1ULL<<38))?1ULL<<39:0)):c.f1_next;
    c.enq_ready=queue.enqueue_ready(c.parcels.mask);
    c.enq_valid=s2.valid&&(cached.control.response_valid||s2.fault);
    c.ras_next=do_call?(s2.ras_head==5?0:(s2.ras_head+1)&7):
      do_ret?(s2.ras_head==0?5:(s2.ras_head-1)&7):s2.ras_head;
    c.ras_activity=(do_call||do_ret)&&s2.valid&&c.enq_ready&&cached.control.response_valid;
    c.ras_write=c.ras_activity&&do_call;c.ras_index=c.ras_next;
    c.ras_address=((s2.pc&~uint64_t(7))+2*c.parcels.redirect_index+
      ((c.parcels.npc_plus4_mask&(1u<<s2.prediction.index))?4:2))&mask40;
    c.f2_target=c.parcels.redirect?s2.f1_target:s2.next_fetch;
    if(c.parcels.redirect&&do_ret) {
      const auto target=ras[s2.ras_head<6?s2.ras_head:0];
      if(!target&&s2.valid&&cached.control.response_valid&&c.enq_ready&&!in.redirect_flush&&!jump_to_reset)
        throw std::logic_error("FAST_SOURCE_FRONTEND_REQUIRED_RAS_TARGET_UNINITIALIZED pc="+std::to_string(s2.pc)+" head="+std::to_string(s2.ras_head)+" edge="+std::to_string(edges));
      c.f2_target=target.value_or(0);
    }
    c.s1_ras=c.ras_activity?c.ras_next:s1.ras_head;
    if(s1.valid){c.s0_valid=true;c.s0_pc=c.f1_target;c.s0_ras=c.s1_ras;}
    if(s2.valid&&(!cached.control.response_valid||!c.enq_ready)) {
      c.s0_valid=!s2.fault||s2.replay||!c.enq_ready;
      c.s0_pc=s2.pc;c.s0_ras=s2.ras_head;c.s0_replay=cached.control.response_valid;c.f1_clear=true;
    } else if(s2.valid&&c.enq_ready) {
      if(!s1.valid||s1.pc!=c.f2_target) {
        c.f1_clear=true;c.s0_valid=!(s2.fault&&!s2.replay);c.s0_pc=c.f2_target;
        c.s0_ras=c.ras_next;c.s0_replay=false;
      }
    }
    if(in.redirect_flush) {
      c.clear=true;c.f1_clear=true;c.s0_valid=in.redirect_valid;c.s0_pc=in.redirect_pc;
      c.s0_ras=in.redirect_ras_head;c.s0_replay=false;
    }
    if(jump_to_reset){c.clear=true;c.f1_clear=true;c.s0_valid=true;c.s0_pc=0x10000;c.s0_ras=5;c.s0_replay=false;}
    c.cache.request_valid=c.s0_valid;c.cache.request_vaddr=c.s0_pc&mask39;c.cache.s1_kill=c.f1_clear;
    const auto cache_ports=cache.evaluate(c.cache);
    output={};output.memory_request_valid=cache_ports.control.memory_request_valid;
    output.memory_request_address=cache_ports.control.memory_request_address.value_or(0);
    for(unsigned lane=0;lane<2;++lane) {
      const unsigned index=onehot7(rotate7(queue.state().deq,lane));
      output.queue[lane].valid=queue.valid(lane);
      if(output.queue[lane].valid) {
        if(!payload[index])throw std::logic_error("FAST_SOURCE_FRONTEND_VALID_FIFO_PAYLOAD_UNKNOWN");
        output.queue[lane]=*payload[index];output.queue[lane].valid=true;
      }
    }
    if(c.enq_valid&&c.enq_ready&&!c.clear&&!cached.data_word)
      throw std::logic_error("FAST_SOURCE_FRONTEND_REQUIRED_FETCH_PAYLOAD_UNDEFINED");
    const unsigned last=c.parcels.mask?std::bit_width(unsigned(c.parcels.mask))-1:0;
    for(unsigned i=0;i<4;++i) {
      auto& u=c.incoming[i];u.valid=bool(c.parcels.mask&(1u<<i));u.pc=c.parcels.pc[i]&mask40;
      u.raw=c.parcels.raw[i];u.expanded=expanded[i];u.compressed=(u.raw&3)!=3;
      u.edge_instruction=i==0&&previous_half_valid;u.exception=s2.fault;u.exception_cause=1;
      u.prediction_valid=c.parcels.redirect&&i==last;u.predicted_pc=c.f2_target;
      u.btb_valid=s2.prediction.valid;u.btb_entry=s2.prediction.entry;
      u.bht_history=s2.prediction.history;u.bht_value=s2.prediction.value;u.ras_head=s2.ras_head;
      const unsigned funct3=(u.expanded>>12)&7;
      const bool branch=(u.expanded&127)==0x63&&(funct3==0||funct3==1||funct3>=4);
      u.small_forward_branch=branch&&branch_immediate(u.expanded)==(u.compressed?4:6)&&!c.parcels.redirect;
    }
    evaluated=true;return output;
  }
  void commit() {
    if(!evaluated)throw std::logic_error("FAST_SOURCE_FRONTEND_COMMIT_WITHOUT_EVALUATION");
    const auto& c=comb;const auto& in=input;const auto cached=cache.evaluate(c.cache);
    if(c.enq_valid&&c.enq_ready&&!c.clear) {
      unsigned pointer=queue.state().enq;
      for(unsigned parcel=0;parcel<4;++parcel)if(c.parcels.mask&(1u<<parcel)) {
        if(!(queue.state().valid&pointer))payload[onehot7(pointer)]=c.incoming[parcel];
        pointer=rotate7(pointer,1);
      }
    }
    queue.clock({false,c.clear,c.enq_valid,uint8_t(c.parcels.mask),in.ready0,in.ready1});
    if(in.ras.valid) {if(in.ras.head<6)ras[in.ras.head]=in.ras.address;}
    else if(ras_pending&&!in.redirect_valid) {if(ras_pending_index<6)ras[ras_pending_index]=ras_pending_address;}
    ras_pending=c.ras_write&&!in.redirect_valid;ras_pending_index=c.ras_index;ras_pending_address=c.ras_address;
    if(s2.valid&&cached.control.response_valid&&c.enq_ready) {
      previous_half=c.parcels.end_half;previous_half_valid=c.parcels.end_half_valid&&!c.parcels.redirect;
    }
    if(c.clear)previous_half_valid=false;
    Stage2 next2=s2;next2.valid=s1.valid&&!c.f1_clear;
    if(s1.valid) {
      next2.pc=s1.pc;next2.ppc=c.s1_ppc;next2.fault=c.s1_fault;next2.replay=s1.replay;
      next2.ras_head=c.s1_ras;next2.next_fetch=c.f1_next;next2.f1_target=c.f1_target;
      next2.prediction=predictor.evaluate(c.predictor);
    }
    Stage1 next1=s1;next1.valid=c.s0_valid;
    if(c.s0_valid){next1.pc=c.s0_pc&mask40;next1.ras_head=c.s0_ras;next1.replay=c.s0_replay;}
    if(c.s0_replay){next1.fault=s2.fault;next1.ppc=s2.ppc;}
    predictor.commit(c.predictor);cache.commit(c.cache);
    s1=next1;s2=next2;jump_to_reset=false;evaluated=false;++edges;
  }
};
FastSourceFrontend::FastSourceFrontend(uint64_t seed,unsigned reset_edges):impl_(std::make_unique<Impl>(seed,reset_edges)){}
FastSourceFrontend::~FastSourceFrontend()=default;
FastSourceFrontend::FastSourceFrontend(FastSourceFrontend&&) noexcept=default;
FastSourceFrontend& FastSourceFrontend::operator=(FastSourceFrontend&&) noexcept=default;
FastSourceFrontend::Outputs FastSourceFrontend::evaluate(const Inputs& in){return impl_->evaluate(in);}
void FastSourceFrontend::commit(){impl_->commit();}
}
