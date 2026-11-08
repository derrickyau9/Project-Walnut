"""Isolation/provenance regression tests; no network or BSD build required."""

import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock

from backends import netbsd
from backends import revise


class BackendGuards(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="daos-backend-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def fixture(self):
        profile = self.root / "profile"
        profile.mkdir()
        lock = {"format": netbsd.LOCK_FORMAT, "upstream": {"name": "NetBSD", "release": "11.1"},
                "archives": [{"name": name, "url": "https://cdn.netbsd.org/pub/NetBSD/NetBSD-11.1/source/sets/" + name,
                              "sha512": "0" * 128, "extract_prefix": "usr/src"}
                             for name in sorted(netbsd.REQUIRED_ARCHIVES)]}
        lock_path = profile / "sources.lock.json"
        lock_path.write_text(json.dumps(lock))
        (profile / "DAOS_TEST").write_text('include "GENERIC64"\n')
        (profile / "mk.conf").write_text("MKX11=no\n")
        path = profile / "profile.json"
        path.write_text(json.dumps({"format": netbsd.PROFILE_FORMAT, "name": "daos-test",
                                   "backend": "netbsd", "source_lock": "sources.lock.json",
                                   "target": {"machine": "evbarm", "architecture": "aarch64"},
                                   "kernel": {"name": "DAOS_TEST", "config": "DAOS_TEST"},
                                   "world": {"mk_conf": "mk.conf"}}))
        workspace = netbsd._initialize(self.root / "work", netbsd._hash(lock_path))
        source = workspace / "src"
        for anchor in netbsd.SOURCE_ANCHORS:
            target = source / anchor
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# fixture " + anchor + "\n")
        netbsd._write_json(workspace / "state" / "fetch.json",
                           {"status": "success", "lock_sha256": netbsd._hash(lock_path),
                            "source_state": netbsd._source_state(workspace)})
        return path, workspace

    def archive(self, entries):
        archive = self.root / (str(len(list(self.root.iterdir()))) + ".tgz")
        with tarfile.open(archive, "w:gz") as stream:
            for name, data, link in entries:
                info = tarfile.TarInfo(name)
                if link is not None:
                    info.type = tarfile.SYMTYPE
                    info.linkname = link
                    stream.addfile(info)
                else:
                    info.size = len(data)
                    info.mode = 0o644
                    stream.addfile(info, io.BytesIO(data))
        return archive

    def test_rejects_live_paths_and_symlink_workspaces(self):
        for dangerous in ("/", "/usr", "/usr/local/daos", "/home", "/mnt/f/daos/work"):
            with self.subTest(dangerous=dangerous), self.assertRaises(netbsd.BackendError):
                netbsd._workspace(dangerous)
        real = self.root / "real"
        real.mkdir()
        link = self.root / "link"
        link.symlink_to(real, target_is_directory=True)
        with self.assertRaises(netbsd.BackendError):
            netbsd._workspace(link / "work")

    def test_nonempty_unowned_directory_is_not_adopted(self):
        workspace = self.root / "foreign"
        workspace.mkdir()
        valuable = workspace / "valuable"
        valuable.write_text("keep")
        with self.assertRaises(netbsd.BackendError):
            netbsd._initialize(workspace, "0" * 64)
        self.assertEqual(valuable.read_text(), "keep")
        self.assertFalse((workspace / "workspace.json").exists())

    def test_source_set_path_traversal_and_symlink_escape_are_rejected(self):
        archive_spec = {"name": "src.tgz", "extract_prefix": "usr/src"}
        for entries in ([('usr/src/../../../escape', b'bad', None)],
                        [('usr/src/link', b'', '../../../escape')]):
            with self.subTest(entries=entries):
                out = self.root / ("extract-" + str(len(list(self.root.iterdir()))))
                out.mkdir()
                with self.assertRaises(netbsd.BackendError):
                    netbsd._extract_archive(archive_spec, self.archive(entries), out)
                self.assertFalse((self.root / "escape").exists())

    def test_source_sets_cannot_overwrite_conflicting_files(self):
        out = self.root / "extract"
        out.mkdir()
        spec = {"name": "src.tgz", "extract_prefix": "usr/src"}
        netbsd._extract_archive(spec, self.archive([("usr/src/file", b"original", None)]), out)
        with self.assertRaises(netbsd.BackendError):
            netbsd._extract_archive(spec, self.archive([("usr/src/file", b"replaced", None)]), out)
        self.assertEqual((out / "usr/src/file").read_bytes(), b"original")

    def test_content_mutation_with_preserved_size_and_mtime_is_detected(self):
        profile, workspace = self.fixture()
        target = workspace / "src" / "BUILDING"
        info = target.stat()
        original = target.read_bytes()
        target.write_bytes(bytes([original[0] ^ 1]) + original[1:])
        os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))
        with self.assertRaises(netbsd.BackendError):
            netbsd.configure(profile, workspace)

    def test_changed_profile_cannot_reuse_objects(self):
        profile, workspace = self.fixture()
        netbsd.configure(profile, workspace)
        (profile.parent / "mk.conf").write_text("MKX11=yes\n")
        with self.assertRaises(netbsd.BackendError):
            netbsd.build_base(profile, workspace, stage="tools")
        self.assertFalse((workspace / "state/tools.json").exists())

    def test_plan_has_no_side_effects(self):
        profile, workspace = self.fixture()
        missing = self.root / "never-created"
        result = netbsd.plan(profile, missing, stage="tools")
        self.assertFalse(result["executed"])
        self.assertEqual(result["status"], "planned")
        self.assertFalse(missing.exists())

    def test_failed_command_never_creates_success_state(self):
        profile, workspace = self.fixture()
        netbsd.configure(profile, workspace)
        with mock.patch.object(netbsd, "_run", return_value=37):
            with self.assertRaises(netbsd.BackendError):
                netbsd.build_base(profile, workspace, stage="tools")
        self.assertFalse((workspace / "state/tools.json").exists())
        receipt = json.loads(next((workspace / "receipts").glob("tools-*.json")).read_text())
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["returncode"], 37)

    def test_exit_zero_without_toolchain_is_not_success(self):
        profile, workspace = self.fixture()
        netbsd.configure(profile, workspace)
        with mock.patch.object(netbsd, "_run", return_value=0):
            with self.assertRaises(netbsd.BackendError):
                netbsd.build_base(profile, workspace, stage="tools")
        self.assertFalse((workspace / "state/tools.json").exists())
        receipt = json.loads(next((workspace / "receipts").glob("tools-*.json")).read_text())
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["returncode"], 0)


