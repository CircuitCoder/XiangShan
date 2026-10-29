The local SLEC toolchain is installed under `../../bin`, backed by a locked Nix
environment. Mill and firtool are downloaded as upstream binaries with fixed
SHA-256 hashes; the Nix build downloader worked when ordinary shell downloads
could not reach the proxy. Building CIRCT from source is unnecessary.
The environment also contains a fixed-output Maven mirror for Mill's compiler,
Scala 2.13.15, Chisel/plugin 6.7.0, and the libraries declared in `build.sc`.
The Mill launcher selects this mirror first, avoiding live downloads for those
dependencies. The mirror's Coursier build helper explicitly honors Nix's proxy
settings; Java does not automatically use the shell's proxy variables.

| Tool | Selected version |
| --- | --- |
| Mill | 0.12.3, complete distribution JAR |
| firtool | 1.62.1, upstream Linux x86-64 binary |
| Java | OpenJDK 17.0.20.1+1, headless |
| Yosys | 0.70, from the locked nixpkgs revision |
| EQY | v0.70; plugins compiled against the same Yosys |
| SymbiYosys | 0.70, paired with that Yosys |
| Z3 | 5.1.0, from the same package set |

The Arch packages remain installed as before. Local launchers select the Nix
tools so SymbiYosys and EQY use their matching Yosys. The pinned nixpkgs does not
package EQY; `flake.nix` builds upstream v0.70 at commit
`7a92d8441aa442dd1b5543458d6f1060a8f85dd1` with a fixed source hash. Its three
plugins are built using this toolchain's `yosys-config`. It is included in both
the installed toolchain and the development shell.
The Nix SBY package prints `unknown SBY version`; its package definition pins the
v0.70 source and hash, recorded by the nixpkgs lock. The actual solver and Yosys
versions are captured in the verification report.

From the repository root, activate the installed tools with:

```sh
export PATH="$PWD/bin:$PATH"
export LEC_MILL="$PWD/bin/mill"
export LEC_FIRTOOL="$PWD/bin/firtool"
export CHISEL_FIRTOOL_PATH="$PWD/bin"
```

The Mill launcher selects Java 17 for itself and its children. Alternatively,
start the reproducible development shell (the explicit `path:` includes these
files even while they are untracked):

```sh
nix --extra-experimental-features 'nix-command flakes' develop path:./formal/toolchain
```

The shell puts the pinned toolchain's `bin` directory first on `PATH`, so
`eqy`, `yosys`, `sby`, `yosys-abc`, and `z3` work directly without a separate
PATH export or installation into the repository's `bin` directory.

Rebuild and install the same toolchain into `./bin` with:

```sh
bash formal/toolchain/install.sh
```

This builds `path:./formal/toolchain#toolchain`, keeps a Nix GC root at
`bin/lec-toolchain`, adds local executable symlinks, and runs verification.
It preserves unrelated existing files in `bin` and does not install system
packages or change the default system JVM. A future rebuild uses `flake.lock`
and the two binary hashes in `flake.nix`. An offline rebuild requires the
corresponding Nix store paths or substituter cache to be available.

To repeat the verification after activation:

```sh
python3 formal/toolchain/check.py
# Also compile Mill/Scala and elaborate a small Chisel circuit, using only the mirror:
COURSIER_REPOSITORIES="file://$PWD/bin/lec-toolchain/maven2" \
  python3 formal/toolchain/check.py --mill-build
```

It checks the pinned Mill/firtool versions and Java 17, lowers a clocked FIRRTL
counter to SystemVerilog, reads it with Yosys, and runs SBY/Z3 on three small
fixtures: an unbounded induction proof, a reachable cover, and a deliberately
false assertion that must produce a counterexample. All three statuses are
checked explicitly. With `--mill-build`, it also compiles a real Mill build,
compiles a counter with Scala 2.13.15 and the Chisel 6.7.0 compiler plugin, and
emits the CHIRRTL used by the firtool/Yosys check. `install.sh` runs this extended
check with only the local Maven mirror available. These fixtures validate the tools and do not prove Sbuffer
or LoadQueueRAR equivalent. Each verification run regenerates `formal/toolchain/work/check/report.json`
and adjacent logs. These temporary outputs are ignored and are not committed.

The host Yosys 0.66 / Z3 4.16.0 pair was separately checked using:

```sh
python3 formal/toolchain/check.py --system-only
```

For direct SMT export with that Yosys version, the smoke script uses
`async2sync; chformal -lower` before `write_smt2`. Without lowering the newer
`$check` cells, direct export omitted the assertions; the deliberately failing
fixture caught that problem. SBY handles its own formal lowering.

Source submodules remain separate elaboration prerequisites. Dependencies named
only in uninitialized submodules have not yet been audited or added to the mirror.
Tool installation and fixture checks do not
claim that the processor modules have been compiled. The proof workflow remains
in [the Sbuffer plan](../sbuffer/PLAN.md).
