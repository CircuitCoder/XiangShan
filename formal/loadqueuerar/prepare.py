#!/usr/bin/env python3
"""Prepare LoadQueueRAR artifacts and EQY inputs; never checkout or run EQY.

Run from the repository root. Emit each side while its revision is checked out.
The existing build.sc lec module and formal/sbuffer/StandaloneMain.scala are reused.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
HERE = Path("formal/loadqueuerar")
ART = HERE / "artifacts/DefaultConfig/LoadQueueRAR"
WORK = HERE / "work/eqy"
TSO = "io_csrCtrl_tso_enable"
SOURCE = "src/main/scala/xiangshan/mem/lsqueue/LoadQueueRAR.scala"


def run(*args):
    print(" ".join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), check=True)


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def analyze():
    WORK.mkdir(parents=True, exist_ok=True)
    sources = {side: subprocess.check_output(["git", "show", f"lec-{side}:{SOURCE}"], text=True)
               for side in ("base", "tip")}
    specialized = sources["tip"].replace("    val csrCtrl = Flipped(new CustomCSRCtrlIO)", "")
    specialized = specialized.replace(
        "(io.csrCtrl.tso_enable || paddrModule.io.releaseViolationMmask(w)(i))",
        "paddrModule.io.releaseViolationMmask(w)(i)")

    def normalize(text):
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        text = re.sub(r"//[^\n]*", "", text)
        return re.sub(r"\s+", "", text)

    if normalize(sources["base"]) != normalize(specialized):
        raise ValueError("additional source changes found; review before preparing equivalence")
    (WORK / "module.diff").write_text(git("diff", "lec-base", "lec-tip", "--", SOURCE) + "\n")
    print("After removing the added CSR port and simplifying (false || CAM),")
    print("the two module sources match modulo comments and whitespace.")


def emit(side):
    revision = git("rev-parse", f"lec-{side}")
    if git("rev-parse", "HEAD") != revision:
        raise ValueError(f"checkout lec-{side} before emitting {side}")
    expected = subprocess.check_output(["git", "show", f"{revision}:{SOURCE}"])
    if Path(SOURCE).read_bytes() != expected:
        raise ValueError("LoadQueueRAR.scala differs from the selected revision")
    target = ART / side
    target.mkdir(parents=True, exist_ok=True)
    run("mill", "-i", "lec.runMain", "lec.StandaloneMain", "LoadQueueRAR",
        "--config", "DefaultConfig", "--target", "chirrtl", "--target-dir", target)
    (target / "revision.json").write_text(json.dumps({
        "revision": revision, "module_sha256": digest(Path(SOURCE)),
        "firrtl_sha256": digest(target / "LoadQueueRAR.fir"),
        "submodules": git("submodule", "status", "--recursive"),
        "working_changes": git("diff", "--stat"),
    }, indent=2) + "\n")


def lower(side):
    WORK.mkdir(parents=True, exist_ok=True)
    target = ART / side
    rtl = target / "rtl"
    rtl.mkdir(parents=True, exist_ok=True)
    run("firtool", target / "LoadQueueRAR.fir", "--disable-annotation-unknown",
        "--preserve-values=named", "--disable-all-randomization",
        "--lowering-options=disallowPackedArrays,disallowLocalVariables",
        "--split-verilog", "-o", rtl)
    sources = sorted([*rtl.glob("*.sv"), *rtl.glob("*.v")])
    if not sources:
        raise ValueError("firtool emitted no Verilog sources")
    script = WORK / f"{side}-import.ys"
    script.write_text(f"""read_verilog -sv -DSYNTHESIS {" ".join(map(str, sources))}
