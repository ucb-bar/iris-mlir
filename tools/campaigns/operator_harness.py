"""Generate guarded bare-metal inputs and independently check every output word.

Startup, console, allocator and linker sources are explicit execution-plan inputs.
This module does not launch hardware, select a simulator or qualify its timing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

from operator_reference import flat_wrapper, inputs, reference
from operator_suite import checked_case


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fnv(data):
    h = 0xCBF29CE484222325
    for byte in data:
        h = ((h ^ byte) * 0x100000001B3) & ((1 << 64) - 1)
    return h


def strict_loads(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON field: " + key)
            result[key] = value
        return result

    def constant(value):
        raise ValueError("nonfinite JSON value: " + value)

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def generate(row, target, output):
    """Emit one exact case; callers bind its generated files in their run plan."""
    if target.get("compute_harts") != [0]:
        raise ValueError("this single-worker driver requires compute_harts=[0]")
    c, facts = checked_case(row["spec"])
    if c != row["case"] or facts != row["facts"]:
        raise ValueError("derived source manifest does not match its original recipe")
    src = Path(row["source"])
    if sha(src) != row["source_sha256"]:
        raise ValueError("authored source bytes changed")
    vlen = target["vlen_bits"]
    if type(vlen) is not int or vlen < 128 or vlen & (vlen - 1):
        raise ValueError("explicit VLEN power of two required")
    arrays = inputs(facts, c["input_seed"])
    gold = reference(c, arrays).astype("<i4")
    sizes = [a.size for a in arrays]
    words = gold.size
    out = Path(output)
    out.mkdir(parents=True, exist_ok=False)
    golden = out / "expected.bin"
    golden.write_bytes(gold.tobytes(order="C"))
    source = out / "main.c"
    expected = ",".join(
        f"UINT32_C(0x{int(x):08x})" for x in gold.reshape(-1).view("<u4")
    )
    text = r"""#include <stdint.h>
