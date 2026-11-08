"""Explicit, audited configuration revisions without rebuilding valid tools.

The build backend never silently accepts changed inputs.  A caller may review
``plan`` and then pass its ``revision_hash`` to ``apply``.  This narrow route
permits kernel/product-profile changes while source, target and world settings
stay identical.  It preserves original execution receipts and archives the old
private configuration and kernel objects; it never edits upstream sources.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import uuid

from . import netbsd


def _tool_command(command):
    """Parallelism changes scheduling, not the installed toolchain inputs."""
    result = []
    skip = False
    for word in command:
        if skip:
            skip = False
            continue
        if word == "-j":
            skip = True
            continue
        result.append(word)
    return result


def _review(profile_path, workspace):
    workspace = netbsd._workspace(workspace)
    spec = netbsd._profile_inputs(profile_path)
    old = netbsd._read_json(workspace / "state/configure.json")
    if old.get("format") != netbsd.FORMAT or old.get("status") != "success":
        raise netbsd.BackendError("A completed configuration is required before an explicit revision")
    marker = netbsd._read_json(workspace / "workspace.json")
    if marker.get("lock_sha256") != spec["lock_hash"]:
        raise netbsd.BackendError("Source changes require a new workspace, not a configuration revision")
    netbsd._check_layout(workspace)
    netbsd._verify_sources(workspace, spec["lock_hash"])
    previous_inputs = old.get("inputs", {})
    for key in ("lock_sha256", "target", "mk_conf_sha256"):
        if previous_inputs.get(key) != spec["inputs"].get(key):
            raise netbsd.BackendError(f"Configuration revision cannot reuse tools after changing {key}")
    old_profile = old["profile"]
    if old_profile["kernel"]["name"] != spec["profile"]["kernel"]["name"]:
        raise netbsd.BackendError("Kernel-name changes require a new workspace in this revision tool")
    if old.get("paths") != netbsd._paths(workspace, spec):
        raise netbsd.BackendError("Backend output-layout changes require a new workspace")
    # Verify the old private config against its original state even when the
    # caller's profile files have changed.  This prevents legitimizing a
    # manually patched, unrecorded KERNCONFDIR.
    current_config = {"mk_conf": netbsd._hash(workspace / "config/mk.conf"),
                      "kernel_tree": netbsd._tree_fingerprint(workspace / "config/kernel")}
    if current_config != old.get("config_state"):
        raise netbsd.BackendError("Existing private config differs from its receipt; archive it manually for review")
    inherited = []
    for stage in ("tools", "world", "distribution"):
        state_path = workspace / "state" / (stage + ".json")
        if not state_path.exists():
            continue
        receipt = netbsd._read_json(state_path)
        if receipt.get("status") != "success" or receipt.get("input_hash") != old["input_hash"]:
            raise netbsd.BackendError(f"Cannot inherit an unverified {stage} stage")
        expected = netbsd._command(spec, workspace, 12, stage)
        if _tool_command(receipt.get("command", [])) != _tool_command(expected):
            raise netbsd.BackendError(f"Backend command changed for {stage}; use a new workspace")
        netbsd._cached_artifacts(receipt, workspace)
        inherited.append({"stage": stage, "receipt": receipt["receipt"],
                          "receipt_sha256": netbsd._hash(receipt["receipt"]),
                          "outputs_sha256": netbsd._identity(receipt.get("outputs"))})
    review = {"format": "DaOS-netbsd-configuration-revision-v1", "executed": False,
              "status": "planned", "workspace": str(workspace),
              "old_input_hash": old["input_hash"], "new_input_hash": spec["input_hash"],
              "old_inputs": previous_inputs, "new_inputs": spec["inputs"],
              "source_tree_verified": True, "reusable_stages": inherited,
              "invalidated_stages": ["kernel", "release"],
              "source_and_world_unchanged": True,
              "actions": ["archive original private config and configure receipt",
                          "archive prior kernel object directory and kernel/release stage state",
                          "install new private config from the reviewed profile",
                          "record inherited, verified tools/world receipts without claiming execution"]}
    review["revision_hash"] = netbsd._identity(review)
    return review, old, spec


def plan(profile_path, workspace):
    """Verify existing evidence and return a reviewable revision; no build."""
    workspace = netbsd._workspace(workspace)
    with netbsd._locked(workspace):
        review, _, _ = _review(profile_path, workspace)
        return review


def apply(profile_path, workspace, *, revision_hash):
    """Apply exactly the reviewed revision, retaining original evidence."""
    workspace = netbsd._workspace(workspace)
    with netbsd._locked(workspace):
        review, old, spec = _review(profile_path, workspace)
        if revision_hash != review["revision_hash"]:
            raise netbsd.BackendError("Revision evidence changed; inspect a fresh revision plan")
        if review["new_input_hash"] == review["old_input_hash"]:
            return dict(review, status="unchanged", executed=False)
        run_id = "config-" + uuid.uuid4().hex
        attempts = netbsd._inside(workspace, workspace / "attempts")
        attempts.mkdir(exist_ok=True)
        archive = netbsd._inside(workspace, attempts / run_id)
        archive.mkdir()
        transition_path = archive / "revision.json"
        transition = dict(review, executed=True, status="running", started_at=netbsd._now(),
                          archive=str(archive), moves=[])
        netbsd._write_json(transition_path, transition)
        pending = workspace / "tmp" / run_id
        pending_kernel = pending / "kernel"
        shutil.copytree(workspace / "src/sys/arch/evbarm/conf", pending_kernel, symlinks=True)
        shutil.copyfile(spec["kernel_path"], pending_kernel / spec["profile"]["kernel"]["name"])
        if spec["logo_path"]:
            shutil.copyfile(spec["logo_path"], pending_kernel / spec["profile"]["boot_logo"]["kernel_file"])
        shutil.copyfile(spec["mkconf_path"], pending / "mk.conf")
        # Validate every complete move target before the first mutation.
        moves = [(workspace / "config", archive / "config"),
                 (workspace / "state/configure.json", archive / "configure.json")]
        if (workspace / "obj/kernels").exists():
            moves.append((workspace / "obj/kernels", archive / "kernel-objects"))
        for stage in ("kernel", "release"):
            state = workspace / "state" / (stage + ".json")
            if state.exists():
                moves.append((state, archive / (stage + "-state.json")))
        for source, target in moves:
            netbsd._inside(workspace, source)
            netbsd._inside(workspace, target)
            if target.exists():
                raise netbsd.BackendError(f"Revision archive path already exists: {target}")
        try:
            for source, target in moves:
                os.rename(source, target)
                transition["moves"].append({"from": str(source), "to": str(target)})
                netbsd._write_json(transition_path, transition)
            os.rename(pending, workspace / "config")
            new_config = {"format": netbsd.FORMAT, "operation": "configure", "status": "success",
                          "executed": True, "finished_at": netbsd._now(),
                          "input_hash": spec["input_hash"], "inputs": spec["inputs"],
                          "profile": spec["profile"], "profile_path": str(spec["profile_path"]),
                          "paths": netbsd._paths(workspace, spec),
                          "config_state": {"mk_conf": netbsd._hash(workspace / "config/mk.conf"),
                                           "kernel_tree": netbsd._tree_fingerprint(workspace / "config/kernel")},
                          "revision": str(transition_path),
                          "receipt": str(workspace / "state/configure.json")}
            netbsd._write_json(workspace / "state/configure.json", new_config)
            for item in review["reusable_stages"]:
                prior = netbsd._read_json(item["receipt"])
                inherited_path = workspace / "receipts" / (run_id + "-reuse-" + item["stage"] + ".json")
                inherited = dict(prior, executed=False, cached=True, operation="reuse_verified_stage",
                                 input_hash=spec["input_hash"], inputs=spec["inputs"],
                                 inherited_from=item["receipt"], inherited_receipt_sha256=item["receipt_sha256"],
                                 original_execution_input_hash=old["input_hash"],
                                 reuse_reason="source, target, world config and stage command unchanged",
                                 revision=str(transition_path), finished_at=netbsd._now(),
                                 receipt=str(inherited_path))
                netbsd._write_json(inherited_path, inherited)
                netbsd._write_json(workspace / "state" / (item["stage"] + ".json"), inherited)
            transition.update(status="success", finished_at=netbsd._now(),
                              configured_input_hash=spec["input_hash"])
            netbsd._write_json(transition_path, transition)
            return transition
        except BaseException as exc:
            transition.update(status="failed", error=str(exc), finished_at=netbsd._now())
            netbsd._write_json(transition_path, transition)
            raise
