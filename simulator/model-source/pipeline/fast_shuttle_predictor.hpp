#pragma once
#include "frontend_rtl_peer.hpp"
#include <array>
#include <cstdint>
namespace iris::frontend {
// Plain C++ translation of the saved ShuttleBTB control. No RTL runtime,
// parameter fitting, or address->cycle table. The enclosing fetch pipeline
// supplies its real F1 request and actual MEM update ports.
class FastShuttlePredictor {
 public:
  struct Prediction {bool valid=false,taken=false;unsigned entry=0,index=0,history=0,value=0;uint64_t target=0;bool operator==(const Prediction&)const=default;};
  struct Inputs {bool reset=false,advance=false,flush=false;uint64_t pc=0;SavedShuttleFrontend::BtbUpdate btb;SavedShuttleFrontend::BhtUpdate bht;};
  Prediction evaluate(const Inputs&)const;
  void commit(const Inputs&);
  uint64_t duplicate_clear_edges()const{return duplicate_clears_;}
 private:
  struct Entry {unsigned index=0,index_page=0,target=0,target_page=0,cfi=0,branch_index=0;bool useful=false;};
  std::array<Entry,32> entries_{};uint32_t valid_=0,plru_=0;
  std::array<uint64_t,6> pages_{};unsigned page_valid_=0,next_page_=0;
  std::array<unsigned,512> counters_{};unsigned history_=0,reset_cursor_=0;
  SavedShuttleFrontend::BtbUpdate update_{};Prediction previous_response_{};uint64_t duplicate_clears_=0;
  static unsigned replacement(uint32_t,unsigned);
  static uint32_t touched(uint32_t,unsigned,unsigned);
  static unsigned counter_index(uint64_t,unsigned);
  uint32_t index_hits(uint64_t)const;
  unsigned page_hits(uint64_t)const;
};
}
