#pragma once
#include "saturn_timing.hpp"
#include <cstdint>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <vector>
namespace iris::saturn {
struct CallerCommand {
 std::uint64_t node,pc;std::uint32_t instruction;
};
struct OwnedCommand {
 unsigned hart;std::uint64_t reset_epoch,sequence,EX_edge,pc;
 std::uint32_t instruction;bool EX_fired;
 std::optional<std::uint64_t> caller_node;
 bool operator==(const OwnedCommand&)const=default;
};
struct OwnedDispatch {
 std::uint64_t sequence,edge,VAT_generation;
 unsigned VAT;std::uint16_t debug_id;
 OwnedCommand command;
 bool operator==(const OwnedDispatch&)const=default;
};
struct OwnershipEvent {
 std::string kind,source;
 std::uint64_t edge,tb;
 std::optional<OwnedCommand> command;
 std::optional<OwnedDispatch> dispatch;
 std::optional<std::uint64_t> micro_sequence;
 std::uint64_t amount=0;
 std::optional<std::uint64_t> transaction,address,bytes,byte_mask;
 std::optional<unsigned> transport_source,memory_tag;
 std::optional<unsigned> bank,element_group;
 std::vector<std::uint64_t> bit_mask;
 // Raw values of source-selected physical writes. bit_mask is a write-enable
 // mask, not initializedness. Except source zero/scrub, the caller must prove
 // operand definedness independently before publishing functional values.
 std::vector<std::uint64_t> data_words;
 std::optional<unsigned> mrf_cell,mrf_entry;
 std::optional<unsigned> scalar_rd;
 bool operator==(const OwnershipEvent&)const=default;
};
struct PendingOwnership {
 std::map<std::string,std::uint64_t> live;
 bool empty()const{for(const auto&[name,count]:live)if(count)return false;return true;}
};
// Observer metadata only. Never drives source queues, readiness, data or time.
// A caller node is accepted only on that exact source S0 command handshake.
// This object must start with actual reset; missing/ambiguous lifetimes refuse.
class SaturnOwnershipLedger {
 public:
 SaturnOwnershipLedger(Profile,unsigned hart,std::span<const Field> activity,
                       std::span<const Field> ownership);
 ~SaturnOwnershipLedger();
 SaturnOwnershipLedger(SaturnOwnershipLedger&&)noexcept;
 SaturnOwnershipLedger&operator=(SaturnOwnershipLedger&&)noexcept;
 std::vector<OwnershipEvent> observe(const ReferenceInputs&,const ReferenceOutputs&,
                const ActivitySnapshot&,const ActivitySnapshot&,
                std::optional<CallerCommand> accepted_EX_caller={});
 std::uint64_t edges()const;
 const std::map<std::string,std::uint64_t>&counts()const;
 // Conservative metadata lifetimes, not a substitute for source port/queue
 // eligibility and not permission to skip any source edge.
 PendingOwnership pending()const;
 // Unique commands still referenced by any live source-unit metadata token.
 // Union these references with the CPU's own unretired/speculative lifetimes.
 std::vector<OwnedCommand> live_commands()const;
 private:struct Impl;std::unique_ptr<Impl> impl_;
};
}
