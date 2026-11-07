#include "empirical_memory_path.hpp"
#include <algorithm>
#include <limits>
#include <stdexcept>
#include <utility>

namespace iris {
namespace {
Cycle add(Cycle a, Cycle b) {
  if (b >= std::numeric_limits<Cycle>::max() - a)
    throw std::overflow_error("memory transfer cycle overflow");
  return a + b;
}
Cycle multiply(Cycle a, Cycle b) {
  if (b && a > (std::numeric_limits<Cycle>::max() - 1) / b)
    throw std::overflow_error("memory transfer cycle overflow");
  return a * b;
}
}

MemoryPaths::MemoryPaths(Machine& machine, std::vector<MemoryPort> ports)
    : machine_(machine) {
  if (ports.empty()) throw std::invalid_argument("explicit memory ports required");
  for (auto& port : ports) {
    if (port.resource.empty() || !port.lanes || port.source.empty() ||
        !ports_.emplace(port.resource, port).second)
      throw std::invalid_argument("invalid, unsourced or duplicate memory port");
  }
  for (const auto& port : ports_) machine_.configure_resource(port.first, port.second.lanes);
}

MemoryTransfer MemoryPaths::submit(MemoryRequest request) {
  if (!request.access_bytes || request.access_bytes - 1 > UINT64_MAX - request.address ||
      request.path.empty() || request.stages.empty() || !request.issue_period)
    throw std::invalid_argument("explicit bounded memory access and path required");
  std::vector<Op> operations;
  // Validate every stage before the first graph mutation.
  for (const auto& stage : request.stages) {
    if (stage.name.empty() || !ports_.count(stage.resource) || stage.source.empty() ||
        !stage.first_beat_latency || !stage.launch_interval ||
        stage.first_beat_latency == UINT64_MAX || stage.launch_interval == UINT64_MAX)
      throw std::invalid_argument("invalid or unsourced memory stage");
    Cycle latency = stage.first_beat_latency, interval = stage.launch_interval;
    if (stage.bytes) {
      if (!stage.beat_bytes || !stage.beat_interval)
        throw std::invalid_argument("transfer width and beat interval are required");
      const uint64_t beats = 1 + (stage.bytes - 1) / stage.beat_bytes;
      latency = add(latency, multiply(beats - 1, stage.beat_interval));
      interval = std::max(interval, multiply(beats, stage.beat_interval));
    } else if (stage.beat_bytes || stage.beat_interval)
      throw std::invalid_argument("fixed latency stages have no beat parameters");
    Op operation{request.hart, request.path + "." + stage.name, stage.resource, latency, interval};
    operation.pc = request.pc; operation.region = request.region;
    operation.issue_period = stage.issue_period ? stage.issue_period : request.issue_period;
    operation.retires = false;
    operation.bytes = stage.bytes;
    operations.push_back(std::move(operation));
  }
  operations.back().on_complete = std::move(request.on_complete);
  MemoryTransfer result{request.address, request.access_bytes, request.path, {}, {}};
  for (size_t index=0; index<operations.size(); ++index) {
    auto& operation = operations[index];
    const auto& stage = request.stages[index];
    result.parameters.push_back({stage, operation.latency, operation.interval, ports_.at(stage.resource).source});
    operation.deps = result.nodes.empty() ? request.dependencies : std::vector<NodeId>{result.nodes.back()};
    result.nodes.push_back(machine_.issue(std::move(operation)));
  }
  return result;
}
}  // namespace iris
