#pragma once
#include "empirical_cache_v3.hpp"
namespace iris::empirical {
using CacheConfigV4=CacheConfigV3;
CacheConfigV4 iris_geometry_v4();
class CacheHistoryV4 {
 public:
  explicit CacheHistoryV4(CacheConfigV4);
  Delta begin(Access);
  Delta begin_instruction(Access);
  Delta begin_vector(Access);
  // Prediction only, no reservation: caller rechecks at its actual MEM edge.
  bool data_hit(unsigned hart,uint64_t address,uint64_t bytes)const;
  void fence_i(unsigned hart,uint64_t tick);
  void complete(uint64_t id,uint64_t tick);
  // Flush drops timing tags only. It requires no pending actors and does not
  // write memory, reset the backing store or imply an actual hardware flush.
  void reset_timing_tags(uint64_t tick);
  size_t pending()const{return pending_.size();}
 private:
  struct Line {uint64_t tag=0,age=0;bool valid=false,dirty=false;};
  struct Cache { Geometry geometry;std::vector<Line> lines; };
  struct Pending { Access access; Path path; bool instruction; std::vector<uint64_t> lines; std::vector<unsigned> banks; std::vector<uint64_t> dependencies; };
  CacheConfigV4 config_;std::array<Cache,3> l1_;std::array<Cache,3> l1i_;Cache l2_;
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
  Delta begin_internal(Access,bool instruction,bool vector);
};
} // namespace iris::empirical
