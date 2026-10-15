"""Private, resumable pipeline input staging and result persistence; no sandboxes."""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
if HERE.name == "scripts":
    HERE = HERE.parent
DEFAULT_PROJECT = "ark-pipeline-storage"
EXTRA_INPUTS = ("gbif_backbone_species.tsv", "2024_EDGE_species_external_with_gbif.tsv")
LOOKUP_PINS = "acquisition/lookup-pins.json"
RESULT_DIRS = ("tiles", "coarse-tiles", "species", "metrics", "exports")


class TransferError(RuntimeError):
    """A transfer may be retried without discarding committed inputs."""


def committed_file(api, artifact_path, sha, size):
    if not api.artifact_exists(artifact_path):
        return None
    stored = api.artifact(artifact_path)
    if stored.state != "COMMITTED":
        return None
    if stored.metadata.get("sha256") != sha or stored.get_entry("payload").size != size:
        raise ValueError("Existing file artifact failed identity check.")
    return stored


def wait_for_commit(artifact):
    try:
        artifact.wait(timeout=24 * 3600)
    except Exception:
        # SDK error messages can contain authenticated request URLs.
        raise TransferError("Cloud transfer failed; committed files remain reusable.") from None
    if artifact.state != "COMMITTED":
        raise TransferError("Cloud artifact was not committed.")


def credentials():
    if not os.environ.get("WANDB_API_KEY"):
        raise ValueError(
            "Supply WANDB_API_KEY through the environment; credential files are never loaded."
        )
    os.environ.setdefault("WANDB_SILENT", "true")


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(8 * 1024**2):
            h.update(chunk)
    return h.hexdigest()


def safe_path(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Unsafe relative path: {relative}")
    path = root / relative
    if any(
        (root / Path(*relative.parts[:i])).is_symlink() for i in range(1, len(relative.parts) + 1)
    ):
        raise ValueError("Symlinks are not storage inputs")
    if path.resolve().is_relative_to(root.resolve()) and path.is_file():
        if any(p.startswith(".env") or p in {".git", ".DS_Store"} for p in relative.parts):
            raise ValueError(f"Refusing credential/metadata path: {relative}")
        return path
    raise ValueError(f"Missing file or path outside data root: {relative}")


def input_plan(root):
    manifest_path = safe_path(root, "acquisition/current.json")
    manifest = json.loads(manifest_path.read_text())
    selected = {"acquisition/current.json": {}}
    for source in manifest["sources"].values():
        for item in source.get("files", []):
            previous = selected.setdefault(item["path"], item)
            if previous and previous != item:
                raise ValueError(f"Conflicting manifest record: {item['path']}")
    if (root / LOOKUP_PINS).exists() or (root / LOOKUP_PINS).is_symlink():
        pins = json.loads(safe_path(root, LOOKUP_PINS).read_text())
        selected[LOOKUP_PINS] = {}
        for item in pins["files"]:
            name = item["path"]
            if name not in EXTRA_INPUTS:
                raise ValueError("Lookup pins may only select supported lookup files")
            previous = selected.setdefault(name, item)
            if previous != item:
                raise ValueError(f"Conflicting lookup pin: {name}")
    for name in EXTRA_INPUTS:
        if (root / name).is_file():
            selected.setdefault(name, {})
    plan = []
    for relative, record in sorted(selected.items()):
        if relative in EXTRA_INPUTS and (
            type(record.get("bytes")) is not int
            or not isinstance(record.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])
        ):
            raise ValueError(
                "Lookup inputs require explicit size/SHA256 pins in the manifest or " + LOOKUP_PINS
            )
        path = safe_path(root, relative)
        size = path.stat().st_size
        if "bytes" in record and size != record["bytes"]:
            raise ValueError(f"Size differs from acquisition manifest: {relative}")
        plan.append({"path": relative, "bytes": size, "expected_sha256": record.get("sha256")})
    return plan


def result_plan(root, includes):
    chosen = includes or [name for name in RESULT_DIRS if (root / name).is_dir()]
    files = set()
    for name in chosen:
        path = root / name
        if path.is_dir():
            files.update(p.relative_to(root).as_posix() for p in path.rglob("*") if p.is_file())
        else:
            files.add(name)
    if not includes:
        files.update(p.name for p in root.glob("*.json") if p.is_file())
        files.update(p.name for p in root.glob("*.pmtiles") if p.is_file())
    if not files:
        raise ValueError("No final outputs found. Specify --include paths relative to --root.")
    return [{"path": name, "bytes": safe_path(root, name).stat().st_size} for name in sorted(files)]


