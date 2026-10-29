#!/usr/bin/env python3
"""Check the installed tools; run only small toolchain fixtures, never a DUT proof."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent


def run(argv, cwd, log, expected=0):
    command = [str(x) for x in argv]
    command[0] = shutil.which(command[0]) or command[0]

    def invoke(command):
        # Mill forks a worker JVM even with -i. Kill the whole check's process
        # group on timeout so an orphan cannot retain pipes or dependency locks.
        process = subprocess.Popen(command, cwd=cwd, text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            output, _ = process.communicate(timeout=120)
        except subprocess.TimeoutExpired as error:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            output, _ = process.communicate()
            log.write_text(output)
            raise RuntimeError(f"{' '.join(command)} timed out after 120 seconds; see {log}") from error
        return subprocess.CompletedProcess(command, process.returncode, output)

    try:
        result = invoke(command)
    except OSError as error:
        if error.errno != 8:
            raise
        result = invoke(["/bin/sh", *command])
    log.write_text(result.stdout)
    if result.returncode != expected:
        raise RuntimeError(f"{' '.join(command)} exited {result.returncode}; see {log}")
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--system-only", action="store_true",
                        help="check host Yosys/Z3 only; does not certify the complete environment")
    parser.add_argument("--mill-build", action="store_true",
                        help="also compile a Mill build and elaborate Chisel 6.7.0; may download dependencies")
    args = parser.parse_args()
    work = HERE / "work" / ("host-check" if args.system_only else "check")
    work.mkdir(parents=True, exist_ok=True)
    report = {"complete": False, "mill_build_verified": False, "tools": {}, "checks": []}
    try:
        tools = [("yosys", ["-V"]), ("z3", ["--version"])]
        if not args.system_only:
            tools += [("sby", ["--version"]), ("mill", ["-i", "--version"]),
                      ("firtool", ["--version"]), ("java", ["-version"])]
        for name, flags in tools:
            executable = shutil.which(name)
            if not executable:
                raise RuntimeError(f"required executable missing: {name}")
            output = run([executable, *flags], work, work / f"{name}-version.log")
            report["tools"][name] = {"path": executable, "version": output}
            print(f"{name}: {output.splitlines()[0]}", flush=True)
        if not args.system_only:
            if not re.search(r"Mill Build Tool version\s+0\.12\.3\b",
                             report["tools"]["mill"]["version"]):
                raise RuntimeError("Mill must be 0.12.3")
            if "firtool-1.62.1" not in report["tools"]["firtool"]["version"]:
                raise RuntimeError("firtool must be 1.62.1")
            if not re.search(r'version "17[.\"]', report["tools"]["java"]["version"]):
                raise RuntimeError("the Nix environment must select Java 17")

            if args.mill_build:
                mill_work = HERE / "work" / "mill-check"
                mill_work.mkdir(parents=True, exist_ok=True)
                (mill_work / "build.sc").write_text(
                    '''import mill._, scalalib._
object smoke extends ScalaModule {
  def scalaVersion = "2.13.15"
  def ivyDeps = Agg(ivy"org.chipsalliance::chisel:6.7.0")
  def scalacPluginIvyDeps = Agg(ivy"org.chipsalliance:::chisel-plugin:6.7.0")
  def ping = T { "mill-ready" }
}
''')
                output = run(["mill", "-i", "show", "smoke.ping"], mill_work,
                             mill_work / "build.log")
                if '"mill-ready"' not in output:
                    raise RuntimeError("Mill target did not return the expected value")
                report["mill_build_verified"] = True
                report["checks"].append("Mill compiled a build definition and evaluated smoke.ping")
                source = mill_work / "smoke" / "src" / "Smoke.scala"
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text('''import chisel3._
class Smoke extends Module {
  val enable = IO(Input(Bool()))
  val value = IO(Output(UInt(4.W)))
  val count = RegInit(0.U(4.W))
  when(enable) { count := count + 1.U }
  value := count
}
object SmokeEmit {
  def main(args: Array[String]): Unit = {
    val fir = circt.stage.ChiselStage.emitCHIRRTL(new Smoke)
    java.nio.file.Files.writeString(java.nio.file.Paths.get(args(0)), fir)
  }
}
''')
                run(["mill", "-i", "smoke.run", str(work / "Smoke.fir")], mill_work,
                    mill_work / "chisel-elaboration.log")
                report["checks"].append("Scala 2.13.15 / Chisel 6.7.0 compiled and emitted CHIRRTL")

            if not args.mill_build:
                (work / "Smoke.fir").write_text("""circuit Smoke :
  module Smoke :
    input clock : Clock
    input reset : UInt<1>
    input enable : UInt<1>
    output value : UInt<4>
    reg count : UInt<4>, clock with :
      reset => (reset, UInt<4>(0))
    count <= mux(enable, add(count, UInt<4>(1)), count)
    value <= count
