#pragma once
#include "empirical_cache_v4.hpp"
namespace iris::empirical {
using CacheConfigV9=CacheConfigV4;
CacheConfigV9 iris_geometry_v9();
class CacheHistoryV9 {
 public:
  explicit CacheHistoryV9(CacheConfigV9,bool compatible_warm_reads=false,bool source_random_shuttle=false);
  Delta begin(Access);
  // Explicit ordinary scalar RAM read. Unknown/LR/AMO/vector remain begin().
  Delta begin_read(Access);
  const std::array<uint64_t,3>& source_random_allocations()const{return source_random_allocations_;}
  uint64_t warm_read_accepts()const{return warm_read_accepts_;}
  uint64_t compatible_read_dependencies_omitted()const{return compatible_read_dependencies_omitted_;}
  Delta begin_instruction(Access);
  Delta begin_vector(Access);
  // Prediction only, no reservation: caller rechecks at its actual MEM edge.
  bool data_hit(unsigned hart,uint64_t address,uint64_t bytes)const;
  // Conservative predicate over THIS approximate cache's runtime history.
  // Not a hardware permission claim: only aligned integer stores wholly in
  // an already-dirty private DRAM line and with no overlapping pending owner.
  bool owned_store_hit(unsigned hart,uint64_t address,uint64_t bytes)const;
  // Recheck and reserve at the actual publication edge. A failed guard does
  // not mutate tags, ownership, counters or the monotonic owner sequence.
  Delta begin_owned_store(Access);
  bool instruction_hit(unsigned hart,uint64_t address,uint64_t bytes)const;
  Delta touch_instruction(unsigned hart,uint64_t address,uint64_t bytes,uint64_t tick);
  void fence_i(unsigned hart,uint64_t tick);
  void complete(uint64_t id,uint64_t tick,bool successful_response=true);
  // Flush drops timing tags only. It requires no pending actors and does not
  // write memory, reset the backing store or imply an actual hardware flush.
  void reset_timing_tags(uint64_t tick);
  size_t pending()const{return pending_.size();}
 private:
  struct Line {uint64_t tag=0,age=0,readiness_owner=0;bool valid=false,dirty=false,response_ready=false;};
  struct Cache { Geometry geometry;std::vector<Line> lines; };
  struct Pending { Access access; Path path; bool instruction; bool vector; bool ordinary_read; bool warm_read; std::vector<uint64_t> lines; std::vector<unsigned> banks; std::vector<uint64_t> dependencies; };
  bool compatible_warm_reads_=false;
  bool source_random_shuttle_=false;
  std::array<uint16_t,3> source_random_state_{{1,1,1}};
  std::array<uint64_t,3> source_random_allocations_{};
  uint64_t warm_read_accepts_=0,compatible_read_dependencies_omitted_=0;
  CacheConfigV9 config_;std::array<Cache,3> l1_;std::array<Cache,3> l1i_;Cache l2_;
  std::map<uint64_t,Pending> pending_;
  uint64_t age_=0,tick_=0,last_id_=0;
  static void validate_geometry(Geometry);
  static Path classify(unsigned,uint64_t,uint64_t,uint64_t&);
  static unsigned bank(const Cache&,uint64_t);
  static size_t set_start(const Cache&,uint64_t);
  static Line* find(Cache&,uint64_t);
  Line install(Cache&,uint64_t,bool);
  void install_l2(uint64_t,bool,Delta&);
  void advance(uint64_t);
  Delta begin_internal(Access,bool instruction,bool vector,bool ordinary_read=false);
};
} // namespace iris::empirical
