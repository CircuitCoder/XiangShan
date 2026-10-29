# Sbuffer sequential equivalence from a clean checkout

## Overview

Prove that `lec-base` and `lec-tip` Sbuffer produce the same defined observations
for every shared input sequence with TSO permanently disabled. Use DefaultConfig,
the recorded patch resetting `tsoActive` to false, and three common startup
reset edges. Store inputs remain arbitrary during reset; later resets remain
unrestricted. The user reported that the complete check passed with this setup.

First elaborate each revision to FIRRTL, then lower to SystemVerilog and RTLIL.
Prove a small lemma that the tip's `tsoActive` and `tsoTransitioning` remain false
after the first reset edge when the CSR request is zero. Its validity-mask
inputs are arbitrary, so the lemma covers every buffer occupancy pattern.

Use that lemma to specialize the tip for EQY. Preserve arbitrary mode values
before the first reset edge, since unreset pipelines can capture them. Three
startup reset edges clear the resulting PLRU transient. Observation wrappers
compare valid transactions and masked data, retaining their actual reset and
query history. Uninitialized DUT state is not forced to zero, and no equality
of unreset register contents is assumed.

EQY matches candidate signals, partitions the designs, and proves every required
partition. Native SAT induction and SMT induction handle simple relations;
ABC PDR discovers additional invariants where needed. A successful induction
or PDR proof establishes unbounded equivalence. The complete argument requires
both the mode lemma and overall EQY PASS.

This guide requires no saved `work/` or `artifacts/` directories. All generated
files go there and are ignored by [formal/.gitignore](../.gitignore).

## 1. Prepare the checkout and tools

Run from the repository root on x86_64 Linux. The checkout must contain the
committed `formal/` files, both revision refs, and initialized source submodules:

```sh
git rev-parse lec-base lec-tip
git submodule update --init --recursive
nix --extra-experimental-features 'nix-command flakes' develop path:./formal/toolchain
```

Nix supplies Mill 0.12.3, firtool 1.62.1, Java 17, matching EQY/Yosys 0.70,
SBY, ABC, Z3, Python, and make. No repository `bin/` installation is needed.

The `lec` helper in `build.sc` compiles the ordinary Scala dependencies while
omitting simulator resource packaging. If it is not already present, append:

```scala
object lec extends HasChisel {
  override def millSourcePath = pwd
  override def moduleDeps = xiangshan.moduleDeps
  override def ivyDeps = xiangshan.ivyDeps()
  override def sources = T.sources {
    super.sources() ++ Seq(
      PathRef(pwd / "formal" / "sbuffer" / "StandaloneMain.scala")
    )
  }
  override def forkArgs = Seq("-Xmx8G", "-Xss32m")
}
```

The following steps replace tracked files under `src/main/scala`, so start
with no staged or unstaged changes there. These commands replace both the index
and working tree for that directory, then restore them to HEAD afterward. They
retain the current branch, `build.sc`, and
`formal/`; switching to an older branch could remove the committed formal
setup. No additional checkout or source copy is created. These revision refs
have identical source-submodule revisions and build definitions.

## 2. Generate FIRRTL for both revisions

Restore the base Scala sources and elaborate:

```sh
git restore --source=lec-base --staged --worktree src/main/scala
mkdir -p formal/sbuffer/artifacts/DefaultConfig/Sbuffer/base
mill -i lec.runMain lec.StandaloneMain Sbuffer \
  --config DefaultConfig --target chirrtl \
  --target-dir formal/sbuffer/artifacts/DefaultConfig/Sbuffer/base
```

Restore the tip sources, apply the approved reset correction, and elaborate:

```sh
git restore --source=lec-tip --staged --worktree src/main/scala
git apply formal/sbuffer/tso-reset.patch
mkdir -p formal/sbuffer/artifacts/DefaultConfig/Sbuffer/tip
mill -i lec.runMain lec.StandaloneMain Sbuffer \
  --config DefaultConfig --target chirrtl \
  --target-dir formal/sbuffer/artifacts/DefaultConfig/Sbuffer/tip
```

