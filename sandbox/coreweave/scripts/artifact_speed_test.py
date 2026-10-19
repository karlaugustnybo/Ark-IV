"""Small private W&B staging test, followed by bounded cloud downloads."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import time
import urllib.request
from pathlib import Path

os.environ.setdefault("GRPC_DNS_RESOLVER", "native")
os.environ.setdefault("WANDB_SILENT", "true")
from cloud_storage import credentials
from launch import cost_plan

REMOTE = r"""
import hashlib, json, os, time
from pathlib import Path
import wandb
api = wandb.Api(timeout=60)
a = api.artifact(os.environ['ARK_SPEED_ARTIFACT'])
expected = json.loads(os.environ['ARK_SPEED_HASHES'])
results = []
for mode in ['sequential', 'parallel']:
    root = Path('/scratch') / mode
    start = time.monotonic()
    if mode == 'sequential':
        for name in expected:
            a.get_entry(name).download(root=str(root), skip_cache=True)
    else:
        a.download(root=str(root), skip_cache=True)
    elapsed = time.monotonic() - start
    count = 0
    for name, sha in expected.items():
        p = root / name
        assert hashlib.sha256(p.read_bytes()).hexdigest() == sha
        count += p.stat().st_size
    results.append(dict(mode=mode, bytes=count, seconds=elapsed,
                        mb_per_second=count / elapsed / 1e6,
                        sha256_verified=True, local_cache_bypassed=True))
print(json.dumps(results))
"""


def graphql(query, variables=None):
    request = urllib.request.Request(
        "https://api.wandb.ai/graphql",
        data=json.dumps({"query": query, "variables": variables or {}}).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Basic "
            + base64.b64encode(("api:" + os.environ["WANDB_API_KEY"]).encode()).decode(),
        },
    )
    response = json.load(urllib.request.urlopen(request, timeout=30))
    if response.get("errors"):
        raise RuntimeError("Private project API request failed")
    return response["data"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run", action="store_true", help="Explicitly authorize synthetic upload and paid compute"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--payload-mib", type=int, default=16, help="Total payload MiB")
    parser.add_argument("--files", type=int, default=4)
    parser.add_argument("--cpu-rate", type=float, required=True)
    parser.add_argument("--memory-rate", type=float, required=True)
    args = parser.parse_args()
    if not 1 <= args.files <= args.payload_mib <= 256:
        parser.error("Use 1–256 MiB total and between 1 and payload MiB files")
    plan = cost_plan(8, 32, 600, args.cpu_rate, args.memory_rate)
    print(json.dumps(plan), flush=True)
    if not args.run:
        return 0
    import wandb
    from cwsandbox import AuthStrategy, DataPlaneMode, Sandbox, ScratchVolumeOptions

    credentials()
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    output = args.output
    output.mkdir(parents=True, exist_ok=False)
    os.environ["WANDB_DIR"] = str(output)
    os.environ["WANDB_CACHE_DIR"] = str(output / "local-cache")
    report = {
        "status": "starting",
        "payload_bytes": args.payload_mib * 1024**2,
        "rate_usd_per_hour": plan["rate_usd_per_hour"],
        "requested": plan,
    }
    artifact = None
    billable_start = None
    try:
        api = wandb.Api(timeout=30)
        entity = api.default_entity
        project = args.project
        data = graphql(
            """mutation($input:UpsertModelInput!){
            upsertModel(input:$input){project{name access}}}""",
            {
                "input": {
                    "entityName": entity,
                    "name": project,
                    "access": "PRIVATE",
                    "description": "Synthetic storage transfer benchmark",
                }
            },
        )
        if data["upsertModel"]["project"]["access"].upper() != "PRIVATE":
            raise RuntimeError("Project privacy not verified")
        report["project_private_verified"] = True
        hashes = {}
        for i in range(args.files):
            name = f"random-{i}.bin"
            p = output / name
            h = hashlib.sha256()
            with p.open("wb") as f:
                for _ in range(
                    args.payload_mib // args.files + int(i < args.payload_mib % args.files)
                ):
                    chunk = os.urandom(1024**2)
                    f.write(chunk)
                    h.update(chunk)
            hashes[name] = h.hexdigest()
        print("Uploading bounded synthetic artifact; no sandbox running.", flush=True)
        start = time.monotonic()
        with wandb.init(
            entity=entity,
            project=project,
            job_type="storage-speed-test",
            name=f"speed-{stamp}",
            settings=wandb.Settings(init_timeout=60),
        ) as run:
            artifact = wandb.Artifact(f"synthetic-speed-{stamp}", type="speed-test")
            for name in hashes:
                artifact.add_file(str(output / name), name=name)
            artifact = run.log_artifact(artifact)
            artifact.wait(timeout=300)
            path = f"{entity}/{project}/{artifact.name}"
        report["local_upload_seconds"] = time.monotonic() - start
        report["local_upload_mb_per_second"] = (
            report["payload_bytes"] / report["local_upload_seconds"] / 1e6
        )
        report["artifact_path"] = path
        (output / "speed-report.json").write_text(json.dumps(report, indent=2))
        print(
            f"Artifact staged in {report['local_upload_seconds']:.1f}s. Starting 10-minute capped sandbox.",
            flush=True,
        )
        billable_start = time.monotonic()
        with Sandbox.run(
            container_image=args.image,
            auth=AuthStrategy.WANDB,
            resources={"cpu": "8", "memory": "32Gi"},
            volumes=[ScratchVolumeOptions(name="scratch", mount_path="/scratch", size="512Gi")],
            data_plane_mode=DataPlaneMode.GATEWAY,
            max_lifetime_seconds=600,
            environment_variables={
                "WANDB_API_KEY": os.environ["WANDB_API_KEY"],
                "WANDB_SILENT": "true",
                "WANDB_CACHE_DIR": "/scratch/cache",
                "ARK_SPEED_ARTIFACT": path,
                "ARK_SPEED_HASHES": json.dumps(hashes),
            },
            tags=["ark-artifact-speed-test"],
        ) as sb:
            report["sandbox_id"] = sb.sandbox_id
            (output / "speed-report.json").write_text(json.dumps(report, indent=2))
            sb.exec(["true"], timeout_seconds=30).result()
            print("Sandbox running. Checking prepared downloader.", flush=True)
            install = sb.exec(["python", "-c", "import wandb"], timeout_seconds=120).result()
            if install.returncode:
                raise RuntimeError("Prepared image has no working downloader")
            sb.write_file("/scratch/speed_test.py", REMOTE.encode()).result()
            result = sb.exec(["python", "/scratch/speed_test.py"], timeout_seconds=300).result()
            if result.returncode:
                (output / "cloud-error.log").write_text(
                    result.stderr.replace(os.environ["WANDB_API_KEY"], "[REDACTED]")
                )
                raise RuntimeError("Cloud download failed; see private local log")
            report["downloads"] = json.loads(result.stdout.strip().splitlines()[-1])
        report["cleanup"] = "sandbox context stopped"
        report["allocation_request_to_context_exit_seconds"] = time.monotonic() - billable_start
        report["elapsed_rate_scenario_usd"] = (
            report["allocation_request_to_context_exit_seconds"]
            / 3600
            * report["rate_usd_per_hour"]
        )
        report["status"] = "passed"
    except Exception as exc:
        report["status"] = "failed"
        report["error_type"] = type(exc).__name__
        print(f"Test failed: {type(exc).__name__}; saved report.", flush=True)
    finally:
        report["retention"] = "Synthetic payload and cloud artifact retained; no implicit deletion"
        (output / "speed-report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2), flush=True)
        print(f"Report: {output / 'speed-report.json'}", flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
