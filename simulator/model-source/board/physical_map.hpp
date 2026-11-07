// Generated from frozen elaborated memmap; do not infer transfer timing.
#pragma once
#include <cstdint>
#include <cstddef>
namespace iris_board {
enum class Access { Read, Write, Execute, Atomic };
struct Region { const char* name; uint64_t base, size; bool read, write, execute, atomics; };
inline constexpr Region regions[] = {
  {"boot-address-reg@1000", 0x1000ULL, 0x1000ULL, true, true, false, true},
  {"error-device@3000", 0x3000ULL, 0x1000ULL, true, true, true, true},
  {"rom@10000", 0x10000ULL, 0x10000ULL, true, false, true, false},
  {"tile-reset-setter@110000", 0x110000ULL, 0x1000ULL, true, true, false, true},
  {"clint@2000000", 0x2000000ULL, 0x10000ULL, true, true, false, true},
  {"cache-controller@2010000", 0x2010000ULL, 0x1000ULL, true, true, false, true},
  {"trace-encoder-controller0@3000000", 0x3000000ULL, 0x1000ULL, true, true, false, false},
  {"trace-encoder-controller1@3001000", 0x3001000ULL, 0x1000ULL, true, true, false, false},
  {"trace-encoder-controller2@3002000", 0x3002000ULL, 0x1000ULL, true, true, false, true},
  {"trace-sink-dma0@3010000", 0x3010000ULL, 0x1000ULL, true, true, false, false},
  {"trace-sink-dma1@3011000", 0x3011000ULL, 0x1000ULL, true, true, false, false},
  {"trace-sink-dma2@3012000", 0x3012000ULL, 0x1000ULL, true, true, false, true},
  {"interrupt-controller@c000000", 0xc000000ULL, 0x4000000ULL, true, true, false, true},
  {"serial@10020000", 0x10020000ULL, 0x1000ULL, true, true, false, true},
  {"memory@70000000", 0x70000000ULL, 0x20000ULL, true, true, true, true},
  {"memory@70020000", 0x70020000ULL, 0x20000ULL, true, true, true, true},
  {"memory@80000000", 0x80000000ULL, 0x100000000ULL, true, true, true, true},
  {"memory@580000000", 0x580000000ULL, 0x20000ULL, true, true, true, false},
};
inline const Region* region_for(uint64_t address, size_t bytes) {
  if (!bytes) return nullptr;
  for (const auto& region : regions)
    if (address >= region.base && address - region.base < region.size &&
        bytes <= region.size - (address - region.base)) return &region;
  return nullptr;
}
inline bool permits(uint64_t address, size_t bytes, Access access) {
  const auto* r = region_for(address, bytes);
  if (!r) return false;
  switch (access) {
    case Access::Read: return r->read;
    case Access::Write: return r->write;
    case Access::Execute: return r->execute;
    case Access::Atomic: return r->read && r->write && r->atomics;
  }
  return false;
}
}  // namespace iris_board
