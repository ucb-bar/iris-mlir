#pragma once
#include "memory_fabric.hpp"
#include "serial_memory.hpp"
#include <memory>
#include <span>
#include <string>
#include <string_view>
#include <vector>
namespace iris::memory_system {
using Bits=iris::fabric::Bits;
using Input=iris::fabric::Input;
using Field=iris::fabric::Field;
struct ClockEdges {bool rocket=false,uncore=false,bringup=false,serial=false;};
// Source components from two explicitly bound elaborations. The outer boundary
// is the actual FASED AXI port; no latency or functional backing is supplied.
class System {
 public:
  System(unsigned seed,std::vector<std::string> source_arguments,
         iris::serial_memory::AssertionPolicy=iris::serial_memory::AssertionPolicy::SourceAlways);
  ~System();
  System(const System&)=delete;
  System&operator=(const System&)=delete;
  void evaluate(std::span<const Input>);
  std::vector<Bits> sample()const;
  void commit(ClockEdges);
  std::span<const Field> input_fields()const;
  std::span<const Field> output_fields()const;
  // component="path" addresses original SerialMemory fields. All other
  // components use original MemoryFabric component/physical-field names.
  unsigned external_input_index(std::string_view component,std::string_view field)const;
  unsigned output_index(std::string_view component,std::string_view field)const;
  unsigned last_settle_iterations()const;
  std::uint64_t committed_phases()const;
  static const char*source_identity();
 private:struct Impl;std::unique_ptr<Impl>impl_;
};
}
