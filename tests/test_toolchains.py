import hashlib
import io
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from devmark.process import Runner, probe
from devmark.toolchains import PINS, RUST_VERSION, ToolchainError, Toolchains, download, extract, host_platform
from devmark.workloads import Suite, remove_owned


class DownloadTests(unittest.TestCase):
    def test_verified_cache_is_reused_and_corrupt_cache_is_replaced(self):
        content = b"portable artifact"
        checksum = hashlib.sha256(content).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            with patch("urllib.request.urlopen", return_value=io.BytesIO(content)) as fetch:
                artifact = download("https://example.invalid/tool", checksum, cache)
            fetch.assert_called_once()
            with patch("urllib.request.urlopen", side_effect=AssertionError("Unexpected download")):
                self.assertEqual(download("https://example.invalid/tool", checksum, cache), artifact)
            artifact.write_bytes(b"corrupt")
            with patch("urllib.request.urlopen", return_value=io.BytesIO(content)):
                download("https://example.invalid/tool", checksum, cache)
            self.assertEqual(artifact.read_bytes(), content)

    def test_checksum_failure_does_not_publish_or_leave_partial_download(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            with patch("urllib.request.urlopen", return_value=io.BytesIO(b"wrong")):
                with self.assertRaisesRegex(ToolchainError, "SHA-256 mismatch"):
                    download("https://example.invalid/tool", "0" * 64, cache)
            self.assertEqual(list(cache.iterdir()), [])

    def test_archive_cannot_write_outside_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "tool.tar"
            with tarfile.open(archive, "w") as output:
                member = tarfile.TarInfo("../outside")
                member.size = 4
                output.addfile(member, io.BytesIO(b"oops"))
            with self.assertRaises(tarfile.FilterError):
                extract(archive, root / "installation")
            self.assertFalse((root / "outside").exists())


class PlatformTests(unittest.TestCase):
    def test_native_platform_aliases(self):
        for system, machine, expected in (
            ("Windows", "AMD64", "win-64"), ("Linux", "x86_64", "linux-64"),
            ("Linux", "aarch64", "linux-aarch64"), ("Darwin", "arm64", "osx-arm64"),
        ):
            with self.subTest(system=system, machine=machine), \
                    patch("platform.system", return_value=system), patch("platform.machine", return_value=machine):
                self.assertEqual(host_platform()[0], expected)
        with patch("platform.system", return_value="Linux"), patch("platform.machine", return_value="riscv64"):
            with self.assertRaisesRegex(ToolchainError, "Unsupported platform: Linux riscv64"):
                host_platform()

    def test_host_executable_is_never_used_as_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tools = Toolchains(root / "cache", root / "run", Runner(root / "logs"), lambda text: None)
            tools.env["PATH"] = os.environ["PATH"]
            with patch("shutil.which", return_value=str(root / "system" / "cargo")):
                with self.assertRaisesRegex(ToolchainError, "outside the portable installation"):
                    tools.executable("cargo")


@unittest.skipUnless(os.environ.get("DEVMARK_PORTABLE_TESTS") == "1", "Portable download integration tests are opt-in")
class PortableIntegrationTests(unittest.TestCase):
    def test_pinned_tools_build_without_host_toolchains(self):
        with tempfile.TemporaryDirectory(prefix="devmark portable ") as directory:
            root = Path(directory).resolve()
            run = root / "run"
            run.mkdir()
            cache = Path(os.environ.get("DEVMARK_TEST_CACHE", str(root / "cache"))).resolve()
            runner = Runner(run / "logs", timeout=1200)
            suite = Suite(cache, run, runner, lambda text: print(text, flush=True))
            try:
                # Deliberately unusable local compiler settings must not leak in.
                with patch.dict(os.environ, {"CC": "missing-host-cc", "CXX": "missing-host-cxx"}):
                    suite.install({"vite-build", "cargo-build", "go-build"})
                for name, expected in (("git", PINS["git"]), ("node", PINS["nodejs"]),
                                       ("pnpm", PINS["pnpm"]), ("go", PINS["go"]), ("cargo", RUST_VERSION)):
                    command = [suite.require(name, ["version"] if name == "go" else None), "version" if name == "go" else "--version"]
                    version = probe(command, env=suite.env)
                    self.assertIsNotNone(version, command)
                    self.assertIn(expected, version or "")
                source = run / "fixture"
                source.mkdir()
                # C and Rust both need the downloaded linker and platform SDK.
                (source / "hello.c").write_text('int main(void) { return 0; }\n')
                runner.run([suite.env["CC"], "hello.c", "-o", "hello.exe"], source, suite.env)
                (source / "Cargo.toml").write_text('[package]\nname="portable-fixture"\nversion="0.1.0"\nedition="2024"\n')
                (source / "src").mkdir()
                (source / "src/main.rs").write_text('fn main() { println!("portable"); }\n')
                runner.run([suite.require("cargo"), "build", "--offline"], source, suite.env)
                (source / "go.mod").write_text('module fixture\ngo 1.26.3\n')
                (source / "main.go").write_text('package main\nfunc main() {}\n')
                runner.run([suite.require("go", ["version"]), "build", "-o", "go-fixture.exe", "."], source,
                           suite.env | {"CGO_ENABLED": "0"})
                (source / "package.json").write_text('{"name":"portable-fixture","private":true}')
                runner.run([suite.require("pnpm"), "install", "--offline"], source, suite.env | {"CI": "true"})
                runner.run([suite.require("git"), "init", str(source / "repository")], source, suite.env)
                if host_platform()[0].startswith("linux"):
                    pkg_config = suite.tools.executable("pkg-config")
                    runner.run([pkg_config, "--exists", "alsa", "libudev", "xkbcommon", "wayland-client"], source, suite.env)
            finally:
                remove_owned(run / "toolchains", run)


if __name__ == "__main__":
    unittest.main()
