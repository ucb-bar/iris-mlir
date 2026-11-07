#!/usr/bin/env python3
"""Explicit optional35a profile selector; preserves the existing generic runner."""
import argparse,json,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
 p=argparse.ArgumentParser(__doc__);p.add_argument('--package',type=Path,required=True);p.add_argument('--profile',choices=['source_compute','source_os'],required=True);p.add_argument('--elf',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--timeout',type=float,default=600);p.add_argument('--seed',type=int,default=0);a=p.parse_args();manifest=a.package.resolve()/a.profile/'manifest.json'
 m=json.loads(manifest.read_text())
 if m.get('profile')!=a.profile or m.get('package_kind')!='source-VADD35a-finite-v1':p.error('Package/profile mismatch')
 return subprocess.call([sys.executable,'-m','iris_model.empirical_run','--model',str(manifest),'--elf',str(a.elf.resolve()),'--output',str(a.output.resolve()),'--timeout',str(a.timeout),'--seed',str(a.seed)],cwd=ROOT)
if __name__=='__main__':raise SystemExit(main())