def validate_inventory(root, inventory):
    """Validate the entire untrusted inventory before touching any destination."""
    root = Path(root).resolve()
    seen = set()
    for item in inventory["files"]:
        relative = Path(item["path"])
        if (
            not item["path"]
            or relative.as_posix() != item["path"]
            or relative.is_absolute()
            or ".." in relative.parts
            or item["path"] in seen
            or "\\" in item["path"]
        ):
            raise ValueError("Unsafe or duplicate inventory path")
        seen.add(item["path"])
        if any(
            p.startswith(".env") or p in {".git", "_cloud_storage", "_transfer"}
            for p in relative.parts
        ):
            raise ValueError("Reserved inventory path")
        if (
            type(item["bytes"]) is not int
            or item["bytes"] < 0
            or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
        ):
            raise ValueError("Invalid inventory size or checksum")
        if not isinstance(item.get("artifact"), str) or not re.fullmatch(
            r"[^:/]+/[^:/]+/[^:/]+:v[0-9]+", item["artifact"]
        ):
            raise ValueError("Backing artifacts must have immutable version identities")
        target = root
        for part in relative.parts:
            target = target / part
            if target.is_symlink():
                raise ValueError("Symlink in download destination")
        if not target.resolve().is_relative_to(root):
            raise ValueError("Download escapes root")
    return inventory["files"]


def verify_inventory(root, inventory):
    for item in validate_inventory(root, inventory):
        path = Path(root) / item["path"]
        if (
            not path.is_file()
            or path.stat().st_size != item["bytes"]
            or digest(path) != item["sha256"]
        ):
            raise ValueError("Downloaded inventory checksum mismatch: " + item["path"])


def download_file(api, root, item):
    """Restore a single immutable file, verifying before atomically replacing it."""
    validate_inventory(root, {"files": [item]})
    root = Path(root).resolve()
    destination = root / item["path"]
    if (
        destination.is_file()
        and destination.stat().st_size == item["bytes"]
        and digest(destination) == item["sha256"]
    ):
        return "reused"
    stage = (
        root / "_cloud_storage" / "downloads" / hashlib.sha256(item["path"].encode()).hexdigest()
    )
    for path in [root / "_cloud_storage", root / "_cloud_storage/downloads", stage]:
        if path.is_symlink():
            raise ValueError("Symlink in transfer staging")
    stage.mkdir(parents=True, exist_ok=True)
    blob = api.artifact(item["artifact"])
    if blob.state != "COMMITTED":
        raise TransferError("Backing artifact is not committed")
    downloaded = Path(blob.get_entry("payload").download(root=str(stage), skip_cache=True))
    if downloaded.is_symlink() or not downloaded.resolve().is_relative_to(stage.resolve()):
        raise ValueError("SDK download escaped staging")
    if downloaded.stat().st_size != item["bytes"] or digest(downloaded) != item["sha256"]:
        raise ValueError("Downloaded checksum mismatch")
    destination.parent.mkdir(parents=True, exist_ok=True)
    downloaded.replace(destination)
    return "verified"


def private_project(entity, project):
    query = """mutation($input:UpsertModelInput!){
        upsertModel(input:$input){project{name access}}}"""
    request = urllib.request.Request(
        "https://api.wandb.ai/graphql",
        data=json.dumps(
            {
                "query": query,
                "variables": {
                    "input": {"entityName": entity, "name": project, "access": "PRIVATE"}
                },
            }
        ).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Basic "
            + base64.b64encode(("api:" + os.environ["WANDB_API_KEY"]).encode()).decode(),
        },
    )
    data = json.load(urllib.request.urlopen(request, timeout=60))
    if data.get("errors") or data["data"]["upsertModel"]["project"]["access"].upper() != "PRIVATE":
        raise RuntimeError("Could not establish a private storage project.")