#include <stddef.h>
#include "htif.h"
@WRAPPER@
static const uint32_t expected[@WORDS@]={@EXPECTED@};
static struct {unsigned char pre[64];int8_t data[@A_SIZE@];unsigned char post[64];} a;
static struct {unsigned char pre[64];int8_t data[@B_SIZE@];unsigned char post[64];} b;
static struct {unsigned char pre[64];uint32_t data[@WORDS@];unsigned char post[64];} o;
static void u64(uint64_t x){char buf[24];unsigned n=0;do{buf[n++]=(char)('0'+x%10);x/=10;}while(x);while(n)htif_putc(buf[--n]);}
static void field(const char*s,uint64_t n){htif_puts(s);u64(n);}
static uint64_t hash(const unsigned char*p,size_t n){uint64_t h=UINT64_C(0xcbf29ce484222325);for(size_t i=0;i<n;++i)h=(h^p[i])*UINT64_C(0x100000001b3);return h;}
static inline uint64_t cycles(void){uint64_t x;__asm__ volatile("csrr %0,mcycle":"=r"(x)::"memory");return x;}
static inline uint64_t retired(void){uint64_t x;__asm__ volatile("csrr %0,minstret":"=r"(x)::"memory");return x;}
int main(unsigned long hart){
 if(hart)for(;;)__asm__ volatile("wfi");
 console_init();uint64_t vb;__asm__ volatile("csrr %0,vlenb":"=r"(vb));
 if(vb!=@VLEN_BYTES@){htif_puts("FAIL vlenb\n");htif_exit(1);}
 for(size_t i=0;i<64;++i)a.pre[i]=a.post[i]=b.pre[i]=b.post[i]=o.pre[i]=o.post[i]=0x5a;
 for(size_t i=0;i<@A_SIZE@;++i)a.data[i]=(int8_t)((int)((i*29+UINT64_C(@SEED@))&255)-128);
 for(size_t i=0;i<@B_SIZE@;++i)b.data[i]=(int8_t)((int)((i*37+UINT64_C(@SEED@)+11)&255)-128);
 for(size_t i=0;i<@WORDS@;++i)o.data[i]=expected[i]^UINT32_C(0xdeadbeef);
 /* Initialization/pre-fence and all UART/checking are outside the measured ROI.
    Completion fence is inside; this does not claim a device-specific drain. */
 __asm__ volatile("fence rw,rw":::"memory");uint64_t c0=cycles(),i0=retired();
 run_operator(a.data,b.data,o.data);
 __asm__ volatile("fence rw,rw":::"memory");uint64_t i1=retired(),c1=cycles();
 unsigned bad=0,guards=0,changed=0;
 for(size_t i=0;i<@WORDS@;++i)bad+=o.data[i]!=expected[i];
 for(size_t i=0;i<64;++i)guards+=(a.pre[i]!=0x5a)+(a.post[i]!=0x5a)+(b.pre[i]!=0x5a)+(b.post[i]!=0x5a)+(o.pre[i]!=0x5a)+(o.post[i]!=0x5a);
 for(size_t i=0;i<@A_SIZE@;++i)changed+=a.data[i]!=(int8_t)((int)((i*29+UINT64_C(@SEED@))&255)-128);
 for(size_t i=0;i<@B_SIZE@;++i)changed+=b.data[i]!=(int8_t)((int)((i*37+UINT64_C(@SEED@)+11)&255)-128);
 uint64_t checksum=hash((const unsigned char*)o.data,sizeof(o.data));
 htif_puts("IRIS_REGION {\"case_id\":\"@CASE@\",\"region\":\"operator\",\"hart\":0");
 field(",\"start_cycle\":",c0);field(",\"end_cycle\":",c1);field(",\"start_instret\":",i0);field(",\"end_instret\":",i1);field(",\"instret_delta\":",i1-i0);
 field(",\"checksum\":",checksum);field(",\"expected_checksum\":",UINT64_C(@CHECKSUM@));field(",\"compared_words\":",@WORDS@);field(",\"mismatch_words\":",bad);field(",\"guard_errors\":",guards);field(",\"input_errors\":",changed);field(",\"vlen_bytes\":",vb);
 htif_puts(",\"pass\":");htif_puts(bad||guards||changed?"false":"true");htif_puts("}\n");
 htif_puts("IRIS_OUTPUT_HEX {\"case_id\":\"@CASE@\",\"words\":@WORDS@,\"data\":\"");
 const char *digits="0123456789abcdef";const unsigned char*p=(const unsigned char*)o.data;
 for(size_t i=0;i<sizeof(o.data);++i){htif_putc(digits[p[i]>>4]);htif_putc(digits[p[i]&15]);}
 htif_puts("\"}\nIRIS_BATCH_DONE {\"cases\":1,\"regions\":1,\"failed\":");u64(!!(bad||guards||changed));htif_puts("}\n");
 htif_exit(!!(bad||guards||changed));
}
"""
    replacements = {
        "WRAPPER": flat_wrapper(facts),
        "WORDS": str(words),
        "EXPECTED": expected,
        "A_SIZE": str(sizes[0]),
        "B_SIZE": str(sizes[1]),
        "VLEN_BYTES": str(vlen // 8),
        "SEED": str(c["input_seed"]),
        "CASE": c["id"],
        "CHECKSUM": str(fnv(golden.read_bytes())),
    }
    for name, value in replacements.items():
        text = text.replace("@" + name + "@", value)
    source.write_text(text)
    manifest = {
        "schema": "iris.operator_harness.v1",
        "case_id": c["id"],
        "authored_source": {"path": str(src), "sha256": sha(src)},
        "source": {"path": str(source.resolve()), "sha256": sha(source)},
        "golden": {"path": str(golden.resolve()), "sha256": sha(golden)},
        "expected_checksum": fnv(golden.read_bytes()),
        "output_words": words,
        "vlen_bytes": vlen // 8,
        "hart": 0,
        "scope": facts["scope"],
        "functional_pass": False,
        "hardware_contacted": False,
        "timing_qualified": False,
    }
    path = out / "harness.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    # The generated wrapper and its transitive inputs are bound by the execution plan.
    checker = out / "check.py"
    checker.write_text(
        "import sys\nfrom pathlib import Path\n"
        f"sys.path.insert(0,{str(Path(__file__).resolve().parent)!r})\n"
        "from operator_harness import check_main\n"
        f"check_main(Path({str(path.resolve())!r}),{sha(path)!r})\n"
    )
    manifest["checker"] = {"path": str(checker.resolve()), "sha256": sha(checker)}
    return manifest


def check_console(console, manifest, semantics):
    if not isinstance(semantics, str) or not semantics:
        raise ValueError("counter semantics must be explicit")
    for name in ("authored_source", "source", "golden"):
        bound = manifest[name]
        if sha(bound["path"]) != bound["sha256"]:
            raise ValueError("bound input changed: " + name)
    golden = Path(manifest["golden"]["path"]).read_bytes()
    if (
        len(golden) != 4 * manifest["output_words"]
        or fnv(golden) != manifest["expected_checksum"]
    ):
        raise ValueError("golden metadata disagrees with its exact bytes")
    lines = [line.strip() for line in console.splitlines()]
    if any(line.startswith(("FAIL", "FATAL", "TRAP", "IRIS_FATAL")) for line in lines):
        raise ValueError("guest reports a failure")

    def one(prefix):
        rows = [
            strict_loads(line[len(prefix) :])
            for line in lines
            if line.startswith(prefix)
        ]
        if len(rows) != 1 or not isinstance(rows[0], dict):
            raise ValueError("exactly one complete record required: " + prefix)
        return rows[0]

    row = one("IRIS_REGION ")
    actual = one("IRIS_OUTPUT_HEX ")
    done = one("IRIS_BATCH_DONE ")
    for key in (
        "hart",
        "start_cycle",
        "end_cycle",
        "start_instret",
        "end_instret",
        "instret_delta",
        "checksum",
        "expected_checksum",
        "compared_words",
        "mismatch_words",
        "guard_errors",
        "input_errors",
        "vlen_bytes",
    ):
        if type(row.get(key)) is not int or not 0 <= row[key] < (1 << 64):
            raise ValueError("unsigned exact counter/metadata required: " + key)
    if (
        row.get("case_id") != manifest["case_id"]
        or row.get("region") != "operator"
        or row["hart"] != manifest["hart"]
    ):
        raise ValueError("region identity disagrees with the admitted case")
    if row.get("pass") is not True or any(
        row[k] for k in ("mismatch_words", "guard_errors", "input_errors")
    ):
        raise ValueError("complete-output/input/guard checks failed")
    if (
        row["compared_words"] != manifest["output_words"]
        or row["vlen_bytes"] != manifest["vlen_bytes"]
    ):
        raise ValueError("output coverage or actual VLEN disagrees")
    if (
        row["checksum"] != manifest["expected_checksum"]
        or row["expected_checksum"] != row["checksum"]
    ):
        raise ValueError("independent checksum mismatch")
    if (
        row["end_cycle"] <= row["start_cycle"]
        or row["end_instret"] < row["start_instret"]
        or row["end_instret"] - row["start_instret"] != row["instret_delta"]
    ):
        raise ValueError("counter endpoints reversed, wrapped or inconsistent")
    if (
        actual.get("case_id") != manifest["case_id"]
        or type(actual.get("words")) is not int
        or actual["words"] != manifest["output_words"]
    ):
        raise ValueError("raw output identity or complete coverage mismatch")
    encoded = actual.get("data")
    if (
        not isinstance(encoded, str)
        or len(encoded) != len(golden) * 2
        or not re.fullmatch("[0-9a-f]+", encoded)
        or bytes.fromhex(encoded) != golden
    ):
        raise ValueError("independent raw output comparison failed")
    if (
        set(done) != {"cases", "regions", "failed"}
        or any(type(x) is not int for x in done.values())
        or done != {"cases": 1, "regions": 1, "failed": 0}
    ):
        raise ValueError("batch did not finish exactly")
    if [line for line in lines if line.startswith("EXIT ")] != ["EXIT 0"]:
        raise ValueError("exact successful guest exit required")
    return {
        "pass": True,
        "schema": "iris.operator_postgate.v1",
        "counter_semantics": semantics,
        "regions": [row],
        "raw_output_sha256": hashlib.sha256(golden).hexdigest(),
        "output_words": manifest["output_words"],
        "physical_utilization": None,
        "timing_certification": False,
    }


def check_main(manifest_path, expected_sha256):
    p = argparse.ArgumentParser()
    p.add_argument("--console", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--counter-semantics", required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    report = {"pass": False}
    try:
        if sha(manifest_path) != expected_sha256:
            raise ValueError("harness manifest changed")
        report = check_console(
            args.console.read_text(),
            strict_loads(manifest_path.read_text()),
            args.counter_semantics,
        )
    except (ValueError, TypeError, KeyError, OSError) as error:
        report["error"] = f"{type(error).__name__}: {error}"
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    if not report["pass"]:
        raise SystemExit(1)