The patch changes only `tsoActive` from `RegInit(true.B)` to `RegInit(false.B)`.
`tsoTransitioning` already resets false. If the tip ref already incorporates
the correction, inspect that source and omit the patch command.

Each side directory contains `Sbuffer.fir`, `ports.tsv`, and `parameters.txt`.
Review the parameter records, especially buffer size, address widths, and
pipeline widths. The matching/collection rules target the DefaultConfig
16-entry, four-word buffer.

Restore the original Scala working tree after elaboration:

```sh
git restore --source=HEAD --staged --worktree src/main/scala
```

Subsequent stages read only the generated files. The approved reset patch is
preserved in this directory and in the tip FIRRTL used for the proof.

## 3. Lower FIRRTL to SV, RTLIL, and JSON

Run the same conversion for each saved FIRRTL file:

```sh
for side in base tip; do
  mkdir -p "formal/sbuffer/artifacts/DefaultConfig/Sbuffer/$side/rtl"
  firtool "formal/sbuffer/artifacts/DefaultConfig/Sbuffer/$side/Sbuffer.fir" \
    --disable-annotation-unknown --preserve-values=named \
    --disable-all-randomization \
    --lowering-options=disallowPackedArrays,disallowLocalVariables \
    --split-verilog \
    -o "formal/sbuffer/artifacts/DefaultConfig/Sbuffer/$side/rtl"

  yosys -p "
    read_verilog -sv -DSYNTHESIS formal/sbuffer/artifacts/DefaultConfig/Sbuffer/$side/rtl/*.sv formal/sbuffer/artifacts/DefaultConfig/Sbuffer/$side/rtl/*.v
    prep -top Sbuffer -flatten
    select -assert-none A:blackbox
    rename -top Sbuffer_$side
    check -assert
    write_rtlil formal/sbuffer/artifacts/DefaultConfig/Sbuffer/$side.il
    write_json formal/sbuffer/artifacts/DefaultConfig/Sbuffer/$side.json
  "
done
```

The annotation option handles project annotations such as
`DisableAllPrintAnnotation`, which standalone firtool cannot execute. The
lowering options avoid SV constructs unsupported by the Yosys frontend.
`--split-verilog` writes modules and black-box resources as separate files.
Without it, firtool can bundle auxiliary file-list contents into the single
output, producing `unexpected TOK_ID` when Yosys reaches a filename. Import
both `*.sv` and `*.v` from `rtl/`, including the constant-input reader modules;
do not feed either `.f` file to `read_verilog`. The black-box check rejects an
incomplete import.
`SYNTHESIS` excludes simulation logging and assertion tasks; EQY supplies the
equivalence properties. Disabling randomization omits simulation code without
initializing otherwise uninitialized hardware registers.

Retain the original state names: the mode extraction needs `validMask`,
`tsoActive`, and `tsoTransitioning` in `tip.il`. Do not run a state-renaming
or technology-mapping pass before that extraction.

## 4. Prove the TSO mode lemma

```sh
python3 formal/sbuffer/prepare_eqy.py mode
yosys -s formal/sbuffer/tso_mode.ys
sby -d formal/sbuffer/work/tso-mode formal/sbuffer/tso_mode.sby
cat formal/sbuffer/work/tso-mode/status
```

Require PASS with successful base and induction checks. The depth-2 proof
establishes the invariant for every subsequent cycle, not just two cycles.

The extraction script ties `io_csrCtrl_tso_enable` to zero and replaces
`validMask` with arbitrary inputs using `cutpoint`. It retains the mode bits,
clock, and reset, then removes the irrelevant datapath. The harness requires
reset on the first sampled edge and asserts both flags false afterward,
including during subsequent resets.

The helper records input hashes before extraction. Later preparation verifies
those hashes and the model copied into the successful proof. Repeat this stage
if the tip RTLIL, extraction script, or lemma harness changes.

## 5. Audit the interface and prepare the EQY models

```sh
python3 formal/sbuffer/prepare_eqy.py eqy
yosys -s formal/sbuffer/work/eqy/base.ys
yosys -s formal/sbuffer/work/eqy/tip.ys
```

