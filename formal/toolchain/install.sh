#!/usr/bin/env bash
set -euo pipefail

toolchain_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "$toolchain_dir/../.." && pwd)
mkdir -p "$repo_dir/bin"

# path: includes these preparation files even before they are Git-tracked.
# The release downloads have fixed hashes in flake.nix.
nix --extra-experimental-features 'nix-command flakes' build \
  "path:$toolchain_dir#toolchain" --out-link "$repo_dir/bin/lec-toolchain"

for tool in mill firtool eqy sby yosys yosys-smtbmc yosys-abc z3 java javac; do
  if [[ ! -x "$repo_dir/bin/lec-toolchain/bin/$tool" ]]; then
    echo "Missing executable in toolchain: $tool" >&2
    exit 1
  fi
  destination="$repo_dir/bin/$tool"
  target="lec-toolchain/bin/$tool"
  if [[ -e "$destination" || -L "$destination" ]]; then
    if [[ ! -L "$destination" || "$(readlink -- "$destination")" != "$target" ]]; then
      echo "Preserving existing $destination; move it aside before installing." >&2
      exit 1
    fi
  else
    ln -s -- "$target" "$destination"
  fi
done

# Prove that the checked Scala/Chisel path needs no live Maven repository.
COURSIER_REPOSITORIES="file://$repo_dir/bin/lec-toolchain/maven2" \
  PATH="$repo_dir/bin:$PATH" python3 "$toolchain_dir/check.py" --mill-build
