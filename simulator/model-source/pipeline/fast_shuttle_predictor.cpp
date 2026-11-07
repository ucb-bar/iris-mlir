#include "fast_shuttle_predictor.hpp"
#include <bit>
#include <stdexcept>
namespace iris::frontend {
namespace {unsigned onehot_index(unsigned mask){unsigned n=0;for(unsigned i=0;i<32;++i)if(mask&(1u<<i))n|=i;return n;}unsigned rotate6(unsigned m){return((m<<1)|(m>>5))&63;}}
unsigned FastShuttlePredictor::replacement(uint32_t state,unsigned ways){if(ways==2)return state&1;const unsigned half=ways/2,side=(state>>(ways-2))&1;const uint32_t sub=side?state>>(half-1):state;return side*half+replacement(sub&((1u<<(half-1))-1),half);}
uint32_t FastShuttlePredictor::touched(uint32_t state,unsigned way,unsigned ways){if(ways==2)return!(way&1);const unsigned half=ways/2,shift=(way&half)?half-1:0;const uint32_t mask=(1u<<(half-1))-1;state=(state&~(mask<<shift))|(touched((state>>shift)&mask,way&(half-1),half)<<shift);state=(state&~(1u<<(ways-2)))|((!(way&half))<<(ways-2));return state;}
unsigned FastShuttlePredictor::counter_index(uint64_t pc,unsigned history){const unsigned h=((history*221)&255)>>5;return((pc>>3)&511)^((pc>>12)&3)^(h<<6);}
uint32_t FastShuttlePredictor::index_hits(uint64_t pc)const{uint32_t hit=0;for(unsigned i=0;i<32;++i)if((valid_&(1u<<i))&&entries_[i].index==((pc>>1)&8191))hit|=1u<<i;return hit;}
unsigned FastShuttlePredictor::page_hits(uint64_t pc)const{unsigned hit=0;for(unsigned i=0;i<6;++i)if((page_valid_&(1u<<i))&&pages_[i]==pc>>14)hit|=1u<<i;return hit;}
FastShuttlePredictor::Prediction FastShuttlePredictor::evaluate(const Inputs&in)const{
 if(in.pc>>39||in.btb.pc>>39||in.btb.target>>39||in.btb.branch_pc>>39||in.btb.prediction_entry>63||in.btb.cfi_type>3||in.bht.pc>>39||in.bht.history>255||in.bht.value>3)throw std::invalid_argument("source Shuttle predictor physical width");
 Prediction out;const auto hit=index_hits(in.pc);unsigned ip=0,tp=0,idx=0,tgt=0;bool isbranch=false;
 for(unsigned i=0;i<32;++i)if(hit&(1u<<i)){const auto&e=entries_[i];ip|=e.index_page;tp|=e.target_page;idx|=e.branch_index;tgt|=e.target;isbranch|=e.cfi==0;}
 out.valid=bool(((page_hits(in.pc)<<1)>>ip)&1);out.entry=onehot_index(hit);out.index=idx;out.target=((tp<6&&(page_valid_&(1u<<tp))?pages_[tp]:0)<<14)|(uint64_t(tgt)<<1);out.history=history_;out.value=reset_cursor_<512?0:counters_[counter_index(in.pc,history_)];out.taken=!isbranch||bool(out.value&1);return out;
}
void FastShuttlePredictor::commit(const Inputs&in){
 const auto response=evaluate(in);
 if(in.reset){valid_=plru_=page_valid_=next_page_=history_=reset_cursor_=0;update_={};previous_response_={};duplicate_clears_=0;return;}
 const auto r=update_;const auto hit=index_hits(in.pc);const auto page_hit=page_hits(in.pc),update_page_hit=page_hits(r.pc);const bool update_hit=r.prediction_entry<32,use_update_page=update_page_hit!=0,use_page=page_hit!=0;
 const auto way=update_hit?r.prediction_entry:replacement(plru_,32);const bool replace_index=!use_update_page,same_page=(r.pc>>14)==(r.target>>14),replace_target=!same_page&&!use_page;
 const auto index_replacement=rotate6(page_hit)|(use_page?0:1u<<next_page_);const auto index_update_oh=use_update_page?update_page_hit:index_replacement;const auto index_update=onehot_index(index_update_oh),index_replace_en=replace_index?index_replacement:0;
 const auto target_replacement=same_page?index_update_oh:rotate6(index_update_oh),target_update=onehot_index(page_hit|(use_page?0:target_replacement)),target_replace_en=replace_target?target_replacement:0;
 const bool insert=r.valid&&r.mispredict&&!(update_hit&&entries_[way].useful&&(valid_&(1u<<way)));
 if((previous_response_.valid&&previous_response_.taken)||(r.valid&&r.mispredict))plru_=touched(plru_,r.valid?way:previous_response_.entry,32);
 if(r.valid)entries_[way].useful=!r.mispredict;
 if(insert){auto&e=entries_[way];e.index=(r.pc>>1)&8191;e.target=(r.target>>1)&8191;e.index_page=index_update+1;e.target_page=target_update;e.cfi=r.cfi_type;e.branch_index=(r.branch_pc>>1)&3;e.useful=true;
  for(unsigned i=0;i<6;++i){const bool index_bank=(i&1)==(index_update&1);const auto en=index_bank?index_replace_en:target_replace_en;if(en&(1u<<i))pages_[i]=(index_bank?r.pc:r.target)>>14;}page_valid_|=index_replace_en|target_replace_en;
 }
 if(in.flush)valid_=0;else if(std::popcount(hit)>=2){valid_&=~hit;++duplicate_clears_;}else if(insert){if(r.is_valid)valid_|=1u<<way;else valid_&=~(1u<<way);}
 if(r.valid&&r.mispredict&&(replace_index||replace_target)){const auto next=next_page_+(replace_index&&replace_target?2:1);next_page_=next>=6?next&1:next;}
 if(reset_cursor_<512)counters_[reset_cursor_++]=0;else if(in.bht.valid&&in.bht.branch)counters_[counter_index(in.bht.pc,in.bht.history)]=((in.bht.taken^(in.bht.value&1))<<1)|unsigned(in.bht.value==1||((in.bht.value&2)&&in.bht.taken));
 if(in.bht.valid&&in.bht.mispredict)history_=in.bht.branch?(unsigned(in.bht.taken)<<7)|(in.bht.history>>1):in.bht.history;else if(in.advance&&response.valid)history_=((response.value&1)<<7)|(history_>>1);
 update_=in.btb;previous_response_=response;
}
}
