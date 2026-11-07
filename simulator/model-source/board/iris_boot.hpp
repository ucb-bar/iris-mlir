#pragma once

// Header-only board helpers. Linking these tests does not install them into Spike.
#include "abstract_device.h"
#include <algorithm>
#include <fstream>
#include <iterator>
#include <stdexcept>
#include <vector>

namespace iris_board {
constexpr reg_t reset_pc = 0x10000;
constexpr reg_t rom_base = 0x10000;
constexpr reg_t rom_size = 0x10000;
constexpr reg_t dtb_offset = 0xc0;
constexpr reg_t bootreg_base = 0x1000;
constexpr reg_t clint_base = 0x2000000;
constexpr uint64_t mtime_hz = 500000;

inline std::vector<char> read_binary(const std::string& path) {
  std::ifstream stream(path, std::ios::binary);
  if (!stream) throw std::runtime_error("Cannot read board image: " + path);
  return std::vector<char>(std::istreambuf_iterator<char>(stream), {});
}

class BootAddressRegister : public abstract_device_t {
 public:
  explicit BootAddressRegister(uint64_t value = 0x80000000) : value_(value) {}
  bool load(reg_t address, size_t bytes, uint8_t* data) override {
    if (!valid(address, bytes)) return false;
    for (size_t i = 0; i < bytes; ++i)
      data[i] = address + i < 8 ? uint8_t(value_ >> ((address + i) * 8)) : 0;
    return true;
  }
  bool store(reg_t address, size_t bytes, const uint8_t* data) override {
    if (!valid(address, bytes)) return false;
    for (size_t i = 0; i < bytes && address + i < 8; ++i) {
      unsigned shift = unsigned((address + i) * 8);
      value_ = (value_ & ~(uint64_t(0xff) << shift)) | (uint64_t(data[i]) << shift);
    }
    return true;
  }
  reg_t size() override { return 0x1000; }
  uint64_t value() const { return value_; }
 private:
  uint64_t value_;
  static bool valid(reg_t address, size_t bytes) {
    return bytes && bytes <= 8 && !(bytes & (bytes - 1)) && address % bytes == 0 &&
           address < 0x1000 && bytes <= 0x1000 - address;
  }
};

class ReadOnlyBootROM : public abstract_device_t {
 public:
  ReadOnlyBootROM(const std::vector<char>& prefix, const std::vector<char>& dtb)
      : image_(rom_size, 0) {
    if (prefix.size() != dtb_offset || dtb.size() > rom_size - dtb_offset)
      throw std::invalid_argument("Iris ROM prefix or appended DTB geometry changed");
    // _dtb is exactly the first byte after this actual 192-byte binary prefix.
    std::copy(prefix.begin(), prefix.end(), image_.begin());
    std::copy(dtb.begin(), dtb.end(), image_.begin() + dtb_offset);
  }
  bool load(reg_t address, size_t bytes, uint8_t* data) override {
    if (!bytes || address >= rom_size || bytes > rom_size - address) return false;
    std::copy_n(image_.data() + address, bytes, reinterpret_cast<char*>(data));
    return true;
  }
  bool store(reg_t, size_t, const uint8_t*) override { return false; }
  reg_t size() override { return rom_size; }
 private:
  std::vector<char> image_;
};
}  // namespace iris_board
