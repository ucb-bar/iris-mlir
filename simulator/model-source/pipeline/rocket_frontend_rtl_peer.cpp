#include "rocket_frontend_rtl_peer.hpp"
#include "VIrisRocketFrontendPeer.h"
#include "verilated.h"
#include <stdexcept>
namespace iris::frontend {
struct SavedRocketFrontend::Impl {
  VerilatedContext context;std::unique_ptr<VIrisRocketFrontendPeer> dut;
  bool evaluated=false,request_fire=false,data_fire=false,outstanding=false;unsigned received_beats=0;
  Impl(uint64_t seed,unsigned reset_edges) {
    if(!seed || seed>uint64_t(INT32_MAX))throw std::invalid_argument("source Rocket frontend requires explicit nonzero31-bit unreset-state seed");
    if(reset_edges<2)throw std::invalid_argument("source Rocket frontend requires explicit reset initialization");
    Verilated::threadContextp(&context);context.randSeed(seed);context.randReset(2);context.threads(1);
    dut=std::make_unique<VIrisRocketFrontendPeer>(&context);
#define dut (*this->dut)
#include "rocket_zero_inputs.inc"
#undef dut
    dut->io_ptw_status_prv=3;dut->io_ptw_ptbr_mode=0;dut->reset=1;
    for(unsigned i=0;i<reset_edges;++i)edge();
    dut->reset=0;dut->eval();
  }
  void edge() {Verilated::threadContextp(&context);dut->clock=1;dut->eval();context.timeInc(1);dut->clock=0;dut->eval();context.timeInc(1);}
};
SavedRocketFrontend::SavedRocketFrontend(uint64_t seed,unsigned reset_edges):impl_(std::make_unique<Impl>(seed,reset_edges)) {}
SavedRocketFrontend::~SavedRocketFrontend()=default;
SavedRocketFrontend::SavedRocketFrontend(SavedRocketFrontend&&) noexcept=default;
SavedRocketFrontend& SavedRocketFrontend::operator=(SavedRocketFrontend&&) noexcept=default;
SavedRocketFrontend::Outputs SavedRocketFrontend::evaluate(const Inputs& in) {
  Verilated::threadContextp(&impl_->context);impl_->evaluated=false;
  if(in.redirect_pc>>40 || in.btb.pc>>39 || in.btb.branch_pc>>39 || in.btb.prediction_entry>31 || in.btb.cfi_type>3 || in.bht.pc>>39 || in.bht.history>255)
    throw std::invalid_argument("source Rocket frontend interface width exceeded");
  for(const auto& p:in.pmp)if(p.mode>3 || p.address>>33 || p.mask>>35)throw std::invalid_argument("source Rocket frontend PMP width exceeded");
  auto& d=*impl_->dut;
  d.auto_icache_master_out_a_ready=in.memory_request_ready;d.auto_icache_master_out_d_valid=bool(in.memory_data);
  d.auto_icache_master_out_d_bits_opcode=1;d.auto_icache_master_out_d_bits_size=6;
  d.auto_icache_master_out_d_bits_corrupt=in.memory_data && in.memory_data->corrupt;
  if(in.memory_data)d.auto_icache_master_out_d_bits_data=in.memory_data->value;
  d.cpu_id_ready=in.id_ready;d.io_cpu_might_request=in.might_request;d.io_cpu_progress=in.progress;
  d.io_cpu_req_valid=in.redirect_valid;d.io_cpu_req_bits_speculative=in.redirect_speculative;d.io_cpu_req_bits_pc=in.redirect_pc;
  d.io_cpu_flush_icache=in.invalidate_icache;
  d.io_cpu_btb_update_valid=in.btb.valid;d.io_cpu_btb_update_bits_prediction_entry=in.btb.prediction_entry;
  d.io_cpu_btb_update_bits_isValid=in.btb.is_valid;d.io_cpu_btb_update_bits_pc=in.btb.pc;
  d.io_cpu_btb_update_bits_br_pc=in.btb.branch_pc;d.io_cpu_btb_update_bits_cfiType=in.btb.cfi_type;
  d.io_cpu_bht_update_valid=in.bht.valid;d.io_cpu_bht_update_bits_prediction_history=in.bht.history;
  d.io_cpu_bht_update_bits_pc=in.bht.pc;d.io_cpu_bht_update_bits_branch=in.bht.branch;
  d.io_cpu_bht_update_bits_taken=in.bht.taken;d.io_cpu_bht_update_bits_mispredict=in.bht.mispredict;
  d.io_ptw_customCSRs_csrs_0_value=in.custom_csr;
#define PMP(n) {const auto& p=in.pmp[n];d.io_ptw_pmp_##n##_cfg_l=p.locked;d.io_ptw_pmp_##n##_cfg_a=p.mode;d.io_ptw_pmp_##n##_cfg_x=p.execute;d.io_ptw_pmp_##n##_cfg_w=p.write;d.io_ptw_pmp_##n##_cfg_r=p.read;d.io_ptw_pmp_##n##_addr=p.address;d.io_ptw_pmp_##n##_mask=p.mask;}
  PMP(0);PMP(1);PMP(2);PMP(3);PMP(4);PMP(5);PMP(6);PMP(7);
#undef PMP
  d.eval();
  if(in.memory_data && !impl_->outstanding)throw std::logic_error("source Rocket frontend data beat without accepted request");
  impl_->request_fire=d.auto_icache_master_out_a_valid && in.memory_request_ready;
  impl_->data_fire=bool(in.memory_data);
  if(impl_->request_fire && impl_->outstanding)throw std::logic_error("source Rocket frontend overlapped refill request");
  if(d.io_ptw_req_valid)throw std::runtime_error("source Rocket frontend VM walk outside M-mode peer");
  Outputs out;out.memory_request_valid=d.auto_icache_master_out_a_valid;out.memory_request_address=d.auto_icache_master_out_a_bits_address;
  out.fetch_valid=d.io_cpu_resp_valid;out.fetch_pc=d.io_cpu_resp_bits_pc;out.fetch_replay=d.io_cpu_resp_bits_replay;
  auto& u=out.id;u.valid=d.ibuf_inst_0_valid;u.compressed=d.ibuf_inst_0_bits_rvc;u.replay=d.ibuf_inst_0_bits_replay;
  u.pc=d.ibuf_pc;u.raw=d.ibuf_inst_0_bits_raw;u.expanded=d.ibuf_inst_0_bits_inst_bits;
  u.btb_entry=d.ibuf_btb_resp_entry;u.bht_history=d.ibuf_btb_resp_bht_history;
  u.first_page_fault=d.ibuf_inst_0_bits_xcpt0_pf_inst;u.first_guest_page_fault=d.ibuf_inst_0_bits_xcpt0_gf_inst;u.first_access_fault=d.ibuf_inst_0_bits_xcpt0_ae_inst;
  u.second_page_fault=d.ibuf_inst_0_bits_xcpt1_pf_inst;u.second_guest_page_fault=d.ibuf_inst_0_bits_xcpt1_gf_inst;u.second_access_fault=d.ibuf_inst_0_bits_xcpt1_ae_inst;
  impl_->evaluated=true;return out;
}
void SavedRocketFrontend::commit() {
  Verilated::threadContextp(&impl_->context);
  if(!impl_->evaluated)throw std::logic_error("source Rocket frontend edge without evaluated inputs");
  impl_->edge();
  if(impl_->request_fire){impl_->outstanding=true;impl_->received_beats=0;}
  if(impl_->data_fire && ++impl_->received_beats==8)impl_->outstanding=false;
  impl_->evaluated=false;
}
}
