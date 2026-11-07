#pragma once
#include "memory_fabric.hpp"
#include <algorithm>
#include <deque>
#include <functional>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>
namespace iris::frontend {
// Only the still-external CBUS endpoint. No post-cork manager port exists
// here, so this actor cannot bypass the source Serial/Bringup/FASED path.
class CbusFunctionalFixture {
 public:
  using Bits=iris::fabric::Bits;using Input=iris::fabric::Input;
  using Index=std::function<unsigned(const std::string&,const std::string&)>;
  using Read=std::function<uint64_t(uint64_t,unsigned)>;
  using Write=std::function<void(uint64_t,unsigned,uint64_t,uint64_t)>;
  struct Transaction {uint64_t address,source,opcode,size,accepted,finished=0;};
  CbusFunctionalFixture(Index input,Index output,uint64_t first,uint64_t gap,Read read,Write write):first_(first),gap_(gap),read_(std::move(read)),write_(std::move(write)){
    if(!first||!gap||!read_||!write_)throw std::invalid_argument("IRIS_CBUS_FIXTURE_EXPLICIT_PARAMETERS_REQUIRED");
    auto in=[&](const std::string&name){return input("noc","_cbus_auto_bus_xing_in_"+name);};auto out=[&](const std::string&name){return output("noc","_sbus_system_bus_noc_acd_noc_nif_slave_acd_io_tilelink_"+name);};
    ar_=in("a_ready");dv_=in("d_valid");dop_=in("d_bits_opcode");dsz_=in("d_bits_size");dsrc_=in("d_bits_source");dd_=in("d_bits_data");ddeny_=in("d_bits_denied");dcorrupt_=in("d_bits_corrupt");av_=out("a_valid");aop_=out("a_bits_opcode");asz_=out("a_bits_size");asrc_=out("a_bits_source");addr_=out("a_bits_address");adata_=out("a_bits_data");amask_=out("a_bits_mask");dr_=out("d_ready");
  }
  void drive(std::span<Input>inputs,uint64_t edge){
    if(prepared_&&*prepared_!=edge)throw std::logic_error("IRIS_CBUS_FIXTURE_UNCOMMITTED_PHASE");prepared_=edge;inputs[ar_].value=Bits(1);inputs[dv_].value=Bits(0);serving_=!requests_.empty()&&edge>=requests_.front().next;
    if(serving_){const auto&q=requests_.front();inputs[dv_].value=Bits(1);inputs[dop_].value=Bits(q.opcode==4?1:0);inputs[dsz_].value=Bits(q.size);inputs[dsrc_].value=Bits(q.source);inputs[dd_].value=Bits(q.opcode==4?q.data.at(q.beat):0);inputs[ddeny_].value=inputs[dcorrupt_].value=Bits(0);}
  }
  void observe(std::span<const Bits>outputs,uint64_t edge){
    if(!prepared_||*prepared_!=edge)throw std::logic_error("IRIS_CBUS_FIXTURE_OBSERVE_WITHOUT_DRIVE");prepared_.reset();completed_.clear();auto get=[&](unsigned field){return outputs[field].limbs[0];};
    if(serving_&&get(dr_)){auto&q=requests_.front();if(++q.beat==(q.opcode==4?q.data.size():1)){transactions_[q.transaction].finished=edge;completed_.push_back(q.transaction);requests_.pop_front();}else q.next=edge+gap_;}
    if(!get(av_))return;const auto op=get(aop_),size=get(asz_),address=get(addr_),source=get(asrc_);if((op!=4&&op!=0&&op!=1)||size>6||requests_.size()>=64)throw std::runtime_error("IRIS_CBUS_EXTERNAL_OPERATION_UNMODELED");
    if(op==4){Request q{address,source,op,size,0,edge+first_,{},transactions_.size()};const auto bytes=uint64_t(1)<<size,beats=std::max(uint64_t(1),bytes/8);for(unsigned b=0;b<beats;++b)q.data.push_back(read_(bytes<8?address:address+8*b,bytes<8?bytes:8)<<(bytes<8?(address&7)*8:0));transactions_.push_back({address,source,op,size,edge});requests_.push_back(std::move(q));}
    else{if(size>3)throw std::runtime_error("IRIS_CBUS_MULTI_BEAT_WRITE_UNMODELED");write_(address&~uint64_t(7),8,get(adata_),get(amask_));transactions_.push_back({address,source,op,size,edge});requests_.push_back({address,source,op,size,0,edge+first_,{},transactions_.size()-1});}
  }
  const std::vector<Transaction>&transactions()const{return transactions_;}
  const std::vector<size_t>&completed_on_last_edge()const{return completed_;}
 private:
  struct Request {uint64_t address,source,opcode,size,beat,next;std::vector<uint64_t>data;size_t transaction;};
  unsigned ar_,dv_,dop_,dsz_,dsrc_,dd_,ddeny_,dcorrupt_,av_,aop_,asz_,asrc_,addr_,adata_,amask_,dr_;
  uint64_t first_,gap_;Read read_;Write write_;std::optional<uint64_t>prepared_;bool serving_=false;std::deque<Request>requests_;std::vector<Transaction>transactions_;std::vector<size_t>completed_;
};
}
