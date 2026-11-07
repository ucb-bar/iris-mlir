"""Owned source-event timelines; numeric credit requires actual publication.

This consumer observes simulator source actors, never physical FPGA activity or
memory traffic. Unknown event families remain raw and receive no work credit.
"""
import argparse,bisect,collections,json
from pathlib import Path
from .guest_regions import Instructions
from .report import symbols
from .empirical_profile import sha

def need(v,s):
 if not v:raise ValueError(s)
def integer(x,name,positive=False):
 need(type(x)is int and x>=int(positive),name+' must be a typed nonnegative integer');return x
def merged(intervals):
 out=[]
 for a,b in sorted(intervals):
  if a>=b:continue
  if out and a<=out[-1][1]:out[-1][1]=max(out[-1][1],b)
  else:out.append([a,b])
 return out

def region_bounds(console,prefixes,code,region_map=None,region_format='iris-json'):
 rows=[]
 if region_map is not None:
  data=json.loads(Path(region_map).read_text());need(data.get('schema')=='iris.source_event_region_map.v1'and isinstance(data.get('regions'),list),'Explicit region-map schema required');rows=data['regions']
 elif region_format=='iris-json':
  for line in console.splitlines():
   if line.startswith('IRIS_REGION '):
    x=json.loads(line.split(' ',1)[1]);need(x.get('pass')is True,'Failed explicit guest region is not a qualified ROI');rows.append(x)
 elif region_format=='merlin-kernel-v1':
  for line in console.splitlines():
   if line.startswith('REGION '):
    f=line.split();need(len(f)==5,'Exact Merlin REGION hart/start/end/instret marker');h,a,b,ir=map(int,f[1:]);need(ir>0,'Actual positive Merlin retired count');rows.append(dict(case_id='merlin.kernel.'+str(len(rows)),hart=h,start_cycle=a,end_cycle=b))
 elif region_format!='none':raise ValueError('Unsupported explicit region format')
 if not rows:return [],'No supported explicit region markers/map; wholeprogram attribution only'
 wanted={(integer(r['hart'],'region hart'),integer(r[k],k))for r in rows for k in('start_cycle','end_cycle')};selected={};last={}
 for line in Path(prefixes).open():
  p=json.loads(line);need(p.get('schema')=='iris.empirical.prefix.v1','Unknown CSR prefix schema');h=integer(p['hart'],'prefix hart');seq=integer(p['sequence'],'sequence');cycle=integer(p['read_cycle'],'read_cycle');value=integer(p['value'],'counter value')
  need(h<3 and seq>last.get(h,-1),'Ordered actual CSR prefix');last[h]=seq;raw=integer(p['raw'],'CSRraw');pc=integer(p['pc'],'CSRpc')
  need(code.read(pc)==raw and raw&127==0x73 and raw>>20 in(0xb00,0xc00)and p['csr']==raw>>20 and p['destination']==((raw>>7)&31),'Actual ELF-qualified MCYCLE observation')
  if(h,value)in wanted and p.get('result_discarded')is False:
   need((h,value)not in selected,'Ambiguous explicit CSR boundary');selected[h,value]=p
 need(set(selected)==wanted,'Explicit regions require every actual CSR endpoint')
 result=[];seen=set()
 for i,r in enumerate(rows):
  h=r['hart'];identity=(h,r['start_cycle'],r['end_cycle']);need(identity not in seen,'Duplicate explicit region');seen.add(identity);a,b=[selected[h,r[k]]for k in('start_cycle','end_cycle')];need(a['sequence']<b['sequence']and a['read_cycle']<b['read_cycle'],'Ordered explicit CSR region');name=r.get('case_id',r.get('name'));need(isinstance(name,str)and name,'Explicit nonempty region name')
  result.append(dict(region_id=i,case_id=name,hart=h,start_cycle=r['start_cycle'],end_cycle=r['end_cycle'],global_begin=a['read_cycle'],global_end=b['read_cycle'],program_mcycle_delta=(b['value']-a['value'])%2**64,source_global_span_cycles=b['read_cycle']-a['read_cycle'],begin_pc=a['pc'],end_pc=b['pc']))
 return result,'Explicit guest/map endpoints joined to actual returning MCYCLE CSR reads; no inferred pairs'

