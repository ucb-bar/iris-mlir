#pragma once
#include <array>
#include <cstdint>
#include <memory>
#include <span>
#include <string_view>
#include <vector>
namespace iris::fabric {
struct Bits {
  std::array<std::uint64_t,4> limbs{};
  constexpr Bits()=default;
  constexpr Bits(std::uint64_t low):limbs{low,0,0,0}{}
  bool operator==(const Bits&)const=default;
};
struct Field {std::string_view name;unsigned width;};
enum class Adapter {RocketTile,RocketClockSource,RocketClockSink,CoherenceManager};
// Physical source cut only. No guessed request routing, delay, or memory service.
// Evaluate every peer to a same-edge fixed point before committing any peer.
class Adapters {
 public:
  explicit Adapters(Adapter,std::uint32_t random_seed=1);
  ~Adapters();
  Adapters(const Adapters&)=delete;
  Adapters&operator=(const Adapters&)=delete;
  void evaluate(std::span<const Bits>);
  std::vector<Bits> sample()const;
  void commit();
  std::vector<Bits> step(std::span<const Bits>);
  std::uint64_t edges()const;
  Adapter component()const;
  std::span<const Field> input_fields()const;
  std::span<const Field> output_fields()const;
  static const char*source_identity();
 private:struct Impl;std::unique_ptr<Impl>impl_;
};
}
