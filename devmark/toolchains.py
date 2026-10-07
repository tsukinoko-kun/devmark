import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import tarfile
import tempfile
import tomllib
from typing import Callable
import urllib.error
import urllib.request

from .process import Runner, probe


PLATFORMS = {
    ("Windows", "x86_64"): ("win-64", "x86_64-pc-windows-gnu", "90085767d811e08a0fe240f7cb7a713915e1e5f24e3b0acfad9b37daa64c98fe"),
    ("Linux", "x86_64"): ("linux-64", "x86_64-unknown-linux-gnu", "9496f94a8b78c536573c93d946ec9bba74bd9ff79ee55aaa4b546e30db8f511b"),
    ("Linux", "arm64"): ("linux-aarch64", "aarch64-unknown-linux-gnu", "0289350c2c89f7410554041e25b444cce30df326b227e70ead3e4a1e0b316286"),
    ("Darwin", "arm64"): ("osx-arm64", "aarch64-apple-darwin", "aa23d0e01d6f492f43aa86720c0f4c8db91978b81f8af46a852f6c4fcf6737d5"),
}
PINS = {"git": "2.51.1", "nodejs": "22.13.0", "pnpm": "10.11.0", "go": "1.26.3"}
RUST_VERSION = "1.98.0"
RUST_MANIFEST_SHA256 = "3f7d139b73bbbd0004ef6e58b430831c68cdad2b1f64ee2eb35d54c09199489a"
SDK_URL = "https://github.com/joseluisq/macosx-sdks/releases/download/14.5/MacOSX14.5.sdk.tar.xz"
SDK_SHA256 = "6e146275d19f027faa2e8354da5e0267513abf013b8f16ad65a231653a2b1c5d"


class ToolchainError(RuntimeError):
    pass


def packages_for(subdir: str, tests: set[str]) -> list[str]:
    specs = [f"git={PINS['git']}"]
    if tests & {"pnpm-install", "vite-build"}:
        specs.extend(f"{name}={PINS[name]}" for name in ("nodejs", "pnpm"))
    if "go-build" in tests:
        specs.extend([f"go={PINS['go']}", "_go_select=2.2.0=nocgo"])
    if "cargo-build" in tests:
        if subdir.startswith("linux"):
            specs.extend([
                f"gcc_{subdir}=14.2.0", f"gxx_{subdir}=14.2.0", "pkg-config=0.29.2",
                "alsa-lib=1.2.14", "libudev=257.4", "libxkbcommon=1.8.1", "wayland=1.23.1",
                "xorg-libx11=1.8.12", "xorg-libxcursor=1.2.3", "xorg-libxi=1.8.2", "xorg-libxrandr=1.5.4",
            ])
        elif subdir == "osx-arm64":
            specs.extend(["clang_osx-arm64=19.1.7", "clangxx_osx-arm64=19.1.7", "sdkroot_env_osx-arm64=14.4"])
        else:
            specs.append("gcc_win-64=15.2.0")
    return specs


def host_platform() -> tuple[str, str, str]:
    machine = platform.machine().lower()
    machine = {"amd64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    try:
        return PLATFORMS[platform.system(), machine]
    except KeyError:
        raise ToolchainError(
            f"Unsupported platform: {platform.system()} {platform.machine()}. "
            "Supported: Windows x86-64, Linux x86-64 or arm64, and macOS arm64."
        ) from None


def download(url: str, sha256: str, cache: Path) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / sha256
    if destination.is_file():
        with destination.open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() == sha256:
                return destination
    with tempfile.NamedTemporaryFile(dir=cache, delete=False) as partial:
        temporary = Path(partial.name)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "devmark"})
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output)
        with temporary.open("rb") as source:
            actual = hashlib.file_digest(source, "sha256").hexdigest()
        if actual != sha256:
            raise ToolchainError(f"SHA-256 mismatch for {url}: expected {sha256}, got {actual}.")
        temporary.replace(destination)
    except (OSError, urllib.error.URLError) as error:
        raise ToolchainError(f"Cannot download {url}: {error}") from error
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def extract(archive: Path, destination: Path) -> None:
    # The data filter also validates link targets and rejects special files.
    if not hasattr(tarfile, "data_filter"):
        raise ToolchainError("Archive extraction requires Python 3.11.8 or newer. Update Python, or run with uv.")
    with tarfile.open(archive) as source:
        source.extractall(destination, filter="data")


