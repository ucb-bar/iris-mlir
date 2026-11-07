#pragma once
#include "memory_fabric_adapters.hpp"
#include <memory>
#include <span>
#include <string_view>
#include <vector>
namespace iris::fabric {
struct Input {Bits value;Bits known;};
struct ClockEdges {bool rocket=false;bool uncore=false;};
// Rocket cache + exact tile width/arbiter/CDC + complete NoC + L2/manager.
// External fields are physical source boundaries, not fitted readiness/state.
// Every supplied input bit must be explicitly known; unknown input refuses the
// edge. Numeric output payloads do not themselves certify internal reset state.
class MemoryFabric {
 public:
  explicit MemoryFabric(std::uint32_t random_seed=1);
  ~MemoryFabric();
  MemoryFabric(const MemoryFabric&)=delete;
  MemoryFabric&operator=(const MemoryFabric&)=delete;
  void evaluate(std::span<const Input>);
  std::vector<Bits> sample()const;
  void commit(ClockEdges);
  std::vector<Bits> step(std::span<const Input>,ClockEdges);
  std::uint64_t rocket_edges()const;
  std::uint64_t uncore_edges()const;
  unsigned last_settle_iterations()const;
  static std::span<const Field> input_fields();
  static std::span<const Field> output_fields();
  // Only external inputs can be driven. Joined TL ready/data are unavailable
  // through this lookup and are computed by their real source peers.
  static unsigned external_input_index(std::string_view component,std::string_view physical_field);
  static unsigned output_index(std::string_view component,std::string_view physical_field);
  static const char*source_identity();
 private:struct Impl;std::unique_ptr<Impl>impl_;
};
}
