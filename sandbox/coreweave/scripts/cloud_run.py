"""Launch, watch and stop a full CoreWeave pipeline run (`just cloud-run`).

`launch` shows the allocation and its maximum cost, asks for confirmation,
creates the sandbox, uploads the working tree (tracked and unignored files) plus
the private extras and opens the live monitor. The sandbox's main command runs
bootstrap, driver and retention, then exits, so the sandbox ends itself whether
or not anyone is watching. Ctrl-C only detaches; `just cloud-watch` reattaches.

Private details (driver, extra files, image, size, rates) live in the ignored
`results/cloud-run.json`; allocations are recorded in `results/cloud-runs.json`.
Credentials come from the environment only.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import time
from types import SimpleNamespace

import watch_run

REPOSITORY = watch_run.REPOSITORY
PRIVATE = REPOSITORY / "sandbox/coreweave/results"
CONFIG = PRIVATE / "cloud-run.json"
LEDGER = PRIVATE / "cloud-runs.json"
MODES = {"fast": "config/spatial_semantics_iucn_richness_v3_fast_partial_basins.toml",
         "precise": "config/spatial_semantics_iucn_richness_v3_partial_basins.toml"}
SECRET = re.compile(rb"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{36,}"
                    rb"|AKIA[A-Z0-9]{16}|sk-[A-Za-z0-9_-]{32,}")

# The sandbox's main command. It starts once the launcher's upload is complete
# (bootstrap.sh appears last) and exits after the run, which completes the
# sandbox and ends billing. The linger lets watchers read the final journal.
ENTRY = """
for _ in $(seq 900); do [ -f /probe/fullrun/bootstrap.sh ] && break; sleep 2; done
[ -f /probe/fullrun/bootstrap.sh ] || exit 1
bash /probe/fullrun/bootstrap.sh
code=$?
sleep 120
exit $code
"""

# Runs in the sandbox only (it installs system tools). One command per line so
# `set -e`/ERR see every failure. Any failure before the driver owns the journal
# is written as a terminal `failed` journal.
BOOTSTRAP = r"""
set -euo pipefail
cd /probe && mkdir -p fullrun
exec >>fullrun/bootstrap.log 2>&1
fail() {
  python3 - "$1" <<'PY'
import json, os, sys, time
from pathlib import Path
path = Path('/probe/fullrun/status.json')
try:
    journal = json.loads(path.read_text())
except (OSError, ValueError):
    journal = {'started_epoch': float(os.environ['FULLRUN_STARTED']), 'steps': []}
if journal.get('status') not in {'passed', 'failed'}:
    journal.update(status='failed', error=sys.argv[1], finished_epoch=time.time())
    path.write_text(json.dumps(journal))
PY
}
trap 'fail "bootstrap failed at line $LINENO"' ERR
echo "bootstrap start $(date -u +%FT%TZ)"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq build-essential libsqlite3-dev zlib1g-dev git curl >/dev/null
curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
export PATH=/root/.local/bin:$PATH
uv tool install rust-just==1.58.0
git clone -q --depth 1 --branch 2.75.1 https://github.com/felt/tippecanoe /tmp/tippecanoe
make -s -C /tmp/tippecanoe -j"$(nproc)" >/dev/null
make -s -C /tmp/tippecanoe install >/dev/null
uv sync --frozen --group edge -q
uv pip install -q wandb
echo "bootstrap done $(date -u +%FT%TZ)"
trap - ERR
.venv/bin/python "$DRIVER" >>fullrun/driver.log 2>&1 || fail "driver exited $?"
"""


def load_config() -> dict:
    if not CONFIG.exists():
        raise SystemExit(f"Missing private run config {CONFIG.relative_to(REPOSITORY)}; see scripts/README.md.")
    return json.loads(CONFIG.read_text())


def load_ledger() -> dict:
    return json.loads(LEDGER.read_text()) if LEDGER.exists() else {"runs": []}


def save_ledger(ledger: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(ledger, indent=2) + "\n")


def cost(config: dict, hours: float) -> float:
    rates = config["rates"]
    return (config["cpu"] * rates["cpu_hour"] + config["memory_gib"] * rates["gib_hour"]) * hours


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=REPOSITORY, text=True)


def payload(extras: list[str]) -> tuple[bytes, dict]:
    """The working tree as the run sees it: tracked and unignored files plus private extras."""
    names = sorted({*git("ls-files", "-co", "--exclude-standard", "-z").split("\0"), *extras} - {""})
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name in names:
            path = REPOSITORY / name
            if not path.is_file():
                if name in extras:
                    raise SystemExit(f"Private extra is missing: {name}")
                continue  # deleted but still tracked
            data = path.read_bytes()
            if SECRET.search(data):
                raise SystemExit(f"Possible credential in {name}; not uploading.")
            item = tarfile.TarInfo(name)
            item.size, item.mode, item.mtime = len(data), 0o755 if os.access(path, os.X_OK) else 0o644, path.stat().st_mtime
            archive.addfile(item, io.BytesIO(data))
    data = buffer.getvalue()
    identity = {"commit": git("rev-parse", "HEAD").strip(), "dirty": bool(git("status", "--porcelain").strip()),
                "payload_sha256": hashlib.sha256(data).hexdigest(), "payload_bytes": len(data)}
    return data, identity


def sandbox(sandbox_id: str):
    from cwsandbox import AuthStrategy, DataPlaneMode, Sandbox

    return Sandbox.from_id(sandbox_id, auth=AuthStrategy.WANDB, data_plane_mode=DataPlaneMode.GATEWAY).result()


def check(result, what: str) -> None:
    if result.returncode:
        raise RuntimeError(f"{what} failed: {result.stderr[-400:]}")


def launch(args) -> int:
    from cloud_storage import credentials
    from cwsandbox import AuthStrategy, DataPlaneMode, Sandbox

    config = load_config()
    mode = "precise" if args.precise else "fast"
    hours = args.hours or config["hours"][mode]
    lifetime = int(hours * 3600)
    data, identity = payload(config["extras"])
    print(f"Mode       {mode} · {MODES[mode]}")
    print(f"Sandbox    {config['cpu']} CPU · {config['memory_gib']} GiB · up to {hours:g} h")
    print(f"Max cost   ${cost(config, hours):.2f} if it runs the full lifetime (compute only; stopped on finish)")
    print(f"Code       {identity['commit'][:12]}{' + uncommitted changes' if identity['dirty'] else ''}"
          f" · {identity['payload_bytes'] / 2**20:.0f} MiB")
    if args.continue_from:
        print(f"Continue   {args.continue_from}")
    if not args.yes and input("Start this run? [y/N] ").strip().lower() not in {"y", "yes"}:
        print("Not started.")
        return 0
    credentials()
    started = time.time()
    record = {"mode": mode, "cpu": config["cpu"], "memory_gib": config["memory_gib"], "lifetime_seconds": lifetime,
              "reserved_usd": round(cost(config, hours), 4), "started_epoch": started, "status": "requested", **identity}
    ledger = load_ledger()
    ledger["runs"].append(record)
    save_ledger(ledger)
    environment = {"WANDB_API_KEY": os.environ["WANDB_API_KEY"], "WANDB_SILENT": "true",
                   "FULLRUN_STARTED": str(started), "FULLRUN_DEADLINE": str(int(started + lifetime)),
                   "ARK_COVERAGE_MODE": mode, "DRIVER": "/probe/" + config["driver"]}
    if args.continue_from:
        environment["FULLRUN_CONTINUE"] = args.continue_from
    box = Sandbox.run("bash", "-c", ENTRY, container_image=config["image"], auth=AuthStrategy.WANDB, max_lifetime_seconds=lifetime,
                      resources={"cpu": str(config["cpu"]), "memory": f"{config['memory_gib']}Gi"},
                      environment_variables=environment, data_plane_mode=DataPlaneMode.GATEWAY,
                      tags=["ark-full-run", f"ark-{mode}"])
    record.update(sandbox_id=box.sandbox_id, status="running")
    save_ledger(ledger)
    print(f"Sandbox    {box.sandbox_id}")
    try:
        check(box.exec(["mkdir", "-p", "/probe/fullrun"], timeout_seconds=60).result(), "Preparing /probe")
        box.write_file_streaming("/probe/code.tar", data, timeout_seconds=600).result()
        check(box.exec(["tar", "-m", "-xf", "/probe/code.tar", "-C", "/probe"], timeout_seconds=300).result(), "Unpacking code")
        # The driver replaces this journal; until then the monitor shows bootstrap as restore time.
        journal = {"status": "running", "mode": mode, "started_epoch": started, "steps": []}
        box.write_file_streaming("/probe/fullrun/status.json", json.dumps(journal).encode(), timeout_seconds=60).result()
        box.write_file_streaming("/probe/fullrun/bootstrap.tmp", BOOTSTRAP.encode(), timeout_seconds=60).result()
        check(box.exec(["mv", "/probe/fullrun/bootstrap.tmp", "/probe/fullrun/bootstrap.sh"], timeout_seconds=60).result(),
              "Starting the run")
    except BaseException:
        print("Launch failed; stopping the sandbox.")
        stop_sandbox(box.sandbox_id)
        raise
    return follow(box.sandbox_id, mode)


def follow(sandbox_id: str, mode: str) -> int:
    """Watch until the run ends; the sandbox stops itself, so Ctrl-C only detaches."""
    from cloud_storage import credentials

    credentials()
    mirror = REPOSITORY / ".tmp/cloud-runs" / sandbox_id
    mirror.mkdir(parents=True, exist_ok=True)
    options = SimpleNamespace(sandbox_id=sandbox_id, mode=mode, baseline=watch_run.latest_baseline(mode=mode),
                              preview_root="/probe/full-candidate", journal="/probe/fullrun/status.json",
                              mirror=mirror, poll_seconds=1.0, once=False, keep_open=False)
    code = watch_run.watch(watch_run.sandbox_executor(sandbox_id), options)
    if code == 130:
        print(f"Detached; the run continues and its sandbox stops itself when done. "
              f"Reattach: just cloud-watch · stop early: just cloud-stop {sandbox_id}")
        return code
    record_status(sandbox_id, "passed" if code == 0 else "failed")
    return code


def record_status(sandbox_id: str, status: str) -> None:
    ledger = load_ledger()
    for record in ledger["runs"]:
        if record.get("sandbox_id") == sandbox_id:
            record.update(status=status, ended_epoch=time.time())
    save_ledger(ledger)


def stop_sandbox(sandbox_id: str, status: str = "stopped") -> None:
    sandbox(sandbox_id).stop().result()
    record_status(sandbox_id, status)
    print(f"Stopped sandbox {sandbox_id} (run {status}).")


def latest_running() -> dict:
    running = [r for r in load_ledger()["runs"] if r.get("sandbox_id") and r["status"] == "running"]
    if not running:
        raise SystemExit("No running cloud run in the ledger; pass a sandbox id.")
    return running[-1]


def resolve(sandbox_id: str | None) -> dict:
    if not sandbox_id:
        return latest_running()
    known = next((r for r in load_ledger()["runs"] if r.get("sandbox_id") == sandbox_id), None)
    return known or {"sandbox_id": sandbox_id, "mode": "fast"}


def parser():
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = result.add_subparsers(dest="command", required=True)
    start = commands.add_parser("launch", help="Start a full run and watch it.")
    start.add_argument("--precise", action="store_true",
                       help="Exact edge/corner coverage (correct mode) instead of fast native overlap.")
    start.add_argument("--hours", type=float, help="Hard sandbox lifetime (default from the private config).")
    start.add_argument("--continue-from", help="Retained interrupted-output catalogue to resume from.")
    start.add_argument("--yes", action="store_true", help="Skip the cost confirmation.")
    for name, text in (("watch", "Reattach the live monitor to a run."),
                       ("stop", "Stop a run's sandbox now.")):
        command = commands.add_parser(name, help=text)
        command.add_argument("sandbox_id", nargs="?", help="Default: latest running run in the ledger.")
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    os.environ.setdefault("WANDB_SILENT", "true")
    if args.command == "launch":
        return launch(args)
    record = resolve(args.sandbox_id)
    if args.command == "watch":
        return follow(record["sandbox_id"], record.get("mode", "fast"))
    from cloud_storage import credentials

    credentials()
    stop_sandbox(record["sandbox_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
