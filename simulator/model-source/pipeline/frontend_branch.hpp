#pragma once
#include <array>
#include <cstdint>
#include <stdexcept>

namespace iris::frontend {
// Saved Shuttle MEM branch/BTB/RAS control, Core.scala:690-748. EX ALU
// comparison and write-data are stage inputs, not guest effects executed early.
struct ShuttleBranch {
  struct Uop {
    bool valid=false,branch=false,jal=false,jalr=false,sfence=false;
    uint32_t expanded=0;
    uint64_t pc=0,wdata=0,predicted_pc=0;
    bool compressed=false,taken=false,predicted_valid=false,sfb_branch=false;
    unsigned ras_head=0;
  };
  struct Result {
    bool selected=false,taken=false,mispredict=false,short_forward_pair=false;
    bool predictor_update=false,redirect=false,ras_update=false;
    uint64_t next_pc=0,branch_pc=0,fetch_pc=0,ras_address=0;
    unsigned lane=0,ras_update_head=0,redirect_ras_head=0;
    // These two are MEM corrective decisions, not frontend predecode's
    // broader odd-rd / x1-or-x5 classification.
    bool core_call=false,core_return=false;
  };
  static bool compare(uint32_t expanded,uint64_t rs1,uint64_t rs2) {
    if((expanded&127)!=0x63)throw std::invalid_argument("SHUTTLE_BRANCH_COMPARE_NON_BRANCH");
    const bool signed_less=((rs1^rs2)>>63)?bool(rs1>>63):rs1<rs2;
    switch((expanded>>12)&7) {
      case 0:return rs1==rs2;
      case 1:return rs1!=rs2;
      case 4:return signed_less;
      case 5:return !signed_less;
      case 6:return rs1<rs2;
      case 7:return rs1>=rs2;
      default:throw std::invalid_argument("SHUTTLE_BRANCH_COMPARE_UNDECODED_FUNCT3");
    }
  }
  static Result resolve(const std::array<Uop,2>& packet) {
    Result out;unsigned selected=0;
    for(unsigned i=0;i<2;++i) {
      const auto& u=packet[i];
      if(u.valid && (u.branch || u.jal || u.jalr || u.sfence || u.predicted_valid)) {
        if(selected++)throw std::logic_error("SHUTTLE_MORE_THAN_ONE_MEM_BRANCH_OR_PREDICTED_NEXT");
        out.lane=i;out.selected=true;
      }
    }
    if(!out.selected)return out;
    const auto& u=packet[out.lane];
    if(u.pc>>40 || u.predicted_pc>>40 || u.ras_head>7)throw std::invalid_argument("SHUTTLE_MEM_BRANCH_SOURCE_WIDTH");
    const auto inst=u.expanded;
    const auto sext=[](uint64_t v,unsigned bits){return (v^(uint64_t(1)<<(bits-1)))-(uint64_t(1)<<(bits-1));};
    uint64_t offset=u.compressed?2:4;
    if(u.branch && u.taken)offset=sext(((inst>>31)<<12)|(((inst>>7)&1)<<11)|(((inst>>25)&63)<<5)|(((inst>>8)&15)<<1),13);
    else if(u.jal)offset=sext(((inst>>31)<<20)|(((inst>>12)&255)<<12)|(((inst>>20)&1)<<11)|(((inst>>21)&1023)<<1),21);
    constexpr auto mask40=(uint64_t(1)<<40)-1;
    uint64_t next=(u.pc+offset)&mask40;
    if(u.jalr || u.sfence) {
      // encodeVirtualAddress(a0=wdata,ea=wdata), vaddrBits=39,
      // vaddrBitsExtended=40. Noncanonical inputs keep a fault marker.
      const auto upper=u.wdata>>39;
      const bool canonical=upper==0 || upper==((uint64_t(1)<<25)-1);
      const bool bit39=canonical?bool((u.wdata>>39)&1):!bool((u.wdata>>38)&1);
      next=(uint64_t(bit39)<<39)|(u.wdata&((uint64_t(1)<<39)-1));
    }
    out.next_pc=next&~uint64_t(1);
    out.taken=(u.branch && u.taken)||u.jalr||u.jal;
    out.mispredict=(out.taken && (!u.predicted_valid || u.predicted_pc!=out.next_pc)) ||
      (((u.branch && !u.taken)||!(u.branch||u.jal||u.jalr)) && u.predicted_valid);
    out.short_forward_pair=packet[0].sfb_branch && packet[1].valid;
    out.predictor_update=!out.short_forward_pair;
    out.redirect=out.mispredict && !out.short_forward_pair;
    const unsigned rd=(inst>>7)&31,rs1=(inst>>15)&31;
    out.core_call=(u.jal||u.jalr) && rd==1;
    out.core_return=u.jalr && rs1==1 && rd==0;
    out.ras_update=out.core_call;
    const unsigned next_head=u.ras_head==5?0:(u.ras_head+1)&7;
    out.ras_update_head=next_head;out.ras_address=u.wdata&mask40;
    out.redirect_ras_head=out.core_call?next_head:out.core_return?(u.ras_head==0?5:(u.ras_head-1)&7):u.ras_head;
    out.branch_pc=(u.pc+(((u.pc>>1)&3)==3 && !u.compressed?2:0))&((uint64_t(1)<<39)-1);
    out.fetch_pc=out.branch_pc&~uint64_t(7);
    return out;
  }
};
}
