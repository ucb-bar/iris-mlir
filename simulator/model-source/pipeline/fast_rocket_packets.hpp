#pragma once
#include "fast_rocket_ibuf.hpp"
#include <deque>
#include <functional>
#include <optional>
#include <string>
#include <vector>
namespace iris::frontend {
// Demand-driven experimental word producer feeding the exact source IBuf.
// It supplies at most one 4-byte word per owning core edge. Cache service and
// prediction are explicit caller actors; this is not the complete Frontend.
class FastRocketPackets {
 public:
  struct Word {uint32_t data=0,known=0;};
  struct Instruction {uint64_t pc=0;uint32_t raw=0;unsigned bytes=0;uint64_t prediction=0;std::vector<uint64_t>words;};
  struct AcceptedWord {uint64_t id=0,address=0;uint32_t data=0,known=0;};
  struct Edge {uint64_t tick=0;std::optional<AcceptedWord>word;std::optional<Instruction>instruction;bool partial=false,kill=false;std::string reason;};
  struct Metrics {uint64_t edges=0,words=0,instructions=0,partial_edges=0,redirects=0,held_observations=0;};
  using Reader=std::function<Word(uint64_t)>;
  using Predictor=std::function<uint64_t(uint64_t,uint32_t,unsigned)>;
  FastRocketPackets(uint64_t divider,uint64_t reset_pc);
  bool prepare(uint64_t tick,uint64_t pc,uint32_t raw,unsigned bytes,const Reader&,const Predictor&);
  void consume(uint64_t tick);
  std::optional<Edge> finish(uint64_t tick);
  void redirect(uint64_t tick,uint64_t pc,std::string reason,unsigned priority=1);
  std::optional<uint64_t> prediction(uint64_t pc,uint32_t raw,unsigned bytes)const;
  std::optional<uint64_t> next_edge()const{return retry_;}
  const Metrics& metrics()const{return metrics_;}
  const FastRocketIBuf::Inputs& inputs()const{return inputs_;}
  const FastRocketIBuf::Outputs& outputs()const{return outputs_;}
 private:
  struct Partial {uint64_t pc=0,word=0;uint16_t bits=0;};
  struct PendingWord {AcceptedWord value;FastRocketIBuf::Packet packet;uint64_t next_pc=0;std::optional<Partial>partial;std::vector<Instruction>instructions;};
  struct Redirect {uint64_t tick,pc;std::string reason;unsigned priority;};
  uint64_t divider_,producer_pc_,next_word_=1;
  std::optional<uint64_t>committed_,retry_;
  std::optional<Redirect>redirect_;
  std::optional<PendingWord>word_;
  std::optional<Partial>partial_;
  std::deque<Instruction>instructions_;
  FastRocketIBuf model_;FastRocketIBuf::Inputs inputs_;FastRocketIBuf::Outputs outputs_;
  bool prepared_=false,consumed_=false,partial_edge_=false;
  uint64_t prepared_tick_=0,expected_pc_=0;uint32_t expected_raw_=0;unsigned expected_bytes_=0;
  Metrics metrics_;
  uint64_t following(uint64_t)const;
  PendingWord read_word(const Reader&,const Predictor&)const;
};
}
