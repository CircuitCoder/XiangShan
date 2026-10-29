# Sbuffer proof refinements

The user reported that the complete three-edge-reset EQY check passed. This
note retains findings from the earlier failed sweep that explain the current
configuration. Generated models, experiments, traces, and logs were removed
during cleanup. Follow [PLAN.md](PLAN.md) to regenerate the complete check.

## Initial sweep

The original single-edge-reset sweep proved 2,743 of 2,852 partitions. Four
partitions failed a base case, 103 remained UNKNOWN, and two timed out.
These historical results describe the old setup, not the completed proof.

| Partitions | Initial result | Resolution |
| --- | --- | --- |
| PLRU `dut.state_reg` | FAIL | Three startup reset edges clear the first-edge mode transient; retain and prove PLRU equality |
| Performance events 1, 2, 3 | FAIL | Keep observation-history logic inside consuming partitions |
| Cache data, inflight/timeout flags, cache control, and delayed performance events | UNKNOWN | Add PDR to discover auxiliary invariants |
| Cache request mask | TIMEOUT | PDR proved the tested obligation |
| Forward lane 0, byte 10 | TIMEOUT | More SAT time proved the original obligation; retaining forwarding history also improved it |

## Startup reset and PLRU

`tsoTransitioning` and the corrected `tsoActive` reset false, but their values
before the first synchronous reset edge remain unconstrained. Those values can
change a store acceptance captured by an unreset access-valid pipeline. With
only one reset edge, the delayed access can perturb PLRU after its reset.
This is a real internal-state mismatch under that reset contract, not evidence
of an error in the base implementation.

The current wrappers preserve arbitrary first-edge mode values and require
three startup reset edges. This clears the transient while leaving store
inputs arbitrary during reset. PLRU remains a matched proof obligation and
passes under this contract. See [STATE_REG_FINDINGS.md](STATE_REG_FINDINGS.md)
for the original mechanism and concrete diagnostic values.

## Observation history must retain its recurrence

The performance comparison uses `history_valid` to defer observing unreset
pipelines. Matching that helper independently turned it into an arbitrary
shared cut input, allowing impossible values such as `010` before the first
reset edge. Consumers could then compare pipeline contents too early.

Removing the helper match retains its initialization and shift/reset logic
inside each consuming partition. The same reasoning applies to
`forward_valid_d_*`: previous-query qualification must retain its relationship
to the actual query input. Neither helper is explicitly matched in the current
configuration. No performance output was omitted or given a longer delay.

## Why PDR is a fallback

A partition's output equality over a few cycles need not imply the state
relations needed for the next cycle. Fixed-depth induction can therefore fail
its induction step even when every reset-reachable execution is equivalent.
PDR searches for additional inductive invariants and proved representative
obligations for cache data/masks/valid, inflight and timeout flags, and delayed
performance events. The final flow includes it after SAT and SMT induction.

An unsuccessful induction step is not a reachable counterexample. UNKNOWN
and timeout are unresolved results. A partition is discharged only by a
successful strategy, and the whole check requires overall EQY PASS.

## Rejected matching experiment

Separating resettable pipeline validity from payload created an unsuitable
abstraction: a consumer could invent a valid transaction immediately after
reset and observe unconstrained payload. That produced a spurious cache-data
base-case failure. Those extra match points were not retained. The working
configuration keeps the original tag-match patterns and confines wrapper
history to consumers.

Changing engines does not repair a bad cut or an incorrect observation/reset
contract. Inspect partition inputs and reachable base cases before changing
matching rules or proof budgets.
