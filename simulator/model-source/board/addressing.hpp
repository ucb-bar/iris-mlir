#pragma once
#include <cstdint>

namespace iris_board {
// Shuttle Tile.scala tcmMasterReplicator/AddressOffsetter: the private upper
// replicated region is visible only to its own hart's instruction/data/ATL
// masters. It is not additional RAM on SBUS. Source citations and snapshots
// are retained by hardware/memory-paths.json.
inline uint64_t storage_address(unsigned hart, uint64_t address) {
  if (hart < 2 && address >= 0x70080000ULL && address < 0x70100000ULL)
    return 0x70000000ULL + uint64_t(hart) * 0x20000ULL + (address & 0x1ffffULL);
  return address;
}
}  // namespace iris_board
