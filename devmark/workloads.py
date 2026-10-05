from dataclasses import dataclass, field
from pathlib import Path
import json
import os
import platform
import shutil
import stat
import sys
import tomllib
import re
from typing import Callable

from .process import Runner, probe


ROOT = Path(__file__).resolve().parent.parent
REPOSITORIES = json.loads((ROOT / "repositories.json").read_text())
TESTS = {
    "git-clone-web": ("Git clone · dashboard", "web"),
    "git-clone-rust": ("Git clone · Bevy", "rust"),
    "git-clone-go": ("Git clone · Podman", "go"),
    "pnpm-install": ("pnpm install · offline", "web"),
    "vite-build": ("Vite build · dashboard", "web"),
    "cargo-build": ("Cargo build · Bevy breakout", "rust"),
    "go-build": ("Go build · Podman remote", "go"),
}
NO_DOWNLOAD = {"COREPACK_ENABLE_NETWORK": "0", "COREPACK_ENABLE_PROJECT_SPEC": "0", "GOTOOLCHAIN": "local"}


class Skipped(RuntimeError):
    pass


def remove_owned(path: Path, owner: Path) -> None:
    # All deletions are restricted to this invocation's own run directory.
    path = Path(os.path.abspath(path))
    owner = owner.resolve()
    if not path.parent.resolve().is_relative_to(owner):
        raise ValueError(f"Refusing cleanup outside run directory: {path}")
    if path.is_symlink():
        path.unlink()
    elif path.is_dir():
        def writable(function, target, error):
            if not isinstance(error, PermissionError):
                raise error
            os.chmod(target, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
            function(target)
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=writable)
        else:
            shutil.rmtree(path, onerror=lambda fn, target, exc: writable(fn, target, exc[1]))
    elif path.exists():
        path.unlink()


@dataclass
class Workload:
    command: list[str]
    cwd: Path
    env: dict[str, str]
    reset: Callable[[], None]
    metadata: dict = field(default_factory=dict)


