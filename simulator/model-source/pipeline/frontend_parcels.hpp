#pragma once
#include <array>
#include <cstdint>
#include <stdexcept>

namespace iris::frontend {
// Timing predecode only, exactly the opcode/register bits read by pinned
// Frontend.scala:261-265. This does not decide architectural legality or
// execute a branch. In particular JALR funct3/reserved bits are not masked
// into a fabricated whole-instruction match.
struct FetchControl {
  bool jump=false,jalr=false,branch=false,call=false,ret=false;
  bool operator==(const FetchControl&) const=default;
};
inline FetchControl fetch_control(uint32_t raw) {
  unsigned opcode=raw&127,rd=(raw>>7)&31,rs1=(raw>>15)&31;
  if((raw&3)!=3) {
    const unsigned q=raw&3,f=(raw>>13)&7;
    opcode=0;rd=0;rs1=0;
    if(q==1 && f==5)opcode=0x6f;
    if(q==1 && (f==6 || f==7))opcode=0x63;
    if(q==2 && f==4 && ((raw>>2)&31)==0 && ((raw>>7)&31)!=0) {
      opcode=0x67;rd=(raw>>12)&1;rs1=(raw>>7)&31;
    }
  }
  FetchControl result;
  result.jump=opcode==0x6f;result.jalr=opcode==0x67;result.branch=opcode==0x63;
  result.call=(result.jump || result.jalr) && (rd&1);
  result.ret=result.jalr && !(rd&1) && (rs1==1 || rs1==5);
  return result;
}
struct FetchParcels {
  std::array<uint32_t,4> raw{};
  std::array<uint64_t,4> pc{};
  std::array<FetchControl,4> control{};
  uint8_t mask=0,call_mask=0,return_mask=0,npc_plus4_mask=0;
  bool edge_instruction=false,end_half_valid=false,redirect=false;
  unsigned redirect_index=0;
  uint16_t end_half=0;
};
struct ParcelPrediction {
  bool btb_valid=false,btb_taken=false,bht_taken=false;
  unsigned btb_index=0;
};
inline FetchParcels decode_parcels(bool valid,uint64_t pc,uint64_t data,
                                   bool previous_half_valid,uint16_t previous_half,
                                   ParcelPrediction pred={}) {
  if(pc&1)throw std::invalid_argument("SHUTTLE_FETCH_PC_NOT_HALFWORD_ALIGNED");
  if(pred.btb_index>3)throw std::invalid_argument("SHUTTLE_BTB_INDEX_OUTSIDE_FOUR_PARCELS");
  FetchParcels out;out.edge_instruction=previous_half_valid;
  const uint64_t aligned=pc&~uint64_t(7);const unsigned offset=(pc>>1)&3;
  bool redirect_found=false;
  for(unsigned i=0;i<4;++i) {
    if(i==0 && previous_half_valid)out.raw[i]=(uint32_t(data&65535)<<16)|previous_half;
    else out.raw[i]=uint32_t(data>>(16*i));
    out.pc[i]=aligned+2*i-unsigned(i==0 && previous_half_valid)*2;
    out.control[i]=fetch_control(out.raw[i]);
    bool parcel_valid=true;
    const auto previous_full=[&] {
      return i && (out.mask&(1u<<(i-1))) && (out.raw[i-1]&3)==3;
    };
    if(i==1)parcel_valid=previous_half_valid || !previous_full();
    else if(i==3)parcel_valid=!(previous_full() || (out.raw[i]&3)==3);
    else if(i==2)parcel_valid=!previous_full();
    if(valid && i>=offset && parcel_valid && !redirect_found)out.mask|=1u<<i;
    if(out.control[i].call)out.call_mask|=1u<<i;
    if(out.control[i].ret)out.return_mask|=1u<<i;
    if((out.raw[i]&3)==3 && !(i==0 && previous_half_valid))out.npc_plus4_mask|=1u<<i;
    const bool br=out.control[i].branch && pred.btb_valid && pred.btb_index==i && pred.btb_taken && pred.bht_taken;
    if((out.mask&(1u<<i)) && (out.control[i].jump || out.control[i].jalr || br)) {
      out.redirect=true;out.redirect_index=i;redirect_found=true;
    }
  }
  out.end_half=uint16_t(data>>48);
  out.end_half_valid=!((out.mask&4) && (out.raw[2]&3)==3) && (out.end_half&3)==3;
  return out;
}
}