class Toolchains:
    def __init__(self, cache: Path, run_dir: Path, runner: Runner, update: Callable[[str], None]) -> None:
        self.cache = cache
        self.directory = run_dir / "toolchains"
        self.runner = runner
        self.update = update
        self.env: dict[str, str] = {}

    def install(self, tests: set[str]) -> None:
        subdir, target, checksum = host_platform()
        self.directory.mkdir(parents=True)
        downloads = self.cache / "downloads"
        self.update("Download portable toolchain installer · not timed")
        suffix = ".exe" if subdir == "win-64" else ""
        url = f"https://github.com/mamba-org/micromamba-releases/releases/download/2.3.3-0/micromamba-{subdir}{suffix}"
        mamba = self.directory / f"micromamba{suffix}"
        shutil.copyfile(download(url, checksum, downloads), mamba)
        mamba.chmod(0o755)
        prefix = self.directory / "environment"
        specs = packages_for(subdir, tests)
        env = {
            "MAMBA_ROOT_PREFIX": str(self.directory / "mamba"),
            "CONDA_PKGS_DIRS": str(self.cache / "packages"),
            "PATH": os.defpath if os.name != "nt" else os.pathsep.join([
                str(Path(os.environ["SystemRoot"]) / "System32"), os.environ["SystemRoot"],
            ]),
            "CC": "", "CXX": "", "CPP": "", "LIBRARY_PATH": "", "CPATH": "",
            "CMAKE_PREFIX_PATH": "", "CMAKE_MAKE_PROGRAM": "", "PKG_CONFIG_PATH": "",
        }
        if "cargo-build" in tests and subdir == "osx-arm64":
            self.update("Download macOS 14.5 SDK · not timed")
            extract(download(SDK_URL, SDK_SHA256, downloads), self.directory)
            sdk = str(self.directory / "MacOSX14.5.sdk")
            env |= {"SDKROOT": sdk, "CONDA_BUILD_SYSROOT": sdk}
        self.update("Install pinned portable tools · not timed")
        self.runner.run([
            str(mamba), "--no-rc", "create", "--yes", "--prefix", str(prefix),
            "--override-channels", "--channel", "https://conda.anaconda.org/conda-forge", *specs,
        ], self.directory, env)
        # Capture activation once so it is outside all measured commands. Compiler
        # activation scripts provide the sysroot, linker, and native library paths.
        activated = probe([
            str(mamba), "--no-rc", "run", "--prefix", str(prefix), str(Path(sys.executable).resolve()),
            "-c", "import json, os; print(json.dumps(dict(os.environ)))",
        ], cwd=self.directory, env=env)
        if activated is None:
            raise ToolchainError(f"Cannot activate portable tools at {prefix}. See {self.runner.log_dir}.")
        self.env = json.loads(activated)
        self.env |= {
            "GOTOOLCHAIN": "local", "GOENV": "off", "GOROOT": str(prefix / "go"),
            "GOMODCACHE": str(self.cache / "go-modules"), "CARGO_HOME": str(self.cache / "cargo-home"),
            "COREPACK_ENABLE_NETWORK": "0", "COREPACK_ENABLE_PROJECT_SPEC": "0",
            "PNPM_MANAGE_PACKAGE_MANAGER_VERSIONS": "false",
        }
        # Avoid leaking a temporary installer location into subsequent commands.
        for name in ("MAMBA_ROOT_PREFIX", "CONDA_PKGS_DIRS"):
            self.env.pop(name, None)
        if "go-build" not in tests:
            self.env.pop("GOROOT", None)
        if "cargo-build" in tests:
            self.install_rust(target, downloads)
            compiler = self.env.get("CC_FOR_BUILD") or self.env.get("CC")
            if not compiler:
                compiler = shutil.which("gcc", path=self.env["PATH"])
            if not compiler:
                raise ToolchainError("Portable C compiler activation did not provide CC or gcc.")
            compiler = shutil.which(compiler, path=self.env["PATH"])
            if compiler is None or not Path(compiler).resolve().is_relative_to(self.directory.resolve()):
                raise ToolchainError(f"C compiler resolved outside the portable installation: {compiler}.")
            self.env["CC"] = compiler
            self.env["CXX"] = self.env.get("CXX_FOR_BUILD") or self.env.get("CXX", "")
            self.env[f"CARGO_TARGET_{target.upper().replace('-', '_')}_LINKER"] = compiler
            self.env["RUSTFLAGS"] = ""
            self.env["CARGO_ENCODED_RUSTFLAGS"] = ""
            if subdir.startswith("linux"):
                self.env["PKG_CONFIG_PATH"] = str(prefix / "lib" / "pkgconfig")
                self.env["PKG_CONFIG_LIBDIR"] = str(prefix / "lib" / "pkgconfig")
        # Retain the exact solved package builds with the run's dependency locks.
        locks = self.directory.parent / "locks"
        locks.mkdir(exist_ok=True)
        packages = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((prefix / "conda-meta").glob("*.json"))]
        (locks / "toolchains.json").write_text(json.dumps({
            "platform": subdir, "rust": RUST_VERSION if "cargo-build" in tests else None,
            "packages": [{key: package.get(key) for key in ("name", "version", "build", "url", "sha256")} for package in packages],
        }, indent=2) + "\n", encoding="utf-8")

    def install_rust(self, target: str, downloads: Path) -> None:
        self.update(f"Download Rust {RUST_VERSION} · not timed")
        manifest = download(
            f"https://static.rust-lang.org/dist/channel-rust-{RUST_VERSION}.toml", RUST_MANIFEST_SHA256, downloads,
        )
        packages = tomllib.loads(manifest.read_text(encoding="utf-8"))["pkg"]
        prefix = self.directory / "rust"
        for name in ("rustc", "cargo", "rust-std"):
            artifact = packages[name]["target"][target]
            archive = download(artifact["xz_url"], artifact["xz_hash"], downloads)
            staging = self.directory / "rust-unpack"
            extract(archive, staging)
            distribution = next(staging.iterdir())
            for component in (distribution / "components").read_text().splitlines():
                shutil.copytree(distribution / component, prefix, dirs_exist_ok=True)
            shutil.rmtree(staging)
        self.env["PATH"] = str(prefix / "bin") + os.pathsep + self.env["PATH"]
        self.env["RUSTC"] = str(prefix / "bin" / ("rustc.exe" if os.name == "nt" else "rustc"))

    def executable(self, name: str) -> str:
        executable = shutil.which(name, path=self.env.get("PATH", ""))
        if executable is None:
            raise ToolchainError(f"Portable installation is missing {name}: {self.directory}.")
        if not Path(executable).resolve().is_relative_to(self.directory.resolve()):
            raise ToolchainError(f"{name} resolved outside the portable installation: {executable}.")
        return executable
