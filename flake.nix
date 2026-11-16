{
  description = "Leansail: a Rust host and Lean backend for the Lean language server";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-parts.url = "github:hercules-ci/flake-parts";
    treefmt-nix.url = "github:numtide/treefmt-nix";
    leanfran.url = "github:conao3/lean-leanfran/1bb464c6d419da8037698053f52dd23476a042d9";
    leanfran.inputs.nixpkgs.follows = "nixpkgs";
    touchstone.url = "github:conao3/rust-lsp-touchstone/84764fdb6850811dca2dc02bd9b9540c89ba2e18";
    touchstone.inputs.nixpkgs.follows = "nixpkgs";
  };

  outputs =
    inputs:
    inputs.flake-parts.lib.mkFlake { inherit inputs; } {
      imports = [ inputs.treefmt-nix.flakeModule ];
      systems = [
        "x86_64-linux"
        "aarch64-darwin"
      ];
      perSystem =
        { pkgs, system, ... }:
        let
          backend = pkgs.stdenv.mkDerivation {
            pname = "leansail-backend";
            version = "0.1.0";
            src = ./native;
            nativeBuildInputs = [ pkgs.lean4 ];
            buildPhase = ''
              lake build
            '';
            installPhase = ''
              install -Dm755 .lake/build/bin/leansail-backend $out/bin/leansail-backend
              mkdir -p $out/src
              ln -s ${pkgs.lean4}/src/lean $out/src/lean
            '';
          };
          host = pkgs.rustPlatform.buildRustPackage {
            pname = "leansail";
            version = "0.1.0";
            src = pkgs.lib.cleanSource ./.;
            cargoLock.lockFile = ./Cargo.lock;
            nativeBuildInputs = [ pkgs.makeWrapper ];
            postInstall = ''
              wrapProgram $out/bin/leansail \
                --set LEANSAIL_BACKEND ${backend}/bin/leansail-backend \
                --set LEAN_SYSROOT ${pkgs.lean4}
            '';
            meta = {
              mainProgram = "leansail";
              license = pkgs.lib.licenses.asl20;
              platforms = pkgs.lib.platforms.unix;
            };
          };
        in
        {
          packages.default = host;
          packages.backend = backend;
          devShells.default = pkgs.mkShell {
            packages = [
              pkgs.cargo
              pkgs.rustc
              pkgs.rustfmt
              pkgs.clippy
              pkgs.gcc
              pkgs.lean4
              pkgs.gnumake
              pkgs.python3
              pkgs.ruff
              inputs.leanfran.packages.${system}.rust-compiler
              inputs.touchstone.packages.${system}.default
            ]
            ++ pkgs.lib.optionals pkgs.stdenv.hostPlatform.isLinux [ pkgs.procps ];
            LEANFRAN_CLI = "${inputs.leanfran.packages.${system}.rust-compiler}/bin/leanfran";
            LEAN_SYSROOT = "${pkgs.lean4}";
          };
          treefmt.programs.nixfmt.enable = true;
          treefmt.programs.rustfmt.enable = true;
        };
    };
}
