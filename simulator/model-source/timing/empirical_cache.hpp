#pragma once
// Experimental timing tags only: source geometry, explicit LRU approximation.
// No values, knownness, measured ready/state, hidden latency or benchmark IDs.
#include <array>
#include <cstdint>
#include <map>
#include <vector>
#include <string>

namespace iris::empirical {
enum class Path { LocalTCM, RemoteTCM, Scratchpad, DRAM, Uncached };
const char* path_name(Path);
struct Geometry { unsigned sets,ways,banks,line_bytes; };
struct CacheConfig {
  // Caller must explicitly select geometry; iris_geometry() identifies the
  // saved elaboration profile and is NOT a claim about source/image pairing.
  std::array<Geometry,3> l1d;
  Geometry l2;
};
CacheConfig iris_geometry();
// id is a strictly increasing positive request owner across all harts.
struct Access { uint64_t id; unsigned hart; uint64_t address,bytes; bool write; uint64_t tick; };
struct Delta {
  Path path=Path::Uncached; uint64_t canonical_address=0,bytes=0,lines=0;
  uint64_t l1_hits=0,l1_misses=0,l2_hits=0,l2_misses=0;
  uint64_t l1_dirty_victims=0,l2_dirty_victims=0;
  uint64_t coherence_invalidations=0,dirty_owner_forwards=0;
  uint64_t same_bank_contenders=0,other_bank_contenders=0,shared_line_contenders=0;
  // Accepted same-line misses/conflicting stores must remain dependent on
  // the named real pending events; tags must never hide unfinished service.
  std::vector<uint64_t> wait_for;
};
class CacheHistory {
 public:
  explicit CacheHistory(CacheConfig);
  Delta begin(Access);
  void complete(uint64_t id,uint64_t tick);
  // Flush drops timing tags only. It requires no pending actors and does not
  // write memory, reset the backing store or imply an actual hardware flush.
  void reset_timing_tags(uint64_t tick);
  size_t pending()const{return pending_.size();}
 private:
  struct Line {uint64_t tag=0,age=0;bool valid=false,dirty=false;};
  struct Cache { Geometry geometry;std::vector<Line> lines; };
  struct Pending { Access access; Path path; std::vector<uint64_t> lines; std::vector<unsigned> banks; std::vector<uint64_t> dependencies; };
  CacheConfig config_;std::array<Cache,3> l1_;Cache l2_;
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
};
} // namespace iris::empirical
