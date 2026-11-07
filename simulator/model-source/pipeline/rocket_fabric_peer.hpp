#pragma once
#include "memory_fabric.hpp"
#include "rocket_dcache_arbiter.hpp"
#include "rocket_frontend_rtl_peer.hpp"
#include <memory>
#include <span>
#include <vector>
namespace iris::frontend {
// Same-edge composition of actual Rocket Frontend/IBuf, its scalar/PTW/vector
// Dcache arbiter, and the independently frozen cache/CDC/NoC/L2 source fabric.
// External manager/service/other-client inputs remain explicit physical pins.
class RocketFabricPeer {
 public:
  using Input=iris::fabric::Input;
  using Bits=iris::fabric::Bits;
  struct Inputs {
    SavedRocketFrontend::Inputs frontend;
    // Full physical schemas, but internally joined entries must be zero and
    // cannot be driven by the caller. No recorded ready/data is admitted.
    std::span<const Input> arbiter;
    std::span<const Input> fabric;
  };
  struct Outputs {
    SavedRocketFrontend::Outputs frontend;
    std::vector<Bits> arbiter;
    std::vector<Bits> fabric;
    unsigned global_settle_iterations=0;
  };
  explicit RocketFabricPeer(unsigned seed,unsigned reset_edges);
  ~RocketFabricPeer();
  RocketFabricPeer(const RocketFabricPeer&)=delete;
  RocketFabricPeer&operator=(const RocketFabricPeer&)=delete;
  void evaluate(const Inputs&);
  Outputs sample() const;
  void commit(iris::fabric::ClockEdges);
  static std::span<const iris::fabric::Field> arbiter_input_fields();
  static std::span<const iris::fabric::Field> arbiter_output_fields();
  static std::span<const iris::fabric::Field> fabric_input_fields();
  static std::span<const iris::fabric::Field> fabric_output_fields();
  bool arbiter_input_joined(unsigned) const;
  bool fabric_input_joined(unsigned) const;
 private:
  struct Impl;std::unique_ptr<Impl> impl_;
};
}
