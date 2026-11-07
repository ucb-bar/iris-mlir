#pragma once
#include "frontend_cache_pipeline.hpp"
#include <array>
#include <cstdint>
#include <optional>

namespace iris::frontend {
// Saved ShuttleICache's qualified tag/control implementation plus its actual
// synchronous data/tag RAM ports. Untouched RAM bytes remain unknown.
class FastSourceICache {
 public:
  struct Inputs {
    bool reset=false,request_valid=false;
    uint64_t request_vaddr=0,s1_paddr=0;
    bool s1_kill=false,s2_kill=false,invalidate=false,memory_a_ready=false;
    std::optional<uint64_t> data_beat;
  };
  struct Outputs {
    ShuttleCachePipeline::Outputs control;
    std::optional<uint64_t> data_word;
  };
  Outputs evaluate(const Inputs&) const;
  void commit(const Inputs&);
  const ShuttleCachePipeline& metadata() const { return metadata_; }
 private:
  ShuttleCachePipeline metadata_;
  std::array<std::array<std::optional<uint64_t>,512>,2> data_{};
  std::array<std::array<std::optional<uint32_t>,2>,64> tag_data_{};
  std::array<std::optional<uint64_t>,2> sram_output_{},s2_data_{};
  std::array<std::optional<uint32_t>,2> tag_output_{};
  std::optional<unsigned> s2_mask_;
  bool invalidated_=false;
  static ShuttleCachePipeline::Inputs control_inputs(const Inputs&);
};
}
