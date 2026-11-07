#pragma once
#include <algorithm>
#include <cstdint>
#include <deque>
#include <functional>
#include <map>
#include <optional>
#include <stdexcept>
#include <vector>
namespace iris::frontend {
// Explicit functional fixture for the source LP's *external* egress path.
// This is not the missing full FASED host ingress/egress/token bridge.
// Reads snapshot at actual AR acceptance. Writes linearize only after an
// owned AW and a complete W burst exist. Conflicting outstanding accesses
// refuse rather than assume a cross-channel ordering or relaxFunctionalModel.
class AxiFunctionalFixture {
 public:
  struct Word {uint64_t value=0,known=0;};
  struct Request {uint64_t address=0;unsigned id=0,len=0,size=0,burst=1;};
  struct ReadBeat {unsigned id=0;uint64_t data=0;bool last=false;};
  using Read=std::function<Word(uint64_t,unsigned)>;
  using Write=std::function<void(uint64_t,unsigned,uint64_t,unsigned)>;
  AxiFunctionalFixture(unsigned read_capacity,unsigned write_capacity,Read read,Write write):read_capacity_(read_capacity),write_capacity_(write_capacity),read_(std::move(read)),write_(std::move(write)){
    if(!read_capacity||!write_capacity||read_capacity>16||write_capacity>16||!read_||!write_)throw std::invalid_argument("IRIS_AXI_FIXTURE_EXPLICIT_CAPACITIES_REQUIRED");
  }
  void accept_ar(Request request){
    validate(request);if(read_count_>=read_capacity_)throw std::runtime_error("IRIS_AXI_FIXTURE_READ_CAPACITY");
    if(!partial_w_.empty()||!completed_w_.empty())throw std::runtime_error("IRIS_AXI_FIXTURE_READ_WITH_UNOWNED_WRITE_DATA");
    for(const auto&[sequence,write]:live_writes_)if(overlap(request,write))throw std::runtime_error("IRIS_AXI_FIXTURE_CONFLICTING_READ_WRITE_UNQUALIFIED");
    ReadOwner owner{next_sequence_++,request,{}};
    for(unsigned beat=0;beat<=request.len;++beat){const auto address=request.address+(uint64_t(beat)<<request.size);const unsigned bytes=1u<<request.size;const auto word=read_(address,bytes);const uint64_t mask=bytes==8?~uint64_t(0):(uint64_t(1)<<(bytes*8))-1;
      if((word.known&mask)!=mask)throw std::runtime_error("IRIS_AXI_FIXTURE_UNDEFINED_READ_SNAPSHOT");
      owner.data.push_back((word.value&mask)<<((address&7)*8));
    }
    live_reads_.emplace(owner.sequence,request);reads_.push_back(std::move(owner));++read_count_;
  }
  void accept_aw(Request request){
    validate(request);if(write_count_>=write_capacity_)throw std::runtime_error("IRIS_AXI_FIXTURE_WRITE_CAPACITY");
    for(const auto&[sequence,read]:live_reads_)if(overlap(request,read))throw std::runtime_error("IRIS_AXI_FIXTURE_CONFLICTING_WRITE_READ_UNQUALIFIED");
    for(const auto&[sequence,write]:live_writes_)if(overlap(request,write))throw std::runtime_error("IRIS_AXI_FIXTURE_CONFLICTING_WRITES_UNQUALIFIED");
    const auto sequence=next_sequence_++;live_writes_.emplace(sequence,request);aw_.push_back({sequence,request});++write_count_;drain_writes();
  }
  void accept_w(uint64_t data,unsigned strobe,bool last,uint64_t known=~uint64_t(0)){
    if(strobe>255||partial_w_.size()>=8||retained_w_beats_>=write_capacity_*8)throw std::runtime_error("IRIS_AXI_FIXTURE_WRITE_BEAT_BOUND");
    for(unsigned byte=0;byte<8;++byte)if((strobe&(1u<<byte))&&((known>>(byte*8))&255)!=255)throw std::runtime_error("IRIS_AXI_FIXTURE_UNDEFINED_WRITTEN_BYTE");
    partial_w_.push_back({data,strobe});++retained_w_beats_;
    if(last){completed_w_.push_back(std::move(partial_w_));partial_w_.clear();drain_writes();}
  }
  // Called on the source LP egReq handshake. The payload becomes available
  // to the caller for the following target phase, never by rereading memory.
  void request_read_egress(unsigned id){
    if(active_read_||reads_.empty()||reads_.front().request.id!=id)throw std::runtime_error("IRIS_AXI_FIXTURE_READ_EGRESS_OWNER");
    active_read_=std::move(reads_.front());reads_.pop_front();read_beat_=0;
  }
  std::optional<ReadBeat> read_payload()const{
    if(!active_read_)return {};
    const auto&owner=*active_read_;
    return ReadBeat{owner.request.id,owner.data.at(read_beat_),read_beat_==owner.request.len};
  }
  void consume_r(){
    if(!active_read_)throw std::runtime_error("IRIS_AXI_FIXTURE_UNOWNED_R");
    if(read_beat_++==active_read_->request.len){live_reads_.erase(active_read_->sequence);active_read_.reset();read_beat_=0;--read_count_;}
  }
  void request_write_egress(unsigned id){
    if(active_b_||writes_.empty()||writes_.front().request.id!=id)throw std::runtime_error("IRIS_AXI_FIXTURE_WRITE_EGRESS_OWNER");
    active_b_=writes_.front();writes_.pop_front();
  }
  std::optional<unsigned> write_payload()const{return active_b_?std::optional<unsigned>(active_b_->request.id):std::nullopt;}
  void consume_b(){if(!active_b_)throw std::runtime_error("IRIS_AXI_FIXTURE_UNOWNED_B");live_writes_.erase(active_b_->sequence);active_b_.reset();--write_count_;}
  bool idle()const{return !read_count_&&!write_count_&&!retained_w_beats_;}
  unsigned read_count()const{return read_count_;}
  unsigned write_count()const{return write_count_;}
 private:
  struct ReadOwner {uint64_t sequence;Request request;std::vector<uint64_t>data;};
  struct WriteOwner {uint64_t sequence;Request request;};
  struct WBeat {uint64_t data;unsigned strobe;};
  unsigned read_capacity_,write_capacity_,read_count_=0,write_count_=0,retained_w_beats_=0,read_beat_=0;
  uint64_t next_sequence_=0;Read read_;Write write_;
  std::deque<ReadOwner>reads_;std::deque<WriteOwner>aw_,writes_;
  std::deque<std::vector<WBeat>>completed_w_;std::vector<WBeat>partial_w_;
  std::optional<ReadOwner>active_read_;std::optional<WriteOwner>active_b_;
  std::map<uint64_t,Request>live_reads_,live_writes_;
  static uint64_t end(const Request&r){return r.address+((uint64_t(r.len)+1)<<r.size);}
  static bool overlap(const Request&a,const Request&b){return a.address<end(b)&&b.address<end(a);}
  static void validate(const Request&r){
    if(r.id>15||r.len>7||r.size>3||r.burst!=1||(r.address&((uint64_t(1)<<r.size)-1))||r.address>UINT64_MAX-((uint64_t(r.len)+1)<<r.size)||(r.address>>12)!=((end(r)-1)>>12))throw std::runtime_error("IRIS_AXI_FIXTURE_REQUEST_SHAPE_UNQUALIFIED");
  }
  void drain_writes(){
    while(!aw_.empty()&&!completed_w_.empty()){
      const auto owner=aw_.front();const auto&data=completed_w_.front();
      if(data.size()!=owner.request.len+1)throw std::runtime_error("IRIS_AXI_FIXTURE_WRITE_BURST_LENGTH");
      for(unsigned beat=0;beat<data.size();++beat){const auto address=owner.request.address+(uint64_t(beat)<<owner.request.size);const unsigned lanes=((1u<<(1u<<owner.request.size))-1)<<(address&7);
        if(data[beat].strobe&~lanes)throw std::runtime_error("IRIS_AXI_FIXTURE_STROBE_OUTSIDE_TRANSFER");
      }
      // Validate the complete burst before committing any byte. The callback
      // receives physical bus-aligned address/data and explicit byte strobes.
      for(unsigned beat=0;beat<data.size();++beat)write_((owner.request.address+(uint64_t(beat)<<owner.request.size))&~uint64_t(7),8,data[beat].data,data[beat].strobe);
      retained_w_beats_-=data.size();aw_.pop_front();completed_w_.pop_front();writes_.push_back(owner);
    }
  }
};
}
