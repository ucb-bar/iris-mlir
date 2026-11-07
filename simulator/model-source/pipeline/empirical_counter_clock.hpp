#pragma once
#include <cstdint>
#include <exception>
#include <optional>
#include <stdexcept>
#include <utility>

namespace iris::frontend {
// CSR.scala650/1093–1095/1231: reg_cycle runs on ungated_clock and uses
// pre-edge !csr_stall. Locally enabled pending interrupts clear reg_wfi,
// independently of global mstatus.MIE. CY and counter-write priority remain
// owned by the existing source-matched wide-counter implementation.
class SourceCounterClock {
 public:
  struct Input {bool locally_enabled_pending=false;std::exception_ptr unknown_pending;};
  struct Edge {uint64_t cycle=0;bool stall_before=false,stall_after=false,count_enable=false,pending=false;};
  explicit SourceCounterClock(uint64_t period):period_(period) {
    if(!period)throw std::invalid_argument("IRIS_SOURCE_COUNTER_CLOCK_PERIOD_ZERO");
  }
  Edge sample(uint64_t cycle,Input input) {
    if(!cycle || cycle==UINT64_MAX || cycle%period_ || (edge_?(cycle<=edge_->cycle || cycle-edge_->cycle!=period_):cycle!=period_))
      throw std::logic_error("IRIS_SOURCE_COUNTER_SKIPPED_UNGATED_EDGE");
    if(wfi_ && !cease_ && input.unknown_pending)std::rethrow_exception(input.unknown_pending);
    const bool stall=wfi_||cease_;
    wfi_=wfi_ && !input.locally_enabled_pending;
    Edge next{cycle,stall,wfi_||cease_,!stall,input.locally_enabled_pending};edge_=next;input_=input;return next;
  }
  // Bulk equivalent of repeated source samples under unchanged pending inputs.
  // Caller must split spans at every modeled device/CSR/interrupt change.
  std::pair<uint64_t,uint64_t> sample_span(uint64_t cycle,Input input) {
    if(!cycle||cycle==UINT64_MAX||cycle%period_||cycle<next_edge())throw std::logic_error("IRIS_EMPIRICAL_COUNTER_SPAN_EDGE");
    const auto first=next_edge(),edges=(cycle-first)/period_+1;
    if(wfi_&&!cease_&&input.unknown_pending)std::rethrow_exception(input.unknown_pending);
    const bool before=wfi_||cease_;wfi_=wfi_&&!input.locally_enabled_pending;const bool after=wfi_||cease_;
    const uint64_t inactive=before?(after?edges:1):0;
    const bool final_before=edges==1?before:after;
    edge_=Edge{cycle,final_before,after,!final_before,input.locally_enabled_pending};input_=input;
    return {inactive,edges-inactive};
  }
  void retire_wfi(uint64_t cycle) {
    require_edge(cycle);
    if(input_.unknown_pending)std::rethrow_exception(input_.unknown_pending);
    // The source pending-interrupt assignment has priority over WFI entry
    // on the same edge. WFI does not write mie or global interrupt enables.
    wfi_=!input_.locally_enabled_pending;edge_->stall_after=wfi_||cease_;
  }
  void retire_cease(uint64_t cycle) {require_edge(cycle);cease_=true;edge_->stall_after=true;}
  void exception(uint64_t cycle) {require_edge(cycle);wfi_=false;edge_->stall_after=cease_;}
  bool stalled() const {return wfi_||cease_;}
  bool ceased() const {return cease_;}
  bool blocks_rrd(uint64_t cycle) const {
    return stalled() || (edge_ && edge_->cycle==cycle && edge_->stall_before);
  }
  uint64_t next_edge() const {
    const auto last=edge_?edge_->cycle:0;
    if(period_>=UINT64_MAX-last)throw std::overflow_error("IRIS_SOURCE_COUNTER_CLOCK_OVERFLOW");
    return last+period_;
  }
  const std::optional<Edge>& edge() const {return edge_;}
 private:
  uint64_t period_;bool wfi_=false,cease_=false;
  std::optional<Edge> edge_;Input input_;
  void require_edge(uint64_t cycle) const {
    if(!edge_ || edge_->cycle!=cycle)throw std::logic_error("IRIS_SOURCE_COUNTER_WFI_UNMODELED_COMPLETION_EDGE");
  }
};
}
