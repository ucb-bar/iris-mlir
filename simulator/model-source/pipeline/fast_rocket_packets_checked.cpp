#include "fast_rocket_packets.hpp"
#include <algorithm>
#include <limits>
#include <stdexcept>
namespace iris::frontend {
FastRocketPackets::FastRocketPackets(uint64_t divider,uint64_t pc):divider_(divider),producer_pc_(pc){
 if(!divider||pc>>40||pc&1)throw std::invalid_argument("IRIS_ROCKET_PACKET_EXPLICIT_CLOCK_RESET_REQUIRED");
 // Component reset initialization precedes the guest model clock, as does
 // Spike's architectural reset. It is not counted as retired guest work.
 FastRocketIBuf::Inputs in;in.reset=true;for(unsigned i=0;i<2;++i){model_.evaluate(in);model_.commit();}
}
uint64_t FastRocketPackets::following(uint64_t tick)const{if(divider_>=std::numeric_limits<uint64_t>::max()-tick)throw std::overflow_error("IRIS_ROCKET_PACKET_CLOCK_HORIZON");return tick+divider_;}
FastRocketPackets::PendingWord FastRocketPackets::read_word(const Reader&read,const Predictor&predict)const{
 PendingWord w;w.value.id=next_word_;w.value.address=producer_pc_&~3ull;const auto bytes=read(w.value.address);w.value.data=bytes.data;w.value.known=bytes.known;
 if(bytes.known!=0xffffffffu)throw std::runtime_error("IRIS_ROCKET_PACKET_UNKNOWN_FETCH_WORD");
 w.packet.pc=producer_pc_;w.packet.data=bytes.data;w.packet.known=bytes.known;w.next_pc=w.value.address+4;
 uint64_t pc=partial_?partial_->pc:producer_pc_;
 while(pc<w.value.address+4){
  uint32_t raw;unsigned length;std::vector<uint64_t>owners{w.value.id};
  if(pc<w.value.address){
   if(!partial_||pc+2!=w.value.address||(partial_->bits&3)!=3)throw std::logic_error("IRIS_ROCKET_PACKET_PARTIAL_OWNER");
   raw=partial_->bits|(bytes.data<<16);length=4;owners.insert(owners.begin(),partial_->word);
  }else{
   const auto offset=unsigned(pc-w.value.address);raw=bytes.data>>(8*offset);length=(raw&3)==3?4:2;
   if(pc+length>w.value.address+4){w.partial=Partial{pc,w.value.id,uint16_t(raw)};break;}
   if(length==2)raw=uint16_t(raw);
  }
  const auto next=predict(pc,raw,length);w.instructions.push_back({pc,raw,length,next,std::move(owners)});
  if(next!=pc+length){w.packet.taken=true;w.packet.branch_index=((pc+length-2)>>1)&1;w.next_pc=next;w.partial.reset();break;}
  pc+=length;
 }
 return w;
}
bool FastRocketPackets::prepare(uint64_t tick,uint64_t pc,uint32_t raw,unsigned bytes,const Reader&read,const Predictor&predict){
 if(tick%divider_||!read||!predict||pc&1||(bytes!=2&&bytes!=4))throw std::invalid_argument("IRIS_ROCKET_PACKET_REQUEST_SCOPE");
 if(committed_&&tick<*committed_)throw std::logic_error("IRIS_ROCKET_PACKET_CLOCK_REVERSED");
 if(committed_&&tick==*committed_){retry_=following(tick);return false;}
 if(prepared_){
  if(prepared_tick_!=tick)throw std::logic_error("IRIS_ROCKET_PACKET_UNCOMMITTED_EDGE");
  if(consumed_){retry_=following(tick);return false;}
  if(expected_pc_!=pc||expected_bytes_!=bytes||expected_raw_!=raw)throw std::logic_error("IRIS_ROCKET_PACKET_MULTIPLE_HEADS_ONE_EDGE");
  return !redirect_&&outputs_.valid;
 }
 prepared_=true;prepared_tick_=tick;expected_pc_=pc;expected_raw_=raw;expected_bytes_=bytes;consumed_=false;partial_edge_=false;inputs_={};inputs_.packet.pc=producer_pc_&((1ull<<40)-1);
 if(redirect_){inputs_.kill=true;outputs_=model_.evaluate(inputs_);partial_edge_=true;return false;}
 outputs_=model_.evaluate(inputs_);
 if(!outputs_.valid){
  if(!word_){word_=read_word(read,predict);++next_word_;}
  inputs_.valid=true;inputs_.packet=word_->packet;outputs_=model_.evaluate(inputs_);
 }
 if(outputs_.valid){
  const uint32_t mask=bytes==2?65535:0xffffffffu;
  if(outputs_.replay||outputs_.pc!=pc||outputs_.compressed!=(bytes==2)||(outputs_.raw&mask)!=(raw&mask))throw std::runtime_error("IRIS_ROCKET_PACKET_ARCHITECTURAL_HEAD_MISMATCH expected_pc="+std::to_string(pc)+" expected_raw="+std::to_string(raw)+" expected_bytes="+std::to_string(bytes)+" observed_pc="+std::to_string(outputs_.pc)+" observed_raw="+std::to_string(outputs_.raw)+" observed_compressed="+std::to_string(outputs_.compressed)+" observed_replay="+std::to_string(outputs_.replay)+" tick="+std::to_string(tick));
  if(!prediction(pc,raw,bytes))throw std::logic_error("IRIS_ROCKET_PACKET_MISSING_PREDICTION_OWNER");
  return true;
 }
 // The source IBuf may accept the first half of a 32-bit instruction while
 // its output is invalid. Its real ready input must permit that transfer.
 inputs_.ready=true;outputs_=model_.evaluate(inputs_);partial_edge_=true;
 if(!inputs_.valid||!outputs_.imem_ready)throw std::logic_error("IRIS_ROCKET_PACKET_PARTIAL_CANNOT_PROGRESS");
 return false;
}
void FastRocketPackets::consume(uint64_t tick){
 if(!prepared_||prepared_tick_!=tick||consumed_||redirect_||!outputs_.valid)throw std::logic_error("IRIS_ROCKET_PACKET_CONSUME_WITHOUT_READY_HEAD");
 inputs_.ready=true;outputs_=model_.evaluate(inputs_);consumed_=true;
}
std::optional<uint64_t> FastRocketPackets::prediction(uint64_t pc,uint32_t raw,unsigned bytes)const{
 const auto match=[&](const Instruction&i){const uint32_t mask=bytes==2?65535:0xffffffffu;return i.pc==pc&&i.bytes==bytes&&(i.raw&mask)==(raw&mask);};
 for(const auto&i:instructions_)if(match(i))return i.prediction;
 if(word_)for(const auto&i:word_->instructions)if(match(i))return i.prediction;
 return {};
}
void FastRocketPackets::redirect(uint64_t tick,uint64_t pc,std::string reason,unsigned priority){
 if(reason.empty()||pc&1)throw std::invalid_argument("IRIS_ROCKET_PACKET_REDIRECT_SOURCE_REQUIRED");
 if(!redirect_||tick>redirect_->tick||(tick==redirect_->tick&&priority>=redirect_->priority))redirect_=Redirect{tick,pc,std::move(reason),priority};
 retry_=following(tick);
}
std::optional<FastRocketPackets::Edge> FastRocketPackets::finish(uint64_t tick){
 if(tick%divider_)return {};
 if(committed_&&tick==*committed_)return {};
 if(!prepared_){
  if(redirect_){prepared_=true;prepared_tick_=tick;consumed_=false;partial_edge_=true;inputs_={};inputs_.kill=true;inputs_.packet.pc=producer_pc_&((1ull<<40)-1);outputs_=model_.evaluate(inputs_);}
  else {if(retry_&&*retry_<=tick)retry_.reset();++metrics_.held_observations;return {};}
 }
 if(prepared_tick_!=tick)throw std::logic_error("IRIS_ROCKET_PACKET_FINISH_WRONG_EDGE");
 Edge edge;edge.tick=tick;edge.partial=partial_edge_;edge.kill=bool(redirect_);
 if(redirect_){inputs_.kill=true;inputs_.valid=false;inputs_.ready=false;outputs_=model_.evaluate(inputs_);edge.reason=redirect_->reason;}
 model_.commit();++metrics_.edges;
 if(redirect_){producer_pc_=redirect_->pc;word_.reset();partial_.reset();instructions_.clear();redirect_.reset();++metrics_.redirects;}
 else {
  if(inputs_.valid&&outputs_.imem_ready){
   if(!word_)throw std::logic_error("IRIS_ROCKET_PACKET_ACCEPT_WITHOUT_WORD");
   edge.word=word_->value;producer_pc_=word_->next_pc;partial_=word_->partial;for(auto&i:word_->instructions)instructions_.push_back(std::move(i));word_.reset();++metrics_.words;
  }
  if(consumed_){
   const uint32_t mask=expected_bytes_==2?65535:0xffffffffu;
   if(instructions_.empty()||instructions_.front().pc!=expected_pc_||instructions_.front().bytes!=expected_bytes_||(instructions_.front().raw&mask)!=(expected_raw_&mask))throw std::logic_error("IRIS_ROCKET_PACKET_INSTRUCTION_OWNERSHIP");
   edge.instruction=std::move(instructions_.front());instructions_.pop_front();++metrics_.instructions;
  }
 }
 if(instructions_.size()>2)throw std::logic_error("IRIS_ROCKET_PACKET_UNBOUNDED_PREDICTION_QUEUE");
 if(partial_edge_)++metrics_.partial_edges;
 committed_=tick;prepared_=false;retry_=(partial_edge_||consumed_||edge.kill)?std::optional<uint64_t>(following(tick)):std::nullopt;
 return edge;
}
}
