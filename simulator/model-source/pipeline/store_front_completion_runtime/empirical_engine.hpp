#pragma once

#include "empirical_history.hpp"
#include <cstdint>
#include <functional>
#include <iosfwd>
#include <map>
#include <optional>
#include <set>
#include <string>
#include <vector>

namespace iris {
using Cycle = uint64_t;
using NodeId = uint64_t;

// These values are supplied by a calibrated adapter, never inferred as hardware defaults.
struct Op {
  unsigned hart;
  std::string name;
  std::string resource;
  Cycle latency;
  Cycle interval;
  std::vector<NodeId> deps{};
  Cycle earliest = 0;
  std::function<void()> on_complete{};
  bool retires = true;
  uint64_t pc = 0;
  std::string region{};
  uint64_t bytes = 0;
  uint64_t macs = 0;
  Cycle issue_period = 1;
  // Only owned source CPU stages opt in; accepted device/memory actors do not.
  bool cancellable = false;
};

struct Node {
  NodeId id;
  Op op;
  bool issued = false;
  bool completed = false;
  Cycle submitted = 0;
  Cycle start = 0;
  Cycle end = 0;
  unsigned lane = 0;
  bool cancelled = false, squashed = false;
  Cycle cancel_cycle = 0, squash_cycle = 0;
  std::string cancel_reason{}, squash_reason{};
  // A source physical actor is accepted at a real handshake and has no
  // scheduled latency/end event. It finishes only on its matching physical
  // response; it is never placed in the timed event queue.
  bool physical = false;
  bool physical_edge = false;
  bool physical_pc_known = false;
  std::string physical_source{};
};

struct PhysicalOp {
  unsigned hart;
  std::string name,resource;
  uint64_t pc=0,bytes=0,macs=0;
  std::vector<NodeId> deps{};
  std::string source;
  bool pc_known=false;
};

// Single global event clock. Functional side effects belong in completion callbacks.
// The Spike adapter must not execute guest shared-memory operations ahead of this clock.
// UINT64_MAX is reserved as the no-event sentinel; reaching it fails closed.
class Machine {
 public:
  Machine(unsigned harts, uint64_t cycles_per_rtc_tick);
  struct Occupancy {uint64_t union_cycles=0,lane_service_cycles=0;};
  std::map<std::string,Occupancy> occupancy_prefix()const;
  std::map<std::string,unsigned> resource_lanes()const;
  void retain_history(bool value){retain_history_=value;}
  uint64_t completed_total()const{return completed_total_;}
  size_t retained_nodes()const{return nodes_.retained();}
  const std::map<std::string,uint64_t>&completed_by_resource()const{return completed_by_resource_;}
  const std::map<std::string,uint64_t>&completed_busy_by_resource()const{return completed_busy_by_resource_;}
  void configure_resource(const std::string& name, unsigned lanes);
  NodeId issue(Op op);
  void add_dependency(NodeId pending,NodeId dependency);
  void set_completing_payload(NodeId,uint64_t bytes,uint64_t macs=0);
  NodeId accept_physical(PhysicalOp);
  void finish_physical(NodeId);
  NodeId record_physical_edge(PhysicalOp);
  void cancel(NodeId id, const std::string& reason);
  void mark_squashed(NodeId id, const std::string& reason);
  bool mark_cpu_completed_this_cycle_squashed(NodeId,const std::string&);
  bool run_next();
  void run_until(Cycle limit);
  void drain();
  std::optional<Cycle> next_event_cycle() const;
  void on_advance(std::function<void(Cycle, Cycle)> callback);
  Cycle now() const { return now_; }
  Cycle mcycle(unsigned hart) const;
  uint64_t minstret(unsigned hart) const;
  uint64_t mtime() const;
  void write_mtime(uint64_t value);
  void set_msip(unsigned hart, bool pending);
  bool msip(unsigned hart) const;
  void write_mtimecmp(unsigned hart, uint64_t value);
  bool mtip(unsigned hart) const;
  // Callback observes line transitions. A parked hart can be resubmitted here.
  void on_interrupt(std::function<void(unsigned, bool, bool)> callback);
  const IndexedHistory<Node>& nodes() const { return nodes_; }
  void write_json(std::ostream& out, bool snapshot = false) const;

 private:
  struct Resource { std::vector<Cycle> next_issue; };
  unsigned harts_;
  uint64_t rtc_divider_;
  Cycle now_ = 0;
  Cycle rtc_origin_ = 0;
  uint64_t rtc_value_ = 0;
  std::map<std::string, Resource> resources_;
  IndexedHistory<Node> nodes_{Node{0,Op{0,"completed-prefix","",0,0},true,true}};
  struct LiveOccupancy {uint64_t active=0,last=0,union_cycles=0,lane_service_cycles=0;};
  std::map<std::string,LiveOccupancy> occupancy_;
  void occupancy_change(const std::string&,int delta);
  bool retain_history_=true;
  uint64_t completed_total_=0;
  std::map<std::string,uint64_t> completed_by_resource_,completed_busy_by_resource_;
  void compact_completed();
  bool diagnostics_=false;
  uint64_t debug_steps_=0,debug_same_cycle_=0,debug_last_cycle_=UINT64_MAX;
  std::set<NodeId> pending_, inflight_;
  std::set<NodeId> physical_;
  std::optional<NodeId> completing_;
  std::vector<NodeId> owned_cpu_completed_this_cycle_;
  std::function<void(Cycle, Cycle)> advance_callback_;
  std::vector<uint64_t> retired_, compares_;
  std::vector<bool> software_irq_;
  std::vector<bool> delivered_timer_irq_;
  std::function<void(unsigned, bool, bool)> interrupt_callback_;
  Cycle next_event() const;
  void check_hart(unsigned hart) const;
  void refresh_interrupts();
};

struct Region {
  std::string name;
  uint64_t base, size;
  bool read, write, execute, atomics;
};

// Sparse backing preserves real holes and avoids allocating a 4-GiB host buffer.
// Reservation granule is an explicit implementation parameter, not a guessed default.
class Memory {
 public:
  Memory(unsigned harts, uint64_t reservation_granule);
  void map(Region region);
  uint64_t load(uint64_t address, unsigned bytes, bool instruction = false) const;
  void store(uint64_t address, unsigned bytes, uint64_t value);
  uint64_t lr(unsigned hart, uint64_t address, unsigned bytes);
  bool sc(unsigned hart, uint64_t address, unsigned bytes, uint64_t value);
  uint64_t amo_add(uint64_t address, unsigned bytes, uint64_t value);
 private:
  struct Reservation { bool valid = false; uint64_t address = 0; unsigned bytes = 0; };
  uint64_t granule_;
  std::vector<Region> regions_;
  std::map<uint64_t, uint8_t> data_;
  std::vector<Reservation> reservations_;
  const Region& check(uint64_t address, unsigned bytes, char mode, bool atomic = false) const;
  void invalidate(uint64_t address, unsigned bytes);
};
}  // namespace iris
