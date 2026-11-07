#pragma once
#include <bit>
#include <cstdint>
#include <stdexcept>

namespace iris::frontend {
// Saved ShuttleFetchBuffer control state. Payload/decoder/clock stages are
// separate; this reproduces valid bits, capacity, rotations and priorities.
class ShuttleFetchQueue {
 public:
  struct State {uint8_t valid=0,enq=1,deq=1;};
  struct Inputs {bool reset=false,clear=false,enq_valid=false;uint8_t enq_mask=0;bool ready0=false,ready1=false;};
  explicit ShuttleFetchQueue(State state):s_(state) {validate();}
  ShuttleFetchQueue()=default;
  const State& state() const {return s_;}
  bool enqueue_ready(uint8_t mask) const {
    if (mask>15) throw std::invalid_argument("SHUTTLE_FETCH_MASK_OUTSIDE_FOUR_PARCELS");
    return std::popcount(unsigned(s_.valid))+std::popcount(unsigned(mask))<=7;
  }
  bool valid(unsigned lane) const {
    if(lane>1)throw std::invalid_argument("SHUTTLE_FETCH_DEQUEUE_WIDTH_TWO");
    return bool(s_.valid&rotate(s_.deq,lane));
  }
  void clock(const Inputs& in) {
    if(in.reset || in.clear) {s_={};return;}
    if(in.ready1 && !in.ready0) throw std::logic_error("SHUTTLE_FETCH_READY_NOT_PREFIX");
    const auto before=s_;uint8_t remove=0;
    if(in.ready0 && valid(0))remove|=before.deq;
    if(in.ready1 && valid(1))remove|=rotate(before.deq,1);
    if(in.enq_valid && enqueue_ready(in.enq_mask)) {
      auto pointer=before.enq;
      for(unsigned parcel=0;parcel<4;++parcel)if(in.enq_mask&(1u<<parcel)) {
        // Source updates only slots invalid BEFORE this edge. A simultaneous
        // dequeue does not provide combinational capacity or overwrite room.
        if(!(before.valid&pointer))s_.valid|=pointer;
        pointer=rotate(pointer,1);
      }
      s_.enq=pointer;
    }
    s_.valid&=uint8_t(~remove);s_.deq=rotate(before.deq,std::popcount(unsigned(remove)));validate();
  }
 private:
  State s_{};
  static uint8_t rotate(uint8_t value,unsigned n) {
    n%=7;return uint8_t(((unsigned(value)<<n)|(unsigned(value)>>(7-n)))&127);
  }
  void validate() const {
    if(s_.valid>127 || !std::has_single_bit(unsigned(s_.enq)) || !std::has_single_bit(unsigned(s_.deq)) || s_.enq>127 || s_.deq>127)
      throw std::invalid_argument("SHUTTLE_FETCH_STATE_OUTSIDE_SEVEN_SLOTS");
  }
};
}
