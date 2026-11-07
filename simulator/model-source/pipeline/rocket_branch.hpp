#pragma once
#include <cstdint>
#include <stdexcept>
namespace iris::frontend {
// Source RocketCore.scala645–659,1101–1105,1124–1142. The next-PC
// comparator observes the actual younger EX PC, else the current IBuf PC.
struct RocketBranch {
  struct Input {
    uint32_t instruction=0;uint64_t pc=0,wdata=0;
    bool valid=false,exception=false,compressed=false,branch=false,jal=false,jalr=false,sfence=false,taken=false;
    bool ex_pc_valid=false,ibuf_valid=false,fetch_valid=false,take_pc_wb=false;
    uint64_t ex_pc=0,ibuf_pc=0;
  };
  struct Result {
    uint64_t next_pc=0,link=0,branch_pc=0,fetch_pc=0;
    bool wrong_npc=false,redirect=false,btb_update=false,btb_valid=false,bht_update=false;
    unsigned cfi_type=0;
  };
  static Result resolve(const Input& in) {
    constexpr uint64_t mask40=(uint64_t(1)<<40)-1,mask39=(uint64_t(1)<<39)-1;
    if(in.pc>>40 || in.ex_pc>>40 || in.ibuf_pc>>40)throw std::invalid_argument("ROCKET_MEM_SOURCE_PC_WIDTH");
    const auto sext=[](uint64_t value,unsigned bits){return (value^(uint64_t(1)<<(bits-1)))-(uint64_t(1)<<(bits-1));};
    const auto x=in.instruction;uint64_t offset=in.compressed?2:4;
    if(in.branch && in.taken)offset=sext(((x>>31)<<12)|(((x>>7)&1)<<11)|(((x>>25)&63)<<5)|(((x>>8)&15)<<1),13);
    else if(in.jal)offset=sext(((x>>31)<<20)|(((x>>12)&255)<<12)|(((x>>20)&1)<<11)|(((x>>21)&1023)<<1),21);
    const auto target=(in.pc+offset)&mask40;
    auto next=target;
    if(in.jalr || in.sfence){const auto upper=in.wdata>>39;const bool canonical=upper==0 || upper==((uint64_t(1)<<25)-1);
      next=(uint64_t(canonical?bool((in.wdata>>39)&1):!bool((in.wdata>>38)&1))<<39)|(in.wdata&mask39);}
    Result out;out.next_pc=next&~uint64_t(1);
    // With C present the JALR link is mem_br_target, sign extended from
    // source40-bit PC. It becomes available at MEM, not EX or COM.
    out.link=sext(target,40);
    out.wrong_npc=in.ex_pc_valid?out.next_pc!=in.ex_pc:!(in.ibuf_valid||in.fetch_valid)||out.next_pc!=in.ibuf_pc;
    out.redirect=in.valid&&!in.exception&&(out.wrong_npc||in.sfence)&&!in.take_pc_wb;
    const bool cfi=in.branch||in.jal||in.jalr,taken=(in.branch&&in.taken)||in.jal||in.jalr;
    out.btb_update=in.valid&&!in.take_pc_wb&&out.wrong_npc&&(!cfi||taken);out.btb_valid=cfi;
    out.bht_update=in.valid&&!in.take_pc_wb;
    const unsigned rd=(x>>7)&31,rs1=(x>>15)&31;
    out.cfi_type=(in.jal||in.jalr)&&(rd&1)?2:in.jalr&&(rs1==1||rs1==5)?3:in.jal||in.jalr?1:0;
    out.branch_pc=(in.pc+(in.compressed?0:2))&mask39;out.fetch_pc=out.branch_pc&~uint64_t(3);
    return out;
  }
};
}
