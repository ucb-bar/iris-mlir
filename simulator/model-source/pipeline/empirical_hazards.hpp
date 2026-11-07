#pragma once
#include "../timing/shuttle_issue.hpp"
#include <array>
#include <stdexcept>

namespace iris::empirical {
// Same source last-match-wins fold as integer_hazard(reg,true,bypasses),
// evaluated for all 32 registers together in the caller's original order.
// This has no scheduler state, source clock, scoreboard or bypass invention.
class IntegerHazardFold {
 public:
  IntegerHazardFold(){values_.fill(shuttle::Signal::no);}
  void observe(bool valid,unsigned destination,bool can_bypass) {
    if(destination>31)throw std::invalid_argument("invalid bypass register");
    if(valid)values_[destination]=can_bypass?shuttle::Signal::no:shuttle::Signal::yes;
  }
  const std::array<shuttle::Signal,32>& values()const{return values_;}
 private:
  std::array<shuttle::Signal,32>values_;
};
}
