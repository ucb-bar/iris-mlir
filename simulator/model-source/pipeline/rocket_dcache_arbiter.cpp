#include "rocket_dcache_arbiter.hpp"
#include "VHellaCacheArbiter_1.h"
#include "verilated.h"
#include <limits>
#include <stdexcept>
#include <string>
namespace iris::frontend {
namespace {
struct ContextScope {
  VerilatedContext* prior;
  explicit ContextScope(VerilatedContext* own):prior(Verilated::threadContextp()){Verilated::threadContextp(own);}
  ContextScope(VerilatedContext* own,VerilatedContext* saved):prior(saved){Verilated::threadContextp(own);}
  ~ContextScope(){Verilated::threadContextp(prior);}
};
bool fits(const iris::fabric::Bits& value,unsigned width) {
  for(unsigned bit=width;bit<256;++bit)if((value.limbs[bit/64]>>(bit%64))&1)return false;
  return true;
}
bool fully_known(const iris::fabric::Bits& value,unsigned width) {
  for(unsigned bit=0;bit<width;++bit)if(!((value.limbs[bit/64]>>(bit%64))&1))return false;
  return fits(value,width);
}
}
struct SavedRocketDcacheArbiter::Impl {
  // Capture before VerilatedContext's constructor changes threadContextp.
  VerilatedContext* constructor_caller=Verilated::threadContextp();
  std::unique_ptr<VerilatedContext> context;
  std::unique_ptr<VHellaCacheArbiter_1> dut;
  bool evaluated=false;
  std::vector<Bits> output;
  Impl(unsigned seed,unsigned reset_edges):context(std::make_unique<VerilatedContext>()) {
    ContextScope scope(context.get(),constructor_caller);
    if(!seed || seed>unsigned(INT32_MAX) || reset_edges<2)throw std::invalid_argument("IRIS_ARBITER_EXPLICIT_SEED_AND_QUIESCENT_RESET_REQUIRED");
    context->randSeed(seed);context->randReset(2);context->threads(1);dut=std::make_unique<VHellaCacheArbiter_1>(context.get());
#define ZERO_INPUTS
#include "rocket_dcache_arbiter_pins.inc"
#undef ZERO_INPUTS
    // The source has no reset port. Under actual system reset its requestors
    // are quiescent; two genuine edges make its source S1/S2 ownership known.
    for(unsigned i=0;i<reset_edges;++i)edge();
  }
  ~Impl() {
    auto* old=Verilated::threadContextp();const bool self=old==context.get();Verilated::threadContextp(context.get());dut.reset();context.reset();
    Verilated::threadContextp(self?Verilated::defaultContextp():old);
  }
  void edge(){ContextScope scope(context.get());dut->clock=1;dut->eval();context->timeInc(1);dut->clock=0;dut->eval();context->timeInc(1);}
};
SavedRocketDcacheArbiter::SavedRocketDcacheArbiter(unsigned seed,unsigned reset_edges):impl_(std::make_unique<Impl>(seed,reset_edges)){}
SavedRocketDcacheArbiter::~SavedRocketDcacheArbiter()=default;
std::span<const SavedRocketDcacheArbiter::Field> SavedRocketDcacheArbiter::input_fields(){
#define INPUT_FIELDS
#include "rocket_dcache_arbiter_pins.inc"
#undef INPUT_FIELDS
}
std::span<const SavedRocketDcacheArbiter::Field> SavedRocketDcacheArbiter::output_fields(){
#define OUTPUT_FIELDS
#include "rocket_dcache_arbiter_pins.inc"
#undef OUTPUT_FIELDS
}
void SavedRocketDcacheArbiter::evaluate(std::span<const Input> in) {
  ContextScope scope(impl_->context.get());impl_->evaluated=false;const auto fields=input_fields();
  if(in.size()!=fields.size())throw std::invalid_argument("IRIS_ARBITER_INPUT_COUNT");
  for(unsigned i=0;i<in.size();++i){
    if(!fits(in[i].value,fields[i].width))throw std::invalid_argument("IRIS_ARBITER_INPUT_WIDTH "+std::string(fields[i].name));
    if(!fully_known(in[i].known,fields[i].width))throw std::runtime_error("IRIS_ARBITER_UNKNOWN_INPUT "+std::string(fields[i].name));
  }
  auto& dut=impl_->dut;
#define SET_INPUTS
#include "rocket_dcache_arbiter_pins.inc"
#undef SET_INPUTS
  dut->eval();impl_->output.assign(output_fields().size(),Bits{});auto& out=impl_->output;
#define GET_OUTPUTS
#include "rocket_dcache_arbiter_pins.inc"
#undef GET_OUTPUTS
  impl_->evaluated=true;
}
std::vector<SavedRocketDcacheArbiter::Bits> SavedRocketDcacheArbiter::sample()const {
  ContextScope scope(impl_->context.get());
  if(!impl_->evaluated)throw std::logic_error("IRIS_ARBITER_SAMPLE_WITHOUT_EVALUATE");return impl_->output;
}
void SavedRocketDcacheArbiter::commit() {
  ContextScope scope(impl_->context.get());
  if(!impl_->evaluated)throw std::logic_error("IRIS_ARBITER_COMMIT_WITHOUT_EVALUATE");impl_->edge();impl_->evaluated=false;
}
}
