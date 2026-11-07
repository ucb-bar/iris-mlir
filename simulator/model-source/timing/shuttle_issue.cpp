#include "shuttle_issue.hpp"
#include <algorithm>
#include <stdexcept>

namespace iris::shuttle {
namespace {
Signal bit(bool value) { return value ? Signal::yes : Signal::no; }
Signal any(std::initializer_list<Signal> values) {
  Signal result=Signal::no;
  for (auto value:values) result=either(result,value);
  return result;
}
struct DecodeRow { uint32_t mask, match, controls; const char* name; };
#include "shuttle_decode_rows.inc"
int32_t branch_immediate(uint32_t instruction) {
  const uint32_t value=((instruction>>31)<<12)|(((instruction>>7)&1)<<11)|
    (((instruction>>25)&63)<<5)|(((instruction>>8)&15)<<1);
  return int32_t(value<<19)>>19;
}
}

Signal both(Signal a, Signal b) {
  if (a==Signal::no || b==Signal::no) return Signal::no;
  return a==Signal::yes && b==Signal::yes ? Signal::yes : Signal::unknown;
}
Signal either(Signal a, Signal b) {
  if (a==Signal::yes || b==Signal::yes) return Signal::yes;
  return a==Signal::no && b==Signal::no ? Signal::no : Signal::unknown;
}
Signal inverse(Signal a) { return a==Signal::unknown ? a : a==Signal::yes ? Signal::no : Signal::yes; }

FrontendSignals FrontendSignals::sequential_fixture() { return {Signal::no,Signal::no,false}; }
bool Uop::uses_alu() const {
  return has(WXD) && !(controls & (MEM|DIV|MUL|CSR_EN|FP|ROCC|JALR|VECTOR));
}
Uop decode_scalar(uint32_t expanded, uint32_t raw, FrontendSignals frontend) {
  Uop uop;
  uop.expanded=expanded; uop.raw=raw;
  uop.rs1=(expanded>>15)&31; uop.rs2=(expanded>>20)&31; uop.rd=(expanded>>7)&31;
  uop.compressed=(raw&3)!=3; uop.exception=frontend.exception;
  uop.predicted_next_pc_valid=frontend.predicted_next_pc_valid;
  if ((expanded&3)!=3) { uop.decode_note="RVC expansion is required"; return uop; }
  for (const auto& row:decode_rows) {
    if ((expanded&row.mask)!=row.match) continue;
    uop.controls=row.controls; uop.instruction_name=row.name;
    if (frontend.vector_decoder_legal!=Signal::no) {
      uop.decode_note="Saturn override must be resolved before using scalar control";
      return uop;
    }
    uop.control_verified=true;
    uop.sfb_branch=both(bit(uop.has(BRANCH) && branch_immediate(expanded)==(uop.compressed?4:6)),
                         inverse(frontend.predicted_next_pc_valid));
    return uop;
  }
  uop.decode_note="No enabled scalar control template; requires final RTL-equivalent decode";
  return uop;
}
size_t scalar_decode_rows() { return sizeof(decode_rows)/sizeof(decode_rows[0]); }
bool integer_hazard(uint8_t source, bool scoreboard_ready, const std::vector<Bypass>& bypasses) {
  if (source>31) throw std::invalid_argument("invalid integer register");
  bool ready=scoreboard_ready;
  for (const auto& bypass:bypasses) {
    if (bypass.destination>31) throw std::invalid_argument("invalid bypass register");
    if (bypass.valid && bypass.destination==source) ready=bypass.can_bypass;
  }
  return !ready;
}

IssueSignals IssueSignals::empty_pipeline_fixture() {
  IssueSignals state;
  state.integer_hazard.fill(Signal::no);
  state.ex_stall=state.csr_stall=state.trace_stall=state.single_step=Signal::no;
  state.ex_busy=state.mem_busy=state.com_busy=Signal::no;
  state.integer_scoreboard_busy=state.fp_scoreboard_busy=Signal::no;
  state.dmem_ordered=Signal::yes; state.rocc_busy=Signal::no;
  state.vector_backend_busy=state.vector_trap_busy=Signal::no;
  state.rocc_command_ready=Signal::yes; state.vconfig_inflight=Signal::no;
  state.mem_lane1_will_be_latealu=Signal::no;
  return state;
}

PacketPlan select_rrd(const std::vector<Uop>& packet, const IssueSignals& s) {
  if (packet.size()>2) throw std::invalid_argument("Iris Shuttle has two issue lanes");
  PacketPlan result;
  Signal older_stall=Signal::no, found_memory=Signal::no, found_cfg=Signal::no, found_branch=Signal::no;
  for (size_t i=0;i<packet.size();++i) {
    const auto& u=packet[i];
    LanePlan lane;
    if (!u.control_verified) {
      lane.missing_inputs.push_back("final_decode: "+u.decode_note);
      result.lanes.push_back(lane); older_stall=Signal::unknown;
      continue;
    }
    if (u.rs1>31 || u.rs2>31 || u.rd>31) throw std::invalid_argument("invalid uop register");
    const auto& p0=packet[0];
    const bool previous_writes=i>0 && p0.has(WXD);
    // These source field comparisons intentionally are NOT qualified by rxs1/2.
    // The actual hardware can retime an unused encoded source field as well.
    const bool rs1_hit=previous_writes && p0.rd==u.rs1;
    const bool rs2_hit=previous_writes && p0.rd==u.rs2;
    const bool consumer=u.uses_alu() && !u.cfi() && !u.has(SETS_VCFG);
    const bool can_x=previous_writes && p0.uses_alu() && consumer;
    const bool can_w=previous_writes && p0.has(MEM) && consumer;
    const Signal x1=both(bit(can_x && rs1_hit && u.rs1!=0),inverse(s.mem_lane1_will_be_latealu));
    const Signal x2=both(bit(can_x && rs2_hit && u.rs2!=0),inverse(s.mem_lane1_will_be_latealu));
    const Signal w1=bit(can_w && rs1_hit && u.rs1!=0), w2=bit(can_w && rs2_hit && u.rs2!=0);
    const Signal same1=both(bit(rs1_hit),inverse(either(x1,w1)));
    const Signal same2=both(bit(rs2_hit),inverse(either(x2,w2)));
    const Signal shadow=i==1 ? p0.sfb_branch : Signal::no;
    lane.sfb_shadow_known=shadow; lane.sfb_shadow=shadow==Signal::yes;
    lane.uses_memalu=both(either(x1,x2),inverse(shadow));
    lane.uses_latealu=both(either(w1,w2),inverse(shadow));
    const Signal same_rd=both(bit(previous_writes && p0.rd==u.rd && !p0.uses_alu()),inverse(lane.uses_latealu));
    const Signal hazard1=both(bit(u.has(RXS1) && u.rs1!=0),either(s.integer_hazard[u.rs1],same1));
    const Signal hazard2=both(bit(u.has(RXS2) && u.rs2!=0),either(s.integer_hazard[u.rs2],same2));
    const Signal hazard_rd=both(bit(u.has(WXD) && u.rd!=0),either(s.integer_hazard[u.rd],same_rd));
    const Signal busy=any({s.ex_busy,s.mem_busy,s.com_busy,s.integer_scoreboard_busy,s.fp_scoreboard_busy,
                          inverse(s.dmem_ordered),s.rocc_busy,s.vector_backend_busy,s.vector_trap_busy});
    const bool amo_fence=u.has(AMO) && ((u.expanded>>25)&3)!=0;
    const bool draining=u.has(SYSTEM)||u.has(FENCE)||u.has(SFENCE)||u.has(FENCE_I)||amo_fence;
    const Signal pipe0=any({bit(u.has(SYSTEM)||u.has(VECTOR)||u.has(FENCE)||u.has(AMO)||u.has(FENCE_I)||
        u.has(CSR_EN)||u.has(SFENCE)||u.has(CSR_ANY)||u.exception||u.has(MUL)||u.has(DIV)||u.has(ROCC)||u.has(USES_FP)),
        u.sfb_branch,both(shadow,bit(!u.uses_alu()||u.cfi()||!u.compressed))});
    const Signal branch=either(bit(u.cfi()||u.has(SFENCE)),u.predicted_next_pc_valid);
    Signal stalled=Signal::no;
    auto gate=[&](Signal value,const char* label) {
      stalled=either(stalled,value);
      if (value==Signal::yes) lane.reasons.emplace_back(label);
      if (value==Signal::unknown) lane.missing_inputs.emplace_back(label);
    };
    gate(hazard1,"rrd.integer.rs1_hazard"); gate(hazard2,"rrd.integer.rs2_hazard"); gate(hazard_rd,"rrd.integer.waw_hazard");
    gate(both(bit(i==0 && draining),busy),"rrd.drain_required");
    gate(both(bit(i==0 && u.has(ROCC)),inverse(s.rocc_command_ready)),"rrd.rocc_not_ready");
    gate(both(bit(i!=0),pipe0),"rrd.pipe0_only");
    gate(both(found_memory,bit(u.has(MEM))),"rrd.one_memory_per_packet");
    gate(both(found_cfg,bit(u.has(SETS_VCFG))),"rrd.one_vconfig_per_packet");
    gate(both(s.vconfig_inflight,bit(u.has(VECTOR)||u.has(SETS_VCFG))),"rrd.vconfig_inflight");
    gate(both(found_branch,branch),"rrd.one_branch_port_per_packet");
    gate(older_stall,"rrd.older_blocked_or_youngest");
    gate(s.csr_stall,"rrd.csr_stall"); gate(s.ex_stall,"rrd.ex_stall"); gate(s.trace_stall,"rrd.trace_stall");
    gate(both(s.single_step,any({s.ex_busy,s.mem_busy,s.com_busy})),"rrd.single_step_drain");
    gate(both(s.single_step,bit(i!=0)),"rrd.single_step_lane0");
    lane.availability=stalled==Signal::yes ? Availability::blocked :
                       stalled==Signal::unknown ? Availability::needs_input : Availability::ready;
    if (u.has(WXD)) {
      if (lane.uses_latealu==Signal::yes) lane.integer_result_stage=ResultStage::wb;
      else if (lane.uses_memalu==Signal::yes) lane.integer_result_stage=ResultStage::mem;
      else if (lane.uses_latealu==Signal::unknown || lane.uses_memalu==Signal::unknown) lane.integer_result_stage=ResultStage::unresolved;
      else lane.integer_result_stage=u.uses_alu() ? ResultStage::ex : ResultStage::unresolved;
    }
    result.lanes.push_back(lane);
    older_stall=either(stalled,bit(u.exception||u.has(CSR_EN)||u.has(SYSTEM)));
    found_memory=either(found_memory,bit(u.has(MEM)));
    found_cfg=either(found_cfg,bit(u.has(SETS_VCFG)));
    found_branch=either(found_branch,branch);
  }
  for (const auto& lane:result.lanes) {
    if (lane.availability!=Availability::ready) break;
    ++result.ready_prefix;
  }
  return result;
}

bool pure_integer_bundle(const std::vector<Uop>& packet,const PacketPlan& plan) {
  if (packet.size()!=2 || plan.ready_prefix!=2 || plan.lanes.size()!=2) return false;
  for (size_t i=0;i<packet.size();++i) {
    const auto& u=packet[i];
    if (!u.control_verified || !u.uses_alu() || u.cfi() ||
        (u.controls & (SETS_VCFG|CSR_ANY|SYSTEM|FENCE|FENCE_I|AMO)) || u.exception ||
        plan.lanes[i].integer_result_stage==ResultStage::unresolved ||
        u.predicted_next_pc_valid!=Signal::no || u.sfb_branch!=Signal::no || plan.lanes[i].sfb_shadow_known!=Signal::no)
      return false;
  }
  return true;
}
unsigned maximum_issue_width(unsigned hart) {
  if (hart>2) throw std::invalid_argument("Iris has architectural harts0,1,2");
  return hart<2 ? 2 : 1;
}
const char* stage_name(ResultStage stage) {
  switch(stage) {
    case ResultStage::none:return "none"; case ResultStage::ex:return "EX";
    case ResultStage::mem:return "MEM"; case ResultStage::wb:return "WB";
    case ResultStage::unresolved:return "unresolved";
  }
  throw std::invalid_argument("invalid result stage");
}
}  // namespace iris::shuttle
