#!/usr/bin/env python3
"""Build the isolated bounded empirical fast successor; never touch a frozen SDK."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import time
ROOT=Path(__file__).resolve().parents[1]
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def main():
    p=argparse.ArgumentParser(__doc__);p.add_argument('--source',type=Path,default=ROOT/'pipeline/riscv-isa-sim-empirical-fast');p.add_argument('--build',type=Path,default=ROOT/'build/spike-empirical-fast-v1');p.add_argument('--jobs',type=int,default=24);a=p.parse_args();source=a.source.resolve();build=a.build.resolve();build.mkdir(exist_ok=True,parents=True)
    if source==ROOT/'third_party/riscv-isa-sim' or build in (ROOT/'spike/build',ROOT/'spike/bin'):raise ValueError('new isolated paths required')
    parent=ROOT/'build/saturn-native-frozen-16972504/manifest.json'
    if sha(parent)!='e13b31104cfcb927ba2d81bf0d99a2352c343a0a775088edaaff9f6543521c88':raise ValueError('frozen169 parent identity changed')
    if subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()!='72958551566a30f19444bd7e354552824e4d0a98':raise ValueError('wrong Spike base')
    shuttle=ROOT/'build/frontend-rtl-peer-component-v2';front=ROOT/'build/rocket-source-frontend-component-v3/obj';arb=ROOT/'build/rocket-dcache-arbiter-component-v3';fabric=ROOT/'build/memory-fabric-coupled-native-v4/sdk';scoped=ROOT/'build/memory-fabric-context-sdk-v2';runtime=Path('/scratch/eygao/iris-cy/.conda-env/share/verilator/include');context=ROOT/'build/frontend-owned-contexts-v2'
    libs=[shuttle/'obj/VShuttleFrontend__ALL.a',front/'VIrisRocketFrontendPeer__ALL.a',arb/'obj/VHellaCacheArbiter_1__ALL.a',fabric/'libiris_memory_fabric.a',scoped/'cache/sdk/libiris_cache_hierarchy.a',scoped/'noc/sdk/libiris_noc.a',scoped/'adapters/sdk/libiris_memory_adapters.a']
    system=ROOT/'build/serial-memory-system-native-v2'; system_build=json.loads((system/'build.json').read_text()); lp=ROOT/'build/serial-memory-context-sdk-v2/fased/sdk'
    libs += [system/'sdk/libiris_serial_memory_system.a',lp/'libiris_fased_service.a',*[Path(x)for x in system_build['required_static_libraries_in_link_order']]]
    saturn=ROOT/'build/saturn-timing-live-bridge-v4/sdk';saturn_numeric=ROOT/'build/saturn-timing-ownership-sdk-v7/sdk'
    if sha(saturn/'libiris_saturn_live_bridge.a')!='047ae13dab928557175002c9a4622ccadae163ec1f6d150eee4e399367d258e6':raise ValueError('qualified live bridge changed')
    libs += [saturn/'libiris_saturn_live_bridge.a',saturn_numeric/'libiris_saturn_timing.a']
    libs=list(dict.fromkeys(libs))
    for path,digest in system_build['required_child_library_sha256'].items():
        if sha(path)!=digest:raise ValueError('fixed child library changed')
    receipts=[parent,ROOT/'build/saturn-timing-ownership-component-v1-final/report.json',ROOT/'build/saturn-timing-live-bridge-inactive-v1/report.json',ROOT/'build/serial-memory-system-component-v1/report.json',ROOT/'build/serial-memory-system-component-v1-verify/report.json',ROOT/'build/rocket-serial-peer-component-v1/results.json',ROOT/'build/rocket-frontend-fabric-component-v6/results.json',ROOT/'build/memory-fabric-component-v1/report.json',ROOT/'build/memory-fabric-component-v1-verify/report.json',arb/'results.json']
    for path in receipts[1:]:
        r=json.loads(path.read_text())
        if not(r.get('pass')is True or r.get('status','').startswith('passed')):raise ValueError('component proof missing: '+str(path))
    include=[saturn,system/'sdk',lp,*[Path(x)for x in system_build['required_include_directories']],shuttle,shuttle/'obj',front,arb,arb/'obj',fabric,context,runtime,runtime/'vltstd']
    compiler=Path(shutil.which('g++')).resolve();env=dict(os.environ,PATH='/scratch/eygao/iris-cy/.conda-env/bin:'+os.environ['PATH'],CC=shutil.which('gcc'),CXX=str(compiler));flags='-O2 -g0 -std=c++20 '+' '.join('-I'+str(x)for x in include)
    # LIBS is emitted after libriscv.a by Spike's link rule. Grouped ordinary
    # archives select exactly one runtime and resolve cross-peer references.
    libflags='-Wl,--start-group '+' '.join(map(str,libs))+' -Wl,--end-group -pthread -lstdc++ -lm'
    commands=[];started=time.perf_counter()
    phases=[]if(build/'Makefile').exists()else[('configure',[str(source/'configure'),'--with-boost=no','--with-boost-asio=no','--with-boost-regex=no','CXXFLAGS='+flags,'LIBS='+libflags])]
    phases.append(('native',['make','-j'+str(a.jobs),'spike']))
    for name,argv in phases:
        with(build/(name+'.log')).open('w')as f:r=subprocess.run(argv,cwd=build,env=env,stdout=f,stderr=subprocess.STDOUT)
        commands.append(dict(name=name,argv=argv,exit_status=r.returncode))
        if r.returncode:raise RuntimeError(name+' failed: '+str(build/(name+'.log')))
    sdk=build/'sdk';sdk.mkdir(exist_ok=True)
    for name in ('riscv','fesvr','fdt'):
        path=sdk/name
        if not path.exists():path.symlink_to(source/name,target_is_directory=True)
    dep=build/'uart-plugin.d';plugin=build/'libsifive_uart.so';cmd=[str(compiler),'-std=c++20','-O2','-I'+str(sdk),'-I'+str(build),'-iquote',str(source/'riscv'),'-I'+str(ROOT/'board'),'-I'+str(ROOT/'board/uart-device'),'-fPIC','-shared','-MMD','-MF',str(dep),str(ROOT/'pipeline/uart-plugin-abi4.cc'),'-o',str(plugin)]
    r=subprocess.run(cmd,env=env,text=True,capture_output=True);(build/'uart.build.log').write_text(r.stdout+r.stderr)
    if r.returncode:raise RuntimeError('matching UART SDK build failed')
    commands.append(dict(name='clocked-uart-ABI4',argv=cmd,exit_status=r.returncode))
    inputs=[Path(__file__).resolve(),compiler,*libs,*receipts,*[Path(w).resolve()for w in shlex.split(dep.read_text().replace('\\\n',' ').split(':',1)[1])]]
    r=dict(schema='iris.empirical.isolated_fast_build.v1',status='native_and_matched_clocked_uart_built',source=str(source),build=str(build),binary_sha256=sha(build/'spike'),uart_sha256=sha(plugin),base_commit='72958551566a30f19444bd7e354552824e4d0a98',source_overlay_sha256=hashlib.sha256(subprocess.check_output(['git','-C',str(source),'diff','--binary','HEAD'])).hexdigest(),input_sha256={str(x):sha(x)for x in inputs},commands=commands,host_seconds=time.perf_counter()-started,native_device_abi=4,calibrated=False,rtl_certified=False,held_out_2pct=False,full_soc_timing=False)
    (build/'pipeline-build.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps({k:r[k]for k in ('status','binary_sha256','uart_sha256','host_seconds')}))
if __name__=='__main__':main()
