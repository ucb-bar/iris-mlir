#pragma once
#include <array>
#include <cstdint>
#include <memory>
#include <optional>
namespace iris::frontend {
// Exact saved-Iris Shuttle frontend boundary. No memory-service cost or
// guest architectural effect lives here. This peer is M-mode/VM-disabled.
class SavedShuttleFrontend {
 public:
  struct BtbUpdate {bool valid=false,is_valid=false,mispredict=false;unsigned prediction_entry=0,cfi_type=0;uint64_t pc=0,target=0,branch_pc=0;};
  struct BhtUpdate {bool valid=false,branch=false,taken=false,mispredict=false;unsigned history=0,value=0;uint64_t pc=0;};
  struct RasUpdate {bool valid=false;unsigned head=0;uint64_t address=0;};
  struct DataBeat {uint64_t value=0;};
  struct Inputs {
    bool memory_request_ready=false;
    std::optional<DataBeat> memory_data;
    bool ready0=false,ready1=false;
    bool redirect_flush=false,redirect_valid=false,invalidate_icache=false;
    uint64_t redirect_pc=0;unsigned redirect_ras_head=0;
    BtbUpdate btb;BhtUpdate bht;RasUpdate ras;
  };
  struct Uop {
    bool valid=false,compressed=false,edge_instruction=false,exception=false;
    uint64_t pc=0,exception_cause=0;
    uint32_t raw=0,expanded=0;
    bool prediction_valid=false,btb_valid=false,small_forward_branch=false;
    uint64_t predicted_pc=0;
    unsigned btb_entry=0,bht_history=0,bht_value=0,ras_head=0;
  };
  struct Outputs {std::array<Uop,2> queue;bool memory_request_valid=false;uint64_t memory_request_address=0;};
  // Seed controls unreset implementation state. It is an explicit model
  // policy, never a measurement/attestation of physical power-on RAM bits.
  explicit SavedShuttleFrontend(uint64_t seed,unsigned reset_edges);
  ~SavedShuttleFrontend();
  SavedShuttleFrontend(SavedShuttleFrontend&&) noexcept;
  SavedShuttleFrontend& operator=(SavedShuttleFrontend&&) noexcept;
  Outputs evaluate(const Inputs&);
  void commit();
 private:
  struct Impl;std::unique_ptr<Impl> impl_;
};
}
