#pragma once
#include "memory_fabric.hpp"
#include <memory>
#include <span>
#include <vector>
namespace iris::frontend {
// Exact saved HellaCacheArbiter_1 physical boundary. Requestor1 is the
// scalar Rocket producer;0 is PTW and2 is Saturn. No sourceID is invented.
class SavedRocketDcacheArbiter {
 public:
  using Bits=iris::fabric::Bits;
  using Input=iris::fabric::Input;
  using Field=iris::fabric::Field;
  explicit SavedRocketDcacheArbiter(unsigned seed,unsigned quiescent_reset_edges);
  ~SavedRocketDcacheArbiter();
  SavedRocketDcacheArbiter(const SavedRocketDcacheArbiter&)=delete;
  SavedRocketDcacheArbiter&operator=(const SavedRocketDcacheArbiter&)=delete;
  void evaluate(std::span<const Input>);
  std::vector<Bits> sample() const;
  void commit();
  static std::span<const Field> input_fields();
  static std::span<const Field> output_fields();
 private:
  struct Impl;std::unique_ptr<Impl> impl_;
};
}
