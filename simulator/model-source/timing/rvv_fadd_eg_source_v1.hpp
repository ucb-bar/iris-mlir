#pragma once
#include <array>
#include <cstdint>
#include <deque>
#include <optional>
#include <stdexcept>

namespace iris::rvv_fadd_source {
// Finite saved Compute128 specialization: IEEE VFADD.VV, vm=1, vstart=0,
// full VLmax, aligned LMUL1/2/4/8, no widening/reduction/alternate format.
// Ports are actual boundary inputs. Values, knownness, FS and exception-flag
// accumulation belong to a future owned numeric/publication integration.
struct Command {
  uint64_t id;
  unsigned vat,vd,vs1,vs2,sew,lmul;
  unsigned beats()const{return 2*lmul;}
};
struct Micro {
  Command command;
  unsigned index,eidx,destination,source1,source2;
  uint64_t issue_edge;
  bool head,tail;
};
struct Ports {
  uint64_t older_reads=0,older_writes=0;
  bool source1_ready=true,source2_ready=true,pipe_write_available=true,execution_ready=true;
};
struct Edge {
  bool input_ready=false,input_fire=false,vdq_dequeue=false,iq_dequeue=false,seq_ready=false;
  unsigned vdq_before=0,iq_before=0;
  bool seq_before=false,raw=false,waw=false,war=false;
  uint64_t pending_reads=0,pending_writes=0;
  std::optional<Micro> candidate,issue,write;
  std::optional<Command> iq_head;
  std::array<std::optional<Micro>,4> pipe;
};

class Scheduler {
  std::deque<Command> vdq_,iq_;
  std::optional<Command> seq_;
  unsigned index_=0;
  std::array<std::optional<Micro>,4> pipe_{};
  std::optional<uint64_t> last_edge_,last_id_;
  static uint64_t bit(unsigned n){return uint64_t(1)<<n;}
 public:
  static constexpr unsigned vdq_capacity=4,iq_capacity=3,write_depth=4;
  static void validate(const Command&c){
    if(c.id==0||c.vat>=32||(c.sew!=16&&c.sew!=32&&c.sew!=64)||
       (c.lmul!=1&&c.lmul!=2&&c.lmul!=4&&c.lmul!=8)||
       c.vd>=32||c.vs1>=32||c.vs2>=32||c.vd%c.lmul||c.vs1%c.lmul||c.vs2%c.lmul||
       c.vd+c.lmul>32||c.vs1+c.lmul>32||c.vs2+c.lmul>32)
      throw std::invalid_argument("FADD_SOURCE_FINITE_GEOMETRY");
  }
  bool empty()const{
    if(!vdq_.empty()||!iq_.empty()||seq_)return false;
    for(auto&p:pipe_)if(p)return false;
    return true;
  }
  unsigned buffered()const{return vdq_.size()+iq_.size()+bool(seq_);}
  Edge step(uint64_t now,std::optional<Command> input={},Ports ports={}){
    if(last_edge_&&(now<=*last_edge_||(!empty()&&now!=*last_edge_+1)))
      throw std::invalid_argument("FADD_SOURCE_EDGE_ORDER");
    if(input){
      validate(*input);
      if(last_id_&&input->id<=*last_id_)throw std::invalid_argument("FADD_SOURCE_ID_REUSE");
      auto same_vat=[&](const Command&c){return c.vat==input->vat;};
      for(auto&c:vdq_)if(same_vat(c))throw std::invalid_argument("FADD_SOURCE_LIVE_VAT_REUSE");
      for(auto&c:iq_)if(same_vat(c))throw std::invalid_argument("FADD_SOURCE_LIVE_VAT_REUSE");
      if(seq_&&same_vat(*seq_))throw std::invalid_argument("FADD_SOURCE_LIVE_VAT_REUSE");
      for(auto&p:pipe_)if(p&&same_vat(p->command))throw std::invalid_argument("FADD_SOURCE_LIVE_VAT_REUSE");
    }
    Edge e;e.vdq_before=vdq_.size();e.iq_before=iq_.size();e.seq_before=bool(seq_);
    e.write=pipe_.back();e.pipe=pipe_;
    if(!iq_.empty())e.iq_head=iq_.front();
    if(seq_){
      auto&c=*seq_;const unsigned d=2*c.vd+index_,s1=2*c.vs1+index_,s2=2*c.vs2+index_;
      Micro m{c,index_,index_*(128/c.sew),d,s1,s2,now,index_==0,index_+1==c.beats()};
      e.candidate=m;
      uint64_t writes=ports.older_writes;
      for(auto&p:pipe_)if(p&&p->command.id!=c.id)writes|=bit(p->destination);
      e.raw=bool((bit(s1)|bit(s2))&writes);
      e.waw=bool(bit(d)&writes);e.war=bool(bit(d)&ports.older_reads);
      // Full-register intent masks are released one EG at each actual issue.
      const uint64_t remaining=(c.beats()==64?~uint64_t(0):((uint64_t(1)<<c.beats())-1))&(~uint64_t(0)<<index_);
      e.pending_reads=(remaining<<(2*c.vs1))|(remaining<<(2*c.vs2));
      e.pending_writes=remaining<<(2*c.vd);
      if(!e.raw&&!e.waw&&!e.war&&ports.source1_ready&&ports.source2_ready&&
         ports.pipe_write_available&&ports.execution_ready)e.issue=m;
    }
    // DCEQueue4 has neither flow nor pipe. IQ3 pipes only when deq fires.
    e.seq_ready=!seq_||(e.issue&&e.issue->tail);
    e.iq_dequeue=!iq_.empty()&&e.seq_ready;
    e.vdq_dequeue=!vdq_.empty()&&(iq_.size()<iq_capacity||e.iq_dequeue);
    e.input_ready=vdq_.size()<vdq_capacity;
    e.input_fire=input&&e.input_ready;
    auto next_seq=e.iq_dequeue?std::optional<Command>(iq_.front()):std::nullopt;
    auto next_iq=e.vdq_dequeue?std::optional<Command>(vdq_.front()):std::nullopt;
    for(unsigned i=write_depth-1;i>0;--i)pipe_[i]=pipe_[i-1];
    pipe_[0]=e.issue;
    if(e.issue){if(e.issue->tail)seq_.reset();else ++index_;}
    if(next_seq){seq_=*next_seq;index_=0;}
    if(e.iq_dequeue)iq_.pop_front();
    if(next_iq)iq_.push_back(*next_iq);
    if(e.vdq_dequeue)vdq_.pop_front();
    if(e.input_fire){vdq_.push_back(*input);last_id_=input->id;}
    if(vdq_.size()>vdq_capacity||iq_.size()>iq_capacity||buffered()>8)
      throw std::logic_error("FADD_SOURCE_QUEUE_CAPACITY");
    last_edge_=now;return e;
  }
};
}