The helper checks all common port signatures and requires the tip's only added
port to be the one-bit CSR TSO request. It writes the interface policy to
`artifacts/DefaultConfig/Sbuffer/interface.tsv`; review it alongside [MITER.md](MITER.md).

Generated files under `work/eqy/` are:

| File | Purpose |
| --- | --- |
| `base_observed.sv`, `tip_observed.sv` | Wrappers encoding the reset and output-observation contract |
| `base.ys`, `tip.ys` | Read, flatten, optimize, and check the wrapped designs |
| `base_observed.il`, `tip_observed.il` | Prepared designs produced by Yosys |
| `sbuffer.eqy` | Matching rules and proof strategies |

Inspect the actual preparation commands with:

```sh
cat formal/sbuffer/work/eqy/base.ys
cat formal/sbuffer/work/eqy/tip.ys
```

The tip exposes its two mode bits as inputs. The wrapper supplies arbitrary
initial values on the first edge and zero afterward, as justified by the mode
lemma. Both wrappers hold reset for three edges using a shift register
initialized to `111`. The first-edge `boot` flag is separate from that counter.

The wrappers qualify cache payloads by request valid and byte mask, forwarding
results by query history and forwarding mask, and performance outputs by their
pipeline history. Nine FIRRTL `DontCare` outputs are unobserved. Zeros on
unobserved output bits encode this contract; they do not initialize DUT arrays.

Startup and observation-history helpers remain inside their consumers, rather
than becoming arbitrary cut inputs. EQY matches interfaces, metadata state,
and data/mask arrays, and groups each array word. All required candidate
matches must be proved; matching is not an environment assumption.

## 6. Run the complete equivalence proof

```sh
eqy -j2 -d formal/sbuffer/work/eqy-run \
  formal/sbuffer/work/eqy/sbuffer.eqy
```

This performs setup and runs all partition strategies. The configuration tries:

| Strategy | Method | Depth | Timeout |
| --- | --- | ---: | ---: |
| `local_sat` | Native SAT induction | 2 | 15 seconds |
| `induction` | SBY/Z3 induction | 4 | 60 seconds |
| `pdr` | SBY/ABC PDR | No fixed depth | 90 seconds |

An unresolved strategy falls through to the next one. The startup counter can
make short induction insufficient even for a correct partition. PDR can learn
the additional invariants. Success at any strategy discharges that partition.

Require overall `DONE (PASS, ...)`. Setup success, some passing partitions,
bounded checks, and covers do not complete this proof. Preserve EQY's formal
X propagation for uninitialized reference state.

To inspect partitions before running solvers instead:

```sh
eqy --setup -d formal/sbuffer/work/eqy-inspect \
  formal/sbuffer/work/eqy/sbuffer.eqy
cat formal/sbuffer/work/eqy-inspect/partition.list
```

A setup-only run normally ends UNKNOWN because no proof strategy has run.
Inspect `matched.ids`, `partition.log`, and `partitions/` for the generated
boundaries. If a proof remains unresolved, distinguish a reachable base-case
counterexample from induction insufficiency, timeout, or a cut that discarded
a necessary state relation. Keep required observations and record refinements
in `prepare_eqy.py` before regenerating models and partitions.

## Resume, transfer, and clean up

To resume an interrupted run with unchanged models and configuration:

```sh
eqy -c -j2 -d formal/sbuffer/work/eqy-run \
  formal/sbuffer/work/eqy/sbuffer.eqy
```

After changing models or configuration, use a fresh EQY output directory.
After changing the tip model or mode harness, rerun the mode lemma as well.

To repeat only EQY on another machine, copy the prepared observation RTLIL,
EQY configuration, and pinned toolchain files under the same `formal/` paths;
enter the Nix shell and run the final command from the directory containing
`formal/`. The full argument also includes the separate mode lemma: preserve
its inputs and results, or rerun it and prepare the models again.

Commit the scripts, documents, reset patch, and toolchain flake/lock file.
Generated `work/` and `artifacts/` directories are disposable and ignored.
Archive them separately if a retained proof record is needed. Deleting both
directories returns to the starting point of this guide; no proof result is
implied by retaining the scripts alone.
