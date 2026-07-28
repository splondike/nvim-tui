{
  description = "Use Neovim as a simple TUI via a stdin/stdout RPC";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
    in {
      packages.${system}.default = pkgs.python3Packages.buildPythonApplication {
        pname = "nvimtui";
        version = "1.0.0";
        src = ./.;

        format = "other";

        propagatedBuildInputs = [ pkgs.python3Packages.pynvim ];

        installPhase = ''
          runHook preInstall
          mkdir -p $out/bin
          install -m755 nvimtui.py $out/bin/nvimtui
          runHook postInstall
        '';
      };
    };
}
