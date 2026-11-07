#pragma once
#include "empirical_engine.hpp"

namespace iris {
// Explicit parameters for a shared transfer port. No Iris timing defaults live
// here: address routing, cache state and calibration belong to the adapter.
struct MemoryPort {
  std::string resource;
  unsigned lanes;
  std::string source;
};

struct MemoryStage {
  std::string name;
  std::string resource;
  Cycle first_beat_latency;
  Cycle launch_interval;
  // Zero bytes means a fixed-latency stage. For a transfer, each packet holds
  // the port for ceil(bytes/beat_bytes)*beat_interval base-clock ticks.
  uint64_t bytes = 0;
  uint64_t beat_bytes = 0;
  Cycle beat_interval = 0;
  std::string source;
  // Zero explicitly inherits the request's alignment (legacy fixtures).
  // Real asynchronous paths must supply the stage's own clock period.
  Cycle issue_period = 0;
};

struct MemoryRequest {
  unsigned hart;
  uint64_t address;
  uint64_t access_bytes;
  std::string path;
  std::vector<MemoryStage> stages;
  std::vector<NodeId> dependencies{};
  uint64_t pc = 0;
  std::string region{};
  Cycle issue_period = 1;
  std::function<void()> on_complete{};
};

struct MemoryTransfer {
  uint64_t address;
  uint64_t access_bytes;
  std::string path;
  std::vector<NodeId> nodes;
  struct StageReceipt {
    MemoryStage parameters;
    Cycle derived_latency;
    Cycle derived_interval;
    std::string port_source;
  };
  std::vector<StageReceipt> parameters;
};

class MemoryPaths {
 public:
  MemoryPaths(Machine& machine, std::vector<MemoryPort> ports);
  // Schedule every request/response stage on the same global event graph as
  // all other harts. Guest-visible memory effects belong in on_complete only.
  MemoryTransfer submit(MemoryRequest request);
 private:
  Machine& machine_;
  std::map<std::string, MemoryPort> ports_;
};
}  // namespace iris
