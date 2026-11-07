#include "empirical_scalar_memory.hpp"
#include <stdexcept>
#include <utility>

namespace iris {
namespace {
uint64_t field(uint32_t raw, unsigned low, unsigned width) {
  return (raw >> low) & ((uint64_t{1} << width)-1);
}
uint64_t sext(uint64_t value, unsigned width) {
  const uint64_t sign=uint64_t{1} << (width-1);
  return (value ^ sign)-sign;
}
}
ScalarAccess scalar_memory_access(uint32_t raw, const std::array<uint64_t,32>& r) {
  ScalarAccess out;
  auto reg=[&](unsigned index) { return index ? r[index] : uint64_t{0}; };
  uint64_t base=0,offset=0;
  const auto f3=field(raw,13,3);
  if ((raw&3)!=3) {
    const unsigned quadrant=raw&3;
    if ((quadrant!=0 && quadrant!=2) || (f3!=1 && f3!=2 && f3!=3 && f3!=5 && f3!=6 && f3!=7))
      return out;
    const bool store=f3>=5;
    out.kind=store ? AccessKind::store : AccessKind::load;
    out.bytes=(f3==2 || f3==6) ? 4 : 8;
    if (quadrant==0) {
      base=reg(8+field(raw,7,3));
      offset=out.bytes==4 ? (field(raw,6,1)<<2)+(field(raw,10,3)<<3)+(field(raw,5,1)<<6)
                          : (field(raw,10,3)<<3)+(field(raw,5,2)<<6);
    } else {
      base=reg(2);
      if (!store)
        offset=out.bytes==4 ? (field(raw,4,3)<<2)+(field(raw,12,1)<<5)+(field(raw,2,2)<<6)
                            : (field(raw,5,2)<<3)+(field(raw,12,1)<<5)+(field(raw,2,3)<<6);
      else
        offset=out.bytes==4 ? (field(raw,9,4)<<2)+(field(raw,7,2)<<6)
                            : (field(raw,10,3)<<3)+(field(raw,7,3)<<6);
    }
  } else {
    const unsigned opcode=raw&127, width=field(raw,12,3);
    if (opcode!=0x03 && opcode!=0x23 && opcode!=0x07 && opcode!=0x27 && opcode!=0x2f)
      return out;
    base=reg(field(raw,15,5));
    out.decode=MemoryDecode::unresolved;
    if (opcode==0x2f) {
      if (width!=2 && width!=3) return out;
      out.bytes=1u<<width;
      switch (field(raw,27,5)) {
        case 2: out.kind=AccessKind::lr; break;
        case 3: out.kind=AccessKind::sc; break;
        case 0: case 1: case 4: case 8: case 12: case 16: case 20: case 24: case 28:
          out.kind=AccessKind::amo; break;
        default: return out;
      }
    } else {
      const bool store=opcode==0x23 || opcode==0x27;
      out.kind=store ? AccessKind::store : AccessKind::load;
      if (opcode==0x07 || opcode==0x27) {
        if (width==0 || width>=5) { out.decode=MemoryDecode::vector; return out; }
        if (width<1 || width>3) return out;
        out.bytes=1u<<width;
      } else {
        if (width>3 && (store || width>6)) return out;
        out.bytes=1u<<(width&3);
      }
      offset=store ? sext(field(raw,7,5)+(field(raw,25,7)<<5),12) : sext(field(raw,20,12),12);
    }
  }
  out.decode=MemoryDecode::scalar;
  out.address=base+offset; // Architectural RV64 addition wraps modulo 2^64.
  return out;
}

ScalarMemoryRoutes::ScalarMemoryRoutes(unsigned harts, std::vector<AddressRoute> routes,
    std::map<std::string,std::vector<RoutedStage>> paths)
    : harts_(harts), routes_(std::move(routes)), paths_(std::move(paths)) {
  if (!harts_ || routes_.empty() || paths_.empty())
    throw std::invalid_argument("explicit scalar memory routes and paths required");
  for (const auto& route:routes_) {
    if (route.hart>=harts_ || !route.size || route.size-1>UINT64_MAX-route.base ||
        (route.select_value&~route.select_mask) || !paths_.count(route.path))
      throw std::invalid_argument("invalid scalar memory address route");
  }
  for (const auto& path:paths_) {
    if (path.first.empty() || path.second.empty()) throw std::invalid_argument("empty scalar memory path");
    for (const auto& stage:path.second) {
      const auto& s=stage.stage;
      if (s.name.empty() || s.resource.empty() || s.source.empty() || !s.first_beat_latency ||
          !s.launch_interval || s.first_beat_latency==UINT64_MAX || s.launch_interval==UINT64_MAX ||
          ((stage.access_sized || s.bytes) ? (!s.beat_bytes || !s.beat_interval) : (s.beat_bytes || s.beat_interval)))
        throw std::invalid_argument("invalid or unsourced scalar memory path stage");
    }
  }
}

MemoryRequest ScalarMemoryRoutes::request(unsigned hart, const ScalarAccess& access, uint64_t pc,
    Cycle period, std::vector<NodeId> deps, std::function<void()> completion) const {
  if (hart>=harts_ || access.decode!=MemoryDecode::scalar || !access.bytes || access.bytes>8 ||
      (access.bytes&(access.bytes-1)) || !period ||
      access.bytes-1>UINT64_MAX-access.address)
    throw std::invalid_argument("bounded scalar physical memory access required");
  const AddressRoute* selected=nullptr;
  for (const auto& route:routes_) {
    if (route.hart!=hart || access.address<route.base || access.address-route.base>=route.size ||
        access.bytes>route.size-(access.address-route.base) ||
        (access.address&route.select_mask)!=route.select_value) continue;
    // A request crossing a bank-select bit needs explicit splitting, not one
    // arbitrary bank selected from its first byte.
    for (unsigned i=1;i<access.bytes;++i)
      if (((access.address+i)&route.select_mask)!=route.select_value)
        throw std::runtime_error("IRIS_TIMING_MEMORY_CROSS_BANK_REQUEST");
    if (selected) throw std::runtime_error("IRIS_TIMING_AMBIGUOUS_MEMORY_ROUTE");
    selected=&route;
  }
  if (!selected) throw std::runtime_error("IRIS_TIMING_MEMORY_ROUTE_MISSING hart="+std::to_string(hart));
  MemoryRequest out{hart,access.address,access.bytes,selected->path,{}};
  out.pc=pc;out.issue_period=period;out.dependencies=std::move(deps);out.on_complete=std::move(completion);
  for (const auto& stage:paths_.at(selected->path)) {
    auto s=stage.stage;
    if (stage.access_sized) s.bytes=access.bytes;
    out.stages.push_back(std::move(s));
  }
  return out;
}
} // namespace iris
