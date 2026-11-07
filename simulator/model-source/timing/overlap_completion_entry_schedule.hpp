#pragma once
#include <algorithm>
#include <array>
#include <cstdint>
#include <stdexcept>
#include <vector>

namespace iris::opu_entry {
struct MicroIssue {
 unsigned entry,row_subtile,column_subtile;
 std::uint64_t sequencer_edge,array_accept_edge,write_edge,macs;
};
// Finite TK1 source schedule. The supplied dispatch edge is a boundary input;
// mapping a CPU COM to that boundary is a separately disclosed native bridge.
// There is no observed ready, queue state, fitted delay or model-data input.
class Schedule {
 std::uint64_t next_issue_=0;
 std::array<std::uint64_t,16> fp8_allowed_{};
 public:
 std::vector<MicroIssue> reserve(unsigned tile,unsigned tm,unsigned tn,bool fp8,
                                 std::uint64_t dispatch_edge){
  if(tile>=4||!tm||!tn||tm>32||tn>32||dispatch_edge>UINT64_MAX-128)
   throw std::invalid_argument("finite initialized TM/TN1..32 TK1 OPU scope required");
  auto edge=std::max(dispatch_edge+1,next_issue_);
  std::vector<MicroIssue> result;
  for(unsigned r=0;r<(tm+15)/16;++r)for(unsigned c=0;c<(tn+15)/16;++c){
   const unsigned entry=4*tile+2*r+c;
   edge=std::max(edge,fp8?fp8_allowed_[entry]:*std::max_element(fp8_allowed_.begin(),fp8_allowed_.end()));
   result.push_back({entry,r,c,edge,edge+1,edge+(fp8?3:1),
                     std::uint64_t(std::min(16U,tm-16*r))*std::min(16U,tn-16*c)});
   // Source fp8_hist_valid has two entries: t+1 and t+2 conflict; t+3 may issue.
   if(fp8)fp8_allowed_[entry]=edge+3;
   ++edge;
  }
  next_issue_=edge;
  return result;
 }
};
}