class RevisionGuards(unittest.TestCase):
    setUp = BackendGuards.setUp
    fixture = BackendGuards.fixture

    def test_kernel_revision_reuses_verified_tools_and_preserves_execution_receipt(self):
        profile, workspace = self.fixture()
        original_config = netbsd.configure(profile, workspace)
        installed_tool = workspace / "tools" / "verified-test-tool"
        installed_tool.write_text("fixture tool bytes")
        receipt_path = workspace / "receipts" / "original-tools.json"
        original = {"format": netbsd.FORMAT, "stage": "tools", "operation": "build_base",
                    "status": "success", "executed": True,
                    "input_hash": original_config["input_hash"], "inputs": original_config["inputs"],
                    "command": netbsd._command(netbsd._profile_inputs(profile), workspace, 12, "tools"),
                    "outputs": {"tool": netbsd._file_receipt(installed_tool, workspace)},
                    "receipt": str(receipt_path)}
        netbsd._write_json(receipt_path, original)
        netbsd._write_json(workspace / "state/tools.json", original)
        original_hash = netbsd._hash(receipt_path)
        kernel_object = workspace / "obj/kernels/DAOS_TEST/netbsd"
        kernel_object.parent.mkdir(parents=True)
        kernel_object.write_text("old kernel object")
        (profile.parent / "DAOS_TEST").write_text('include "./GENERIC64"\n')
        review = revise.plan(profile, workspace)
        self.assertFalse(review["executed"])
        self.assertEqual(review["reusable_stages"][0]["stage"], "tools")
        result = revise.apply(profile, workspace, revision_hash=review["revision_hash"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(netbsd._hash(receipt_path), original_hash)
        reused = netbsd._read_json(workspace / "state/tools.json")
        self.assertFalse(reused["executed"])
        self.assertEqual(reused["inherited_from"], str(receipt_path))
        self.assertEqual((Path(result["archive"]) / "kernel-objects/DAOS_TEST/netbsd").read_text(), "old kernel object")
        self.assertFalse(kernel_object.exists())
        current = netbsd.build_base(profile, workspace, stage="tools")
        self.assertFalse(current["executed"])

    def test_world_flag_changes_cannot_inherit_toolchain(self):
        profile, workspace = self.fixture()
        netbsd.configure(profile, workspace)
        (profile.parent / "mk.conf").write_text("MKX11=yes\n")
        with self.assertRaises(netbsd.BackendError):
            revise.plan(profile, workspace)
        self.assertTrue((workspace / "state/configure.json").exists())


if __name__ == "__main__":
    unittest.main()
