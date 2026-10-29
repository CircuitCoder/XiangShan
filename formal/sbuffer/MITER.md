# Sbuffer interface audit and miter

The preferred partitioned proof workflow is now in [PLAN.md](PLAN.md). The
whole-module commands below remain useful for model checks and counterexamples.

The current DefaultConfig artifacts have 1,106 common inputs and 186 common
outputs. Every common port has the same name, direction, width, signedness, and
bit indexing. The tip adds exactly one input: `io_csrCtrl_tso_enable`, one bit
wide. The miter connects this input to zero and shares every other input,
including clock, reset, CSR controls, and cache responses.

Run these commands from the repository root to repeat the interface audit and
regenerate the miter:

```sh
python3 formal/sbuffer/make_miter.py
```

This reads `base.json`, `tip.json`, and both `Sbuffer.fir` files under
`formal/sbuffer/artifacts/DefaultConfig/Sbuffer`. It writes two files there:

* `interface.tsv`: every port's direction, width, connection or observation
  policy, and comparison qualifier.
* `miter.sv`: explicit declarations and named connections for every DUT port,
  followed by reset handling, named equivalence assertions, and covers.

The generator rejects unexpected interface differences, unknown output ports,
and changes to the FIRRTL `DontCare` assignments it excludes. It does not modify
the designs, their RTLIL/JSON files, or `PLAN.md`.

## Observation contract

| Output | Comparison |
| --- | --- |
| Both enqueue ready signals | Every cycle after reset |
| Cache request valid | Every cycle after reset |
| Cache request command, addresses, mask, ID | When either DUT asserts request valid, including stalled requests |
| Cache request data | When either DUT asserts request valid, for bytes selected by either DUT's mask |
| Forwarding fast masks | With the current query's valid signal |
| Forwarding masks, `dataInvalid`, `matchInvalid` | With the previous cycle's query-valid signal |
| Forwarding data bytes | With the previous query valid, for bytes selected by either DUT's forwarding mask |
| `sbempty`, `flush.empty` | Every cycle after reset |
| Performance events 0–11 | After two consecutive cycles outside reset |
| Performance events 12–15 | After three consecutive cycles outside reset |

The two-cycle performance delay comes from `HasPerfEvents.generatePerfEvent`.
Events 12–15 additionally use Sbuffer's registered valid-entry count. Their
pipeline registers have no reset, so early values do not define an equivalence
obligation. This qualification applies again after each subsequent reset.

The miter excludes nine output ports: the three forwarding `addrInvalid` ports
and all six store-prefetch output ports. Both input FIRRTL files explicitly mark
each of these as invalid (`DontCare`). In this configuration prefetching is
disabled. The exclusions follow the FIRRTL contract, even though lowering chose
zero for these outputs.

The 177 compared output ports yield 240 named assertions because the 512-bit
cache-data port is checked as 64 separate masked bytes. There are also ten covers
for enqueue, cache requests/backpressure, draining, flush, replay input, and
forwarding. Covers are reachability targets; they do not prove legal environment
behavior or equivalence.

## Reset and inputs

Reset on the first three sampled clock edges is assumed. Subsequent resets remain
arbitrary and shared. The miter compares functional outputs beginning with the
first cycle after the initial reset sequence; it does not impose a global warm-up interval.
Forwarding-query history clears on reset so pre-reset queries do not create
post-reset response obligations.

Other inputs are unrestricted. In particular, this initial miter has no lane
ordering, address-consistency, backpressure-stability, or cache-response
scoreboard assumptions. A counterexample involving such inputs needs review
against the parent module's actual contract before adding assumptions.

The DUTs' simulation assertion tasks were excluded during synthesis-mode SV
import. The miter adds formal equivalence assertions; it does not restore or
prove those internal DUT assertions. No assumption equates internal registers,
relates data arrays, or initializes otherwise uninitialized DUT registers.
This is a sequential comparison from reset, allowing different internal states.
An unbounded proof may require additional *asserted* state-relation invariants.

## Check the combined model

```sh
artifacts=formal/sbuffer/artifacts/DefaultConfig/Sbuffer
bin/yosys -p "
  read_rtlil $artifacts/base.il
  read_rtlil $artifacts/tip.il
  read_verilog -formal -sv $artifacts/miter.sv
  prep -top miter
  check -assert
  select -assert-none A:blackbox
  stat
"
```

This checks the combined model's construction. It does not run an equivalence
proof. To run the bounded check using the supplied SBY configuration:

```sh
export PATH="$PWD/bin:$PATH"
sby -f -d formal/sbuffer/work/bmc formal/sbuffer/miter.sby bmc
```

The configuration uses depth 32. Inspect any failure trace before attempting
unbounded induction:

```sh
sby -f -d formal/sbuffer/work/prove formal/sbuffer/miter.sby prove
```

Use task `cover` with a separate output directory to run the covers. A bounded
PASS alone does not establish unbounded equivalence.
