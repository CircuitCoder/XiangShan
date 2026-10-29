#!/usr/bin/env python3
"""Freeze the two revisions and elaborate real modules; never install tools or run proofs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
WORK = HERE / "work"
REFS = {"base": "lec-base", "tip": "lec-tip"}
SUBMODULES = (
    "rocket-chip", "difftest", "huancun", "fudian", "utility", "yunsuan",
    "coupledL2", "openLLC", "ChiselAIA", "ChiselIOPMP",
)
OVERLAY = """

// Added by formal/sbuffer/prepare.py in this isolated checkout only.
// Compile the original Scala sources/dependencies, without XiangShan's native
// simulation resource packaging (which otherwise requires ready-to-run binaries).
object lec extends HasChisel {
  override def millSourcePath = pwd
  override def moduleDeps = xiangshan.moduleDeps
  override def ivyDeps = xiangshan.ivyDeps()
  override def forkArgs = Seq("-Xmx8G", "-Xss32m")
}
"""


def run(argv, cwd=ROOT, check=True):
    command = [str(a) for a in argv]
    command[0] = shutil.which(command[0]) or command[0]
    try:
        return subprocess.run(command, cwd=cwd, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=check)
    except OSError as error:
        # Older Mill distributions are shell/JAR polyglots without a shebang.
        if error.errno != 8:
            raise
        return subprocess.run(["/bin/sh", *command], cwd=cwd, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=check)


def git(*args, cwd=ROOT):
    return run(["git", *args], cwd).stdout.strip()


def mill_runner():
    local = ROOT / "bin/mill"
    return os.environ.get("LEC_MILL") or (str(local) if local.is_file() else "mill")


def links(cwd, revision):
    return {line.split("\t", 1)[1]: line.split()[2]
            for line in git("ls-tree", revision, cwd=cwd).splitlines()
            if line.startswith("160000 ")}


def copy_local_dependencies(source, destination, revision, missing):
    for name, sha in links(source, revision).items():
        if source == ROOT and name not in SUBMODULES:
            continue
        src, dst = source / name, destination / name
        if not (src / ".git").exists():
            missing.append(str(src.relative_to(ROOT)))
            continue
        if not (dst / ".git").exists():
            run(["git", "clone", "--quiet", "--shared", "--no-checkout", src, dst])
            run(["git", "checkout", "--quiet", "--detach", sha], dst)
        if git("rev-parse", "HEAD", cwd=dst) != sha:
            raise RuntimeError(f"wrong dependency revision: {dst}")
        copy_local_dependencies(src, dst, sha, missing)


def apply_tip_reset(destination, revision):
    path = "src/main/scala/xiangshan/mem/sbuffer/Sbuffer.scala"
    original = run(["git", "show", f"{revision}:{path}"], destination).stdout
    old, new = "val tsoActive = RegInit(true.B)", "val tsoActive = RegInit(false.B)"
    if original.count(old) != 1:
        raise RuntimeError("tip reset source changed; review the recorded reset patch")
    expected = original.replace(old, new)
    current = (destination / path).read_text()
    if current == original:
        run(["git", "apply", "--check", HERE / "tso-reset.patch"], destination)
        run(["git", "apply", HERE / "tso-reset.patch"], destination)
    elif current != expected:
        raise RuntimeError(f"unexpected Sbuffer edits in {destination}; preserve and review them")
    if (destination / path).read_text() != expected:
        raise RuntimeError("recorded reset patch does not match the approved one-line change")
    return [{"file": "formal/sbuffer/tso-reset.patch", "sha256":
             hashlib.sha256((HERE / "tso-reset.patch").read_bytes()).hexdigest()}]


def snapshot():
    WORK.mkdir(parents=True, exist_ok=True)
    manifest = {"root": str(ROOT), "revisions": {}, "overlays": [
        "src/main/scala/lec/StandaloneMain.scala (elaboration driver)",
        "build.sc: appended lec module (omit native simulator resources)",
    ]}
    for side, ref in REFS.items():
        sha = git("rev-parse", f"{ref}^{{commit}}")
        dest = WORK / side
        if not dest.exists():
            run(["git", "clone", "--quiet", "--shared", "--no-checkout", ROOT, dest])
            run(["git", "checkout", "--quiet", "--detach", sha], dest)
        if git("rev-parse", "HEAD", cwd=dest) != sha:
            raise RuntimeError(f"{dest} has a different HEAD; preserve it and use a new work directory")
        missing = []
        copy_local_dependencies(ROOT, dest, sha, missing)
        source_patches = apply_tip_reset(dest, sha) if side == "tip" else []
        driver = dest / "src/main/scala/lec/StandaloneMain.scala"
        driver.parent.mkdir(parents=True, exist_ok=True)
        driver.write_bytes((HERE / "StandaloneMain.scala").read_bytes())
        # Always reconstruct the build overlay from the frozen commit.
        original_build = git("show", f"{sha}:build.sc")
        (dest / "build.sc").write_text(original_build + "\n" + OVERLAY)
        manifest["revisions"][side] = {
            "ref": ref, "sha": sha, "submodule_commits": links(ROOT, sha),
            "source_patches": source_patches,
            "missing_local_submodules": missing,
            "module_sha256": {
                name: hashlib.sha256((dest / path).read_bytes()).hexdigest()
                for name, path in {
                    "Sbuffer": "src/main/scala/xiangshan/mem/sbuffer/Sbuffer.scala",
                    "LoadQueueRAR": "src/main/scala/xiangshan/mem/lsqueue/LoadQueueRAR.scala",
                }.items()
            },
        }
        print(f"{side}: {sha}; {len(missing)} local source dependencies unavailable")
    (WORK / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    diff = git("diff", "lec-base..lec-tip", "--",
               "src/main/scala/xiangshan/mem/sbuffer/Sbuffer.scala",
               "src/main/scala/xiangshan/mem/lsqueue/LoadQueueRAR.scala",
               "src/main/scala/xiangshan/Bundle.scala")
    (WORK / "module.diff").write_text(diff + "\n")


def check(side):
    dest = WORK / side
    errors = []
    if not (dest / "build.sc").exists():
        raise RuntimeError("run snapshot first")
    for name in SUBMODULES:
        if not (dest / name / "src").is_dir():
            errors.append(f"missing source submodule {name}")
    for name in ("rocket-chip/cde", "rocket-chip/hardfloat", "openLLC/openNCB"):
        if not (dest / name / ".git").exists():
            errors.append(f"missing nested source submodule {name}")
    runner = mill_runner()
    try:
        version = run([runner, "-i", "--version"], dest, check=False)
        print(version.stdout.strip())
        match = re.search(r"Mill Build Tool version\s+(\S+)", version.stdout)
        expected = (dest / ".mill-version").read_text().strip()
        if version.returncode or not match or match.group(1) != expected:
            errors.append(f"need Mill {expected}; set LEC_MILL to its launcher")
    except OSError as error:
        errors.append(f"cannot execute Mill launcher {runner}: {error}")
    for name in ("yosys", "eqy", "sby", "z3"):
        print(f"proof tool {name}: {shutil.which(name) or 'not on PATH'}")
    for error in errors:
        print(f"BLOCKER: {error}")
    return errors


def emit(args):
    if check(args.side):
        raise RuntimeError("elaboration blocked by prerequisites listed above; no FIRRTL emitted")
    dest = WORK / args.side
    output = WORK / "artifacts" / args.config / args.module / args.side
    output.mkdir(parents=True, exist_ok=True)
    runner = mill_runner()
    argv = [shutil.which(runner) or runner, "-i", "lec.runMain", "lec.StandaloneMain",
            args.module, "--config", args.config, "--target", "chirrtl", "--target-dir", output]
    print("Running:", " ".join(str(x) for x in argv), flush=True)
    with (output / "elaboration.log").open("w") as log:
        try:
            result = subprocess.run([str(x) for x in argv], cwd=dest, stdout=log, stderr=subprocess.STDOUT)
        except OSError as error:
            if error.errno != 8:
                raise
            result = subprocess.run(["/bin/sh", *map(str, argv)], cwd=dest,
                                    stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f"elaboration failed; see {output / 'elaboration.log'}")
    firrtl = output / f"{args.module}.fir"
    if not firrtl.is_file() or not firrtl.stat().st_size:
        raise RuntimeError(f"stage did not produce expected {firrtl}; inspect elaboration.log")
    print(f"Emitted {firrtl}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("snapshot", help="make isolated local checkouts and record exact revisions")
    preflight = commands.add_parser("check", help="inspect prerequisites without fetching or installing")
    preflight.add_argument("side", choices=REFS)
    elaboration = commands.add_parser("emit", help="elaborate to CHIRRTL, without lowering or proving")
    elaboration.add_argument("side", choices=REFS)
    elaboration.add_argument("module", choices=("Sbuffer", "LoadQueueRAR"))
    elaboration.add_argument("--config", default="DefaultConfig")
    args = parser.parse_args()
    if args.command == "snapshot":
        snapshot()
    elif args.command == "check":
        return int(bool(check(args.side)))
    else:
        emit(args)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError):
            print(error.stdout, file=sys.stderr)
        sys.exit(1)
