"""Replay one measured stage against verified inputs from an earlier benchmark."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

from ark_pipeline.runtime.benchmark_estimates import markdown_report
from ark_pipeline.runtime.checkpoints import artifact_inventory, pipeline_code, validate_inventory
from ark_pipeline.runtime.dashboard import Dashboard
from ark_pipeline.runtime.provenance import atomic_json, iso_now, runtime_identity, sha256
from ark_pipeline.spatial.coverage import load_spatial_profile

H3 = "spatial/serving/current"
CROSSWALK = "crosswalk/current/iucn_goat_crosswalk.parquet"
SPECIES = "species/species.parquet"
SYSTEMS = "species/species_systems.parquet"

# Only these flags are inputs. Output paths and scratch always come from plan().
INPUT_FLAGS = {
    "metadata": {"--h3-root": H3, "--crosswalk": CROSSWALK},
    "coarse_db": {"--species-parquet": SPECIES, "--species-systems-parquet": SYSTEMS,
                  "--h3-res3": f"{H3}/h3_res3_species_global_merged.parquet", "--crosswalk": CROSSWALK},
    "fine_metrics": {"--parts-dir": f"{H3}/res7_merged_parts", "--species": SPECIES, "--species-systems": SYSTEMS},
    "prepared_inputs": {"--h3-root": H3, "--parts-dir": "metrics", "--build-duckdb": "build.duckdb",
                        "--species": SPECIES, "--species-systems": SYSTEMS,
                        "--metadata-template": "coarse-tiles/map-metadata.json"},
    "tiles": {"--prepared-inputs": "prepared-inputs.json"},
}
PREREQUISITES = {
    "source_scan": (), "crosswalk": (), "boundaries": (),
    "pairs": ("source_scan",), "lists": ("pairs",),
    "metadata": ("lists", "crosswalk"),
    "coarse_db": ("metadata", "lists", "crosswalk"),
    "coarse_cache": ("coarse_db",), "fine_metrics": ("lists", "metadata"),
    "prepared_inputs": ("coarse_cache", "fine_metrics"), "tiles": ("prepared_inputs",),
}


def validate_replay_inventory(source, inventory, directories):
    """Check recorded members first, then reject additions to consumed directories."""
    validate_inventory(source, inventory)
    for relative in directories:
        directory = source / relative
        expected = {member for member in inventory if Path(member).is_relative_to(relative)}
        actual = set()
        for file in directory.rglob("*"):
            if not file.is_file():
                continue
            resolved = file.resolve(strict=True)
            if not resolved.is_relative_to(source):
                raise ValueError(f"Replay input escapes its benchmark: {file}")
            actual.add(str(file.relative_to(source)))
        if actual != expected:
            raise ValueError(f"Saved benchmark directory membership changed: {relative}")


def validate_replay_identities(config, selection=None):
    if load_spatial_profile(Path(config["profile"])).digest != config["profile_sha256"]:
        raise ValueError("Spatial profile changed during stage replay")
    if selection is not None:
        sample = Path(selection["path"])
        if sha256(sample) != selection["sha256"] or sha256(sample.with_suffix(".json")) != selection["metadata_sha256"]:
            raise ValueError("Polygon sample or metadata changed during stage replay")


def replay_plan(args):
    from ark_pipeline.cli.benchmark_pipeline import REPOSITORY_ROOT, plan

    if not args.only_stage or args.from_run is None:
        raise ValueError("Use --only-stage STAGE together with --from-run DIRECTORY")
    if args.rebuild_sample or args.max_per_bin is not None:
        raise ValueError("Stage replay inherits the saved sample; omit --rebuild-sample and --max-per-bin")
    source = args.from_run.expanduser().resolve(strict=True)
    saved = json.loads((source / "config.json").read_text())
    previous = json.loads((source / "benchmark-report.json").read_text())
    completed = {stage["name"] for stage in previous["stages"] if stage["status"] == "passed"}
    missing = set(PREREQUISITES[args.only_stage]) - completed
    if missing:
        raise ValueError("Previous benchmark lacks completed prerequisites: " + ", ".join(sorted(missing)))
    root = Path(saved["root"])
    output = args.output_root or root / "benchmarks/stages" / (iso_now().replace(":", "-") + "-" + uuid.uuid4().hex[:8])
    replay_args = argparse.Namespace(**{**vars(args), "root": root, "output_root": output,
        "sample": Path(saved["sample"]), "profile": Path(saved["profile"]),
        "max_per_bin": saved["max_per_bin"], **{key: Path(path) for key, path in saved["boundaries"].items()}})
    config, planned, environment = plan(replay_args)
    output = Path(config["output"])
    if output == source or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("Replay output must be separate from the input benchmark directory")
    if output.exists():
        raise ValueError(f"Benchmark output already exists; choose a new --output-root: {output}")
    name = args.only_stage
    if name == "boundaries":
        stage = {"name": name, "command": [sys.executable, "-m", "ark_pipeline.cli.benchmark_pipeline",
                                          "--stage", name, "--config", str(output / "config.json")]}
    else:
        stage = next(stage for stage in planned if stage["name"] == name)
    if name == "tiles":
        stage["timeout_seconds"] = 1200
    required = []
    for flag, relative in INPUT_FLAGS.get(name, {}).items():
        path = source / relative
        required.append(path)
        stage["command"][stage["command"].index(flag) + 1] = str(path)
    if name == "pairs":
        required.append(source / "selection.json")
    if name == "lists":
        config["input_root"] = str(source)
        required += [source / relative for relative in ("pairs.parquet", "pairs-report.json", "selection.json")]
    if name == "coarse_cache":
        required.append(source / "source.duckdb")
        environment["SOURCE_DUCKDB_PATH"] = str(source / "source.duckdb")
    boundary_inputs = {}
    if name in {"coarse_cache", "tiles"}:
        for path in config["boundaries"].values():
            boundary = Path(path).resolve()
            if boundary.is_relative_to(source):
                required.append(boundary)
            else:
                boundary_inputs[str(boundary)] = None
    report = {"schema_version": 1, "scope": "stage-replay", "status": "planned", "created_at": iso_now(),
              **config, "from_run": str(source), "only_stage": name, "planned_stages": [stage],
              "environment": environment, "stages": [], "reused_inputs": list(map(str, required)),
              "reference_stage": next((s for s in previous["stages"] if s["name"] == name), None),
              "reference_resources": saved["resources"],
              "reference_runtime": previous.get("runtime"),
              "reference_measurement_environment": previous.get("measurement_environment"),
              "warnings": ["Stage replay uses previously built inputs; it does not measure a full pipeline build.",
                           "Source, profile, sample and boundary paths are inherited from --from-run; worker and memory settings are those shown for this invocation."]}
    if not args.dry_run:
        if sha256(root / "acquisition/current.json") != previous["acquisition_manifest_sha256"]:
            raise ValueError("Acquisition manifest changed since the input benchmark")
        if load_spatial_profile(Path(config["profile"])).digest != saved["profile_sha256"]:
            raise ValueError("Spatial profile changed since the input benchmark")
        # Validate only consumed artifacts; unrelated outputs need not be present.
        inventory, directories = {}, []
        for path in required:
            target = path.resolve(strict=True)
            if not target.is_relative_to(source):
                raise ValueError(f"Replay input escapes its benchmark: {path}")
            relative = str(target.relative_to(source))
            recorded = previous.get("artifacts", {})
            if target.is_dir():
                members = {member: expected for member, expected in recorded.items()
                           if Path(member).is_relative_to(relative)}
                if not members:
                    raise ValueError(f"Replay input was not recorded as complete: {relative}")
                inventory.update(members)
                directories.append(relative)
            else:
                if relative not in recorded:
                    raise ValueError(f"Replay input was not recorded as complete: {relative}")
                inventory[relative] = recorded[relative]
        validate_replay_inventory(source, inventory, directories)
        if boundary_inputs:
            identity = json.loads((source / "dashboard-state.json").read_text()).get("identity") or {}
            expected_boundaries = {str(Path(path).resolve()): digest for path, digest in identity.get("external_files", {}).items()}
            for path in boundary_inputs:
                digest = sha256(Path(path))
                if digest != expected_boundaries.get(path):
                    raise ValueError(f"Boundary changed or was not fingerprinted by the input benchmark: {path}")
                boundary_inputs[path] = digest
        report["boundary_inputs_sha256"] = boundary_inputs
        report["input_inventory"] = inventory
        report["input_directories"] = directories
        report["acquisition_manifest_sha256"] = previous["acquisition_manifest_sha256"]
        report["code_sha256"] = pipeline_code(REPOSITORY_ROOT)
        report["runtime"] = runtime_identity(REPOSITORY_ROOT)
    return report, stage, config, environment


def run_replay(args):
    from ark_pipeline.cli.benchmark_pipeline import run_stage, select_sample

    report, stage, config, environment = replay_plan(args)
    if args.dry_run:
        return report
    output, source = Path(config["output"]), Path(report["from_run"])
    selection = None
    if stage["name"] == "pairs":
        selection = json.loads((source / "selection.json").read_text())
        # Reapply current exclusions and ensure the saved fixture has not changed.
        current = select_sample(Path(config["sample"]), Path(config["root"]),
                                load_spatial_profile(Path(config["profile"])), config["max_per_bin"])
        if current != selection:
            raise ValueError("Polygon selection changed since the input benchmark")
    validate_replay_identities(config, selection)
    output.mkdir(parents=True, exist_ok=False)
    atomic_json(output / "config.json", config)
    if stage["name"] == "pairs":
        shutil.copy2(source / "selection.json", output / "selection.json")
    report["status"] = "running"

    def save():
        atomic_json(output / "benchmark-report.json", report)
        (output / "benchmark-report.md").write_text(markdown_report(report))

    save()
    started = time.perf_counter()
    dashboard = Dashboard([stage["name"]], output, config["resources"], ui=args.ui)
    try:
        with dashboard:
            measured = run_stage(stage, output, {**os.environ, **environment}, dashboard=dashboard)
            report["stages"].append(measured)
            if measured["status"] != "passed":
                raise RuntimeError(f"{stage['name']} failed; see {measured['log']}")
            validate_replay_inventory(source, report["input_inventory"], report["input_directories"])
            validate_replay_identities(config, selection)
            for path, digest in report["boundary_inputs_sha256"].items():
                if sha256(Path(path)) != digest:
                    raise ValueError(f"Boundary changed during stage replay: {path}")
            if sha256(Path(config["root"]) / "acquisition/current.json") != report["acquisition_manifest_sha256"]:
                raise ValueError("Acquisition manifest changed during stage replay")
            report["artifacts"] = artifact_inventory(output)
            report["status"] = "passed"
            dashboard.status = "passed"
    except (Exception, KeyboardInterrupt) as error:
        report.update(status="interrupted" if isinstance(error, KeyboardInterrupt) else "failed", error=str(error) or "Interrupted")
    finally:
        report["wall_seconds"] = time.perf_counter() - started
        save()
    return report
