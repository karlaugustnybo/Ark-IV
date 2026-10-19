"""Explicit bounded CoreWeave SDK execution, injectable for offline contract tests."""

from __future__ import annotations

import contextlib
import json
import math
import os
import signal
import subprocess
import time
from pathlib import Path

import psutil

PROCESS_TERM_GRACE_SECONDS = 15


def _stop_process_group(child):
    """Finish the session we launched, even if its leader has already exited."""

    def send(sig):
        try:
            os.killpg(child.pid, sig)
            return True
        except ProcessLookupError:
            return False

    def running():
        child.poll()
        if not send(0):
            return False
        # Orphan zombies may await PID 1; they no longer execute or hold memory.
        for process in psutil.process_iter():
            try:
                if (
                    os.getpgid(process.pid) == child.pid
                    and process.status() != psutil.STATUS_ZOMBIE
                ):
                    return True
            except (ProcessLookupError, psutil.NoSuchProcess):
                pass
        return False

    for sig in (signal.SIGTERM, signal.SIGKILL):
        if not send(sig):
            break
        deadline = time.monotonic() + PROCESS_TERM_GRACE_SECONDS
        while running():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.05, remaining))
        else:
            break
    else:
        raise RuntimeError("Owned process group did not stop after KILL")
    child.wait(timeout=PROCESS_TERM_GRACE_SECONDS)


@contextlib.contextmanager
def owned_process(command, **kwargs):
    """Bound local stage cleanup to its new process group on every exit path.

    TERM has a finite grace before KILL, including descendants left by a successful
    leader. Commands must keep their workers in this group rather than daemonize.
    """
    child = subprocess.Popen(command, start_new_session=True, **kwargs)
    try:
        yield child
    except BaseException as error:
        try:
            _stop_process_group(child)
        except BaseException as cleanup_error:
            error.add_note("Owned process-group cleanup failed: " + type(cleanup_error).__name__)
        raise
    else:
        _stop_process_group(child)


def cost_plan(cpu, memory_gib, lifetime_seconds, cpu_rate, memory_rate):
    values = (cpu, memory_gib, lifetime_seconds, cpu_rate, memory_rate)
    if not all(math.isfinite(v) and v > 0 for v in values):
        raise ValueError("Resources, lifetime and user-supplied rates must be positive and finite")
    rate = cpu * cpu_rate + memory_gib * memory_rate
    return {
        "cpu": cpu,
        "memory_gib": memory_gib,
        "lifetime_seconds": lifetime_seconds,
        "rate_usd_per_hour": rate,
        "maximum_compute_scenario_usd": lifetime_seconds / 3600 * rate,
        "cost_scope": "User-supplied rate scenario; excludes storage, other account use and retries; not an invoice",
    }


def sdk_options(plan, scratch_gib, image):
    from cwsandbox import AuthStrategy, DataPlaneMode, ScratchVolumeOptions

    return {
        "container_image": image,
        "auth": AuthStrategy.WANDB,
        "resources": {"cpu": str(plan["cpu"]), "memory": str(plan["memory_gib"]) + "Gi"},
        "volumes": [
            ScratchVolumeOptions(
                name="scratch", mount_path="/scratch", size=str(scratch_gib) + "Gi"
            )
        ],
        "data_plane_mode": DataPlaneMode.GATEWAY,
        "max_lifetime_seconds": plan["lifetime_seconds"],
        "environment_variables": {
            "WANDB_API_KEY": os.environ["WANDB_API_KEY"],
            "TMPDIR": "/scratch",
            **{
                key: os.environ[key]
                for key in ("IUCN_REDLIST_API_KEY", "IUCN_REDLIST_KEY")
                if os.environ.get(key)
            },
        },
        "tags": ("ark-explicit-job",),
    }


def validate_stages(stages):
    """Reject malformed argv/log names before allocation or any command launch."""
    if not isinstance(stages, list):
        raise ValueError("Stages must be a JSON list")
    names = set()
    for stage in stages:
        name = stage.get("name") if isinstance(stage, dict) else None
        if (
            not isinstance(name, str)
            or not name
            or Path(name).name != name
            or name in {".", "..", "extract", "driver-report"}
            or name in names
        ):
            raise ValueError(
                "Stage names must be unique simple filenames, excluding driver-reserved names"
            )
        names.add(name)
        command = stage.get("command")
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(value, str) or "\0" in value for value in command)
            or type(stage.get("timeout")) is not int
            or stage["timeout"] <= 0
        ):
            raise ValueError("Each stage needs string argv and a positive integer timeout")
    return stages


def run_remote(payload, identity, commands, output, options, *, sandbox_factory=None, recover=()):
    """Stop the owned sandbox on success and failure; recover selected reports first.

    The provider hard lifetime can still destroy scratch before recovery finishes.
    Output is private: logs may expose input paths or provider details.
    """
    validate_stages(commands)
    if sandbox_factory is None:
        from cwsandbox import Sandbox

        sandbox_factory = Sandbox.run
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    report = {"status": "starting", "payload": identity, "stages": [], "cleanup": "not-created"}
    started = time.monotonic()

    def save():
        (output / "driver-report.json").write_text(json.dumps(report, indent=2) + "\n")

    save()
    try:
        with sandbox_factory(**options) as sandbox:
            report["cleanup"] = "context-active"
            report["sandbox_id"] = sandbox.sandbox_id
            save()
            try:
                result = sandbox.exec(["mkdir", "-p", "/probe"], timeout_seconds=30).result()
                if result.returncode:
                    raise RuntimeError("Remote prepare failed")
                sandbox.write_file_streaming(
                    "/probe/code.tar", payload, timeout_seconds=300
                ).result()
                stages = [
                    {
                        "name": "extract",
                        "command": ["tar", "-xf", "/probe/code.tar", "-C", "/probe"],
                        "timeout": 60,
                    },
                    *commands,
                ]
                for stage in stages:
                    name = stage["name"]
                    if not name or Path(name).name != name or name in {".", ".."}:
                        raise ValueError("Invalid stage log name")
                    result = sandbox.exec(
                        stage["command"], timeout_seconds=stage["timeout"]
                    ).result()
                    (output / (name + ".log")).write_text(result.stdout + "\n" + result.stderr)
                    report["stages"].append({"name": name, "exit_code": result.returncode})
                    save()
                    if result.returncode:
                        raise RuntimeError("Remote stage failed: " + name)
            finally:
                report["recovered"] = []
                for remote, local in recover:
                    if Path(local).name != local or local in {".", ".."}:
                        raise ValueError("Recovery destination must be a filename")
                    try:
                        data = sandbox.read_file(remote, timeout_seconds=60).result()
                        (output / local).write_bytes(data)
                        report["recovered"].append(local)
                    except Exception:
                        report.setdefault("recovery_failed", []).append(local)
        report["cleanup"] = "context-exited"
        report["status"] = "passed"
    except BaseException as error:
        # Do not serialize SDK exception text or authentication options.
        report.update(
            status="failed", error_type=type(error).__name__, cleanup="context-exit-requested"
        )
        raise
    finally:
        report["elapsed_seconds"] = time.monotonic() - started
        save()
    return report