class Suite:
    def __init__(self, cache: Path, run_dir: Path, runner: Runner, update: Callable[[str], None]) -> None:
        self.cache = cache
        self.run_dir = run_dir
        self.runner = runner
        self.update = update
        self.sources: dict[str, Path] = {}
        self.web_ready = False
        self.versions: dict[str, str] = {"python": platform.python_version()}

    def require(self, name: str, args: list[str] | None = None) -> str:
        executable = shutil.which(name)
        if executable is None:
            raise Skipped(f"Missing {name} on PATH.")
        version = probe([executable, *(args or ["--version"])], cwd=ROOT, env=NO_DOWNLOAD)
        if not version:
            raise Skipped(f"{name} is installed but cannot run with automatic toolchain downloads disabled.")
        self.versions[name] = version
        return executable

    def seed(self, key: str) -> Path:
        git = self.require("git")
        repository = REPOSITORIES[key]
        mirror = self.cache / "repositories" / key / f"{repository['commit']}.git"
        mirror.parent.mkdir(parents=True, exist_ok=True)
        commit = probe([git, "--git-dir", str(mirror), "rev-parse", "refs/heads/devmark"], env=NO_DOWNLOAD)
        if commit != repository["commit"]:
            self.update(f"Download {key} source snapshot · not timed")
            self.runner.run([git, "init", "--bare", str(mirror)], ROOT)
            self.runner.run([git, "--git-dir", str(mirror), "fetch", "--depth", "1", "--no-tags", repository["url"], repository["commit"]], ROOT)
            self.runner.run([git, "--git-dir", str(mirror), "update-ref", "refs/heads/devmark", repository["commit"]], ROOT)
            self.runner.run([git, "--git-dir", str(mirror), "symbolic-ref", "HEAD", "refs/heads/devmark"], ROOT)
        return mirror

    def clone_command(self, key: str, destination: Path) -> list[str]:
        mirror = self.seed(key)
        return [
            shutil.which("git"), "-c", "core.longpaths=true", "-c", "gc.auto=0",
            "clone", "--no-local", "--depth", "1", "--no-tags", "--branch", "devmark",
            mirror.as_uri(), str(destination),
        ]

    def source(self, key: str) -> Path:
        if key not in self.sources:
            destination = self.run_dir / "sources" / key
            destination.parent.mkdir(parents=True, exist_ok=True)
            self.runner.run(self.clone_command(key, destination), ROOT, NO_DOWNLOAD)
            self.sources[key] = destination
        return self.sources[key]

    def web(self) -> tuple[str, Path, Path]:
        self.require("node")
        node = re.search(r"v(\d+)\.(\d+)\.(\d+)", self.versions["node"])
        if node:
            version = tuple(map(int, node.groups()))
            if not (version[0] == 20 and version >= (20, 19, 0)) and version < (22, 12, 0):
                raise Skipped(f"Vite 7 requires Node 20.19.x or Node 22.12 or newer; installed: {self.versions['node']}.")
        pnpm = self.require("pnpm")
        source = self.source("web")
        workspace = source / "pnpm-workspace.yaml"
        if not workspace.exists():
            major = int(self.versions["pnpm"].split(".")[0])
            workspace.write_text(
                "allowBuilds:\n  esbuild: true\n" if major >= 11 else "onlyBuiltDependencies:\n  - esbuild\n",
                encoding="utf-8",
            )
        store = self.cache / "pnpm-store"
        if not self.web_ready:
            self.update("Download dashboard dependencies · not timed")
            lock = source / "pnpm-lock.yaml"
            cached_lock = self.cache / "locks" / f"web-{REPOSITORIES['web']['commit']}.yaml"
            if not lock.exists() and cached_lock.exists():
                shutil.copyfile(cached_lock, lock)
            self.runner.run([
                pnpm, "install", "--frozen-lockfile" if lock.exists() else "--no-frozen-lockfile", "--store-dir", str(store),
                "--side-effects-cache=false",
            ], source, NO_DOWNLOAD | {"CI": "true"})
            if not cached_lock.exists():
                cached_lock.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(lock, cached_lock)
            self.web_ready = True
        return pnpm, source, store

    def prepare(self, test: str) -> Workload:
        if test.startswith("git-clone-"):
            key = TESTS[test][1]
            destination = self.run_dir / f"{test}-checkout"
            command = self.clone_command(key, destination)
            return Workload(command, ROOT, NO_DOWNLOAD, lambda: remove_owned(destination, self.run_dir), {
                "clone_transport": "file://, no hardlinks or alternates", "history_depth": 1,
            })
        if test == "pnpm-install":
            pnpm, source, store = self.web()
            return Workload([
                pnpm, "install", "--offline", "--frozen-lockfile", "--store-dir", str(store),
                "--side-effects-cache=false",
            ], source, NO_DOWNLOAD | {"CI": "true", "npm_config_offline": "true"},
                lambda: remove_owned(source / "node_modules", self.run_dir),
                {"dependency_cache": "warm local pnpm store", "node_modules": "removed before each run"})
        if test == "vite-build":
            pnpm, source, _ = self.web()
            # pnpm-install may have failed after deleting node_modules.
            if not (source / "node_modules" / ".bin" / "vite").exists() and not (source / "node_modules" / ".bin" / "vite.cmd").exists():
                self.web_ready = False
                self.web()
            def reset() -> None:
                for relative in ("dist", ".output", ".nitro", ".tanstack", "node_modules/.vite", "node_modules/.cache"):
                    remove_owned(source / relative, self.run_dir)
            return Workload([pnpm, "exec", "vite", "build"], source,
                NO_DOWNLOAD | {"CI": "true", "npm_config_offline": "true"}, reset,
                {"profile": "production", "dependency_cache": "installed node_modules", "output_cache": "removed before each run"})
        if test == "cargo-build":
            return self.rust()
        if test == "go-build":
            return self.go()
        raise ValueError(f"Unknown test: {test}")

    def rust(self) -> Workload:
        cargo = self.require("cargo")
        self.require("rustc")
        system = platform.system()
        if system == "Windows":
            for tool in ("cl", "link", "rc"):
                if shutil.which(tool) is None:
                    raise Skipped(f"Missing {tool}. Use a Visual Studio developer shell with the Windows SDK.")
        else:
            self.require("cc")
            if system == "Darwin" and not probe(["xcrun", "--show-sdk-path"]):
                raise Skipped("Missing macOS SDK. Install Xcode Command Line Tools.")
            if system == "Linux":
                self.require("pkg-config")
                missing = [library for library in ("alsa", "libudev", "wayland-client", "xkbcommon") if probe(["pkg-config", "--exists", library]) is None]
                if missing:
                    raise Skipped(f"Missing Bevy native development libraries: {', '.join(missing)}.")
        source = self.source("rust")
        env = NO_DOWNLOAD | {
            "CARGO_TARGET_DIR": str(self.run_dir / "cargo-target"),
            "CARGO_INCREMENTAL": "0", "RUSTC_WRAPPER": "", "RUSTC_WORKSPACE_WRAPPER": "",
            "CCACHE_DISABLE": "1", "SCCACHE_DISABLE": "1",
        }
        if shutil.which("rustup"):
            active = probe(["rustup", "show", "active-toolchain"], cwd=ROOT)
            if not active:
                raise Skipped("No installed active Rust toolchain.")
            env["RUSTUP_TOOLCHAIN"] = active.split()[0]
        version = probe(["rustc", "--version"], cwd=source, env=env)
        if not version:
            raise Skipped("The installed Rust compiler cannot run in the Bevy checkout.")
        self.versions["rustc"] = version
        required = tomllib.loads((source / "Cargo.toml").read_text())["package"]["rust-version"]
        match = re.search(r"rustc (\d+)\.(\d+)\.(\d+)", version)
        if match and tuple(map(int, match.groups())) < tuple(map(int, required.split("."))):
            raise Skipped(f"Bevy requires Rust {required} or newer; installed: {version}.")
        # Bevy does not ship a Cargo.lock. Keep the first resolution across runs.
        self.update("Resolve and download Bevy dependencies · not timed")
        lock = source / "Cargo.lock"
        cached_lock = self.cache / "locks" / f"bevy-{REPOSITORIES['rust']['commit']}.lock"
        if cached_lock.exists():
            shutil.copyfile(cached_lock, lock)
        elif not lock.exists():
            self.runner.run([cargo, "generate-lockfile"], source, env)
            cached_lock.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(lock, cached_lock)
        self.runner.run([cargo, "fetch", "--locked"], source, env)
        command = [cargo, "build", "--example", "breakout", "--locked", "--offline"]
        return Workload(command, source, env | {"CARGO_NET_OFFLINE": "true"},
            lambda: remove_owned(self.run_dir / "cargo-target", self.run_dir), {
                "example": "breakout", "profile": "dev", "features": "upstream defaults",
                "incremental": "disabled", "dependency_cache": "warm Cargo registry and git cache",
                "output_cache": "entire target directory removed before each run",
            })

    def go(self) -> Workload:
        go = self.require("go", ["version"])
        source = self.source("go")
        host = probe([go, "env", "GOHOSTOS", "GOHOSTARCH"], cwd=ROOT, env=NO_DOWNLOAD)
        if not host or len(host.splitlines()) != 2:
            raise Skipped("Cannot determine the installed Go toolchain's native target.")
        goos, goarch = host.splitlines()
        target = self.run_dir / "go-cache"
        binary = self.run_dir / ("podman-remote.exe" if goos == "windows" else "podman-remote")
        env = NO_DOWNLOAD | {
            "GOOS": goos, "GOARCH": goarch, "CGO_ENABLED": "0", "GOFLAGS": "",
            "GOENV": "off", "GOWORK": "off", "GOCACHE": str(target),
        }
        required = re.search(r"^go (\d+)\.(\d+)(?:\.(\d+))?", (source / "go.mod").read_text(), re.M)
        installed = re.search(r"go(\d+)\.(\d+)(?:\.(\d+))?", self.versions["go"])
        if required and installed:
            need = tuple(int(part or 0) for part in required.groups())
            have = tuple(int(part or 0) for part in installed.groups())
            if have < need:
                raise Skipped(f"Podman requires Go {'.'.join(map(str, need))} or newer; installed: {self.versions['go']}.")
        self.update("Download Podman dependencies · not timed")
        # Modules are fetched even though upstream currently also ships vendor/.
        self.runner.run([go, "mod", "download"], source, env)
        def reset() -> None:
            remove_owned(target, self.run_dir)
            remove_owned(binary, self.run_dir)
        return Workload([
            go, "build", "-mod=readonly", "-trimpath", "-tags",
            "remote exclude_graphdriver_btrfs containers_image_openpgp", "-o", str(binary), "./cmd/podman",
        ], source, env | {"GOPROXY": "off", "GOSUMDB": "off"}, reset, {
            "target": "Podman remote client", "goos": goos, "goarch": goarch,
            "cgo": "disabled", "dependency_cache": "warm Go module cache",
            "output_cache": "isolated GOCACHE and binary removed before each run, including cached stdlib",
        })
