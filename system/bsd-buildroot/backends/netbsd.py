"""Unprivileged, source-only NetBSD backend.

Every workspace belongs to one source lock and one build configuration.  A
different lock or configuration requires a new workspace; failed runs retain
their logs and receipts.  No command in this module installs into the host.
The public ``plan`` function is deliberately non-executing; only ``fetch``,
``configure`` and ``build_base`` perform work.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tarfile
import time
import urllib.parse
import urllib.request
import uuid


class BackendError(RuntimeError):
    """An input, isolation, provenance, or real build check failed."""


FORMAT = "DaOS-netbsd-backend-v1"
LOCK_FORMAT = "DaOS-bsd-source-lock-v1"
PROFILE_FORMAT = "DaOS-bsd-build-profile-v1"
STAGES = ("tools", "world", "distribution", "kernel", "release", "all")
REQUIRED_ARCHIVES = {"src.tgz", "syssrc.tgz", "gnusrc.tgz", "sharesrc.tgz"}
SOURCE_ANCHORS = ("build.sh", "Makefile", "BUILDING", "sys/conf/osrelease.sh",
                  "share/mk/bsd.own.mk", "sys/arch/evbarm/conf/GENERIC64")
OWNED_DIRS = ("downloads", "state", "receipts", "logs", "tmp")
BUILD_DIRS = ("obj", "tools", "destdir", "release")


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _hash(path, algorithm="sha256"):
    h = hashlib.new(algorithm)
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode()).hexdigest()


def _read_json(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise BackendError(f"Required regular JSON file is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise BackendError(f"Invalid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BackendError(f"JSON object required: {path}")
    return value


def _write_json(path, value):
    path = Path(path)
    if path.is_symlink():
        raise BackendError(f"Refusing to overwrite symlink: {path}")
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temp.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def _no_symlink_components(path):
    path = Path(os.path.abspath(os.fspath(path)))
    for component in (path, *path.parents):
        if component.is_symlink():
            raise BackendError(f"Symlink path component is not allowed: {component}")
    return path


def _workspace(workspace):
    raw = Path(workspace).expanduser()
    if not raw.is_absolute():
        raise BackendError("Workspace must be an explicit absolute path")
    path = _no_symlink_components(raw)
    if os.name != "posix":
        raise BackendError("Run the NetBSD backend inside WSL/Linux on the F:-backed filesystem")
    # /home/user/project and /tmp/test/project are fine; live OS trees are not.
    protected = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc", "/dev",
                 "/proc", "/sys", "/run", "/boot", "/var", "/opt")
    if len(path.parts) < 4 or path == Path.home():
        raise BackendError(f"Workspace is too broad: {path}")
    for root in protected:
        if path == Path(root) or Path(root) in path.parents:
            raise BackendError(f"Workspace overlaps a protected host tree: {path}")
    if path == Path("/mnt") or Path("/mnt") in path.parents:
        raise BackendError("Use the F:-backed WSL ext4 filesystem, not a /mnt Windows mount")
    if path.exists() and not path.is_dir():
        raise BackendError(f"Workspace is not a directory: {path}")
    return path


def _inside(root, path):
    path = _no_symlink_components(path)
    if path == root or root not in path.parents:
        raise BackendError(f"Output escapes or replaces the workspace: {path}")
    return path


def _check_layout(workspace):
    for name in (*OWNED_DIRS, *BUILD_DIRS, "src", "xsrc", "config"):
        path = _inside(workspace, workspace / name)
        if path.exists() and not path.is_dir():
            raise BackendError(f"Expected a private directory: {path}")


def _lock_inputs(lock_path):
    path = _no_symlink_components(Path(lock_path).expanduser().absolute())
    lock = _read_json(path)
    if lock.get("format") != LOCK_FORMAT:
        raise BackendError(f"Expected {LOCK_FORMAT}: {path}")
    upstream = lock.get("upstream", {})
    if upstream.get("name") != "NetBSD" or not re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", str(upstream.get("release", ""))):
        raise BackendError("Lock must identify an official NetBSD release")
    archives = lock.get("archives")
    if not isinstance(archives, list):
        raise BackendError("Source lock archives must be a list")
    result, seen = [], set()
    for archive in archives:
        if not isinstance(archive, dict):
            raise BackendError("Invalid source archive record")
        name = archive.get("name", "")
        if name not in REQUIRED_ARCHIVES | {"xsrc.tgz"} or name in seen:
            raise BackendError(f"Unsupported or duplicate source archive: {name}")
        seen.add(name)
        expected_prefix = "usr/xsrc" if name == "xsrc.tgz" else "usr/src"
        if archive.get("extract_prefix") != expected_prefix:
            raise BackendError(f"Wrong extract_prefix for {name}: expected {expected_prefix}")
        if not re.fullmatch(r"[0-9a-fA-F]{128}", archive.get("sha512", "")):
            raise BackendError(f"Pinned SHA512 required for {name}")
        url = urllib.parse.urlparse(archive.get("url", ""))
        host = (url.hostname or "").lower()
        if (url.scheme != "https" or not (host == "netbsd.org" or host.endswith(".netbsd.org"))
                or url.username or url.password or url.query or url.fragment
                or not url.path.endswith("/source/sets/" + name)):
            raise BackendError(f"Official HTTPS NetBSD source-set URL required: {name}")
        expected_segment = "/NetBSD-" + upstream["release"] + "/"
        if expected_segment not in url.path:
            raise BackendError(f"Archive URL release does not match lock: {name}")
        size = archive.get("size")
        if size is not None and (not isinstance(size, int) or isinstance(size, bool) or size <= 0):
            raise BackendError(f"Invalid archive size: {name}")
        item = dict(archive)
        item["sha512"] = item["sha512"].lower()
        if not item.get("optional", False):
            result.append(item)
    if not REQUIRED_ARCHIVES <= {a["name"] for a in result}:
        raise BackendError("All four base source sets are required (src, syssrc, gnusrc, sharesrc)")
    # Stable order makes receipts independent of irrelevant JSON key ordering.
    result.sort(key=lambda a: a["name"])
    return path, lock, result, _hash(path)


def _initialize(workspace, lock_hash, archives=None):
    workspace = _workspace(workspace)
    marker = workspace / "workspace.json"
    if marker.exists():
        value = _read_json(marker)
        if value.get("format") != FORMAT or value.get("lock_sha256") != lock_hash:
            raise BackendError("Workspace belongs to a different source lock; use a new workspace")
    else:
        if workspace.exists() and any(workspace.iterdir()):
            # A caller may prefetch the exact locked archives before invoking
            # the backend.  This is the sole nonempty-directory adoption case.
            entries = list(workspace.iterdir())
            download_dir = workspace / "downloads"
            if not archives or entries != [download_dir] or download_dir.is_symlink() or not download_dir.is_dir():
                raise BackendError("Refusing to adopt a nonempty directory without a backend workspace marker")
            allowed = {archive["name"]: archive for archive in archives}
            for existing in download_dir.iterdir():
                archive = allowed.get(existing.name)
                if (archive is None or existing.is_symlink() or not existing.is_file()
                        or _hash(existing, "sha512") != archive["sha512"]
                        or (archive.get("size") is not None and existing.stat().st_size != archive["size"])):
                    raise BackendError(f"Unverified file in prefetched archive cache: {existing}")
        workspace.mkdir(parents=True, exist_ok=True)
        _write_json(marker, {"format": FORMAT, "lock_sha256": lock_hash,
                             "workspace": str(workspace), "created_at": _now()})
    for name in OWNED_DIRS:
        path = _inside(workspace, workspace / name)
        path.mkdir(exist_ok=True)
    _check_layout(workspace)
    return workspace


@contextlib.contextmanager
def _locked(workspace):
    import fcntl
    path = _inside(workspace, workspace / "state" / "backend.lock")
    with path.open("a", encoding="utf-8") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackendError("Another backend operation is using this workspace") from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def _tree_fingerprint(root):
    """Bind every path, type, mode, timestamp, link target and file byte."""
    h = hashlib.sha256()
    count = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        files.sort()
        for name in sorted(dirs + files):
            path = Path(directory) / name
            info = path.lstat()
            record = [path.relative_to(root).as_posix(), stat.S_IFMT(info.st_mode),
                      stat.S_IMODE(info.st_mode), info.st_size, info.st_mtime_ns]
            if stat.S_ISLNK(info.st_mode):
                record.append(os.readlink(path))
            elif stat.S_ISREG(info.st_mode):
                record.append(_hash(path))
            elif not stat.S_ISDIR(info.st_mode):
                raise BackendError(f"Unexpected special file in source/config tree: {path}")
            h.update(json.dumps(record, separators=(",", ":")).encode())
            h.update(b"\n")
            count += 1
    return {"algorithm": "sha256-tree-content-v1", "sha256": h.hexdigest(), "entries": count}


def _source_state(workspace):
    source = workspace / "src"
    anchors = {}
    for name in SOURCE_ANCHORS:
        path = _inside(source, source / name)
        if not path.is_file():
            raise BackendError(f"Incomplete source tree: {path}")
        anchors[name] = _hash(path)
    result = {"anchors": anchors, "tree": _tree_fingerprint(source)}
    if (workspace / "xsrc").is_dir():
        result["xsrc_tree"] = _tree_fingerprint(workspace / "xsrc")
    return result


def _verify_sources(workspace, lock_hash):
    receipt_path = workspace / "state" / "fetch.json"
    receipt = _read_json(receipt_path)
    if receipt.get("status") != "success" or receipt.get("lock_sha256") != lock_hash:
        raise BackendError("Fetch did not complete successfully for this source lock")
    current = _source_state(workspace)
    if current != receipt.get("source_state"):
        raise BackendError("Source tree changed since verified extraction; use a new workspace")
    return receipt


def _download(archive, workspace):
    path = _inside(workspace, workspace / "downloads" / archive["name"])
    expected_size = archive.get("size")
    if path.exists():
        if not path.is_file() or (expected_size is not None and path.stat().st_size != expected_size) or _hash(path, "sha512") != archive["sha512"]:
            raise BackendError(f"Existing download is not the pinned archive: {path}")
        return {"path": str(path), "size": path.stat().st_size, "sha512": archive["sha512"], "cached": True}
    partial = path.with_name(path.name + "." + uuid.uuid4().hex + ".partial")
    request = urllib.request.Request(archive["url"], headers={"User-Agent": "DaOS-BSD-Buildroot/1"})
    digest, size, next_progress = hashlib.sha512(), 0, 64 * 1024 * 1024
    print(f"Fetching pinned source: {archive['name']}", flush=True)
    with urllib.request.urlopen(request, timeout=90) as response, partial.open("xb") as stream:
        redirected = urllib.parse.urlparse(response.geturl())
        if redirected.scheme != "https":
            raise BackendError("Source download redirected away from HTTPS")
        while True:
            block = response.read(4 * 1024 * 1024)
            if not block:
                break
            stream.write(block)
            digest.update(block)
            size += len(block)
            if expected_size is not None and size > expected_size:
                raise BackendError(f"Source download exceeds pinned size: {archive['name']}")
            if size >= next_progress:
                print(f"  {archive['name']}: {size // (1024 * 1024)} MiB", flush=True)
                next_progress += 64 * 1024 * 1024
        stream.flush()
        os.fsync(stream.fileno())
    if (expected_size is not None and size != expected_size) or digest.hexdigest() != archive["sha512"]:
        raise BackendError(f"Source SHA512/size mismatch; retained diagnostic file: {partial}")
    os.rename(partial, path)
    return {"path": str(path), "size": size, "sha512": digest.hexdigest(), "cached": False}


def _tar_path(name):
    if not isinstance(name, str) or not name or "\\" in name or any(ord(c) < 32 for c in name):
        raise BackendError("Unsafe archive pathname")
    while name.startswith("./"):
        name = name[2:]
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise BackendError(f"Unsafe archive path: {name}")
    return path


def _extract_archive(archive, download, extract_root):
    prefix = PurePosixPath(archive["extract_prefix"])
    deferred = []
    count = 0
    print(f"Extracting verified source: {archive['name']}", flush=True)
    with tarfile.open(download, "r:gz") as stream:
        for member in stream:
            relative = _tar_path(member.name)
            if relative != prefix and prefix not in relative.parents:
                if member.isdir() and relative in prefix.parents:
                    continue  # usr/ ancestor records in the official source sets
                raise BackendError(f"Archive entry is outside {prefix}: {relative}")
            destination = extract_root.joinpath(*relative.parts)
            _inside(extract_root, destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if member.isdir():
                destination.mkdir(exist_ok=True)
                if not destination.is_dir() or destination.is_symlink():
                    raise BackendError(f"Archive directory collision: {relative}")
            elif member.isfile():
                source = stream.extractfile(member)
                if source is None:
                    raise BackendError(f"Unreadable archive member: {relative}")
                if destination.exists():
                    if not destination.is_file() or destination.is_symlink():
                        raise BackendError(f"Archive file collision: {relative}")
                    incoming = hashlib.sha256()
                    for block in iter(lambda: source.read(4 * 1024 * 1024), b""):
                        incoming.update(block)
                    if destination.stat().st_size != member.size or _hash(destination) != incoming.hexdigest():
                        raise BackendError(f"Conflicting files between source sets: {relative}")
                else:
                    with destination.open("xb") as target:
                        shutil.copyfileobj(source, target, 4 * 1024 * 1024)
                    os.chmod(destination, member.mode & 0o777)
                    os.utime(destination, (member.mtime, member.mtime))
            elif member.issym():
                link = member.linkname
                if not link or "\\" in link or PurePosixPath(link).is_absolute() or any(ord(c) < 32 for c in link):
                    raise BackendError(f"Unsafe source symlink: {relative}")
                resolved = Path(os.path.normpath(str(destination.parent / link)))
                allowed = extract_root.joinpath(*prefix.parts)
                if resolved != allowed and allowed not in resolved.parents:
                    raise BackendError(f"Source symlink escapes its source root: {relative} -> {link}")
                if destination.is_symlink():
                    if os.readlink(destination) != link:
                        raise BackendError(f"Conflicting source symlink: {relative}")
                elif destination.exists():
                    raise BackendError(f"Source symlink collision: {relative}")
                else:
                    os.symlink(link, destination)
            elif member.islnk():
                target = _tar_path(member.linkname)
                if target != prefix and prefix not in target.parents:
                    raise BackendError(f"Unsafe archive hardlink: {relative}")
                deferred.append((destination, extract_root.joinpath(*target.parts)))
            else:
                raise BackendError(f"Special files are forbidden in source archives: {relative}")
            count += 1
    for destination, target in deferred:
        _inside(extract_root, destination)
        _inside(extract_root, target)
        if not target.is_file() or target.is_symlink() or destination.exists():
            raise BackendError(f"Invalid source hardlink: {destination}")
        os.link(target, destination)
    return {"archive": archive["name"], "entries": count}


def fetch(lock_path, workspace):
    """Download pinned official source sets, verify SHA512, extract privately."""
    lock_path, lock, archives, lock_hash = _lock_inputs(lock_path)
    workspace = _initialize(workspace, lock_hash, archives)
    with _locked(workspace):
        state_path = workspace / "state" / "fetch.json"
        if state_path.exists():
            receipt = _verify_sources(workspace, lock_hash)
            # Revalidate archive bytes as well, rather than trusting cache names.
            for archive in archives:
                _download(archive, workspace)
            return dict(receipt, cached=True, receipt=str(state_path))
        if (workspace / "src").exists() or (workspace / "xsrc").exists():
            raise BackendError("Unreceipted source tree exists; retain it and use a new workspace")
        run_id = "fetch-" + uuid.uuid4().hex
        receipt_path = workspace / "receipts" / (run_id + ".json")
        receipt = {"format": FORMAT, "operation": "fetch", "status": "running",
                   "executed": True, "started_at": _now(), "lock_path": str(lock_path),
                   "lock_sha256": lock_hash, "upstream": lock["upstream"],
                   "workspace": str(workspace), "source_dir": str(workspace / "src"),
                   "archives": [], "extracted": [], "receipt": str(receipt_path)}
        _write_json(receipt_path, receipt)
        try:
            for archive in archives:
                receipt["archives"].append(_download(archive, workspace))
                _write_json(receipt_path, receipt)
            extract_root = workspace / "tmp" / run_id
            extract_root.mkdir()
            for archive in archives:
                receipt["extracted"].append(_extract_archive(archive, workspace / "downloads" / archive["name"], extract_root))
            source = extract_root / "usr" / "src"
            if not source.is_dir():
                raise BackendError("Official source sets did not produce usr/src")
            os.rename(source, workspace / "src")
            if (extract_root / "usr" / "xsrc").is_dir():
                os.rename(extract_root / "usr" / "xsrc", workspace / "xsrc")
            receipt["source_state"] = _source_state(workspace)
            receipt["status"] = "success"
            receipt["finished_at"] = _now()
            _write_json(receipt_path, receipt)
            _write_json(state_path, receipt)
            return receipt
        except BaseException as exc:
            receipt.update(status="failed", finished_at=_now(), error=str(exc))
            _write_json(receipt_path, receipt)
            raise


def _profile_inputs(profile_path):
    path = _no_symlink_components(Path(profile_path).expanduser().absolute())
    profile = _read_json(path)
    if profile.get("format") != PROFILE_FORMAT or profile.get("backend") != "netbsd":
        raise BackendError(f"Expected a NetBSD {PROFILE_FORMAT} profile")
    name = profile.get("name", "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", name):
        raise BackendError("Profile name must be a plain identifier")
    target = profile.get("target", {})
    if target.get("machine") != "evbarm" or target.get("architecture") != "aarch64":
        raise BackendError("This backend currently supports evbarm/aarch64 targets only")
    kernel = profile.get("kernel", {})
    if not re.fullmatch(r"[A-Za-z0-9_]+", kernel.get("name", "")):
        raise BackendError("Kernel name must be a plain identifier")
    def local_file(value, label):
        if not isinstance(value, str) or not value or Path(value).is_absolute():
            raise BackendError(f"{label} must be relative to the profile directory")
        file = _no_symlink_components(path.parent / value)
        if not file.is_file():
            raise BackendError(f"Missing {label}: {file}")
        return file
    lock_path = local_file(profile.get("source_lock"), "source_lock")
    kernel_path = local_file(kernel.get("config"), "kernel config")
    mkconf_path = local_file(profile.get("world", {}).get("mk_conf"), "mk.conf")
    for line in mkconf_path.read_text(encoding="utf-8").splitlines():
        setting = line.split("#", 1)[0].strip()
        if setting and not re.fullmatch(r"MK[A-Z0-9_]+\s*=\s*(?:yes|no|[0-9]+)", setting):
            raise BackendError("World mk.conf may contain only explicit MK*=yes/no/integer settings; output paths are owned by the backend")
    logo = profile.get("boot_logo")
    logo_path = None
    if logo is not None:
        if not isinstance(logo, dict) or logo.get("mechanism") != "netbsd-genfb-splashscreen":
            raise BackendError("Unsupported boot logo mechanism")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+\.png", logo.get("kernel_file", "")):
            raise BackendError("Boot logo kernel_file must be a plain PNG filename")
        logo_path = local_file(logo.get("asset"), "boot logo PNG")
        with logo_path.open("rb") as stream:
            if stream.read(8) != b"\x89PNG\r\n\x1a\n":
                raise BackendError("Boot logo must be an actual PNG image")
    # The kernel and world profiles are private configuration, never upstream edits.
    for file in (kernel_path, mkconf_path):
        if path.parent != file.parent:
            raise BackendError(f"Build config must be in the profile directory: {file}")
    _, lock, _, lock_hash = _lock_inputs(lock_path)
    inputs = {"profile_sha256": _hash(path), "kernel_sha256": _hash(kernel_path),
              "mk_conf_sha256": _hash(mkconf_path), "lock_sha256": lock_hash,
              "backend_sha256": _hash(Path(__file__)), "profile_name": name,
              "target": target, "kernel_name": kernel["name"]}
    if logo_path:
        inputs["boot_logo_sha256"] = _hash(logo_path)
    return {"profile_path": path, "profile": profile, "lock_path": lock_path,
            "lock": lock, "lock_hash": lock_hash, "kernel_path": kernel_path,
            "mkconf_path": mkconf_path, "logo_path": logo_path, "inputs": inputs,
            "input_hash": _identity(inputs)}


def _paths(workspace, spec):
    kernel = spec["profile"]["kernel"]["name"]
    return {"workspace": str(workspace), "source_dir": str(workspace / "src"),
            "config_dir": str(workspace / "config"),
            "kernel_config_dir": str(workspace / "config" / "kernel"),
            "mk_conf": str(workspace / "config" / "mk.conf"),
            "objdir": str(workspace / "obj"), "toolchain_dir": str(workspace / "tools"),
            "destdir": str(workspace / "destdir"), "release_dir": str(workspace / "release"),
            "kernel_path": str(workspace / "obj" / "kernels" / kernel / "netbsd")}


def _configured(workspace, spec):
    marker = _read_json(workspace / "workspace.json")
    if marker.get("format") != FORMAT or marker.get("lock_sha256") != spec["lock_hash"]:
        raise BackendError("Workspace/source-lock identity mismatch")
    _check_layout(workspace)
    receipt = _read_json(workspace / "state" / "configure.json")
    if receipt.get("status") != "success" or receipt.get("input_hash") != spec["input_hash"]:
        raise BackendError("Build inputs differ from configured workspace; use a new workspace")
    config_state = {"mk_conf": _hash(workspace / "config" / "mk.conf"),
                    "kernel_tree": _tree_fingerprint(workspace / "config" / "kernel")}
    if config_state != receipt.get("config_state"):
        raise BackendError("Private configuration changed after configure; use a new workspace")
    return receipt


def configure(profile_path, workspace):
    """Freeze a source-locked profile and private kernel/world configuration."""
    spec = _profile_inputs(profile_path)
    workspace = _initialize(workspace, spec["lock_hash"])
    with _locked(workspace):
        _verify_sources(workspace, spec["lock_hash"])
        state_path = workspace / "state" / "configure.json"
        if state_path.exists():
            return dict(_configured(workspace, spec), cached=True, receipt=str(state_path))
        config = workspace / "config"
        if config.exists() or any((workspace / name).exists() for name in BUILD_DIRS):
            raise BackendError("Unreceipted config/build directories exist; use a new workspace")
        kernel_dir = config / "kernel"
        shutil.copytree(workspace / "src" / "sys" / "arch" / "evbarm" / "conf", kernel_dir, symlinks=True)
        shutil.copyfile(spec["kernel_path"], kernel_dir / spec["profile"]["kernel"]["name"])
        if spec["logo_path"]:
            shutil.copyfile(spec["logo_path"], kernel_dir / spec["profile"]["boot_logo"]["kernel_file"])
        shutil.copyfile(spec["mkconf_path"], config / "mk.conf")
        for name in BUILD_DIRS:
            (workspace / name).mkdir()
        receipt = {"format": FORMAT, "operation": "configure", "status": "success",
                   "executed": True, "finished_at": _now(), "input_hash": spec["input_hash"],
                   "inputs": spec["inputs"], "profile": spec["profile"],
                   "profile_path": str(spec["profile_path"]), "paths": _paths(workspace, spec),
                   "config_state": {"mk_conf": _hash(config / "mk.conf"),
                                    "kernel_tree": _tree_fingerprint(kernel_dir)}}
        receipt["receipt"] = str(state_path)
        _write_json(state_path, receipt)
        return receipt


def _stage_sequence(stage):
    if stage not in STAGES:
        raise BackendError(f"Unknown build stage {stage!r}; choose one of {STAGES}")
    return ["tools", "distribution", "kernel"] if stage == "all" else [stage]


def _command(spec, workspace, jobs, stage):
    if not isinstance(jobs, int) or isinstance(jobs, bool) or jobs < 1 or jobs > 256:
        raise BackendError("jobs must be an integer between 1 and 256")
    paths = _paths(workspace, spec)
    target = spec["profile"]["target"]
    operation = {"tools": "tools", "world": "build", "distribution": "distribution",
                 "kernel": "kernel=" + spec["profile"]["kernel"]["name"],
                 "release": "release"}[stage]
    # -u is safe here because configuration/source identity was verified first.
    # KERNOBJDIR makes the actual kernel output path unambiguous.
    command = ["/bin/sh", str(workspace / "src" / "build.sh"), "-U", "-u",
               "-O", paths["objdir"], "-T", paths["toolchain_dir"],
               "-D", paths["destdir"], "-R", paths["release_dir"],
               "-m", target["machine"], "-a", target["architecture"], "-j", str(jobs),
               "-V", "KERNCONFDIR=" + paths["kernel_config_dir"],
               "-V", "KERNOBJDIR=" + str(workspace / "obj" / "kernels"),
               "-V", "KERNEL_SETS=" + spec["profile"]["kernel"]["name"]]
    xsrc_required = any(item["name"] == "xsrc.tgz" and not item.get("optional", False)
                        for item in spec["lock"]["archives"])
    if xsrc_required:
        command += ["-X", str(workspace / "xsrc")]
    command.append(operation)
    return command


def plan(profile_path, workspace, jobs=12, stage="all"):
    """Return an inspectable plan.  Never downloads, configures, or builds."""
    spec = _profile_inputs(profile_path)
    workspace = _workspace(workspace)
    sequence = _stage_sequence(stage)
    return {"format": FORMAT, "operation": "plan", "executed": False,
            "status": "planned", "input_hash": spec["input_hash"],
            "paths": _paths(workspace, spec), "stages": sequence,
            "environment": {"MAKECONF": str(workspace / "config" / "mk.conf"),
                            "TMPDIR": str(workspace / "tmp"), "LC_ALL": "C"},
            "commands": [_command(spec, workspace, jobs, item) for item in sequence]}


def _file_receipt(path, workspace):
    path = Path(path)
    resolved = path.resolve(strict=True)
    if workspace not in resolved.parents or not resolved.is_file():
        raise BackendError(f"Build product is not a private regular file: {path}")
    return {"path": str(path), "resolved_path": str(resolved),
            "size": resolved.stat().st_size, "sha256": _hash(resolved)}


def check_target_elf(path):
    """Check ELF64 little-endian AArch64 without executing a target program."""
    path = Path(path)
    with path.open("rb") as stream:
        header = stream.read(64)
    if len(header) != 64 or header[:6] != b"\x7fELF\x02\x01":
        raise BackendError(f"Expected a little-endian ELF64 build product: {path}")
    machine = struct.unpack_from("<H", header, 18)[0]
    elf_type = struct.unpack_from("<H", header, 16)[0]
    if machine != 183 or elf_type not in (1, 2, 3):
        raise BackendError(f"Wrong target ELF machine/type ({machine}/{elf_type}): {path}")
    return {"class": "ELF64", "endian": "little", "machine": "AArch64", "machine_id": machine,
            "type_id": elf_type, "osabi": header[7]}


def _toolchain_outputs(workspace):
    bindir = workspace / "tools" / "bin"
    compiler = bindir / "aarch64--netbsd-gcc"
    cxx = bindir / "aarch64--netbsd-g++"
    config = bindir / "nbconfig"
    make = bindir / "nbmake-evbarm"
    for path in (compiler, cxx, config, make):
        if not path.is_file() or not os.access(path, os.X_OK):
            raise BackendError(f"Toolchain build did not produce executable tool: {path}")
    probe = subprocess.run([str(compiler), "-dumpmachine"], text=True, capture_output=True, check=True, timeout=30)
    triple = probe.stdout.strip()
    if not re.fullmatch(r"aarch64[^\s]*netbsd[^\s]*", triple):
        raise BackendError(f"Unexpected target compiler triple: {triple}")
    test_dir = workspace / "tmp" / ("toolchain-probe-" + uuid.uuid4().hex)
    test_dir.mkdir()
    source = test_dir / "probe.c"
    source.write_text("int daos_toolchain_probe(void) { return 183; }\n", encoding="ascii")
    target = test_dir / "probe.o"
    compilation = subprocess.run([str(compiler), "-ffreestanding", "-c", str(source), "-o", str(target)],
                                 text=True, capture_output=True, timeout=60)
    (test_dir / "compile.log").write_text(compilation.stdout + compilation.stderr, encoding="utf-8")
    if compilation.returncode:
        raise BackendError(f"Target compiler object probe failed: {test_dir / 'compile.log'}")
    # build.sh regenerates nbmake-evbarm for every invocation, including its
    # argv in a comment.  It is an executable wrapper, not immutable compiler
    # output; recording its bytes would invalidate tools after every stage.
    return {"target_triple": triple, "tools": [_file_receipt(path, workspace) for path in (compiler, cxx, config)],
            "make_wrapper": {"path": str(make), "generated_per_invocation": True},
            "probe": dict(_file_receipt(target, workspace), elf=check_target_elf(target))}


def _stage_outputs(stage, workspace, spec):
    if stage == "tools":
        return _toolchain_outputs(workspace)
    if stage == "kernel":
        path = Path(_paths(workspace, spec)["kernel_path"])
        return {"kernel": dict(_file_receipt(path, workspace), elf=check_target_elf(path))}
    if stage in ("world", "distribution", "release"):
        destdir = workspace / "destdir"
        outputs = []
        for relative in ("bin/sh", "sbin/init"):
            path = destdir / relative
            outputs.append(dict(_file_receipt(path, workspace), elf=check_target_elf(path)))
        libc = sorted((destdir / "lib").glob("libc.so.*"))
        if not libc:
            raise BackendError("Base build is missing target libc in destdir/lib")
        outputs.append(dict(_file_receipt(libc[-1], workspace), elf=check_target_elf(libc[-1])))
        if stage in ("distribution", "release"):
            for relative in ("etc/rc", "etc/mtree/set.base", "METALOG"):
                outputs.append(_file_receipt(destdir / relative, workspace))
        result = {"base_files": outputs}
        if stage == "release":
            sets = sorted((workspace / "release").glob("*/binary/sets/base.tar.*"))
            if not sets:
                # NetBSD sets may use the .tgz suffix rather than .tar.xz.
                sets = sorted((workspace / "release").glob("*/binary/sets/base.tgz"))
            if not sets:
                raise BackendError("Release command completed without a base binary set")
            result["release_sets"] = [_file_receipt(path, workspace) for path in sets]
        return result
    raise BackendError(f"No artifact verifier for stage: {stage}")


def _build_environment(workspace):
    # Environment overrides can otherwise redirect writes or inject host ABI.
    environment = {key: os.environ[key] for key in ("HOME", "USER", "LOGNAME", "TERM", "SOURCE_DATE_EPOCH") if key in os.environ}
    environment.update(PATH="/usr/bin:/bin", LC_ALL="C", TZ="UTC", HOST_SH="/bin/sh",
                       MAKECONF=str(workspace / "config" / "mk.conf"),
                       TMPDIR=str(workspace / "tmp"))
    return environment


def _run(command, workspace, logfile):
    print("Running: " + " ".join(command), flush=True)
    print(f"Full build log: {logfile}", flush=True)
    with logfile.open("xb") as log:
        log.write((json.dumps({"argv": command, "cwd": str(workspace / "src"),
                               "MAKECONF": str(workspace / "config" / "mk.conf")}) + "\n").encode())
        log.flush()
        process = subprocess.Popen(command, cwd=workspace / "src", env=_build_environment(workspace),
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while process.poll() is None:
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    print(f"Build running; log {logfile.name}: {log.tell()} bytes", flush=True)
            return process.returncode
        except BaseException:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise


def _cached_artifacts(receipt, workspace):
    """A successful receipt is reusable only while every recorded artifact agrees."""
    def visit(value):
        if isinstance(value, dict):
            if {"path", "size", "sha256"} <= value.keys():
                actual = _file_receipt(Path(value["path"]), workspace)
                if actual["sha256"] != value["sha256"] or actual["size"] != value["size"]:
                    raise BackendError(f"Cached build product changed: {value['path']}")
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(receipt.get("outputs", {}))


def build_base(profile_path, workspace, jobs=12, stage="all"):
    """Execute NetBSD build.sh and verify real tools, base files, and kernel."""
    spec = _profile_inputs(profile_path)
    workspace = _workspace(workspace)
    sequence = _stage_sequence(stage)
    # Check argument validity before touching build state.
    for item in sequence:
        _command(spec, workspace, jobs, item)
    with _locked(workspace):
        config = _configured(workspace, spec)
        _verify_sources(workspace, spec["lock_hash"])
        results = []
        for item in sequence:
            state_path = workspace / "state" / (item + ".json")
            if state_path.exists():
                previous = _read_json(state_path)
                if previous.get("status") != "success" or previous.get("input_hash") != spec["input_hash"]:
                    raise BackendError(f"Unusable stage state: {item}")
                _cached_artifacts(previous, workspace)
                results.append(dict(previous, cached=True))
                continue
            if item != "tools":
                tools_state = _read_json(workspace / "state" / "tools.json")
                if tools_state.get("status") != "success" or tools_state.get("input_hash") != spec["input_hash"]:
                    raise BackendError("Build the verified tools stage before compiling target code")
                _cached_artifacts(tools_state, workspace)
            run_id = item + "-" + uuid.uuid4().hex
            receipt_path = workspace / "receipts" / (run_id + ".json")
            logfile = workspace / "logs" / (run_id + ".log")
            command = _command(spec, workspace, jobs, item)
            receipt = {"format": FORMAT, "operation": "build_base", "stage": item,
                       "status": "running", "executed": True, "started_at": _now(),
                       "input_hash": spec["input_hash"], "inputs": spec["inputs"],
                       "paths": config["paths"], "command": command,
                       "environment": {"MAKECONF": str(workspace / "config" / "mk.conf"),
                                       "TMPDIR": str(workspace / "tmp"), "LC_ALL": "C"},
                       "log": str(logfile), "receipt": str(receipt_path)}
            _write_json(receipt_path, receipt)
            try:
                returncode = _run(command, workspace, logfile)
                receipt["returncode"] = returncode
                if returncode:
                    raise BackendError(f"NetBSD {item} exited {returncode}; see {logfile}")
                _configured(workspace, spec)
                _verify_sources(workspace, spec["lock_hash"])
                receipt["outputs"] = _stage_outputs(item, workspace, spec)
                receipt.update(status="success", finished_at=_now())
                _write_json(receipt_path, receipt)
                _write_json(state_path, receipt)
                results.append(receipt)
            except BaseException as exc:
                receipt.update(status="failed", finished_at=_now(), error=str(exc))
                _write_json(receipt_path, receipt)
                raise
        return {"format": FORMAT, "operation": "build_base", "status": "success",
                "executed": any(not result.get("cached") for result in results),
                "input_hash": spec["input_hash"], "paths": config["paths"], "stages": results}
