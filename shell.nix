{ pkgs ? import <nixpkgs> {} }:

let
  python = pkgs.python3.withPackages (ps: [
    ps.rich
    ps.psutil
    ps.py-cpuinfo
  ]);

  linuxLibs = pkgs.lib.optionals pkgs.stdenv.hostPlatform.isLinux (with pkgs; [
    alsa-lib
    libxkbcommon
    udev
    vulkan-loader
    wayland
    libx11
    libxcursor
    libxi
    libxrandr
  ]);
in
pkgs.mkShell {
  packages = [
    python
    pkgs.uv
    pkgs.git
    pkgs.nodejs
    pkgs.pnpm
    pkgs.rustc
    pkgs.cargo
    pkgs.go
    pkgs.pkg-config
  ] ++ pkgs.lib.optionals pkgs.stdenv.hostPlatform.isLinux [
    pkgs.pciutils
    pkgs.dmidecode
  ];

  # Compile-time only. Do not set LD_LIBRARY_PATH: Nix's glibc/alsa/udev
  # would then be injected into host binaries such as Cursor.
  buildInputs = linuxLibs;
}
