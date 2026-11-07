#include "rocket_fabric_peer.hpp"
#include "verilated.h"
#include <algorithm>
#include <optional>
#include <stdexcept>
#include <string>
namespace iris::frontend {
namespace {
struct CallerScope {VerilatedContext* caller=Verilated::threadContextp();~CallerScope(){Verilated::threadContextp(caller);}};
using Bits=iris::fabric::Bits;using Input=iris::fabric::Input;
Bits known(unsigned width){Bits value;for(unsigned i=0;i<width;++i)value.limbs[i/64]|=uint64_t(1)<<(i%64);return value;}
bool zero(const Bits& value){return std::all_of(value.limbs.begin(),value.limbs.end(),[](auto x){return x==0;});}
Input driven(Bits value,unsigned width){return {value,known(width)};}
}
struct RocketFabricPeer::Impl {
  std::unique_ptr<SavedRocketFrontend> frontend;
  std::unique_ptr<SavedRocketDcacheArbiter> arbiter;
  std::unique_ptr<iris::fabric::MemoryFabric> fabric;
  struct Binding {unsigned from,to,width;};
  std::vector<Binding> arbiter_to_fabric,fabric_to_arbiter;
  std::vector<bool> arbiter_joined,fabric_joined;
  unsigned ia_valid=0,ia_address=0,ia_ready=0,id_valid=0,id_opcode=0,id_size=0,id_data=0,id_corrupt=0;
  bool evaluated=false;
  Outputs observed;
  Impl(unsigned seed,unsigned reset_edges){
    CallerScope caller;
    if(reset_edges<2)throw std::invalid_argument("IRIS_ROCKET_FABRIC_EXPLICIT_RESET_REQUIRED");
    if(std::string(iris::fabric::MemoryFabric::source_identity())!="6227466fbb9a064a12f322928adb9e9be21ed3a403d5fa5895cb026fd6feeb42")throw std::runtime_error("IRIS_ROCKET_FABRIC_SOURCE_SDK_MISMATCH");
    frontend=std::make_unique<SavedRocketFrontend>(seed,reset_edges);arbiter=std::make_unique<SavedRocketDcacheArbiter>(seed,reset_edges);fabric=std::make_unique<iris::fabric::MemoryFabric>(seed);
    const auto ai=arbiter->input_fields(),ao=arbiter->output_fields(),fi=fabric->input_fields(),fo=fabric->output_fields();
    arbiter_joined.assign(ai.size(),false);fabric_joined.assign(fi.size(),false);
    auto cache_name=[](std::string_view field){if(!field.starts_with("io_mem_"))throw std::logic_error("arbiter physical namespace mismatch");return "io_cpu_"+std::string(field.substr(7));};
    for(unsigned i=0;i<ai.size();++i)if(ai[i].name.starts_with("io_mem_")){
      const auto index=fabric->output_index("rocket_cache",cache_name(ai[i].name));
      if(fo[index].width!=ai[i].width)throw std::logic_error("IRIS_ROCKET_FABRIC_RETURN_WIDTH_MISMATCH");
      fabric_to_arbiter.push_back({index,i,ai[i].width});arbiter_joined[i]=true;
    }
    for(unsigned i=0;i<ao.size();++i)if(ao[i].name.starts_with("io_mem_")){
      const auto index=fabric->external_input_index("rocket_cache",cache_name(ao[i].name));
      if(fi[index].width!=ao[i].width)throw std::logic_error("IRIS_ROCKET_FABRIC_REQUEST_WIDTH_MISMATCH");
      arbiter_to_fabric.push_back({i,index,ao[i].width});fabric_joined[index]=true;
    }
    ia_valid=fabric->external_input_index("rocket_tile","_frontend_auto_icache_master_out_a_valid");ia_address=fabric->external_input_index("rocket_tile","_frontend_auto_icache_master_out_a_bits_address");
    fabric_joined[ia_valid]=fabric_joined[ia_address]=true;
    ia_ready=fabric->output_index("rocket_tile","_tlMasterXbar_auto_anon_in_1_a_ready");id_valid=fabric->output_index("rocket_tile","_tlMasterXbar_auto_anon_in_1_d_valid");
    id_opcode=fabric->output_index("rocket_tile","_tlMasterXbar_auto_anon_in_1_d_bits_opcode");id_size=fabric->output_index("rocket_tile","_tlMasterXbar_auto_anon_in_1_d_bits_size");id_data=fabric->output_index("rocket_tile","_tlMasterXbar_auto_anon_in_1_d_bits_data");id_corrupt=fabric->output_index("rocket_tile","_tlMasterXbar_auto_anon_in_1_d_bits_corrupt");
    // Exact reset pins only; no cache/predictor/queue contents are installed.
    // Reset quiescence is an explicit constructor policy, not elapsed guest
    // execution or an asserted physical startup timing calibration.
    std::vector<Input> reset(fi.size());
    for(unsigned i=0;i<fi.size();++i){reset[i].known=known(fi[i].width);const auto name=fi[i].name;if(name.ends_with("_reset")||name.ends_with("/reset"))reset[i].value=Bits(1);}
    for(unsigned i=0;i<reset_edges;++i){fabric->evaluate(reset);fabric->commit({true,true});}
  }
  ~Impl(){CallerScope caller;fabric.reset();arbiter.reset();frontend.reset();}
};
RocketFabricPeer::RocketFabricPeer(unsigned seed,unsigned reset_edges):impl_(std::make_unique<Impl>(seed,reset_edges)){}
RocketFabricPeer::~RocketFabricPeer()=default;
std::span<const iris::fabric::Field> RocketFabricPeer::arbiter_input_fields(){return SavedRocketDcacheArbiter::input_fields();}
std::span<const iris::fabric::Field> RocketFabricPeer::arbiter_output_fields(){return SavedRocketDcacheArbiter::output_fields();}
std::span<const iris::fabric::Field> RocketFabricPeer::fabric_input_fields(){return iris::fabric::MemoryFabric::input_fields();}
std::span<const iris::fabric::Field> RocketFabricPeer::fabric_output_fields(){return iris::fabric::MemoryFabric::output_fields();}
bool RocketFabricPeer::arbiter_input_joined(unsigned i)const{return impl_->arbiter_joined.at(i);}
bool RocketFabricPeer::fabric_input_joined(unsigned i)const{return impl_->fabric_joined.at(i);}
void RocketFabricPeer::evaluate(const Inputs& input){
  CallerScope caller;auto& s=*impl_;s.evaluated=false;const auto ai=arbiter_input_fields(),fi=fabric_input_fields();
  if(input.arbiter.size()!=ai.size()||input.fabric.size()!=fi.size())throw std::invalid_argument("IRIS_ROCKET_FABRIC_EXTERNAL_SCHEMA_COUNT");
  if(input.frontend.memory_request_ready||input.frontend.memory_data)throw std::invalid_argument("IRIS_ROCKET_FABRIC_JOINED_FRONTEND_INPUT");
  for(unsigned i=0;i<ai.size();++i)if(s.arbiter_joined[i]&&!zero(input.arbiter[i].value))throw std::invalid_argument("IRIS_ROCKET_FABRIC_JOINED_ARBITER_INPUT");
  for(unsigned i=0;i<fi.size();++i)if(s.fabric_joined[i]&&!zero(input.fabric[i].value))throw std::invalid_argument("IRIS_ROCKET_FABRIC_JOINED_CACHE_INPUT");
  auto arb=std::vector<Input>(input.arbiter.begin(),input.arbiter.end()),fabric=std::vector<Input>(input.fabric.begin(),input.fabric.end());
  SavedRocketFrontend::Inputs front=input.frontend;
  std::vector<Bits> feedback(ai.size());bool ready=false;std::optional<SavedRocketFrontend::DataBeat> beat;
  auto equal_beat=[](const auto&a,const auto&b){return bool(a)==bool(b)&&(!a||(a->value==b->value&&a->corrupt==b->corrupt));};
  for(unsigned iteration=1;iteration<=64;++iteration){
    front.memory_request_ready=ready;front.memory_data=beat;
    const auto f=s.frontend->evaluate(front);
    for(const auto& link:s.fabric_to_arbiter)arb[link.to]=driven(feedback[link.to],link.width);
    s.arbiter->evaluate(arb);auto a=s.arbiter->sample();
    for(const auto& link:s.arbiter_to_fabric)fabric[link.to]=driven(a[link.from],link.width);
    fabric[s.ia_valid]=driven(Bits(f.memory_request_valid),1);fabric[s.ia_address]=driven(Bits(f.memory_request_address),35);
    s.fabric->evaluate(fabric);auto m=s.fabric->sample();const bool next_ready=m[s.ia_ready].limbs[0];
    std::optional<SavedRocketFrontend::DataBeat> next_beat;
    if(m[s.id_valid].limbs[0]){
      if(m[s.id_opcode].limbs[0]!=1||m[s.id_size].limbs[0]!=6)throw std::runtime_error("IRIS_ROCKET_FABRIC_ICACHE_RETURN_PROTOCOL");
      next_beat=SavedRocketFrontend::DataBeat{m[s.id_data].limbs[0],bool(m[s.id_corrupt].limbs[0])};
    }
    bool stable=ready==next_ready&&equal_beat(beat,next_beat);
    for(const auto&link:s.fabric_to_arbiter){stable&=feedback[link.to]==m[link.from];feedback[link.to]=m[link.from];}
    if(stable){s.observed={f,std::move(a),std::move(m),iteration};s.evaluated=true;return;}
    ready=next_ready;beat=next_beat;
  }
  throw std::runtime_error("IRIS_ROCKET_FABRIC_GLOBAL_FIXED_POINT_FAILED");
}
RocketFabricPeer::Outputs RocketFabricPeer::sample()const{
  CallerScope caller;if(!impl_->evaluated)throw std::logic_error("IRIS_ROCKET_FABRIC_SAMPLE_WITHOUT_EVALUATE");return impl_->observed;
}
void RocketFabricPeer::commit(iris::fabric::ClockEdges edges){
  CallerScope caller;auto& s=*impl_;
  if(!s.evaluated)throw std::logic_error("IRIS_ROCKET_FABRIC_COMMIT_WITHOUT_EVALUATE");s.evaluated=false;
  if(!edges.rocket&&!edges.uncore)throw std::invalid_argument("IRIS_ROCKET_FABRIC_NO_CLOCK_EDGE");
  // All three peers retain the last globally settled inputs. No evaluation
  // between commits and no additional queue/link/handoff edge is introduced.
  if(edges.rocket){s.frontend->commit();s.arbiter->commit();}
  s.fabric->commit(edges);
}
}
