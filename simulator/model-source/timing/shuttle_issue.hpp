#pragma once

#include <array>
#include <cstdint>
#include <string>
#include <vector>

namespace iris::shuttle {
// Unknown is a missing modeled input, not an arbitrary false hardware value.
enum class Signal : uint8_t { unknown, no, yes };
Signal both(Signal a, Signal b);
Signal either(Signal a, Signal b);
Signal inverse(Signal a);

enum Control : uint32_t {
  FP=1u<<0, ROCC=1u<<1, BRANCH=1u<<2, JAL=1u<<3, JALR=1u<<4,
  RXS1=1u<<5, RXS2=1u<<6, WXD=1u<<7, MEM=1u<<8, MUL=1u<<9,
  DIV=1u<<10, CSR_EN=1u<<11, SYSTEM=1u<<12, CSR_ANY=1u<<13,
  FENCE=1u<<14, FENCE_I=1u<<15, AMO=1u<<16, SFENCE=1u<<17,
  USES_FP=1u<<18, SETS_VCFG=1u<<19, VECTOR=1u<<20,
  RFS1=1u<<21, RFS2=1u<<22, RFS3=1u<<23, WFD=1u<<24
};

struct FrontendSignals {
  // This is the frontend's per-uop prediction tag, including wrong-BTB hits
  // on non-CFIs. It cannot be inferred from the instruction opcode alone.
  Signal predicted_next_pc_valid = Signal::unknown;
  // Scalar decode is followed by a Saturn vector override in the RTL. The
  // caller must explicitly confirm that it did not match this scalar template.
  Signal vector_decoder_legal = Signal::unknown;
  bool exception = false;
  static FrontendSignals sequential_fixture();
};

struct Uop {
  uint32_t expanded = 0, raw = 0, controls = 0;
  uint8_t rs1 = 0, rs2 = 0, rd = 0;
  bool compressed = false, exception = false, control_verified = false;
  Signal predicted_next_pc_valid = Signal::unknown;
  Signal sfb_branch = Signal::unknown;
  std::string instruction_name, decode_note;
  bool has(Control field) const { return (controls & field) != 0; }
  bool cfi() const { return has(BRANCH) || has(JAL) || has(JALR); }
  bool uses_alu() const;
};

// Exact masks/control rows generated from the enabled pinned scalar decoder
// tables. Input must already be RVC-expanded as in ShuttleFetchBuffer. This
// does not check CSR/privilege/FS/VS legality and never invents an ISA trap.
Uop decode_scalar(uint32_t expanded, uint32_t raw, FrontendSignals frontend);
size_t scalar_decode_rows();

struct Bypass {
  bool valid;
  uint8_t destination;
  bool can_bypass;
};
// The RTL visits ll, wb, com, mem, ex, each in lane order. Last matching
// entry wins, even when an earlier match was ready and the last one is not.
bool integer_hazard(uint8_t source, bool scoreboard_ready,
                    const std::vector<Bypass>& source_order);

struct IssueSignals {
  std::array<Signal,32> integer_hazard{};
  Signal ex_stall = Signal::unknown, csr_stall = Signal::unknown;
  Signal trace_stall = Signal::unknown, single_step = Signal::unknown;
  Signal ex_busy = Signal::unknown, mem_busy = Signal::unknown, com_busy = Signal::unknown;
  Signal integer_scoreboard_busy = Signal::unknown, fp_scoreboard_busy = Signal::unknown;
  Signal dmem_ordered = Signal::unknown, rocc_busy = Signal::unknown;
  Signal vector_backend_busy = Signal::unknown, vector_trap_busy = Signal::unknown;
  Signal rocc_command_ready = Signal::unknown, vconfig_inflight = Signal::unknown;
  // mem_uops_reg(1).valid && uses_latealu: the MEM ALU is occupied by an
  // older packet that will reuse it in WB. Required for same-packet X->M.
  Signal mem_lane1_will_be_latealu = Signal::unknown;
  static IssueSignals empty_pipeline_fixture();
};

enum class Availability { ready, blocked, needs_input };
enum class ResultStage { none, ex, mem, wb, unresolved };
struct LanePlan {
  Availability availability = Availability::needs_input;
  ResultStage integer_result_stage = ResultStage::none;
  Signal uses_memalu = Signal::no, uses_latealu = Signal::no;
  bool sfb_shadow = false;
  Signal sfb_shadow_known = Signal::unknown;
  std::vector<std::string> reasons, missing_inputs;
};
struct PacketPlan {
  std::vector<LanePlan> lanes;
  unsigned ready_prefix = 0;
};

// Source-specific RRD selection for exactly the configured two-lane Shuttle.
// All input uops are valid and in frontend order. It is not a retirement or
// latency model; EX/MEM/COM replay/flush/FP/memory/vector effects remain separate.
PacketPlan select_rrd(const std::vector<Uop>& packet, const IssueSignals& signals);

// Safe initial integration subset: both instructions are source-decoded pure
// integer ALUs (no CFI, config, trap, FP or memory). This does NOT forbid RAW:
// an accepted RAW pair is assigned MEM and must retain that extra stage.
bool pure_integer_bundle(const std::vector<Uop>& packet, const PacketPlan& plan);
unsigned maximum_issue_width(unsigned architectural_hart);
const char* stage_name(ResultStage stage);
}  // namespace iris::shuttle
