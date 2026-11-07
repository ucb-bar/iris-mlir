#pragma once
#include "frontend_rtl_peer.hpp"
#include <memory>

namespace iris::frontend {
// Source-derived plain C++ frontend. Same physical peer boundary as the saved
// RTL wrapper: external A.ready/eight D beats, actual RRD ready prefix and MEM
// redirect/predictor/RAS updates only. No latency, queue-state or prediction
// injection. First scope is M-mode with the saved executable PMA policy.
class FastSourceFrontend {
 public:
  using Inputs=SavedShuttleFrontend::Inputs;
  using Outputs=SavedShuttleFrontend::Outputs;
  using Uop=SavedShuttleFrontend::Uop;
  using DataBeat=SavedShuttleFrontend::DataBeat;
  FastSourceFrontend(uint64_t unreset_seed,unsigned reset_edges);
  ~FastSourceFrontend();
  FastSourceFrontend(FastSourceFrontend&&) noexcept;
  FastSourceFrontend& operator=(FastSourceFrontend&&) noexcept;
  Outputs evaluate(const Inputs&);
  void commit();
 private:
  struct Impl;std::unique_ptr<Impl> impl_;
};
}
