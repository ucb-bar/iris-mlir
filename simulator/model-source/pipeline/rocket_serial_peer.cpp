#include "rocket_serial_peer.hpp"
#include "verilated.h"
#include <algorithm>
#include <optional>
#include <stdexcept>
namespace iris::frontend {
namespace {
struct CallerScope {VerilatedContext*caller=Verilated::threadContextp();~CallerScope(){Verilated::threadContextp(caller);}};
using Bits=iris::fabric::Bits;using Input=iris::fabric::Input;
Bits known(unsigned width){Bits value;for(unsigned i=0;i<width;++i)value.limbs[i/64]|=uint64_t(1)<<(i%64);return value;}
uint64_t mask(unsigned width){return width==64?~uint64_t(0):(uint64_t(1)<<width)-1;}
bool zero(const Bits&value){return std::all_of(value.limbs.begin(),value.limbs.end(),[](auto x){return x==0;});}
Input driven(Bits value,unsigned width){return {value,known(width)};}
}
struct RocketSerialPeer::Impl {
  std::unique_ptr<SavedRocketFrontend>frontend;
  std::unique_ptr<SavedRocketDcacheArbiter>arbiter;
  std::unique_ptr<iris::memory_system::System>system;
  std::unique_ptr<iris::fased::Model>latency;
  struct Binding {unsigned from,to,width;};
  std::vector<Binding>arbiter_to_system,system_to_arbiter,latency_to_system,system_to_latency;
  std::vector<bool>arbiter_joined,system_joined,latency_joined;
  unsigned ia_valid,ia_address,ia_ready,id_valid,id_opcode,id_size,id_data,id_corrupt;
  bool evaluated=false;Outputs observed;
  explicit Impl(const Configuration&cfg){
    if(!cfg.seed||cfg.seed>INT32_MAX||cfg.frontend_reset_edges<2||!cfg.system_reset_phases||cfg.source_arguments.empty()||cfg.expected_system_identity.empty()||cfg.expected_latency_identity.empty())throw std::invalid_argument("IRIS_ROCKET_SERIAL_EXPLICIT_CONFIGURATION_REQUIRED");
    if(cfg.expected_system_identity!=iris::memory_system::System::source_identity())throw std::runtime_error("IRIS_ROCKET_SERIAL_SYSTEM_IDENTITY_MISMATCH");
    frontend=std::make_unique<SavedRocketFrontend>(cfg.seed,cfg.frontend_reset_edges);
    arbiter=std::make_unique<SavedRocketDcacheArbiter>(cfg.seed,cfg.frontend_reset_edges);
    system=std::make_unique<iris::memory_system::System>(cfg.seed,cfg.source_arguments,cfg.assertion_policy);
    latency=std::make_unique<iris::fased::Model>(cfg.latency,cfg.seed,cfg.source_arguments);
    if(cfg.expected_latency_identity!=latency->source_identity())throw std::runtime_error("IRIS_ROCKET_SERIAL_LATENCY_IDENTITY_MISMATCH");
    const auto ai=arbiter->input_fields(),ao=arbiter->output_fields(),si=system->input_fields(),so=system->output_fields();const auto li=latency->input_fields();
    arbiter_joined.assign(ai.size(),false);system_joined.assign(si.size(),false);latency_joined.assign(li.size(),false);
    auto cache_name=[](std::string_view name){return "io_cpu_"+std::string(name.substr(7));};
    for(unsigned i=0;i<ai.size();++i)if(ai[i].name.starts_with("io_mem_")){
      const auto index=system->output_index("rocket_cache",cache_name(ai[i].name));if(ai[i].width!=so[index].width)throw std::logic_error("IRIS_ROCKET_SERIAL_CACHE_RETURN_WIDTH");system_to_arbiter.push_back({index,i,ai[i].width});arbiter_joined[i]=true;
    }
    for(unsigned i=0;i<ao.size();++i)if(ao[i].name.starts_with("io_mem_")){
      const auto index=system->external_input_index("rocket_cache",cache_name(ao[i].name));if(ao[i].width!=si[index].width)throw std::logic_error("IRIS_ROCKET_SERIAL_CACHE_REQUEST_WIDTH");arbiter_to_system.push_back({i,index,ao[i].width});system_joined[index]=true;
    }
    ia_valid=system->external_input_index("rocket_tile","_frontend_auto_icache_master_out_a_valid");ia_address=system->external_input_index("rocket_tile","_frontend_auto_icache_master_out_a_bits_address");system_joined[ia_valid]=system_joined[ia_address]=true;
    auto io=[&](const std::string&name){return system->output_index("rocket_tile","_tlMasterXbar_auto_anon_in_1_"+name);};
    ia_ready=io("a_ready");id_valid=io("d_valid");id_opcode=io("d_bits_opcode");id_size=io("d_bits_size");id_data=io("d_bits_data");id_corrupt=io("d_bits_corrupt");
    auto lp_in=[&](const std::string&name){for(unsigned i=0;i<li.size();++i)if(name==li[i].name)return i;throw std::logic_error("IRIS_ROCKET_SERIAL_LP_INPUT_MISSING");};
    auto lp_out=[&](const std::string&name){auto fields=latency->output_fields();for(unsigned i=0;i<fields.size();++i)if(name==fields[i].name)return i;throw std::logic_error("IRIS_ROCKET_SERIAL_LP_OUTPUT_MISSING");};
    for(const auto*name:{"aw_ready","w_ready","ar_ready","b_valid","b_bits_id","r_valid","r_bits_id","r_bits_data","r_bits_last"}){
      const auto from=lp_out("tNasti_io_tNasti_"+std::string(name)),to=system->external_input_index("path","fased_axi4_mem_0_bits_"+std::string(name));
      if(latency->output_fields()[from].width!=si[to].width)throw std::logic_error("IRIS_ROCKET_SERIAL_LP_RETURN_WIDTH");
      latency_to_system.push_back({from,to,si[to].width});system_joined[to]=true;
    }
    for(const auto*name:{"aw_valid","aw_bits_id","aw_bits_len","aw_bits_burst","w_valid","w_bits_last","ar_valid","ar_bits_id","ar_bits_len","ar_bits_burst","r_ready","b_ready"}){
      const auto from=system->output_index("path","fased_axi4_mem_0_bits_"+std::string(name)),to=lp_in("tNasti_io_tNasti_"+std::string(name));
      if(so[from].width!=li[to].width)throw std::logic_error("IRIS_ROCKET_SERIAL_LP_REQUEST_WIDTH");
      system_to_latency.push_back({from,to,li[to].width});latency_joined[to]=true;
    }
  }
  ~Impl(){CallerScope caller;latency.reset();system.reset();arbiter.reset();frontend.reset();}
};
RocketSerialPeer::RocketSerialPeer(Configuration cfg){
  // The outer scope survives complete failed-construction member unwinding.
  // Every owned context is destroyed before restoring the caller.
  CallerScope caller;impl_=std::make_unique<Impl>(cfg);
  auto&s=*impl_;std::vector<Input>m(system_input_fields().size());std::vector<iris::fased::Bits>l(latency_input_fields().size());
  for(unsigned i=0;i<m.size();++i){const auto f=system_input_fields()[i];m[i].known=known(f.width);if(f.name.ends_with("_reset")||f.name.ends_with("/reset")||f.name=="path/iris_pad_reset_io")m[i].value=Bits(1);}
  for(unsigned i=0;i<l.size();++i){const auto f=latency_input_fields()[i];l[i]={0,mask(f.width)};if(std::string_view(f.name)=="reset")l[i].value=1;}
  // Separate component reset-reference setup: Frontend/IBuf and the arbiter
  // have already received their explicit reset edges. Hold those producers
  // quiescent while resetting the source backend clocks; never let a fetch
  // be "accepted" into a backend which is still being reset.
  // LP metadata is explicitly inactive under this full-reset reference
  // scope, matching the qualified source-system fixture. No guest tick or
  // active transaction is skipped, and no source register is installed.
  for(unsigned phase=0;phase<cfg.system_reset_phases;++phase){
    s.latency->evaluate(l);const auto lo=s.latency->sample();
    for(const auto&b:s.latency_to_system)m[b.to]=driven(Bits(lo[b.from]),b.width);
    s.system->evaluate(m);s.system->commit({phase%2==0,phase%2==0,true,phase%10==0});s.latency->commit();
  }
}
RocketSerialPeer::~RocketSerialPeer(){CallerScope caller;impl_.reset();}
std::span<const iris::fabric::Field>RocketSerialPeer::arbiter_input_fields()const{return SavedRocketDcacheArbiter::input_fields();}
std::span<const iris::fabric::Field>RocketSerialPeer::arbiter_output_fields()const{return SavedRocketDcacheArbiter::output_fields();}
std::span<const iris::fabric::Field>RocketSerialPeer::system_input_fields()const{return impl_->system->input_fields();}
std::span<const iris::fabric::Field>RocketSerialPeer::system_output_fields()const{return impl_->system->output_fields();}
std::span<const iris::fased::Field>RocketSerialPeer::latency_input_fields()const{return impl_->latency->input_fields();}
std::span<const iris::fased::Field>RocketSerialPeer::latency_output_fields()const{return impl_->latency->output_fields();}
bool RocketSerialPeer::arbiter_input_joined(unsigned i)const{return impl_->arbiter_joined.at(i);}
bool RocketSerialPeer::system_input_joined(unsigned i)const{return impl_->system_joined.at(i);}
bool RocketSerialPeer::latency_input_joined(unsigned i)const{return impl_->latency_joined.at(i);}
unsigned RocketSerialPeer::system_input_index(std::string_view c,std::string_view f)const{return impl_->system->external_input_index(c,f);}
unsigned RocketSerialPeer::system_output_index(std::string_view c,std::string_view f)const{return impl_->system->output_index(c,f);}
void RocketSerialPeer::evaluate(const Inputs&input){
  CallerScope caller;auto&s=*impl_;s.evaluated=false;
  if(input.arbiter.size()!=s.arbiter_joined.size()||input.system.size()!=s.system_joined.size()||input.latency.size()!=s.latency_joined.size())throw std::invalid_argument("IRIS_ROCKET_SERIAL_EXTERNAL_SCHEMA_COUNT");
  if(input.frontend.memory_request_ready||input.frontend.memory_data)throw std::invalid_argument("IRIS_ROCKET_SERIAL_JOINED_FRONTEND_INPUT");
  for(unsigned i=0;i<input.arbiter.size();++i)if(s.arbiter_joined[i]&&!zero(input.arbiter[i].value))throw std::invalid_argument("IRIS_ROCKET_SERIAL_JOINED_ARBITER_INPUT");
  for(unsigned i=0;i<input.system.size();++i)if(s.system_joined[i]&&!zero(input.system[i].value))throw std::invalid_argument("IRIS_ROCKET_SERIAL_JOINED_SYSTEM_INPUT");
  for(unsigned i=0;i<input.latency.size();++i)if(s.latency_joined[i]&&input.latency[i].value)throw std::invalid_argument("IRIS_ROCKET_SERIAL_JOINED_LP_INPUT");
  std::vector<Input>a(input.arbiter.begin(),input.arbiter.end()),m(input.system.begin(),input.system.end());std::vector<iris::fased::Bits>l(input.latency.begin(),input.latency.end());auto f=input.frontend;
  std::vector<Bits>cache_feedback(a.size());std::vector<uint64_t>lp_feedback(l.size());bool ready=false;std::optional<SavedRocketFrontend::DataBeat>data;
  auto equal_beat=[](const auto&a,const auto&b){return bool(a)==bool(b)&&(!a||(a->value==b->value&&a->corrupt==b->corrupt));};
  for(unsigned iteration=1;iteration<=64;++iteration){
    f.memory_request_ready=ready;f.memory_data=data;auto fo=s.frontend->evaluate(f);
    for(const auto&b:s.system_to_arbiter)a[b.to]=driven(cache_feedback[b.to],b.width);
    s.arbiter->evaluate(a);auto ao=s.arbiter->sample();
    for(const auto&b:s.arbiter_to_system)m[b.to]=driven(ao[b.from],b.width);
    m[s.ia_valid]=driven(Bits(fo.memory_request_valid),1);m[s.ia_address]=driven(Bits(fo.memory_request_address),35);
    for(const auto&b:s.system_to_latency)l[b.to]={lp_feedback[b.to],mask(b.width)};
    s.latency->evaluate(l);auto lo=s.latency->sample();for(const auto&b:s.latency_to_system)m[b.to]=driven(Bits(lo[b.from]),b.width);
    s.system->evaluate(m);auto mo=s.system->sample();const bool next_ready=mo[s.ia_ready].limbs[0];std::optional<SavedRocketFrontend::DataBeat>next_data;
    if(mo[s.id_valid].limbs[0]){if(mo[s.id_opcode].limbs[0]!=1||mo[s.id_size].limbs[0]!=6)throw std::runtime_error("IRIS_ROCKET_SERIAL_ICACHE_RETURN_PROTOCOL");next_data=SavedRocketFrontend::DataBeat{mo[s.id_data].limbs[0],bool(mo[s.id_corrupt].limbs[0])};}
    bool stable=ready==next_ready&&equal_beat(data,next_data);
    for(const auto&b:s.system_to_arbiter){stable&=cache_feedback[b.to]==mo[b.from];cache_feedback[b.to]=mo[b.from];}
    for(const auto&b:s.system_to_latency){const auto value=mo[b.from].limbs[0];stable&=lp_feedback[b.to]==value;lp_feedback[b.to]=value;}
    if(stable){s.observed={fo,std::move(ao),std::move(mo),std::move(lo),iteration};s.evaluated=true;return;}
    ready=next_ready;data=next_data;
  }
  throw std::runtime_error("IRIS_ROCKET_SERIAL_GLOBAL_FIXED_POINT_FAILED");
}
RocketSerialPeer::Outputs RocketSerialPeer::sample()const{CallerScope caller;if(!impl_->evaluated)throw std::logic_error("IRIS_ROCKET_SERIAL_SAMPLE_WITHOUT_EVALUATE");return impl_->observed;}
void RocketSerialPeer::commit(iris::memory_system::ClockEdges edge){
  CallerScope caller;auto&s=*impl_;if(!s.evaluated)throw std::logic_error("IRIS_ROCKET_SERIAL_COMMIT_WITHOUT_EVALUATE");s.evaluated=false;
  if(!edge.rocket&&!edge.uncore&&!edge.bringup&&!edge.serial)throw std::invalid_argument("IRIS_ROCKET_SERIAL_EMPTY_CLOCK_EVENT");
  if(edge.rocket){s.frontend->commit();s.arbiter->commit();}s.system->commit(edge);if(edge.bringup)s.latency->commit();
}
}