def upload(args, plan, kind, build_exit=None):
    import wandb

    total = sum(item["bytes"] for item in plan)
    if total > args.max_gb * 1e9:
        raise ValueError(f"{total / 1e9:.2f} GB exceeds --max-gb {args.max_gb}; nothing uploaded.")
    for item in plan:
        path = safe_path(args.root, item["path"])
        if path.stat().st_size != item["bytes"] or (
            item.get("expected_sha256") and digest(path) != item["expected_sha256"]
        ):
            raise ValueError("Input changed or acquisition checksum mismatch")
    credentials()
    state = Path(args.state_dir).expanduser().resolve()
    state.mkdir(parents=True, exist_ok=True)
    os.environ["WANDB_DIR"] = str(state)
    os.environ["WANDB_CACHE_DIR"] = str(state / "cache")
    api = wandb.Api(timeout=60)
    entity = args.entity or api.default_entity
    private_project(entity, args.project)
    journal_path = state / f"{kind}-receipt.json"
    receipt = {
        "status": "uploading",
        "root": str(args.root),
        "bytes": total,
        "entity": entity,
        "project": args.project,
        "files": [],
        "build_exit_code": build_exit,
    }

    def save():
        temp = journal_path.with_suffix(".tmp")
        temp.write_text(json.dumps(receipt, indent=2) + "\n")
        temp.replace(journal_path)

    save()
    print(
        f"Private {kind} upload: {len(plan)} files, {total / 1e9:.2f} GB. No sandbox created.",
        flush=True,
    )
    with wandb.init(
        entity=entity,
        project=args.project,
        job_type=f"upload-{kind}",
        settings=wandb.Settings(init_timeout=120),
    ) as run:
        if args.input_artifact:
            run.use_artifact(args.input_artifact)
        catalog = wandb.Artifact(
            args.name,
            type="dataset" if kind == "inputs" else "pipeline-results",
            metadata={
                "schema_version": 2,
                "storage_format": "sha256-file-inventory",
                "kind": kind,
                "build_exit_code": build_exit,
                "input_artifact": args.input_artifact,
            },
        )
        for index, item in enumerate(plan, 1):
            path = safe_path(args.root, item["path"])
            before = path.stat()
            print(
                f"[{index}/{len(plan)}] Hashing {item['path']} ({item['bytes'] / 1e9:.3f} GB)",
                flush=True,
            )
            sha = digest(path)
            if item.get("expected_sha256") and item["expected_sha256"] != sha:
                raise ValueError(f"Acquisition checksum mismatch: {item['path']}")
            name = "file-" + sha
            artifact_path = f"{entity}/{args.project}/{name}:latest"
            stored = committed_file(api, artifact_path, sha, item["bytes"])
            if stored is not None:
                action = "reused"
            else:
                blob = wandb.Artifact(
                    name, type="pipeline-file", metadata={"sha256": sha, "bytes": item["bytes"]}
                )
                # Avoid duplicating the entire external dataset onto the Mac's internal disk.
                blob.add_file(str(path), name="payload", policy="immutable", skip_cache=True)
                stored = run.log_artifact(blob)
                print(f"[{index}/{len(plan)}] Uploading {item['path']}", flush=True)
                wait_for_commit(stored)
                action = "uploaded"
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError(f"File changed during upload: {item['path']}")
            # W&B caps artifact dependencies at five. Keep the committed file
            # artifact versions in the inventory rather than native references.
            receipt["files"].append(
                {
                    "path": item["path"],
                    "bytes": item["bytes"],
                    "sha256": sha,
                    "artifact": f"{entity}/{args.project}/{name}:{stored.version}",
                    "action": action,
                }
            )
            save()
            print(f"[{index}/{len(plan)}] Committed ({action}): {item['path']}", flush=True)
        inventory = state / f"{kind}-inventory.json"
        inventory.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "files": receipt["files"],
                    "kind": kind,
                    "build_exit_code": build_exit,
                },
                indent=2,
            )
            + "\n"
        )
        catalog.add_file(str(inventory), name="_cloud_storage/inventory.json")
        catalog = run.log_artifact(catalog)
        wait_for_commit(catalog)
        receipt["catalog"] = f"{entity}/{args.project}/{catalog.name}"
        receipt["status"] = "committed"
        save()
    print(f"COMPLETE: {receipt['catalog']}", flush=True)
    print(f"Receipt: {journal_path}", flush=True)
    return receipt


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    for command in ["upload-inputs", "upload-results", "run-and-save"]:
        c = sub.add_parser(command)
        c.add_argument(
            "--root",
            type=Path,
            required=True,
        )
        c.add_argument("--authorize-transfer", action="store_true")
        c.add_argument("--entity")
        c.add_argument("--project", default=DEFAULT_PROJECT)
        c.add_argument(
            "--name",
            default="pipeline-inputs" if command == "upload-inputs" else "pipeline-results",
        )
        c.add_argument("--max-gb", type=float, default=150 if command == "upload-inputs" else 50)
        c.add_argument("--state-dir", default=str(HERE / "results/cloud-storage"))
        c.add_argument("--input-artifact")
        c.add_argument("--dry-run", action="store_true")
        if command != "upload-inputs":
            c.add_argument(
                "--include",
                action="append",
                help="Relative final output file/directory; repeatable.",
            )
        if command == "run-and-save":
            c.add_argument("pipeline", nargs=argparse.REMAINDER)
    c = sub.add_parser("download", aliases=["download-inputs", "download-results"])
    c.add_argument("--authorize-transfer", action="store_true")
    c.add_argument("--artifact", required=True)
    c.add_argument("--root", type=Path, required=True)
    c.add_argument(
        "--only",
        action="append",
        help="Restore only these exact inventory paths; repeat for an explicit profiling subset",
    )
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if not getattr(args, "dry_run", False) and not args.authorize_transfer:
        raise ValueError("Transfer requires explicit --authorize-transfer after rights review")
    if hasattr(args, "max_gb") and (not math.isfinite(args.max_gb) or args.max_gb <= 0):
        raise ValueError("--max-gb must be positive and finite")
    if args.command in {"download", "download-inputs", "download-results"}:
        if not re.fullmatch(r"[^:/]+/[^:/]+/[^:/]+:v[0-9]+", args.artifact):
            raise ValueError("Select an immutable versioned catalog")
        import wandb

        credentials()
        args.root = args.root.expanduser().resolve()
        for path in [args.root / "_cloud_storage", args.root / "_cloud_storage/inventory.json"]:
            if path.is_symlink():
                raise ValueError("Symlink in inventory destination")
        api = wandb.Api(timeout=60)
        artifact = api.artifact(args.artifact)
        if artifact.state != "COMMITTED":
            raise TransferError("Catalog is not committed")
        artifact.get_entry("_cloud_storage/inventory.json").download(
            root=str(args.root), skip_cache=True
        )
        inventory = json.loads((args.root / "_cloud_storage/inventory.json").read_text())
        files = validate_inventory(args.root, inventory)
        if args.only:
            chosen = set(args.only)
            if not chosen <= {item["path"] for item in files}:
                raise ValueError("Selected path is absent from catalog")
            files = [item for item in files if item["path"] in chosen]
        for index, item in enumerate(files, 1):
            action = download_file(api, args.root, item)
            print(f"[{index}/{len(files)}] {action}: {item['path']}", flush=True)
        print("Downloaded artifact: all SHA-256 checks passed.", flush=True)
        return 0
    args.root = args.root.expanduser().resolve()
    if not args.root.is_dir():
        raise ValueError(
            f"Data root unavailable: {args.root}. Connect/mount the external drive first."
        )
    build_exit = None
    if args.command == "run-and-save":
        pipeline = args.pipeline[1:] if args.pipeline[:1] == ["--"] else args.pipeline
        if not pipeline:
            raise ValueError("Provide the pipeline command after --.")
        if args.dry_run:
            print("Would run:", json.dumps(pipeline))
            print("Would persist final outputs and wait for cloud commit. No command executed.")
            return 0
        build_exit = subprocess.run(pipeline, check=False).returncode
        print(
            f"Pipeline exited {build_exit}; persisting available final outputs before returning.",
            flush=True,
        )
    plan = (
        input_plan(args.root)
        if args.command == "upload-inputs"
        else result_plan(args.root, args.include)
    )
    if args.dry_run:
        total = sum(item["bytes"] for item in plan)
        print(json.dumps({"files": plan, "total_gb": total / 1e9, "max_gb": args.max_gb}, indent=2))
        return 0 if total <= args.max_gb * 1e9 else 1
    state = Path(args.state_dir).expanduser().resolve()
    state.mkdir(parents=True, exist_ok=True)
    with (state / "transfer.lock").open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("An upload is already running in this state directory.") from None
        lock.seek(0)
        lock.truncate()
        lock.write(str(os.getpid()))
        lock.flush()
        kind = "inputs" if args.command == "upload-inputs" else "results"
        try:
            upload(args, plan, kind, build_exit)
        except Exception as exc:
            receipt_path = state / f"{kind}-receipt.json"
            if receipt_path.exists():
                receipt = json.loads(receipt_path.read_text())
                if receipt.get("status") == "uploading":
                    receipt.update(status="interrupted", last_error_type=type(exc).__name__)
                    temp = receipt_path.with_suffix(".tmp")
                    temp.write_text(json.dumps(receipt, indent=2) + "\n")
                    temp.replace(receipt_path)
            raise
    return build_exit if build_exit is not None else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(2)
    except Exception as exc:
        # Only print the error type; request details may include signed URLs.
        print(
            f"Upload/download interrupted ({type(exc).__name__}). Committed files are reusable.",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(1)
