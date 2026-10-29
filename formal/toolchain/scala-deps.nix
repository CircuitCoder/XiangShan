# Fixed-output Maven mirror for Mill and the dependencies named in build.sc.
# Submodule-specific dependencies are audited after checking out their sources.
{ pkgs }:
let
  csNative = pkgs.stdenvNoCC.mkDerivation {
    name = "coursier-native-2.1.26";
    src = pkgs.fetchurl {
      url = "https://github.com/coursier/coursier/releases/download/v2.1.26/cs-x86_64-pc-linux.gz";
      hash = "sha256-NI43vCqMcGZA5rAyxFUaDgVafxU3SFtKkVQOnTWY7G0=";
    };
    dontUnpack = true;
    nativeBuildInputs = [ pkgs.autoPatchelfHook ];
    buildInputs = [ pkgs.stdenv.cc.cc.lib pkgs.zlib ];
    dontConfigure = true;
    dontBuild = true;
    installPhase = ''
      mkdir -p "$out/bin"
      gzip -dc "$src" > "$out/bin/cs"
      chmod +x "$out/bin/cs"
    '';
  };
in pkgs.stdenvNoCC.mkDerivation {
  name = "xiangshan-scala-deps";
  nativeBuildInputs = [ csNative pkgs.python3 ];
  impureEnvVars = pkgs.lib.fetchers.proxyImpureEnvVars;
  dontUnpack = true;
  dontConfigure = true;
  dontBuild = true;
  outputHashMode = "recursive";
  outputHashAlgo = "sha256";
  outputHash = "sha256-wUGmq8f75jy1C4wvjKXSDzw4PXpX9KsUTjQlQjpka6I=";
  installPhase = ''
    export COURSIER_CACHE="$TMPDIR/coursier"
    mapfile -t proxyOptions < <(python3 - <<'PY'
import os
from urllib.parse import urlparse
for scheme in ('http', 'https'):
    proxy = os.environ.get(scheme + '_proxy') or os.environ.get(scheme.upper() + '_PROXY')
    if proxy:
        parsed = urlparse(proxy)
        if parsed.hostname:
            print('-J-D' + scheme + '.proxyHost=' + parsed.hostname)
            print('-J-D' + scheme + '.proxyPort=' + str(parsed.port or 80))
PY
    )
    # Resolve roots separately so the mirror retains versions that a combined
    # resolution would evict but independent Mill dependency groups still need.
    while IFS= read -r dependency; do
      cs "''${proxyOptions[@]}" fetch --no-default \
        --repository https://repo.maven.apache.org/maven2 "$dependency"
    done <<'DEPENDENCIES'
com.lihaoyi:mill-scalalib-worker_2.13:0.12.3
com.lihaoyi:mill-runner-linenumbers_2.13:0.12.3
org.scala-lang:scala-compiler:2.13.15
com.lihaoyi:mill-scala-compiler-bridge_2.13.15:0.0.1
com.lihaoyi:mill-moduledefs_2.13:0.11.1
com.lihaoyi:scalac-mill-moduledefs-plugin_2.13.15:0.11.1
org.chipsalliance:chisel_2.13:6.7.0
org.chipsalliance:chisel-plugin_2.13.15:6.7.0
org.chipsalliance:firtool-resolver_2.13:1.3.0
de.tototec:de.tobiasroeser.mill.vcs.version_mill0.11_2.13:0.4.0
com.lihaoyi:mainargs_2.13:0.7.0
org.json4s:json4s-jackson_2.13:4.0.7
com.lihaoyi:sourcecode_2.13:0.4.2
edu.berkeley.cs:chiseltest_2.13:6.0.0
io.circe:circe-yaml_2.13:1.15.0
io.circe:circe-generic-extras_2.13:0.14.4
DEPENDENCIES
    mkdir -p "$out/maven2"
    cp -R "$COURSIER_CACHE/https/repo.maven.apache.org/maven2/." "$out/maven2/"
    find "$out" -type f ! -name '*.pom' ! -name '*.jar' -delete
  '';
}