""")
            run(["firtool", "Smoke.fir", "--preserve-values=named", "--disable-all-randomization",
                 "--lowering-options=disallowPackedArrays,disallowLocalVariables", "-o", "Smoke.sv"],
                work, work / "firtool-lowering.log")
            run(["yosys", "-Q", "-T", "-p", "read_verilog -sv Smoke.sv; prep -top Smoke; check"],
                work, work / "firtool-yosys.log")
            report["checks"].append("FIRRTL -> firtool -> SystemVerilog -> Yosys")

        (work / "counter.sv").write_text("""module counter(input clock, reset, enable);
  reg [3:0] count = 0;
  always @(posedge clock) begin
    if (reset) count <= 0;
    else if (enable) count <= count + 4'd1;
    if (!$initstate) begin
      if ($past(reset)) assert(count == 0);
      else if ($past(enable)) assert(count == $past(count) + 4'd1);
      else assert(count == $past(count));
    end
`ifdef NEGATIVE
    assert(count == 0);
`endif
    cover(count == 3);
  end
endmodule
""")
        if args.system_only:
            run(["yosys", "-Q", "-T", "-p",
                 "read_verilog -formal -sv counter.sv; prep -top counter; async2sync; chformal -lower; write_smt2 -wires counter.smt2"],
                work, work / "yosys-smt.log")
            run(["yosys-smtbmc", "-s", "z3", "-t", "8", "counter.smt2"],
                work, work / "bmc.log")
            run(["yosys-smtbmc", "-s", "z3", "-i", "-t", "8", "counter.smt2"],
                work, work / "induction.log")
            run(["yosys-smtbmc", "-s", "z3", "-c", "-t", "8", "counter.smt2"],
                work, work / "cover.log")
            run(["yosys", "-Q", "-T", "-p",
                 "read_verilog -formal -sv -DNEGATIVE counter.sv; prep -top counter; async2sync; chformal -lower; write_smt2 -wires negative.smt2"],
                work, work / "negative-yosys.log")
            run(["yosys-smtbmc", "-s", "z3", "-t", "8", "negative.smt2"],
                work, work / "negative.log", expected=1)
            report["checks"].append("host BMC, induction, cover, expected counterexample")
        else:
            (work / "smoke.sby").write_text("""[tasks]
prove
cover
negative

[options]
prove: mode prove
cover: mode cover
negative: mode bmc
negative: expect fail
depth 8

[engines]
smtbmc z3

[script]
negative: read -formal -sv -DNEGATIVE counter.sv
~negative: read -formal -sv counter.sv
prep -top counter

[files]
counter.sv
""")
            for task, status in (("prove", "PASS"), ("cover", "PASS"), ("negative", "FAIL")):
                run(["sby", "-f", "-d", f"sby_{task}", "smoke.sby", task],
                    work, work / f"sby-{task}.log")
                actual = (work / f"sby_{task}" / "status").read_text().strip().split()[0]
                if actual != status:
                    raise RuntimeError(f"{task}: expected {status}, got {actual}")
            report["checks"].append("SBY/Z3 unbounded proof, cover, expected counterexample")
            report["complete"] = True
        print("Toolchain fixtures verified." if report["complete"] else
              "Host Yosys/Z3 verified; full environment still requires Mill, firtool, SBY and Java 17.")
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        report["error"] = str(error)
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    finally:
        (work / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
