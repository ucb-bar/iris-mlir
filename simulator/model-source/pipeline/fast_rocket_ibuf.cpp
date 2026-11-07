#include "fast_rocket_ibuf.hpp"
#include "fast_source_rvc_table.hpp"
#include "fast_rocket_rvc_fields.hpp"
#include <stdexcept>
namespace iris::frontend {
FastRocketIBuf::Outputs FastRocketIBuf::evaluate(const Inputs&in){
 evaluated_=false;capture_.reset();
 if(!reset_seen_&&!in.reset)throw std::runtime_error("IRIS_ROCKET_IBUF_RESET_REQUIRED");pending_reset_=in.reset;
 const auto&p=in.packet;if(p.pc>>40||p.entry>31||p.history>255)throw std::invalid_argument("IRIS_ROCKET_IBUF_PORT_WIDTH");
 if(in.valid&&p.taken&&unsigned(p.branch_index)<((p.pc>>1)&1))throw std::runtime_error("IRIS_ROCKET_IBUF_SOURCE_BRANCH_INDEX");
 if(buffered_&&!buffer_)throw std::logic_error("IRIS_ROCKET_IBUF_UNDEFINED_BUFFER");
 Outputs out;const unsigned pc_half=(p.pc>>1)&1,n_buf=unsigned(buffered_);
 const unsigned n_ic=((p.taken?unsigned(p.branch_index)+1:2)-pc_half)&3;
 const unsigned n_valid=((in.valid?n_ic:0)+n_buf)&3,valid_mask=((1u<<n_valid)-1)&3;
 const uint32_t selected=pc_half?p.data>>16:p.data,selected_known=pc_half?p.known>>16:p.known;
 if(buffered_){out.raw=(buffer_->data&65535)|(selected<<16);out.raw_known=(buffer_->known&65535)|(selected_known<<16);}
 else if(pc_half){out.raw=(p.data>>16)*0x10001u;out.raw_known=(p.known>>16)*0x10001u;}
 else {out.raw=p.data;out.raw_known=p.known;}
 out.compressed=(out.raw&3)!=3;
 const bool length_known=(out.raw_known&3)==3;
 const unsigned buf_mask=(1u<<n_buf)-1;
 const unsigned replay=(buffered_&&buffer_->replay?buf_mask:0)|(p.replay?(valid_mask&~buf_mask):0);
 const bool full=out.compressed||(valid_mask&2)||(buffered_&&buffer_->replay);
 const unsigned n_ready=full?(out.compressed?1:2):0,n_ic_ready=(n_ready-n_buf)&3,remaining=(n_ic-n_ic_ready)&3;
 out.imem_ready=in.ready&&n_ready>=n_buf&&(n_ic_ready>=n_ic||!(remaining&2));
 out.ready_known=length_known||(!in.ready);
 out.valid=(valid_mask&1)&&full;out.replay=(replay&1)||(!out.compressed&&(replay&2));
 out.pc=buffered_?buffer_->pc:p.pc;
 const bool saved_metadata=buffered_&&out.compressed;
 out.entry=saved_metadata?buffer_->entry:p.entry;out.history=saved_metadata?buffer_->history:p.history;
 out.first=buffered_?buffer_->exception:p.exception;if(!out.compressed)out.second=p.exception;
 const uint32_t needed=out.compressed?65535:0xffffffffu;
 out.instruction_known=(out.raw_known&needed)==needed;out.rs3_known=(out.raw_known&0xf8000000u)==0xf8000000u;
 if((valid_mask&1)&&!length_known)throw std::runtime_error("IRIS_ROCKET_IBUF_UNKNOWN_INSTRUCTION_LENGTH");
 if(out.valid&&!out.replay&&!out.instruction_known)throw std::runtime_error("IRIS_ROCKET_IBUF_UNKNOWN_INSTRUCTION_BITS");
 out.expanded=expand_source_rvc(out.raw);const auto fields=source_rocket_registers(out.raw);out.rd=fields&31;out.rs1=(fields>>5)&31;out.rs2=(fields>>10)&31;out.rs3=out.raw>>27;
 const bool capture=in.valid&&n_ready>=n_buf&&n_ic_ready<n_ic&&!(remaining&2);
 next_buffered_=buffered_;
 if(in.ready){next_buffered_=n_ready>=n_buf?false:((n_buf-n_ready)&1);if(capture)next_buffered_=remaining&1;}
 if(in.reset||in.kill)next_buffered_=false;
 if(in.ready&&capture){
  Packet next=p;const unsigned shift=(pc_half+n_ic_ready)&3;
  // Source right shifter pads with the top halfword before slicing low16.
  const unsigned half=shift==0?0:1;
  next.data=half?p.data>>16:p.data&65535;next.known=(half?p.known>>16:p.known&65535)|0xffff0000u;
  next.pc=(p.pc&~3ull)|((p.pc+2*n_ic_ready)&3);capture_=next;
 }
 evaluated_=true;return out;
}
void FastRocketIBuf::commit(){if(!evaluated_)throw std::logic_error("IRIS_ROCKET_IBUF_COMMIT_WITHOUT_EVALUATE");if(capture_)buffer_=std::move(capture_);buffered_=next_buffered_;reset_seen_=reset_seen_||pending_reset_;evaluated_=false;}
}
