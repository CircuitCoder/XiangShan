{
  description = "XiangShan SLEC toolchain: pinned elaboration tools and matching formal tools";

  inputs = {
    # Exact revision from this machine's cached nixpkgs registry. Keep the proof
    # tools from one package set; do not mix the host's Yosys with this SBY.
    nixpkgs.url = "https://releases.nixos.org/nixpkgs/nixpkgs-26.11pre1100332.901c54c08bdc/nixexprs.tar.zst";
  };

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      jdk = pkgs.jdk17_headless;
      scalaDeps = import ./scala-deps.nix { inherit pkgs; };

      mill = pkgs.stdenvNoCC.mkDerivation {
        pname = "mill";
        version = "0.12.3";
        src = pkgs.fetchurl {
          # Full distribution, rather than the small bootstrap downloader.
          url = "https://repo.maven.apache.org/maven2/com/lihaoyi/mill-dist/0.12.3/mill-dist-0.12.3.jar";
          hash = "sha256-M2NGCR7cabJIwwbKUarMtz2IjYv1Jc5F4C34nbRJtB0=";
        };
        dontUnpack = true;
        nativeBuildInputs = [ pkgs.makeWrapper ];
        dontConfigure = true;
        dontBuild = true;
        # Preserve the shell/JAR polyglot byte-for-byte.
        dontPatchShebangs = true;
        installPhase = ''
          runHook preInstall
          install -Dm755 "$src" "$out/libexec/mill-dist"
          makeWrapper ${pkgs.bash}/bin/bash "$out/bin/mill" \
            --add-flags "$out/libexec/mill-dist" \
            --set JAVA_HOME ${jdk.home} \
            --set-default COURSIER_REPOSITORIES "file://${scalaDeps}/maven2|https://repo.maven.apache.org/maven2" \
            --prefix PATH : ${jdk}/bin
          runHook postInstall
        '';
      };

      firtool = pkgs.stdenvNoCC.mkDerivation {
        pname = "firtool";
        version = "1.62.1";
        src = pkgs.fetchurl {
          url = "https://github.com/llvm/circt/releases/download/firtool-1.62.1/firrtl-bin-linux-x64.tar.gz";
          hash = "sha256-w4gVhQ5javniWzHlGXCaUZ70r5cX3Rx7c+26wkDnI3A=";
        };
        nativeBuildInputs = [ pkgs.autoPatchelfHook ];
        buildInputs = [ pkgs.stdenv.cc.cc.lib pkgs.zlib pkgs.ncurses ];
        dontConfigure = true;
        dontBuild = true;
        installPhase = ''
          runHook preInstall
          mkdir -p "$out"
          # unpackPhase has entered the archive's root directory.
          cp -R ./. "$out/"
          test -x "$out/bin/firtool"
          runHook postInstall
        '';
      };

      eqyPython = pkgs.python3.withPackages (ps: [ ps.click ]);
      eqy = pkgs.stdenv.mkDerivation {
        pname = "eqy";
        version = "0.70";
        # The v0.70 tag explicitly matches this package set's Yosys 0.70.
        src = pkgs.fetchzip {
          url = "https://github.com/YosysHQ/eqy/archive/7a92d8441aa442dd1b5543458d6f1060a8f85dd1.tar.gz";
          hash = "sha256-AxYEYGz1WlILFFi3MSdprKWHqbj+1e5nPvzj2HEvBH8=";
        };
        nativeBuildInputs = [ pkgs.yosys eqyPython pkgs.makeWrapper ];
        makeFlags = [ "PREFIX=$(out)" "YOSYS_CONFIG=${pkgs.yosys}/bin/yosys-config" ];
        postPatch = ''
          echo v0.70 > .gittag
          substituteInPlace src/eqy.py \
            --replace-fail '#!/usr/bin/env python3' '#!${eqyPython}/bin/python3'
        '';
        postInstall = ''
          wrapProgram "$out/bin/eqy" --prefix PATH : ${pkgs.lib.makeBinPath [
            pkgs.yosys pkgs.sby pkgs.z3 pkgs.gnumake
          ]}
        '';
        enableParallelBuilding = true;
      };

      toolchain = pkgs.symlinkJoin {
        name = "xiangshan-lec-toolchain";
        # JDK's top-level bin is a directory symlink; join its actual home so
        # symlinkJoin includes java/javac along with the other executables.
        paths = [ mill firtool scalaDeps jdk.home pkgs.yosys pkgs.sby pkgs.z3 eqy ];
      };
    in {
      packages.${system} = {
        inherit mill firtool toolchain eqy;
        scala-deps = scalaDeps;
        inherit (pkgs) yosys sby z3;
        default = toolchain;
      };
      devShells.${system}.default = pkgs.mkShell {
        packages = [ toolchain pkgs.python3 pkgs.git pkgs.gnumake pkgs.curl ];
        JAVA_HOME = jdk.home;
        # chisel's resolver accepts a directory containing the firtool binary.
        CHISEL_FIRTOOL_PATH = "${firtool}/bin";
        shellHook = ''
          # Prefer this matching tool set over inherited host or repo binaries.
          export PATH="${toolchain}/bin:$PATH"
          export LEC_MILL="${mill}/bin/mill"
          export LEC_FIRTOOL="${firtool}/bin/firtool"
        '';
      };
    };
}