def profile(events,features,console,summary,elf,rom,output,nm,region_map=None,region_format='iris-json',execution_complete=True):
 output=Path(output);need(not output.exists(),'Fresh profile directory required');summary_path=Path(summary);summary=json.loads(summary_path.read_text());hz=integer(summary['base_clock_hz'],'baseclock',True);cycles=integer(summary['cycles'],'total cycles');dividers=summary['hart_cycle_dividers'];need(len(dividers)==3 and all(type(d)is int and d>0 for d in dividers),'Actual threehart source clock dividers')
 code=Instructions(elf,rom);syms=symbols(elf,nm);starts=[s[0]for s in syms]
 def function(pc):
  n=bisect.bisect_right(starts,pc)-1
  return syms[n][2]if n>=0 and pc<syms[n][1]else None
 front={};commands={};canceled=set();issues=collections.defaultdict(dict);writes=collections.defaultdict(dict);opu_front={};opu_reserved={};opu_K={};opu_completed=set();opu_published=set();opu_by_due=collections.defaultdict(list);opu_unattributed=0;legacy=collections.Counter();trace=[];tail=b'';unknown=collections.Counter();count=0
 micros=lambda x:x*1e6/hz
 def instant(h,unit,name,t,args):trace.append(dict(ph='i',s='t',pid=h,tid=unit,name=name,ts=micros(t),args=args))
 with Path(events).open('rb')as stream:
  for line in stream:
   if not line.endswith(b'\n'):tail=line;break
   e=json.loads(line);kind=e.get('event');count+=1
   if kind and kind.startswith('fadd_EG_'):
    h=integer(e['hart'],'source hart');need(h<3,'Actual threehart journal')
    if kind in('fadd_EG_front_reservation','fadd_EG_functional_COM','fadd_EG_wrongpath_cancel','fadd_EG_microissue','fadd_EG_physical_write'):
     need(h<2,'Compute source FADD harts0/1 only');g=integer(e['generation'],'generation',True);key=h,g
     if kind=='fadd_EG_front_reservation':need(key not in front,'Duplicate FADD reservation');front[key]=e
     elif kind=='fadd_EG_functional_COM':need(key not in commands,'Duplicate FADD COM');commands[key]=e
     elif kind=='fadd_EG_wrongpath_cancel':need(key not in canceled,'Duplicate FADD cancellation');canceled.add(key)
     else:
      index=integer(e['index'],'EGindex');dest=issues if kind=='fadd_EG_microissue'else writes;need(index not in dest[key],'Duplicate FADD perEG event');dest[key][index]=e
    elif kind=='fadd_EG_counter_read':
     need(e.get('architecturally_retired')is True,'Counter journal must be retired');integer(e['read_cycle'],'counterread');instant(h,'CSR','retired counter read',e['read_cycle'],{k:e[k]for k in('pc','csr','value','phase','async_compute_pending')})
    elif kind=='fadd_EG_fence_admit':instant(h,'CPU-boundaries','FADD FENCE admitted',integer(e['cycle'],'fence cycle'),e)
    else:unknown[kind]+=1
   elif kind=='opu_front_accept':
    h=integer(e['hart'],'OPU hart');i=integer(e['instruction'],'OPUinstruction');need(h<2 and(h,i)not in opu_front,'Unique compute OPU owner');opu_front[h,i]=e
   elif kind=='opu_front_cancel':
    instant(e['hart'],'OPU-front','canceled OPU reservation',e['cycle'],dict(instruction=e['instruction'],work_credit=0))
   elif kind=='opu_K_microissue':opu_K[e['hart'],e['instruction'],e['cycle'],e['entry']]=e
   elif kind=='opu_microissue_reserved':
    h=e['hart'];i=e['instruction'];node=integer(e['backend_node'],'OPUnode',True);need(node not in opu_reserved and(h,i)in opu_front,'Reserved OPU work has actual front owner');f=opu_front[h,i];need(e['pc']==f['pc']and e['seq']>=f['com']+dividers[h]and e['write']>=e['seq'],'OPU reserved source phase');opu_reserved[node]=e;opu_by_due[h,i,e['write']].append(node)
   elif kind in('opu_K_completion','opu_entry_publish'):
    h=e['hart'];i=e['instruction'];t=integer(e['cycle'],'OPU completion');candidates=[]
    for node in opu_by_due[h,i,t]:
     r=opu_reserved[node]
     if r['macs']!=e['cells']:continue
     k=opu_K.get((h,i,r['seq'],r['entry']))
     if kind=='opu_K_completion'and k and(k['k']!=e['k']or k['last_k']!=e['last_k']):continue
     if kind=='opu_entry_publish'and k and not k['last_k']:continue
     candidates.append(node)
    if len(candidates)!=1:
     opu_unattributed+=1;instant(h,'OPU-unattributed','OPU completion with ambiguous owner',t,dict(instruction=i,cells=e['cells'],work_credit=None));continue
    node=candidates[0]
    bucket=opu_completed if kind=='opu_K_completion'else opu_published;need(node not in bucket,'Duplicate OPU completion/publication');bucket.add(node)
    r=opu_reserved[node];instant(h,'OPU-publication','OPU K callback'if kind=='opu_K_completion'else'OPU final entry publication',t,dict(instruction=i,entry=r['entry'],cells=e['cells'],function=function(r['pc']),pc=hex(r['pc']),actual_numeric_projection=(kind=='opu_entry_publish'),logical_MAC_work_credit=None))
   elif kind in('begin','publish','cancel','fp_staged','interrupt_defer','interrupt_pre_completion'):legacy[kind]+=1
   else:unknown[str(kind)]+=1
 need(not(set(commands)&canceled)and set(commands)<=set(front),'Committed FADD cannot be canceled or ownerless');need(set(issues)<=set(commands)and set(writes)<=set(commands),'FADD sourcework requires actual checked COM')
 functions={};completed=[];all_units=collections.defaultdict(list);pending_commands=0
 for key,c in commands.items():
  h,g=key;d=dividers[h];integer(c['cycle'],'COMcycle');pc=integer(c['pc'],'COMpc');raw=integer(c['raw'],'COMraw');sew,vl,lm,den=[integer(c[k],k,True)for k in('SEW','VL','LMUL_numerator','LMUL_denominator')]
  need(c.get('geometry_authority')=='checked_architectural_COM'and type(c.get('new_flags'))is int and c.get('new_flags')==0 and c.get('physical_VRF_restored')is True,'Authoritative zero-newflags COM required');need(sew in(16,32,64)and lm in(1,2,4,8)and den==1 and vl==256*lm//sew,'Finite fullVL FADDgeometry');need(raw==code.read(pc)and(raw&0xfc00707f)==0x1057 and raw&(1<<25),'Actual unmasked VFADD.VV from ELF');need(c['vd']==((raw>>7)&31)and c['vs1']==((raw>>15)&31)and c['vs2']==((raw>>20)&31),'Actual FADD operands');need(front[key]['pc']==pc and front[key]['COM']==c['cycle'],'Actual scheduled/retired COM join')
  expected=set(range(2*lm));need(set(issues[key])<=expected and set(writes[key])<=set(issues[key]),'Published EG requires owned actual source issue');fn=function(pc);agg=functions.setdefault((h,fn),dict(hart=h,function=fn,committed_commands=0,architecturally_retired_logical_add_elements=0,completed_EG_writes=0,completed_logical_add_elements=0,internal_VRF_write_bytes=0,pending_EGs=0))
  agg['committed_commands']+=1;agg['architecturally_retired_logical_add_elements']+=vl;agg['pending_EGs']+=len(expected-set(writes[key]));pending_commands+=bool(expected-set(writes[key]));last=None
  for index,x in issues[key].items():
   t=integer(x['cycle'],'issue cycle');need(x['pc']==pc and x['destination_EG']==2*c['vd']+index and t>=c['cycle']+4*d,'Actual source EG issue');instant(h,'FADD-issue','FADD EG accepted',t,dict(generation=g,index=index,SEW=sew,function=fn,pc=hex(pc),completed=index in writes[key]))
   if index not in writes[key]:continue
   w=writes[key][index];end=integer(w['cycle'],'write cycle');need(w['pc']==pc and w['bytes']==16 and end==t+4*d,'Actual fourstage16B source publication');need(end<=cycles,'Publication after captured model end');agg['completed_EG_writes']+=1;agg['completed_logical_add_elements']+=128//sew;agg['internal_VRF_write_bytes']+=16;last=max(last or 0,end);completed.append(dict(hart=h,owner=key,function=fn,pc=pc,COM=c['cycle'],issue=t,write=end,SEW=sew,elements=128//sew));all_units[h].append((t+d,end+d));instant(h,'FADD-write','actual16B VRF publication',end,dict(generation=g,index=index,SEW=sew,function=fn,internal_VRF_bytes=16));trace.append(dict(ph='X',pid=h,tid='FADD-source-valid',name='published EG source valid entries',ts=micros(t+d),dur=micros(end-t),args=dict(generation=g,index=index,SEW=sew,function=fn,interval='[issue+divider,write+divider)',physical_utilization=False)))
  if expected==set(writes[key]):trace.append(dict(ph='X',pid=h,tid='FADD-command',name='FADD committed command',ts=micros(c['cycle']),dur=micros(last+d-c['cycle']),args=dict(function=fn,pc=hex(pc),SEW=sew,VL=vl,generation=g,source_numeric_tail_complete=True)))
  else:instant(h,'FADD-command','FADD COM with pending publication',c['cycle'],dict(function=fn,pc=hex(pc),SEW=sew,VL=vl,generation=g,unpublished_EGs=len(expected-set(writes[key]))))
 for node,r in opu_reserved.items():
  trace.append(dict(ph='X',pid=r['hart'],tid='OPU-reservations',name='OPU reserved source interval',ts=micros(r['seq']),dur=micros(r['write']-r['seq']),args=dict(function=function(r['pc']),pc=hex(r['pc']),instruction=r['instruction'],entry=r['entry'],reserved_MACs=r['macs'],actual_K_callback=node in opu_completed,actual_final_publication=node in opu_published,completed_MAC_work_credit=None)))
 regions,region_scope=region_bounds(Path(console).read_text(),features,code,region_map,region_format);region_profiles=[]
 for r in regions:
  h=r['hart'];lo,hi=r['global_begin'],r['global_end'];selected=[v for v in completed if v['hart']==h and lo<=v['write']<hi];comkeys=[k for k,c in commands.items()if k[0]==h and lo<=c['cycle']<hi];region_profiles.append(dict(**r,FADD_retired_commands=len(comkeys),FADD_retired_logical_add_elements=sum(commands[k]['VL']for k in comkeys),FADD_actual_completed_EG_writes=len(selected),FADD_actual_completed_add_elements=sum(x['elements']for x in selected),FADD_completed_carry_in_EGs=sum(x['COM']<lo for x in selected),physical_memory_bytes=None));trace.append(dict(ph='X',pid=h,tid='explicit-regions',name=r['case_id'],ts=micros(lo),dur=micros(hi-lo),args=region_profiles[-1]))
 valid={str(h):sum(b-a for a,b in merged(spans))for h,spans in all_units.items()}
 result=dict(schema='iris.source_event_profile.v1',source_clock_hz=hz,whole_program_model_cycles=cycles,normal_execution_complete=execution_complete,journal_complete=not bool(tail),unterminated_tail_bytes=len(tail),journal_records=count,whole_program_includes_boot_setup_unmarked_and_UART=True,FADD=dict(authoritative_COM_commands=len(commands),canceled_reservations_without_work_credit=len(canceled),completed_EG_writes=len(completed),completed_logical_add_elements=sum(x['elements']for x in completed),architecturally_retired_logical_add_elements=sum(c['VL']for c in commands.values()),internal_VRF_write_bytes=16*len(completed),pending_commands=pending_commands,completed_pipeline_valid_union_cycles_by_hart=valid,pipeline_interval='[issue+divider,write+divider); four preedge source valid samples; completed EGs only',pipeline_union_complete=not bool(pending_commands or tail),per_function=list(functions.values())),OPU=dict(front_reservations=len(opu_front),reserved_microissues=len(opu_reserved),actual_owned_K_callbacks=len(opu_completed),actual_final_entry_publications=len(opu_published),ambiguous_or_unmatched_completion_records=opu_unattributed,unmatched_reserved_microissues=len(set(opu_reserved)-opu_completed),logical_MAC_work=None,physical_MAC_peak=None,scope='Reservations remain uncredited until matching callback/publication; no separate authoritative OPU COM geometry journal, so logical MAC/peak unavailable'),legacy_modeled_events=dict(legacy),unrecognized_events_preserved_raw=dict(unknown),regions=region_profiles,region_scope=region_scope,unavailable_metrics=['physical FPGA utilization/switching','physical DRAM/TL traffic or bandwidth','physical roofline/arithmetic intensity','source CPU additive stall breakdown','unobserved unit peaks','active perEG FADD context-switch qualification','OPU logical MAC accounting without authoritative COM geometry'],source_image_attested=False,timing_accuracy_certified=False,CPU_COMplus1_bridge_qualified=False,input_sha256={str(p):sha(p)for p in[Path(__file__).resolve(),Path(events),Path(features),Path(console),summary_path,Path(elf),Path(rom),Path(nm)]})
 if region_map:result['input_sha256'][str(Path(region_map))]=sha(region_map)
 output.mkdir();(output/'summary.json').write_text(json.dumps(result,indent=2)+'\n');trace.sort(key=lambda x:x.get('ts',-1));(output/'trace.json').write_text(json.dumps(dict(traceEvents=trace,displayTimeUnit='ns'))+'\n');return result
