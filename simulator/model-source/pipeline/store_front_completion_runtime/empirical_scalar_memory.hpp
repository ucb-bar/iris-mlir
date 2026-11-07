#pragma once
#include "empirical_memory_path.hpp"
#include <array>

namespace iris {
enum class MemoryDecode { none, scalar, vector, unresolved };
enum class AccessKind { load, store, lr, sc, amo };
struct ScalarAccess {
  MemoryDecode decode = MemoryDecode::none;
  AccessKind kind = AccessKind::load;
  uint64_t address = 0;
  unsigned bytes = 0;
};
// RV64 scalar/RVC effective-address extraction only. No translation, memory
// read, architectural legality decision, PMA/PMP check or instruction effect.
// The caller must prove that effective privilege uses physical addressing.
ScalarAccess scalar_memory_access(uint32_t instruction,
                                 const std::array<uint64_t,32>& registers);

struct AddressRoute {
  unsigned hart;
  uint64_t base, size, select_mask, select_value;
  std::string path;
};
struct RoutedStage {
  MemoryStage stage;
  bool access_sized = false;
};
class ScalarMemoryRoutes {
 public:
  ScalarMemoryRoutes(unsigned harts, std::vector<AddressRoute> routes,
                     std::map<std::string,std::vector<RoutedStage>> paths);
  MemoryRequest request(unsigned hart, const ScalarAccess& access, uint64_t pc,
                        Cycle period, std::vector<NodeId> deps,
                        std::function<void()> completion) const;
 private:
  unsigned harts_;
  std::vector<AddressRoute> routes_;
  std::map<std::string,std::vector<RoutedStage>> paths_;
};
} // namespace iris
