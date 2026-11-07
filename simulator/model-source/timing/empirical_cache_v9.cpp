#include "empirical_cache_v9.hpp"
#include <algorithm>
#include <limits>
#include <stdexcept>
#include <set>
namespace iris::empirical {
CacheConfigV9 iris_geometry_v9(){return {{{{64,2,1,64},{64,2,1,64},{64,4,1,64}}},{{{64,2,1,64},{64,2,1,64},{128,2,1,64}}},{512,4,4,64}};}
void CacheHistoryV9::validate_geometry(Geometry g){
 auto power=[](unsigned x){return x && !(x&(x-1));};
 if(!power(g.sets)||!power(g.ways)||!power(g.banks)||!power(g.line_bytes)||g.line_bytes<8||g.sets>65536||g.ways>64||g.banks>64)throw std::invalid_argument("IRIS_EMPIRICAL_CACHE_GEOMETRY");
}
CacheHistoryV9::CacheHistoryV9(CacheConfigV9 c,bool reads,bool random):compatible_warm_reads_(reads),source_random_shuttle_(random),config_(c){
 if(random)for(unsigned h=0;h<2;++h){const auto&g=c.l1d[h];if(g.sets!=64||g.ways!=2||g.banks!=1||g.line_bytes!=64)throw std::invalid_argument("IRIS_SOURCE_RANDOM_SHUTTLE_GEOMETRY");}
 validate_geometry(c.l2);l2_.geometry=c.l2;l2_.lines.resize(size_t(c.l2.sets)*c.l2.ways*c.l2.banks);
 for(unsigned h=0;h<3;++h){validate_geometry(c.l1d[h]);if(c.l1d[h].line_bytes!=c.l2.line_bytes)throw std::invalid_argument("IRIS_EMPIRICAL_CACHE_LINE_MISMATCH");l1_[h].geometry=c.l1d[h];l1_[h].lines.resize(size_t(c.l1d[h].sets)*c.l1d[h].ways*c.l1d[h].banks);validate_geometry(c.l1i[h]);if(c.l1i[h].line_bytes!=c.l2.line_bytes)throw std::invalid_argument("IRIS_EMPIRICAL_ICACHE_LINE_MISMATCH");l1i_[h].geometry=c.l1i[h];l1i_[h].lines.resize(size_t(c.l1i[h].sets)*c.l1i[h].ways*c.l1i[h].banks);}
}
void CacheHistoryV9::advance(uint64_t t){if(t<tick_)throw std::invalid_argument("IRIS_EMPIRICAL_CACHE_TIME_REVERSED");tick_=t;}
Path CacheHistoryV9::classify(unsigned h,uint64_t a,uint64_t n,uint64_t&canonical){
 if(h>=3||!n||n>4096||a>std::numeric_limits<uint64_t>::max()-n)throw std::invalid_argument("IRIS_EMPIRICAL_CACHE_ACCESS");
 auto inside=[&](uint64_t b,uint64_t size){return a>=b&&a-b<size&&n<=size-(a-b);};
 canonical=a;
 if(a>=0x70080000ull&&a<0x70100000ull){
  if(h==2)throw std::invalid_argument("IRIS_EMPIRICAL_CACHE_HART2_TCM_ALIAS");
  // The alias wraps every128KiB; crossing its canonical TCM extent is not a
  // single request and must be split by the caller's actual memory protocol.
  canonical=0x70000000ull+h*0x20000ull+(a&0x1ffffull);
  if(n>0x20000ull-(a&0x1ffffull))throw std::invalid_argument("IRIS_EMPIRICAL_CACHE_ALIAS_CROSSING");
  return Path::LocalTCM;
 }
 for(unsigned t=0;t<2;++t)if(inside(0x70000000ull+t*0x20000ull,0x20000ull))return h==t?Path::LocalTCM:Path::RemoteTCM;
 if(inside(0x580000000ull,0x20000ull))return Path::Scratchpad;
 if(inside(0x80000000ull,0x100000000ull))return Path::DRAM;
 // Do not silently classify a partly valid DRAM/TCM transaction as MMIO.
 auto overlaps=[&](uint64_t b,uint64_t size){return a<b+size&&a+n>b;};
 if(overlaps(0x70000000ull,0x40000ull)||overlaps(0x580000000ull,0x20000ull)||overlaps(0x80000000ull,0x100000000ull))throw std::invalid_argument("IRIS_EMPIRICAL_CACHE_REGION_CROSSING");
 return Path::Uncached;
}
unsigned CacheHistoryV9::bank(const Cache&c,uint64_t line){return unsigned(line%c.geometry.banks);}
size_t CacheHistoryV9::set_start(const Cache&c,uint64_t line){return size_t((line/c.geometry.banks)%c.geometry.sets*c.geometry.banks+bank(c,line))*c.geometry.ways;}
CacheHistoryV9::Line*CacheHistoryV9::find(Cache&c,uint64_t line){const size_t at=set_start(c,line);for(unsigned w=0;w<c.geometry.ways;++w)if(c.lines[at+w].valid&&c.lines[at+w].tag==line)return &c.lines[at+w];return nullptr;}
CacheHistoryV9::Line CacheHistoryV9::install(Cache&c,uint64_t line,bool dirty){
 if(age_==std::numeric_limits<uint64_t>::max())throw std::overflow_error("IRIS_EMPIRICAL_CACHE_AGE_HORIZON");
 if(auto*p=find(c,line)){p->age=++age_;p->dirty|=dirty;return {};}
 size_t at=set_start(c,line),slot=at;
 for(unsigned w=0;w<c.geometry.ways;++w){size_t p=at+w;if(!c.lines[p].valid){slot=p;break;}if(c.lines[p].age<c.lines[slot].age)slot=p;}
 // Only a new private Shuttle allocation uses the source random selector.
 // Source captures this way in S1 and advances at MSHR acceptance. This
 // atomic intent-time call is an explicit allocation-phase proxy; it does
 // not certify merging/retry/permission-only miss timing. No invalid-way bias.
 if(source_random_shuttle_)for(unsigned h=0;h<2;++h)if(&c==&l1_[h]){
  const uint16_t state=source_random_state_[h];slot=at+(state&1U);
  source_random_state_[h]=uint16_t((state<<1)|(((state>>10)^(state>>12)^(state>>13)^(state>>15))&1));
  ++source_random_allocations_[h];break;
 }
 Line old=c.lines[slot];c.lines[slot]={line,++age_,0,true,dirty,false};return old;
}
void CacheHistoryV9::install_l2(uint64_t line,bool dirty,Delta&d){
 Line evicted=install(l2_,line,dirty);if(!evicted.valid)return;
 bool writeback=evicted.dirty;
 for(auto&cache:l1_)if(auto*p=find(cache,evicted.tag)){writeback|=p->dirty;p->valid=false;++d.coherence_invalidations;}
 if(writeback)++d.l2_dirty_victims;
}
Delta CacheHistoryV9::begin(Access a){return begin_internal(a,false,false);}
Delta CacheHistoryV9::begin_read(Access a){if(a.write)throw std::invalid_argument("IRIS_EMPIRICAL_ORDINARY_READ_REQUIRES_READ");return begin_internal(a,false,false,true);}
Delta CacheHistoryV9::begin_instruction(Access a){if(a.write)throw std::invalid_argument("IRIS_EMPIRICAL_ICACHE_WRITE");return begin_internal(a,true,false);}
Delta CacheHistoryV9::begin_vector(Access a){return begin_internal(a,false,true);}
Delta CacheHistoryV9::begin_internal(Access a,bool instruction,bool vector,bool ordinary_read){
 if(pending_.count(a.id))throw std::invalid_argument("IRIS_EMPIRICAL_CACHE_DUPLICATE_OWNER");
 if(!a.id||a.id<=last_id_)throw std::invalid_argument("IRIS_EMPIRICAL_CACHE_NONMONOTONIC_OWNER");
 uint64_t canonical=0;Path path=classify(a.hart,a.address,a.bytes,canonical);
 if(a.tick<tick_)throw std::invalid_argument("IRIS_EMPIRICAL_CACHE_TIME_REVERSED");
 // Validate full request before mutation (including age-headroom bound).
 const auto first=canonical/config_.l2.line_bytes,last=(canonical+a.bytes-1)/config_.l2.line_bytes;
 if(last-first+1>(std::numeric_limits<uint64_t>::max()-age_)/8)throw std::overflow_error("IRIS_EMPIRICAL_CACHE_AGE_HORIZON");
 const bool warm_read=compatible_warm_reads_&&ordinary_read&&!instruction&&!vector&&!a.write&&path==Path::DRAM&&data_hit(a.hart,a.address,a.bytes);
 advance(a.tick);Delta d;d.path=path;d.canonical_address=canonical;d.bytes=a.bytes;d.lines=last-first+1;
 Pending request{a,path,instruction,vector,ordinary_read,warm_read,{},{},{}};std::set<uint64_t> waits;
 for(uint64_t line=first;line<=last;++line){request.lines.push_back(line);request.banks.push_back(unsigned(line%(path==Path::DRAM?config_.l2.banks:2)));}
 std::set<unsigned> same,other,shared;
 for(const auto&[id,p]:pending_){
  // This request has its own response-ready private copy. A different
  // ordinary reader (including a foreign cold Get) cannot write that data.
  if(warm_read&&p.ordinary_read)continue;
  auto domain=[this](Path q,uint64_t line){if(q==Path::DRAM)return uint64_t(4);if(q==Path::Scratchpad)return uint64_t(3);if(q==Path::LocalTCM||q==Path::RemoteTCM)return uint64_t(1+(line*config_.l2.line_bytes-0x70000000ull)/0x20000ull);return uint64_t(0);};
  if(path==Path::Uncached||domain(path,request.lines.front())!=domain(p.path,p.lines.front()))continue;
  if(p.access.hart==a.hart)continue;
  bool overlap=false,samebank=false;
  for(size_t i=0;i<request.lines.size();++i){
   if(std::find(p.lines.begin(),p.lines.end(),request.lines[i])!=p.lines.end())overlap=true;
   if(std::find(p.banks.begin(),p.banks.end(),request.banks[i])!=p.banks.end())samebank=true;
  }
  (samebank?same:other).insert(p.access.hart);
  if(overlap){shared.insert(p.access.hart);waits.insert(id);}
 }
 d.same_bank_contenders=same.size();d.other_bank_contenders=other.size();d.shared_line_contenders=shared.size();
 // Unknowns, writes, atomics and incomplete fills retain total same-line order.
 // A ready private read is compatible with another explicitly ordinary read.
 for(const auto&[id,p]:pending_){bool overlap=false;for(uint64_t line:request.lines)if(std::find(p.lines.begin(),p.lines.end(),line)!=p.lines.end())overlap=true;
  if(overlap){if(warm_read&&p.ordinary_read)++compatible_read_dependencies_omitted_;else waits.insert(id);}}
 if(warm_read)++warm_read_accepts_;
 d.wait_for.assign(waits.begin(),waits.end());request.dependencies=d.wait_for;
 if(path==Path::DRAM&&vector){
  for(uint64_t line:request.lines){
   // Direct ATL uncached Get/Put shares L2, not the scalar private Dcache.
   // Dirty owners must preserve untouched bytes before a partial Put.
   for(auto&cache:l1_)if(auto*owner=find(cache,line))if(owner->dirty){++d.dirty_owner_forwards;owner->dirty=false;install_l2(line,true,d);}
   if(find(l2_,line))++d.l2_hits;else ++d.l2_misses;
   install_l2(line,a.write,d);
   if(a.write)for(auto&cache:l1_)if(auto*copy=find(cache,line)){copy->valid=false;++d.coherence_invalidations;}
  }
 }else if(path==Path::DRAM){
  auto&mine=instruction?l1i_[a.hart]:l1_[a.hart];
  for(uint64_t line:request.lines){
   auto*hit=find(mine,line);
   if(hit){++d.l1_hits;install(mine,line,a.write);}
   else{
    ++d.l1_misses;
    for(unsigned h=0;h<3;++h)if(instruction||h!=a.hart)if(auto*owner=find(l1_[h],line))if(owner->dirty){++d.dirty_owner_forwards;owner->dirty=false;install_l2(line,true,d);}
    if(find(l2_,line))++d.l2_hits;else ++d.l2_misses;
    install_l2(line,false,d);
    Line victim=install(mine,line,a.write);
    if(victim.valid&&victim.dirty){++d.l1_dirty_victims;install_l2(victim.tag,true,d);}
   }
   // A new allocation and each accepted writer establish a distinct fill
   // generation. An old same-address response cannot validate a newer tag.
   if(compatible_warm_reads_&&!instruction&&(a.write||!hit))if(auto*copy=find(mine,line)){copy->response_ready=false;copy->readiness_owner=a.id;}
   if(a.write)for(unsigned h=0;h<3;++h)if(h!=a.hart)if(auto*p=find(l1_[h],line)){p->valid=false;++d.coherence_invalidations;}
  }
 }
 pending_.emplace(a.id,std::move(request));last_id_=a.id;return d;
}
bool CacheHistoryV9::data_hit(unsigned hart,uint64_t address,uint64_t bytes)const{
 if(hart>=3||!bytes||bytes>4096||address>std::numeric_limits<uint64_t>::max()-bytes)throw std::invalid_argument("IRIS_EMPIRICAL_CACHE_QUERY_ACCESS");
 // This predicts the narrow cacheable DRAM path only. Other addresses return
 // false and retain their existing architectural permission/fault handlers.
 if(address<0x80000000ull||address>=0x180000000ull||bytes>0x180000000ull-address)return false;
 const uint64_t first=address/config_.l2.line_bytes,last=(address+bytes-1)/config_.l2.line_bytes;const Cache&cache=l1_[hart];
 for(uint64_t line=first;line<=last;++line){
  bool hit=false;const size_t at=set_start(cache,line);for(unsigned way=0;way<cache.geometry.ways;++way)if(cache.lines[at+way].valid&&cache.lines[at+way].tag==line&&(!compatible_warm_reads_||cache.lines[at+way].response_ready)){hit=true;break;}
  if(!hit)return false;
  // An early-installed own tag remains unready until successful completion.
  // Other explicitly ordinary readers cannot change this ready copy; every
  // writer/atomic/unknown still blocks, regardless of its own hit/miss state.
  for(const auto&[id,p]:pending_)if(std::find(p.lines.begin(),p.lines.end(),line)!=p.lines.end()&&!(compatible_warm_reads_&&p.ordinary_read))return false;
 }
 return true;
}
bool CacheHistoryV9::owned_store_hit(unsigned hart,uint64_t address,uint64_t bytes)const{
 if(hart>=3||!bytes||bytes>4096||address>std::numeric_limits<uint64_t>::max()-bytes)throw std::invalid_argument("IRIS_EMPIRICAL_CACHE_QUERY_ACCESS");
 // The first native consumer is deliberately RV94 aligned SB/SH/SW/SD only.
 if((bytes!=1&&bytes!=2&&bytes!=4&&bytes!=8)||address%bytes)return false;
 if(address<0x80000000ull||address>=0x180000000ull||bytes>0x180000000ull-address)return false;
 const uint64_t first=address/config_.l2.line_bytes,last=(address+bytes-1)/config_.l2.line_bytes;
 if(first!=last)return false;
 for(const auto&[id,p]:pending_)if(std::find(p.lines.begin(),p.lines.end(),first)!=p.lines.end())return false;
 const auto&mine=l1_[hart];const size_t at=set_start(mine,first);bool owned=false;
 for(unsigned way=0;way<mine.geometry.ways;++way){const auto&line=mine.lines[at+way];if(line.valid&&line.tag==first&&line.dirty)owned=true;}
 if(!owned)return false;
 // A clean cached copy is insufficient: source permission promotion may
 // need probes/metadata changes. Dirty exclusivity is checked explicitly.
 for(unsigned h=0;h<3;++h)if(h!=hart){const auto&peer=l1_[h];const size_t start=set_start(peer,first);for(unsigned way=0;way<peer.geometry.ways;++way)if(peer.lines[start+way].valid&&peer.lines[start+way].tag==first)return false;}
 return true;
}
Delta CacheHistoryV9::begin_owned_store(Access access){
 if(!access.write)throw std::invalid_argument("IRIS_EMPIRICAL_OWNED_STORE_REQUIRES_WRITE");
 if(!owned_store_hit(access.hart,access.address,access.bytes))throw std::logic_error("IRIS_EMPIRICAL_STORE_OWNERSHIP_CHANGED");
 return begin(access);
}
bool CacheHistoryV9::instruction_hit(unsigned hart,uint64_t address,uint64_t bytes)const{
 if(hart>=3||!bytes||bytes>4096||address>std::numeric_limits<uint64_t>::max()-bytes)throw std::invalid_argument("IRIS_EMPIRICAL_ICACHE_QUERY_ACCESS");
 if(address<0x80000000ull||address>=0x180000000ull||bytes>0x180000000ull-address)return false;
 const uint64_t first=address/config_.l2.line_bytes,last=(address+bytes-1)/config_.l2.line_bytes;const Cache&cache=l1i_[hart];
 for(uint64_t line=first;line<=last;++line){
  bool hit=false;const size_t at=set_start(cache,line);for(unsigned way=0;way<cache.geometry.ways;++way)if(cache.lines[at+way].valid&&cache.lines[at+way].tag==line){hit=true;break;}
  if(!hit)return false;
  // Get-only private Icache does not consult pending foreign transactions.
  // Only our own early-installed, uncompleted instruction refill is excluded.
  for(const auto&[id,p]:pending_)if(p.instruction&&p.access.hart==hart&&std::find(p.lines.begin(),p.lines.end(),line)!=p.lines.end())return false;
 }
 return true;
}
Delta CacheHistoryV9::touch_instruction(unsigned hart,uint64_t address,uint64_t bytes,uint64_t tick){
 if(!instruction_hit(hart,address,bytes))throw std::logic_error("IRIS_EMPIRICAL_ICACHE_TOUCH_MISS");
 if(tick<tick_)throw std::invalid_argument("IRIS_EMPIRICAL_CACHE_TIME_REVERSED");
 const uint64_t first=address/config_.l2.line_bytes,last=(address+bytes-1)/config_.l2.line_bytes;
 if(last-first+1>std::numeric_limits<uint64_t>::max()-age_)throw std::overflow_error("IRIS_EMPIRICAL_CACHE_AGE_HORIZON");
 advance(tick);Delta d;d.path=Path::DRAM;d.canonical_address=address;d.bytes=bytes;d.lines=last-first+1;d.l1_hits=d.lines;
 for(uint64_t line=first;line<=last;++line)install(l1i_[hart],line,false);
 return d;
}
void CacheHistoryV9::complete(uint64_t id,uint64_t t,bool successful){auto i=pending_.find(id);if(i==pending_.end())throw std::invalid_argument("IRIS_EMPIRICAL_CACHE_COMPLETION_OWNER");if(t<i->second.access.tick||t<tick_)throw std::invalid_argument("IRIS_EMPIRICAL_CACHE_TIME_REVERSED");for(uint64_t dependency:i->second.dependencies)if(pending_.count(dependency))throw std::logic_error("IRIS_EMPIRICAL_CACHE_PREMATURE_DEPENDENT_COMPLETION");advance(t);
 // A tag installed at accepted intent is not usable until an actual matching
 // successful scalar response. Never infer readiness from another request's
 // warm/cold classification: independent ordinary readers cannot write data.
 const auto&p=i->second;if(compatible_warm_reads_&&p.path==Path::DRAM&&!p.instruction&&!p.vector){for(auto line:p.lines)if(auto*copy=find(l1_[p.access.hart],line))if(copy->readiness_owner==id){if(successful)copy->response_ready=true;else copy->valid=false;}}
 pending_.erase(i);}
void CacheHistoryV9::fence_i(unsigned hart,uint64_t t){if(hart>=3)throw std::invalid_argument("IRIS_EMPIRICAL_ICACHE_HART");if(t<tick_)throw std::invalid_argument("IRIS_EMPIRICAL_CACHE_TIME_REVERSED");for(const auto&[id,p]:pending_)if(p.access.hart==hart)throw std::logic_error("IRIS_EMPIRICAL_ICACHE_FENCE_WITH_PENDING");advance(t);for(auto&line:l1i_[hart].lines)line={};}
void CacheHistoryV9::reset_timing_tags(uint64_t t){if(!pending_.empty())throw std::logic_error("IRIS_EMPIRICAL_CACHE_RESET_WITH_PENDING");advance(t);for(auto&c:l1_)for(auto&l:c.lines)l={};for(auto&cache:l1i_)for(auto&line:cache.lines)line={};for(auto&l:l2_.lines)l={};age_=0;}
} // namespace iris::empirical
