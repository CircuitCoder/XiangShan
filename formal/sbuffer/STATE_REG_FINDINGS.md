# Investigation of the `dut.state_reg` failure

This investigation used the earlier single-edge startup reset contract. The
current flow uses three startup reset edges. The user reported that the complete
updated equivalence check passed; see `PLAN.md` to regenerate it. The temporary
diagnostic models, traces, and logs referenced below were removed during cleanup;
this document retains the findings, not a standalone counterexample fixture.

`dut.state_reg` is the 15-bit pseudo-LRU replacement state, not the
`sbuffer_state` control FSM. The EQY base-case failure is caused by a store
presented on the first reset edge and an unreset register that remembers the
store's replacement-policy access. Literal equality of this internal state is
too strong for the original single-edge reset contract. This investigation has
not established an externally observable Sbuffer mismatch.

## What happens in the failing EQY partition

The waveform is at:

```
formal/sbuffer/work/sbuffer-eqy/strategies/Sbuffer_observed.dut.state_reg/induction/Sbuffer_observed.dut.state_reg/engine_0/trace.vcd
```

The observation wrapper's `boot` forces the first DUT reset edge. Before that
edge, `initial_tsoActive = 0` and `initial_tsoTransitioning = 1`. These arbitrary
startup values are deliberately retained by `prepare_eqy.py`; the mode lemma
only establishes zero values after the reset edge.

Both store lanes are valid on the reset edge. The base accepts them internally;
the tip blocks them because `tsoTransitioning` is set. The store-access pipeline
uses `accessIdx(i).valid := RegNext(accessValid)` without a reset value. Although
reset clears the PLRU register, the delayed access survives that reset and
updates the base's PLRU one edge later. At solver step 2:

```
gold.state_reg = 15'b100000001000100
gate.state_reg = 15'b000000000000000
```

The partition also has arbitrary shared inputs for internal metadata. Therefore
the exact original trace cannot simply be assumed reachable in the full DUT.

## Reproduction without partition cuts

Additional checks live in `formal/sbuffer/work/state-reg-investigation/`,
separately from the first sweep. `base.ys` and `tip.ys` read the original
`base.il` and `tip.il`, expose the PLRU and a few diagnostic wires as outputs,
and discard logic outside their dependency cones. They do not cut any internal
signal or specialize the TSO registers. The tip CSR request remains tied low.

`fixed_startup.sby` restricts the initial state to a concrete example: every
shared register has the same initial value, all zero except entry 5's valid bit.
Only the tip's extra `tsoTransitioning` register starts at one. Both physical
tags and the store address are zero. A single store is valid during the first
reset edge; reset and store valid then deassert. Other inputs are fixed, with
DCache request ready asserted. This restriction constructs an example; it is
not an initialization assumption added to the main equivalence proof.

| Sample | Reset | Base pending store access | Tip pending store access | Base PLRU | Tip PLRU |
| --- | --- | --- | --- | --- | --- |
| Before first edge | 1 | 0 | 0 | `0x0000` | `0x0000` |
| After first edge | 0 | 1, entry 5 | 0 | `0x0000` | `0x0000` |
| After second edge | 0 | 0 | 0 | `0x4065` | `0x4045` |

The assertion fails at step 2. Thus the startup mechanism exists in the
original circuits, independently of partition cuts and independently of
different initial values in shared state.

Control experiments, all bounded to three sampled states:

| Check | Result |
| --- | --- |
| `fixed_startup`: concrete matching shared initial state, one reset edge | FAIL, PLRU differs at step 2 |
| `fixed_zero_mode`: same example, initial transition flag also zero | PASS within the bound |
| `fixed_two_reset`: same example, two reset edges | PASS within the bound |
| `quiet_reset`: arbitrary original initial state, store valid low whenever reset is asserted | PASS within the bound |
| `two_reset`: arbitrary original initial state, initial reset held for two edges | PASS within the bound |

These PASS results are bounded diagnostics, not unbounded equivalence proofs.
An additional experiment forcing the EQY partition's startup mode values to zero
failed induction and timed out in its base-case search; it did not establish a
replacement proof for that partition.

## Implications for the proof

Reset clears the store-entry valid bits, so the differing PLRU bits initially
describe invalid entries. An internal-state mismatch alone does not prove that
the modules can issue different observable transactions. The original single-edge investigation left open whether a suitable state
relation could make the PLRU difference irrelevant. The completed flow instead
uses three startup reset edges and keeps the PLRU equality obligation.

If the actual reset protocol guarantees two edges or inactive store inputs,
that guarantee can be represented explicitly in the harness and the mode lemma.
It would change the proof contract and must be justified by the integration.

If the current single-edge, arbitrary-input contract is retained, the proof must
allow this PLRU-state difference. A next experiment is to remove the direct
`dut.state_reg` match and prove the consuming logic with the PLRU inside its
cone, refining partitions or introducing a validity-aware state relation as
needed. Removing the match alone is not a completed proof.

The DUT RTL, main proof generator, reset contract, and first-sweep configuration
were not changed by this investigation.
