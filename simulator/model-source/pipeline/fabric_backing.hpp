#pragma once
#include <cstdint>
#include <cstring>
#include <elf.h>
#include <fstream>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>
namespace iris::frontend {
// External fixture memory has separate physical storage. Reading a modified
// Spike architectural mirror cannot silently bypass source coherence.
class FabricBacking {
 public:
  explicit FabricBacking(const std::string& path){
    std::ifstream f(path,std::ios::binary);if(!f)throw std::runtime_error("IRIS_ROCKET_FABRIC_INITIAL_ELF_MISSING");std::vector<uint8_t> bytes((std::istreambuf_iterator<char>(f)),{});
    if(bytes.size()<sizeof(Elf64_Ehdr))throw std::runtime_error("IRIS_ROCKET_FABRIC_INITIAL_ELF_TRUNCATED");Elf64_Ehdr h;std::memcpy(&h,bytes.data(),sizeof(h));
    if(std::memcmp(h.e_ident,ELFMAG,SELFMAG)||h.e_ident[EI_CLASS]!=ELFCLASS64||h.e_ident[EI_DATA]!=ELFDATA2LSB||h.e_machine!=EM_RISCV||h.e_phentsize!=sizeof(Elf64_Phdr))throw std::runtime_error("IRIS_ROCKET_FABRIC_INITIAL_ELF_FORMAT");
    if(h.e_phoff>bytes.size()||h.e_phnum>(bytes.size()-h.e_phoff)/sizeof(Elf64_Phdr))throw std::runtime_error("IRIS_ROCKET_FABRIC_INITIAL_ELF_HEADER_BOUNDS");
    for(unsigned n=0;n<h.e_phnum;++n){const auto off=h.e_phoff+uint64_t(n)*h.e_phentsize;if(off>bytes.size()||sizeof(Elf64_Phdr)>bytes.size()-off)throw std::runtime_error("IRIS_ROCKET_FABRIC_INITIAL_ELF_HEADERS");Elf64_Phdr p;std::memcpy(&p,bytes.data()+off,sizeof(p));if(p.p_type!=PT_LOAD)continue;
      if(p.p_filesz>p.p_memsz||p.p_memsz>64*1024*1024||p.p_paddr<0x80000000||p.p_paddr>=0x180000000ULL||p.p_memsz>0x180000000ULL-p.p_paddr||p.p_offset>bytes.size()||p.p_filesz>bytes.size()-p.p_offset)throw std::runtime_error("IRIS_ROCKET_FABRIC_INITIAL_ELF_SEGMENT");
      for(uint64_t i=0;i<p.p_memsz;++i){auto value=i<p.p_filesz?bytes[p.p_offset+i]:uint8_t(0);auto [it,inserted]=data_.emplace(p.p_paddr+i,value);if(!inserted&&it->second!=value)throw std::runtime_error("IRIS_ROCKET_FABRIC_INITIAL_ELF_OVERLAP");}
    }
    if(data_.empty())throw std::runtime_error("IRIS_ROCKET_FABRIC_INITIAL_ELF_NO_DRAM_SEGMENTS");
  }
  uint64_t read(uint64_t address,unsigned bytes)const{if(!bytes||bytes>8)throw std::invalid_argument("external physical read width");uint64_t value=0;for(unsigned i=0;i<bytes;++i){auto it=data_.find(address+i);if(it==data_.end())throw std::runtime_error("IRIS_ROCKET_FABRIC_UNINITIALIZED_EXTERNAL_MEMORY address="+std::to_string(address+i));value|=uint64_t(it->second)<<(8*i);}return value;}
  void write(uint64_t address,unsigned bytes,uint64_t value,uint64_t mask){if(!bytes||bytes>8||address<0x80000000||address>=0x180000000ULL||bytes>0x180000000ULL-address)throw std::runtime_error("IRIS_ROCKET_FABRIC_EXTERNAL_WRITE_RANGE");for(unsigned i=0;i<bytes;++i)if(mask&(uint64_t(1)<<i))data_[address+i]=value>>(8*i);}
 private:std::map<uint64_t,uint8_t>data_;
};
}
