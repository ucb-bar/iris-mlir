#pragma once
#include "saturn_timing_ownership.hpp"
#include <functional>
#include <string>

namespace iris::saturn {
// An initial, deliberately finite integration capability. Every encoding is
// supplied by a provenance-bound caller manifest; no unsupported instruction
// falls back to an estimated latency. Physical memory/FPU service is not yet
// part of this bridge and is rejected before its accepting source edge.
struct InstructionPattern {std::uint32_t mask,value;};
enum class InactiveInputConvention {AllInputsDefined,SourceQualifiedInactivePayload};
struct LiveBridgeCapability {
 std::string manifest_identity;
 std::vector<InstructionPattern> instructions;
 InactiveInputConvention inactive_payload=InactiveInputConvention::AllInputsDefined;
};
struct DefinedReferenceInputs {
 ReferenceInputs values;
 // Per physical field word, one bit for each known input bit. This is a
// caller assertion about the real driver, never inferred from a numeric zero.
 std::vector<std::uint64_t> known_bits;
};
class SaturnLiveBridge {
 public:
 SaturnLiveBridge(Profile,unsigned hart,std::uint32_t seed,LiveBridgeCapability);
 ~SaturnLiveBridge();
 SaturnLiveBridge(SaturnLiveBridge&&)noexcept;
 SaturnLiveBridge&operator=(SaturnLiveBridge&&)noexcept;
 SaturnLiveBridge(const SaturnLiveBridge&)=delete;
 SaturnLiveBridge&operator=(const SaturnLiveBridge&)=delete;
 // Repeated evaluation adds no source edge, callback, or ownership mutation.
 // The global caller must settle peer handshakes before calling commit.
 void evaluate(const DefinedReferenceInputs&);
 ReferenceOutputs sample()const;
 // Metadata is accepted only on the matching actual PFC S0 handshake.
 // Returned events are pre-edge observations committed on exactly this edge.
 // Functional numeric intents remain caller-owned and private until their
 // corresponding physical masked write/response event. This bridge never
 // invokes Spike::step or publishes any guest memory/register side effect.
 std::vector<OwnershipEvent> commit(std::uint64_t global_tick,
                                  std::optional<CallerCommand> caller={});
 std::uint64_t edges()const;
 PendingOwnership pending()const;
 std::vector<OwnedCommand> live_commands()const;
 static const char* component_source_identity();
 private:struct Impl;std::unique_ptr<Impl>impl_;
};
}
