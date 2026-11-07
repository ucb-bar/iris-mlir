#pragma once
#include "rocket_fabric_peer.hpp"
#include <array>
#include <deque>
#include <functional>
#include <string>
namespace iris::frontend {
// Explicit external test actor only. Replaced by real CBUS/ROM/peripherals
// and MBUS/SerialTL/FASED peers as those source cuts become available.
// It never drives an internal joined ready/state pin or supplies a CPU cost.
class RocketFabricFixture {
 public:
  using Bits=iris::fabric::Bits;using Input=iris::fabric::Input;
  using Read=std::function<uint64_t(uint64_t,unsigned)>;
  using Write=std::function<void(uint64_t,unsigned,uint64_t,uint64_t)>;
  struct Transaction {unsigned port;uint64_t address,source,opcode,size,accepted,finished=0;};
  RocketFabricFixture(uint64_t first_edges,uint64_t gap_edges,Read read,Write write);
  void drive(std::span<Input>,uint64_t uncore_edge);
  void observe(std::span<const Bits>,uint64_t uncore_edge);
  const std::vector<Transaction>&transactions()const{return transactions_;}
  const std::vector<size_t>&completed_on_last_edge()const{return completed_;}
  bool idle()const;
 private:
  struct Pins {std::string component,request,response;unsigned a_ready,d_valid,d_opcode,d_size,d_source,d_data,d_denied,d_corrupt;unsigned a_valid,a_opcode,a_size,a_source,a_address,a_data,a_mask,d_ready;};
  struct Request {uint64_t address,source,opcode,size,accepted,beat=0,next=0;std::vector<uint64_t> data;size_t transaction;};
  std::array<Pins,5> pins_;std::array<std::deque<Request>,5> queues_;std::array<std::optional<Request>,5> writes_;
  std::array<bool,5> serving_{};std::optional<uint64_t> prepared_edge_;uint64_t first_,gap_;Read read_;Write write_;
  std::vector<Transaction>transactions_;
  std::vector<size_t>completed_;
};
}
