#pragma once
#include <cstdint>
#include <optional>
namespace iris::frontend {
// Exact saved Rocket IBuf physical transfer: one 4-byte fetch packet, one
// decoded instruction, one retained 16-bit parcel. No predictor/cache latency.
class FastRocketIBuf {
 public:
  struct Exceptions {bool page=false,guest_page=false,access=false;bool operator==(const Exceptions&)const=default;};
  struct Packet {
    uint64_t pc=0;uint32_t data=0,known=0;
    bool taken=false,branch_index=false,replay=false;unsigned entry=0,history=0;
    Exceptions exception;
  };
  struct Inputs {bool reset=false,valid=false,ready=false,kill=false;Packet packet;};
  struct Outputs {
    bool imem_ready=false,ready_known=false,valid=false,compressed=false,replay=false;
    uint64_t pc=0;uint32_t raw=0,raw_known=0,expanded=0;
    unsigned rd=0,rs1=0,rs2=0,rs3=0,entry=0,history=0;
    bool instruction_known=false,rs3_known=false;
    Exceptions first,second;
  };
  Outputs evaluate(const Inputs&);
  void commit();
 private:
  bool buffered_=false,evaluated_=false,next_buffered_=false,reset_seen_=false,pending_reset_=false;
  std::optional<Packet>buffer_,capture_;
};
}
