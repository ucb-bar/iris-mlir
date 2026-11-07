#pragma once
#include "serial_memory_system.hpp"
#include "fased_service.hpp"
#include "rocket_dcache_arbiter.hpp"
#include "rocket_frontend_rtl_peer.hpp"
#include <memory>
#include <span>
#include <string>
#include <vector>
namespace iris::frontend {
// New epoch: source CPU producers + full source memory path + separate LP.
// Functional egress and the full host-token bridge remain external/scoped.
class RocketSerialPeer {
 public:
  using Bits=iris::memory_system::Bits;
  using Input=iris::memory_system::Input;
  struct Configuration {
    unsigned seed=0,frontend_reset_edges=0,system_reset_phases=0;
    std::vector<std::string> source_arguments;
    iris::serial_memory::AssertionPolicy assertion_policy=iris::serial_memory::AssertionPolicy::SourceAlways;
    iris::fased::Parameters latency{0,0,0,0};
    std::string expected_system_identity,expected_latency_identity;
  };
  struct Inputs {
    SavedRocketFrontend::Inputs frontend;
    std::span<const Input> arbiter,system;
    std::span<const iris::fased::Bits> latency;
  };
  struct Outputs {
    SavedRocketFrontend::Outputs frontend;
    std::vector<Bits> arbiter,system;
    std::vector<uint64_t> latency;
    unsigned global_settle_iterations=0;
  };
  explicit RocketSerialPeer(Configuration);
  ~RocketSerialPeer();
  RocketSerialPeer(const RocketSerialPeer&)=delete;
  RocketSerialPeer&operator=(const RocketSerialPeer&)=delete;
  void evaluate(const Inputs&);
  Outputs sample()const;
  void commit(iris::memory_system::ClockEdges);
  std::span<const iris::fabric::Field> arbiter_input_fields()const;
  std::span<const iris::fabric::Field> arbiter_output_fields()const;
  std::span<const iris::fabric::Field> system_input_fields()const;
  std::span<const iris::fabric::Field> system_output_fields()const;
  std::span<const iris::fased::Field> latency_input_fields()const;
  std::span<const iris::fased::Field> latency_output_fields()const;
  bool arbiter_input_joined(unsigned)const;
  bool system_input_joined(unsigned)const;
  bool latency_input_joined(unsigned)const;
  unsigned system_input_index(std::string_view,std::string_view)const;
  unsigned system_output_index(std::string_view,std::string_view)const;
 private:struct Impl;std::unique_ptr<Impl>impl_;
};
}
