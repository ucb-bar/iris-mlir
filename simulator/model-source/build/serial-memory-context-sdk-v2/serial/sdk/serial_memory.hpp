#pragma once
#include <cstdint>
#include <memory>
#include <span>
#include <string>
#include <vector>
namespace iris::serial_memory {
struct Field {const char* name;unsigned width;};
struct Bits {std::uint64_t value,known;};
struct Edges {bool iris,bringup,serial;};
enum class AssertionPolicy {SourceAlways, SuppressOnlyWhileBothExternalResets};
class Model {
 public:
  Model(unsigned seed,std::vector<std::string> source_arguments,AssertionPolicy=AssertionPolicy::SourceAlways);
  ~Model();
  Model(Model&&)noexcept;
  Model& operator=(Model&&)noexcept;
  Model(const Model&)=delete;
  Model& operator=(const Model&)=delete;
  void evaluate(std::span<const Bits> inputs);
  std::vector<std::uint64_t> sample()const;
  void commit(Edges);
  std::span<const Field> input_fields()const;
  std::span<const Field> output_fields()const;
  const char* source_identity()const;
  std::uint64_t committed_phases()const;
 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};
}
