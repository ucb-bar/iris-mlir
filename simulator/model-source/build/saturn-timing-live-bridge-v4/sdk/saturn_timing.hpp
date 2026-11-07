#pragma once
#include <cstdint>
#include <memory>
#include <span>
#include <string_view>
#include <string>
#include <optional>
#include <vector>
namespace iris::saturn {
enum class Profile {Compute128,Rocket256};
// This explicit policy prevents a two-state reference datapath from silently
// claiming initialized architectural VRF/tile state or production availability.
enum class ReferenceScope {ExplicitTwoStateWithExternalDefinedness};
struct Field {std::string_view name;unsigned physical_width,word_offset,word_width;};
struct ReferenceInputs {std::vector<std::uint64_t> words;};
struct ReferenceOutputs {std::vector<std::uint64_t> words;};
struct ActivitySnapshot {
 unsigned hart;std::uint64_t source_edge;std::string_view source_clock_id;
 std::optional<std::uint64_t> uncore_tick;
 // Raw source bits. Knownness/valid/operation qualifiers are mandatory.
 std::vector<std::uint64_t> words;
};
struct DispatchRegisters {
 std::uint64_t source_edge;std::uint16_t next_debug_id;
 unsigned vat_head,vat_tail;std::uint32_t allocated_mask;
};
class SaturnTimingCut {
 public:
 SaturnTimingCut(Profile,unsigned hart,std::uint32_t seed,ReferenceScope);
 SaturnTimingCut(Profile,unsigned hart,std::uint32_t seed,ReferenceScope,std::span<const std::string_view> context_plusargs);
 // Actual owner-context plusarg query; no source edge advances.
 std::string context_argument(std::string_view prefix)const;
 ~SaturnTimingCut();
 SaturnTimingCut(SaturnTimingCut&&)noexcept;
 SaturnTimingCut&operator=(SaturnTimingCut&&)noexcept;
 SaturnTimingCut(const SaturnTimingCut&)=delete;
 SaturnTimingCut&operator=(const SaturnTimingCut&)=delete;
 // Exact scalar EX/MEM/COM + TLB/ATL/Hella/shared-FPU physical ports.
 // No implicit reset, initialization, command retirement, or memory service.
 // Repeated settle adds no edge; caller may converge same-edge ready/credit.
 void evaluate(const ReferenceInputs&);
 ReferenceOutputs sample()const;
 // Pre-edge only; no state/edge/readiness injection. Caller timestamp is metadata.
 ActivitySnapshot sample_activity(std::optional<std::uint64_t> uncore_tick={})const;
 static std::span<const Field> activity_fields(Profile);
 // Read-only supplementary metadata; no automatic payload knownness.
 ActivitySnapshot sample_ownership(std::optional<std::uint64_t> uncore_tick={})const;
 static std::span<const Field> ownership_fields(Profile);
 // Raw registered state, also available after commit for dispatch generations.
 DispatchRegisters inspect_dispatch_registers()const;
 void commit();
 ReferenceOutputs step(const ReferenceInputs&);
 std::uint64_t edges()const;
 Profile profile()const;
 unsigned hart()const;
 static std::span<const Field> input_fields(Profile);
 static std::span<const Field> output_fields(Profile);
 static std::string_view source_clock_id(Profile,unsigned hart);
 static const char* source_identity();
 private:struct Impl;std::unique_ptr<Impl> impl_;
};
}
