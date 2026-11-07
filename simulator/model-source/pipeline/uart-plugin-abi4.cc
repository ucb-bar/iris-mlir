#include "clocked_uart.hpp"
#include <riscv/dts.h>
#include <riscv/sim.h>
#include <fesvr/term.h>
#include <fdt/libfdt.h>
#include <sstream>
#include <fstream>
#include <cstdlib>

using sifive_uart_t = iris_board::ClockedUart;
static sifive_uart_t* parse_uart(const void* fdt,const sim_t* sim,reg_t* base,
                                  std::vector<std::string> args) {
  (void)args;
  if (iris_device_clock_abi_v4()!=4)
    throw std::runtime_error("IRIS_UART_NATIVE_ABI: modeled-device clock support required");
  int node=fdt_node_offset_by_compatible(fdt,-1,"sifive,uart0");
  if (node<0) return nullptr;
  if (fdt_get_node_addr_size(fdt,node,base,nullptr,"reg") || *base!=0x10020000)
    throw std::runtime_error("IRIS_UART_BOARD_MISMATCH: require audited UART at0x10020000");
  int length=0;const auto* irq=static_cast<const fdt32_t*>(fdt_getprop(fdt,node,"interrupts",&length));
  if (!irq || length!=4 || fdt32_to_cpu(*irq)!=1)
    throw std::runtime_error("IRIS_UART_BOARD_MISMATCH: require source1");
  // Frozen Ethan elaboration:256 entries,16-bit divisor135,64-bit port;
  // peripheral clock500MHz and RTC500kHz (not the FPGA emulation rate).
  auto* uart=new sifive_uart_t({256,256,135,8},{500000000,500000});
  if (const char* path=std::getenv("IRIS_UART_RX_FRAMES")) {
    std::ifstream stream(path);
    if (!stream) throw std::runtime_error("IRIS_UART_RX_SCRIPT: cannot open explicit frame stimulus");
    std::string line;
    while (std::getline(stream,line)) {
      if (line.empty() || line[0]=='#') continue;
      std::istringstream row(line);std::string start,complete,value,extra;
      if (!(row>>start>>complete>>value) || row>>extra)
        throw std::runtime_error("IRIS_UART_RX_SCRIPT: expected start_cycle complete_cycle byte");
      auto number=[](const std::string&s) {size_t n=0;auto v=std::stoull(s,&n,0);if(n!=s.size()||s[0]=='-')throw std::runtime_error("IRIS_UART_RX_SCRIPT: unsigned integer required");return v;};
      uint64_t byte=number(value);if(byte>255)throw std::runtime_error("IRIS_UART_RX_SCRIPT: byte exceeds8bits");
      uart->schedule_rx_frame(number(start),number(complete),uint8_t(byte));
    }
  }
  uart->registers().on_interrupt([controller=sim->get_intctrl()](bool level) {
    controller->set_interrupt_level(1,level);
  });
  uart->registers().on_transmitted([](uint8_t value) {canonical_terminal_t::write(value);});
  return uart;
}
static std::string generate_uart(const sim_t*,const std::vector<std::string>& args) {
  size_t parsed=0;
  if (args.size()!=1 || std::stoull(args[0],&parsed,0)!=0x10020000 || parsed!=args[0].size())
    throw std::runtime_error("IRIS_UART_BOARD_MISMATCH: explicit audited base required");
  return " serial@10020000 {compatible=\"sifive,uart0\";reg=<0 0x10020000 0 0x1000>;interrupts=<1>;};\n";
}
REGISTER_DEVICE(sifive_uart,parse_uart,generate_uart);
