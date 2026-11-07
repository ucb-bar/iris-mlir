#pragma once
#include <cstdint>
#include <memory>
#include <span>
#include <string>
#include <vector>

namespace iris::fased {
// Values refer only to this LatencyPipe's target clock. No CPU/SerialTL
// conversion or default calibrated setting is provided by this interface.
struct Parameters {
  std::uint32_t read_latency, write_latency;
  unsigned read_max_requests, write_max_requests;
};
struct Field { const char* name; unsigned width; };
struct Bits { std::uint64_t value, known; };
class Model {
 public:
  Model(Parameters, unsigned seed, std::vector<std::string> source_arguments);
  ~Model();
  Model(Model&&) noexcept;
  Model& operator=(Model&&) noexcept;
  Model(const Model&)=delete;
  Model& operator=(const Model&)=delete;
  void evaluate(std::span<const Bits>);
  std::vector<std::uint64_t> sample() const;
  void commit();
  std::span<const Field> input_fields() const;
  std::span<const Field> output_fields() const;
  const char* source_identity() const;
  std::uint64_t committed_edges() const;
 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
};
}
