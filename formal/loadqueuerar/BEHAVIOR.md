# LoadQueueRAR behavior: lec-base versus lec-tip

With `io.csrCtrl.tso_enable` permanently false, the module's source describes
the same state transitions and outputs. No new mode register, transition
protocol, allocation rule, release rule, or response latency is introduced.
This is a source-level finding; the prepared EQY flow checks the elaborated
DefaultConfig implementations independently.

## What changed

The interface gains the entire `CustomCSRCtrlIO` input bundle, not just a
single TSO input. Only `tso_enable` is read in this module. The parent
`LoadQueue` gains and forwards that bundle. The original query, release,
writeback-pointer, occupancy, full, and performance interfaces remain.

The per-entry violation predicate changes from:

```text
base: allocated && addressMatch && youngerThanQuery && released
tip:  allocated && (tsoEnable || addressMatch) && youngerThanQuery && released
```

Here `addressMatch` is the CAM result for the module's 16-bit hashed partial
physical address. It is not a full physical-address equality test. ROB age
uses the existing circular-pointer comparison. The per-entry match vector
is registered and OR-reduced into `rep_frm_fetch`; response valid remains
the previous cycle's request valid, independently of request ready.

When TSO is false, the predicates are identical. When TSO is true at the
query edge, any allocated, released entry younger than the query can cause
a replay, regardless of its address signature. The TSO bit is sampled through
the match pipeline; the module has no local mode-transition handshake.

## Non-cacheable loads

The enqueue assignment is the same in both versions:

```text
released := is_nc || (data_valid && (matchReleaseNow || matchReleasePrevious))
```

The tip's rewrite only joins lines and moves whitespace. In Scala, `===`
binds more tightly than `&&`, which binds more tightly than `||`, so the
grouping is unchanged. In particular, `is_nc` sets `released` immediately,
even when `data_valid` is false; this behavior already exists in the base.
The later matching-cache-release update and its assignment priority also
remain unchanged.

Consequently:

| Case | Base and TSO-disabled tip | TSO-enabled tip |
| --- | --- | --- |
| Younger allocated NC entry with matching address signature | Can cause replay immediately after enqueue becomes visible | Same |
| Younger allocated NC entry with a different address signature | Does not cause replay through this entry | Can cause replay |
| Younger allocated cacheable entry not yet marked released | Does not cause replay through this entry | Same |
| Younger allocated cacheable entry already marked released, with a different address signature | Does not cause replay through this entry | Can cause replay |

The query itself need not be an NC request. The relevant `is_nc` bit belongs
to the stored younger entry. The comment about NC requests not being allowed
to have RAR is unchanged and does not mean the checker skips NC entries;
marking them released makes them eligible for violation detection.

Thus the recollection of changed NC behavior is correct **when TSO is enabled**.
There is no NC-specific change when TSO is permanently disabled.

## Scope and evidence

`python3 formal/loadqueuerar/prepare.py analyze` compares the actual branch
sources after the two explicit substitutions: remove the added CSR interface,
and simplify `(false || CAM)` to `CAM`. It verifies that the remaining code
matches modulo comments and whitespace, including the NC assignment, and saves
the original diff in `work/eqy/module.diff`.

The direct submodule sources `FreeList.scala` and `LoadQueueData.scala`, memory
query bundles, and the utility submodule revision are unchanged between these
branches. Other branch changes include global parameter changes, so source
inspection alone does not establish that both elaborations have identical
interfaces or identical parameter-dependent hardware. The preparation script
audits the actual lowered port signatures and retains the full logic for EQY.

This proof concerns this module under shared external inputs; it does not
prove equivalence of the surrounding LSU or arbitrary TSO switching behavior.
