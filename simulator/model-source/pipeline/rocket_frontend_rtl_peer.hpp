#pragma once
#include <array>
#include <cstdint>
#include <memory>
#include <optional>
namespace iris::frontend {
// Actual saved Rocket Frontend -> IBuf wiring. One call to commit advances
// one direct source core edge. Memory service and CPU replay belong outside.
class SavedRocketFrontend {
 public:
  struct Pmp {bool locked=false;unsigned mode=0;bool execute=false,write=false,read=false;uint64_t address=0,mask=0;};
  struct BtbUpdate {bool valid=false,is_valid=false;unsigned prediction_entry=0,cfi_type=0;uint64_t pc=0,branch_pc=0;};
  struct BhtUpdate {bool valid=false,branch=false,taken=false,mispredict=false;unsigned history=0;uint64_t pc=0;};
  struct DataBeat {uint64_t value=0;bool corrupt=false;};
  struct Inputs {
    bool memory_request_ready=false;std::optional<DataBeat> memory_data;
    bool id_ready=false,might_request=false,progress=false;
    bool redirect_valid=false,redirect_speculative=false,invalidate_icache=false;
    uint64_t redirect_pc=0,custom_csr=0;
    std::array<Pmp,8> pmp{};
    BtbUpdate btb;BhtUpdate bht;
  };
  struct Uop {
    bool valid=false,compressed=false,replay=false;
    bool first_page_fault=false,first_guest_page_fault=false,first_access_fault=false;
    bool second_page_fault=false,second_guest_page_fault=false,second_access_fault=false;
    uint64_t pc=0;uint32_t raw=0,expanded=0;
    unsigned btb_entry=0,bht_history=0;
  };
  struct Outputs {
    Uop id;
    bool fetch_valid=false,fetch_replay=false;
    uint64_t fetch_pc=0;
    bool memory_request_valid=false;uint64_t memory_request_address=0;
  };
  explicit SavedRocketFrontend(uint64_t seed,unsigned reset_edges);
  ~SavedRocketFrontend();
  SavedRocketFrontend(SavedRocketFrontend&&) noexcept;
  SavedRocketFrontend& operator=(SavedRocketFrontend&&) noexcept;
  Outputs evaluate(const Inputs&);
  void commit();
 private:
  struct Impl;std::unique_ptr<Impl> impl_;
};
}
