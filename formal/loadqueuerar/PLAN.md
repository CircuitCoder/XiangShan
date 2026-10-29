# LoadQueueRAR equivalence with TSO disabled

## Overview

Compare the `lec-base` and `lec-tip` DefaultConfig implementations with the tip's
TSO request held false at every cycle, including before the first reset edge.
The source analysis finds no behavioral change under this condition. The only
functional edit is `addressMatch` becoming `tso_enable || addressMatch` in the
registered violation predicate. NC entries were already marked released on
enqueue. See [BEHAVIOR.md](BEHAVIOR.md) for the enabled-mode difference.

Elaborate and lower each revision separately, then audit the actual interfaces.
The tip adds a whole CSR-control bundle. Verify that all added fields except
TSO are unused, and keep them arbitrary in the comparison wrapper. Tie only
TSO to zero. Constant propagation should remove the new logic directly;
there is no separate mode lemma or special treatment of startup TSO state.

Use EQY to match common state and partition the sequential equivalence problem.
Prove every required partition using short SAT induction, SMT induction, and
ABC PDR as a fallback. A successful induction or PDR result is unbounded.
An UNKNOWN or timeout is unresolved, not a behavioral counterexample.

Both wrappers force one common startup reset edge, suppress observations
during reset, and otherwise compare **every output bit**. This includes invalid
response payloads and early performance outputs, making the comparison stronger
than the valid-qualified functional interface. EQY's formal X handling remains
enabled for uninitialized reference state. No initial equality of unreset
registers is assumed. Unlike Sbuffer, there is no added mode state whose first
reset edge can perturb a common pipeline, so a three-edge reset is unnecessary.

## 1. Enter the tool environment and inspect the source change

Run from the repository root:

```sh
nix --extra-experimental-features 'nix-command flakes' develop path:./formal/toolchain
python3 formal/loadqueuerar/prepare.py analyze
git diff lec-base lec-tip -- src/main/scala/xiangshan/mem/lsqueue/LoadQueueRAR.scala
```

The source check simplifies the TSO predicate and checks the remaining module
text. It is a useful review check, not a replacement for the hardware proof.
Use the existing `lec` module in `build.sc` and the shared
`formal/sbuffer/StandaloneMain.scala` driver from the Sbuffer preparation.
Keep that build overlay and the untracked `formal/` directory across checkouts.
The commands below use manual checkouts and create no additional source copies.

## 2. Generate the two sets of artifacts

```sh
git switch lec-base
python3 formal/loadqueuerar/prepare.py emit base
python3 formal/loadqueuerar/prepare.py lower base

git switch lec-tip
python3 formal/loadqueuerar/prepare.py emit tip
python3 formal/loadqueuerar/prepare.py lower tip
```

`emit` requires the matching branch revision and an unmodified
`LoadQueueRAR.scala`. It runs, for example:

```sh
mill -i lec.runMain lec.StandaloneMain LoadQueueRAR \
  --config DefaultConfig --target chirrtl \
  --target-dir formal/loadqueuerar/artifacts/DefaultConfig/LoadQueueRAR/base
```

Each side directory contains `LoadQueueRAR.fir`, `ports.tsv`, `parameters.txt`,
and `revision.json`. The metadata records the revision, FIRRTL and module
hashes, submodule revisions, and working changes. Review changes outside the
build overlay and compare the relevant queue/address/pipeline parameters.
The recorded Sbuffer reset patch is irrelevant to LoadQueueRAR and is not applied.

`lower` runs firtool with the same Yosys-compatible lowering used for Sbuffer:

```sh
firtool formal/loadqueuerar/artifacts/DefaultConfig/LoadQueueRAR/base/LoadQueueRAR.fir \
  --disable-annotation-unknown --preserve-values=named \
  --disable-all-randomization \
  --lowering-options=disallowPackedArrays,disallowLocalVariables \
  --split-verilog \
  -o formal/loadqueuerar/artifacts/DefaultConfig/LoadQueueRAR/base/rtl
```

Unknown project annotations include the print-disabling annotation that
firtool cannot execute. The Yosys import defines `SYNTHESIS` to omit simulation
assertions and logging; equivalence properties come from EQY. Randomization
code is omitted without forcing otherwise uninitialized hardware to zero.
The helper creates `rtl/` and imports all generated `.sv` and `.v` sources,
excluding `.f` file lists. Splitting avoids parsing bundled auxiliary file
contents as Verilog. The import also checks that no black-box modules remain.

