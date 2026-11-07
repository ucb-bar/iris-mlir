#include "rocket_fabric_fixture.hpp"
#include <stdexcept>
namespace iris::frontend {
RocketFabricFixture::RocketFabricFixture(uint64_t first,uint64_t gap,Read read,Write write):first_(first),gap_(gap),read_(std::move(read)),write_(std::move(write)){
  if(!first||!gap||!read_||!write_)throw std::invalid_argument("IRIS_ROCKET_FABRIC_SYNTHETIC_SERVICE_PARAMETERS_REQUIRED");
  using Model=iris::fabric::MemoryFabric;
  for(unsigned i=0;i<5;++i){auto& p=pins_[i];p.component=i==4?"noc":"manager";p.request=i==4?"_sbus_system_bus_noc_acd_noc_nif_slave_acd_io_tilelink":"auto_coupler_to_bus_named_mbus_bus_xing_out_"+std::to_string(i);p.response=i==4?"_cbus_auto_bus_xing_in":p.request;
    auto in=[&](const std::string&suffix){return Model::external_input_index(p.component,p.response+suffix);};auto out=[&](const std::string&suffix){return Model::output_index(p.component,p.request+suffix);};
    p.a_ready=in("_a_ready");p.d_valid=in("_d_valid");p.d_opcode=in("_d_bits_opcode");p.d_size=in("_d_bits_size");p.d_source=in("_d_bits_source");p.d_data=in("_d_bits_data");p.d_denied=in("_d_bits_denied");p.d_corrupt=in("_d_bits_corrupt");p.a_valid=out("_a_valid");p.a_opcode=out("_a_bits_opcode");p.a_size=out("_a_bits_size");p.a_source=out("_a_bits_source");p.a_address=out("_a_bits_address");p.a_data=out("_a_bits_data");p.a_mask=out("_a_bits_mask");p.d_ready=out("_d_ready");
  }
}
void RocketFabricFixture::drive(std::span<Input>input,uint64_t edge){
  if(prepared_edge_&&edge!=*prepared_edge_)throw std::logic_error("IRIS_ROCKET_FABRIC_SERVICE_UNCOMMITTED_PHASE");prepared_edge_=edge;
  for(unsigned i=0;i<5;++i){const auto&p=pins_[i];auto set=[&](unsigned pin,uint64_t value){input[pin].value=Bits(value);};set(p.a_ready,1);set(p.d_valid,0);serving_[i]=false;
    if(!queues_[i].empty()){const auto&q=queues_[i].front();if(edge>=q.next){serving_[i]=true;set(p.d_valid,1);set(p.d_opcode,q.opcode==4?1:0);set(p.d_size,q.size);set(p.d_source,q.source);set(p.d_data,q.opcode==4?q.data.at(q.beat):0);set(p.d_denied,0);set(p.d_corrupt,0);}}
  }
}
void RocketFabricFixture::observe(std::span<const Bits>output,uint64_t edge){
  if(!prepared_edge_||*prepared_edge_!=edge)throw std::logic_error("IRIS_ROCKET_FABRIC_SERVICE_OBSERVE_WITHOUT_DRIVE");prepared_edge_.reset();completed_.clear();
  for(unsigned i=0;i<5;++i){const auto&p=pins_[i];auto get=[&](unsigned pin){return output[pin].limbs[0];};
    if(serving_[i]&&get(p.d_ready)){auto&q=queues_[i].front();if(++q.beat==(q.opcode==4?q.data.size():1)){transactions_[q.transaction].finished=edge;completed_.push_back(q.transaction);queues_[i].pop_front();}else q.next=edge+gap_;}
    if(!get(p.a_valid))continue;
    const auto op=get(p.a_opcode),size=get(p.a_size),address=get(p.a_address),source=get(p.a_source);
    if(op!=4&&op!=0&&op!=1)throw std::runtime_error("IRIS_ROCKET_FABRIC_EXTERNAL_OPCODE_UNMODELED");
    if(size>6||queues_[i].size()>=64)throw std::runtime_error("IRIS_ROCKET_FABRIC_EXTERNAL_TRANSACTION_BOUND");
    if(writes_[i]){auto&q=*writes_[i];if(op!=q.opcode||size!=q.size||address!=q.address||source!=q.source)throw std::runtime_error("IRIS_ROCKET_FABRIC_EXTERNAL_PUT_OWNERSHIP");}
    if(op==4){
      if(writes_[i])throw std::runtime_error("IRIS_ROCKET_FABRIC_EXTERNAL_INTERLEAVED_PUT");
      const auto bytes=uint64_t(1)<<size,beats=std::max(uint64_t(1),bytes/8);Request q{address,source,op,size,edge,0,edge+first_,{},transactions_.size()};
      for(unsigned b=0;b<beats;++b)q.data.push_back(read_(bytes<8?address:address+b*8,bytes<8?bytes:8)<<(bytes<8?(address&7)*8:0));
      transactions_.push_back({i,address,source,op,size,edge});queues_[i].push_back(std::move(q));
    }else{
      if(!writes_[i]){writes_[i]=Request{address,source,op,size,edge,0,edge+first_,{},transactions_.size()};transactions_.push_back({i,address,source,op,size,edge});}
      auto&q=*writes_[i];const auto bytes=uint64_t(1)<<size,beats=std::max(uint64_t(1),bytes/8);write_((address&~uint64_t(7))+q.beat*8,8,get(p.a_data),get(p.a_mask));
      if(++q.beat==beats){q.beat=0;q.next=edge+first_;queues_[i].push_back(std::move(q));writes_[i].reset();}
    }
  }
}
bool RocketFabricFixture::idle()const{for(unsigned i=0;i<5;++i)if(!queues_[i].empty()||writes_[i])return false;return true;}
}
