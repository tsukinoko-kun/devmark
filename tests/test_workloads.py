from pathlib import Path
import json
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from devmark.process import Runner
from devmark.workloads import Skipped, Suite, remove_owned


class CleanupTests(unittest.TestCase):
    def test_cleanup_keeps_dependency_cache_and_external_symlink_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            owned = root / "run"
            owned.mkdir()
            store = root / "store"
            store.mkdir()
            (store / "package").write_text("keep")
            output = owned / "output"
            output.mkdir()
            (output / "binary").write_text("remove")
            remove_owned(output, owned)
            self.assertFalse(output.exists())
            self.assertTrue((store / "package").exists())
            try:
                (owned / "link").symlink_to(store, target_is_directory=True)
            except OSError:
                return  # Windows may not permit unprivileged symlink creation.
            remove_owned(owned / "link", owned)
            self.assertTrue((store / "package").exists())

    def test_cleanup_refuses_parent_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                remove_owned(root, root)
            with self.assertRaises(ValueError):
                remove_owned(root.parent, root)

    def test_cleanup_refuses_dotdot_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            owned = root / "run"
            owned.mkdir()
            external = root / "external"
            external.mkdir()
            with self.assertRaises(ValueError):
                remove_owned(owned / ".." / "external", owned)
            self.assertTrue(external.exists())

    def test_missing_tool_is_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suite = Suite(root / "cache", root / "run", Runner(root / "logs"), lambda text: None)
            with patch("devmark.workloads.shutil.which", return_value=None):
                with self.assertRaisesRegex(Skipped, "Missing cargo"):
                    suite.require("cargo")


@unittest.skipUnless(shutil.which("git"), "Git not installed")
class LocalCloneTests(unittest.TestCase):
    def test_git_workload_clones_local_snapshot_without_shared_objects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            upstream = root / "upstream"
            upstream.mkdir()
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=upstream, text=True).strip()
            git("init", "--quiet")
            for contents in ("first", "second"):
                (upstream / "source").write_text(contents)
                git("add", "source")
                git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--quiet", "-m", contents)
            commit = git("rev-parse", "HEAD")
            owned = root / "run"
            owned.mkdir()
            suite = Suite(root / "cache", owned, Runner(owned / "logs", timeout=10), lambda text: None)
            with patch.dict("devmark.workloads.REPOSITORIES", {"web": {"url": upstream.as_uri(), "commit": commit}}):
                workload = suite.prepare("git-clone-web")
                workload.reset()
                suite.runner.run(workload.command, workload.cwd, workload.env)
                destination = owned / "git-clone-web-checkout"
                self.assertEqual((destination / "source").read_text(), "second")
                self.assertTrue((destination / ".git" / "shallow").exists())
                self.assertFalse((destination / ".git" / "objects" / "info" / "alternates").exists())
                for pack in (destination / ".git" / "objects" / "pack").glob("*.pack"):
                    self.assertEqual(pack.stat().st_nlink, 1)
                workload.reset()
                self.assertFalse(destination.exists())


@unittest.skipUnless(shutil.which("pnpm") and shutil.which("node"), "pnpm and Node not installed")
class PnpmLockTests(unittest.TestCase):
    def test_repository_without_lock_can_be_installed_offline_and_reuses_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            cache = root / "cache"
            dependency = root / "dependency"
            dependency.mkdir()
            (dependency / "package.json").write_text(json.dumps({
                "name": "esbuild", "version": "0.0.0",
                "scripts": {"postinstall": "node -e \"require('fs').writeFileSync('built', 'yes')\""},
            }))
            original = None
            for index in (1, 2):
                run = root / f"run-{index}"
                source = run / "web"
                source.mkdir(parents=True)
                (source / "package.json").write_text(json.dumps({
                    "name": "local-fixture", "version": "1.0.0", "private": True,
                    "dependencies": {"esbuild": "file:../../dependency"},
                }))
                suite = Suite(cache, run, Runner(run / "logs", timeout=30), lambda text: None)
                suite.require("pnpm")
                # A local tarball must be approved by its artifact path, not by
                # the registry package name used in the production dashboard.
                if int(suite.versions["pnpm"].split(".")[0]) >= 11:
                    (source / "pnpm-workspace.yaml").write_text("allowBuilds:\n  'esbuild@file:../../dependency': true\n")
                with patch.object(suite, "source", return_value=source):
                    workload = suite.prepare("pnpm-install")
                    lock = source / "pnpm-lock.yaml"
                    self.assertTrue(lock.exists())
                    if original is None:
                        original = lock.read_bytes()
                    else:
                        self.assertEqual(lock.read_bytes(), original)
                    workload.reset()
                    suite.runner.run(workload.command, workload.cwd, workload.env)
                    self.assertEqual((source / "node_modules" / "esbuild" / "built").read_text(), "yes")


if __name__ == "__main__":
    unittest.main()