The helper then writes and runs `work/eqy/base-import.ys` (and the corresponding
tip file). Inspect the commands with:

```sh
cat formal/loadqueuerar/work/eqy/base-import.ys
```

They read SV, run `hierarchy`, `proc`, `flatten`, `opt_clean`, rename the top to
`LoadQueueRAR_base` or `LoadQueueRAR_tip`, check the design, and write
`artifacts/DefaultConfig/LoadQueueRAR/{base,tip}.{il,json}`.

## 3. Audit ports and prepare the proof models

```sh
python3 formal/loadqueuerar/prepare.py eqy
yosys -s formal/loadqueuerar/work/eqy/base.ys
yosys -s formal/loadqueuerar/work/eqy/tip.ys
```

This stage needs only the saved artifacts, so it works on either checkout.
The helper rejects missing ports, differing shared widths or directions,
unexpected added interfaces, or use of added CSR controls other than TSO.
Review `artifacts/DefaultConfig/LoadQueueRAR/interface.tsv`.
The base elaboration has 648 input ports and 13 output ports: three query-ready,
three response-valid, three replay-result, full, occupancy, and two performance
outputs. The tip audit determines the number of additional CSR ports.

Each generated wrapper has the same external ports and drives its DUT reset
with `reset || boot`; `boot` clears after the first edge. Added unused CSR
inputs are present on both wrappers, and ignored by the base. Only the tip's
TSO port is tied zero. No load-request, NC, release, redirect, revoke,
writeback-pointer, or external protocol assumptions are added.

The scripts flatten the wrapped DUTs and run `opt -full` before saving
`work/eqy/{base,tip}_observed.il`. The input hashes, generated wrappers, and
configuration are recorded in `work/eqy/inputs.json`. No internal signals are
exposed or forced, and no DUT state is removed through a mode abstraction.

## 4. Run EQY

```sh
eqy -j2 -d formal/loadqueuerar/work/eqy-run \
  formal/loadqueuerar/work/eqy/loadqueuerar.eqy
```

This performs setup and runs all required partition proofs. The generated
configuration matches the interfaces, allocated/released/uop state, violation
vectors, and corresponding freelist and address-module signals. It leaves the
wrapper's startup logic inside consumers rather than cutting it into an
arbitrary shared input. Every required match must be proved; matching is not
an environment assumption.

The strategies are native SAT induction at depth 2 with a 15-second timeout,
SMT/Z3 induction at depth 4 with a 60-second timeout, then ABC PDR with a
90-second timeout. These are starting budgets. Keep the generated configuration
and generator consistent if changing them for a later run.

Completion requires overall `DONE (PASS, ...)`. A setup-only UNKNOWN, partial
success, or an unsuccessful induction step is not a completed equivalence proof.
For a failure, distinguish a reachable base-case counterexample from an
insufficient induction depth, timeout, or unsuitable internal match. In
particular, do not add an NC exclusion to make the proof pass.

To inspect partition boundaries before running solvers instead:

```sh
eqy --setup -d formal/loadqueuerar/work/eqy-inspect \
  formal/loadqueuerar/work/eqy/loadqueuerar.eqy
cat formal/loadqueuerar/work/eqy-inspect/partition.list
```

## Restart and transfer

With unchanged prepared `base_observed.il`, `tip_observed.il`, and
`loadqueuerar.eqy`, run just the final EQY command in a fresh output directory.
Resume an interrupted run of those same models with:

```sh
eqy -c -j2 -d formal/loadqueuerar/work/eqy-run \
  formal/loadqueuerar/work/eqy/loadqueuerar.eqy
```

After changing models or matching rules, use a fresh output directory. Preserve
the artifacts, interface audit, wrappers, configuration, matching and partition
files, and final proof statuses as the proof record.

To move the prepared check to another x86_64 Linux machine, copy `formal/`
including the Git-ignored `work/eqy/` model files and the toolchain flake and
lock file. Enter the Nix shell and run EQY from the directory containing
`formal/`. No `bin/`, Scala checkout, Mill run, or firtool run is needed to
repeat the final proof. Unlike Sbuffer, this argument has no separate mode
lemma to transfer or rerun.