hierarchy -check -top LoadQueueRAR
proc
flatten
opt_clean
select -assert-none A:blackbox
rename -top LoadQueueRAR_{side}
check -assert
write_rtlil {ART}/{side}.il
write_json {ART}/{side}.json
""")
    run("yosys", "-s", script)


def signature(port):
    return tuple(port.get(key, default) for key, default in
                 [("direction", None), ("signed", 0), ("offset", 0), ("upto", 0)]) + (len(port["bits"]),)


def decl(kind, name, port):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", name):
        raise ValueError(f"unsupported port name: {name}")
    if port.get("offset", 0) or port.get("upto", 0):
        raise ValueError(f"review bit indexing: {name}")
    signed = " signed" if port.get("signed", 0) else ""
    width = len(port["bits"])
    size = f" [{width - 1}:0]" if width > 1 else ""
    return f"{kind}{signed}{size} {name}"


def eqy():
    WORK.mkdir(parents=True, exist_ok=True)
    modules = {side: json.loads((ART / f"{side}.json").read_text())["modules"][f"LoadQueueRAR_{side}"]
               for side in ("base", "tip")}
    base, tip = (modules[side]["ports"] for side in ("base", "tip"))
    if base.keys() - tip.keys():
        raise ValueError("base ports missing from tip")
    for name, port in base.items():
        if signature(port) != signature(tip[name]):
            raise ValueError(f"interface mismatch: {name}")
    extra = tip.keys() - base.keys()
    if TSO not in extra or any(not n.startswith("io_csrCtrl_") or tip[n]["direction"] != "input" for n in extra):
        raise ValueError("expected only added CSR-control inputs, including TSO")
    if len(tip[TSO]["bits"]) != 1:
        raise ValueError("TSO must have width 1")
    # All added CSR fields except TSO must be unused. Keep their full interface
    # in the wrappers anyway, so they remain arbitrary rather than tied off.
    cell_inputs = {bit for cell in modules["tip"]["cells"].values()
                   for port, bits in cell["connections"].items()
                   if cell["port_directions"][port] != "output" for bit in bits if isinstance(bit, int)}
    output_bits = {bit for p in tip.values() if p["direction"] == "output" for bit in p["bits"]}
    for name in extra - {TSO}:
        if set(tip[name]["bits"]) & (cell_inputs | output_bits):
            raise ValueError(f"unexpected use of added CSR input {name}; review its semantics")
    inputs = {n: p for n, p in tip.items() if p["direction"] == "input" and n != TSO}
    outputs = {n: p for n, p in base.items() if p["direction"] == "output"}
    # Compare all lowered output bits, even invalid response payloads and early
    # performance values. This is stronger than a valid-qualified contract.
    with (ART / "interface.tsv").open("w") as stream:
        writer = csv.writer(stream, delimiter="\t")
        writer.writerow(["port", "direction", "width", "status"])
        for n, p in sorted(tip.items()):
            writer.writerow([n, p["direction"], len(p["bits"]), "added" if n in extra else "shared"])
    for side in ("base", "tip"):
        lines = ["// Generated by formal/loadqueuerar/prepare.py",
                 "module LoadQueueRAR_observed (",
                 ",\n".join("  " + decl("input wire", n, p) for n, p in inputs.items()) + ",",
                 ",\n".join("  " + decl("output wire", n, p) for n, p in outputs.items()),
                 ");", "  reg boot = 1'b1;",
                 "  always @(posedge clock) boot <= 1'b0;",
                 "  wire dut_reset = reset || boot;"]
        lines += ["  " + decl("wire", "raw_" + n, p) + ";" for n, p in outputs.items()]
        lines.append(f"  LoadQueueRAR_{side} dut (")
        connections = []
        for n, p in modules[side]["ports"].items():
            signal = ("1'b0" if n == TSO else "dut_reset" if n == "reset" else
                      "raw_" + n if p["direction"] == "output" else n)
            connections.append(f"    .{n}({signal})")
        lines += [",\n".join(connections), "  );"]
        lines += [f"  assign {n} = !boot && !reset ? raw_{n} : '0;" for n in outputs]
        lines += ["endmodule", ""]
        (WORK / f"{side}_observed.sv").write_text("\n".join(lines))
        (WORK / f"{side}.ys").write_text(f"""read_rtlil {ART}/{side}.il
read_verilog -formal -sv {WORK}/{side}_observed.sv
prep -top LoadQueueRAR_observed -flatten
opt -full
check -assert
write_rtlil {WORK}/{side}_observed.il
""")
    (WORK / "loadqueuerar.eqy").write_text(f"""[gold]
read_rtlil {WORK}/base_observed.il
prep -top LoadQueueRAR_observed

[gate]
read_rtlil {WORK}/tip_observed.il
prep -top LoadQueueRAR_observed

[match *]
nodefault
gold-match clock
gold-match reset
gold-match io_*
gold-match dut.allocated_*
gold-match dut.released_*
gold-match dut.uop_*
gold-match dut.ldLdViolationMask_*
gold-match dut.freeList.*
gold-match dut.paddrModule.*

[strategy local_sat]
use sat
depth 2
timeout 15

[strategy induction]
use sby
engine smtbmc z3
depth 4
timeout 60

[strategy pdr]
use sby
engine abc pdr
timeout 90
""")
    (WORK / "inputs.json").write_text(json.dumps({str(p): digest(p) for p in
        [ART / "base.il", ART / "tip.il", ART / "base.json", ART / "tip.json",
         Path(__file__), WORK / "base_observed.sv", WORK / "tip_observed.sv",
         WORK / "loadqueuerar.eqy"]}, indent=2) + "\n")
    print(f"Audited {len(base)} shared ports and {len(extra)} added CSR inputs.")
    print(f"Wrote observation wrappers, Yosys scripts, and {WORK}/loadqueuerar.eqy")


def main():
    if Path.cwd() != ROOT:
        raise ValueError("run from the repository root")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="stage", required=True)
    for stage in ("emit", "lower"):
        commands.add_parser(stage).add_argument("side", choices=("base", "tip"))
    commands.add_parser("analyze")
    commands.add_parser("eqy")
    args = parser.parse_args()
    if args.stage == "emit":
        emit(args.side)
    elif args.stage == "lower":
        lower(args.side)
    elif args.stage == "analyze":
        analyze()
    else:
        eqy()


if __name__ == "__main__":
    main()
