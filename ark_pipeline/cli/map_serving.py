"""Publish coarse PMTiles and pinned dynamic H3 partitions as one serving bundle."""

from __future__ import annotations

import argparse
import errno
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import duckdb
import h3
import pyarrow.parquet as pq

from ark_pipeline.runtime.progress import emit
from ark_pipeline.runtime.provenance import atomic_json, code_fingerprint, identity_digest
from ark_pipeline.spatial.boundaries import load_jurisdiction_index
from backend.res7_tiles import REQUIRED_PARTITION_COLUMNS

CONTRACT = "dynamic-h3-v1"
GLOBAL_TILE_DIR = Path(__file__).resolve().parents[2] / "data/global/tiles"
BOUNDARY_SETTINGS = {
    "admin0": "JURISDICTIONS_PATH",
    "admin1": "ADMIN1_BOUNDARIES_PATH",
    "municipality": "MUNICIPALITY_BOUNDARIES_PATH",
    "eez": "EEZ_BOUNDARIES_PATH",
    "conservation_framework": "CONSERVATION_BOUNDARIES_PATH",
}


def pin(source: Path, target: Path) -> None:
    """Hard links survive upstream atomic replacement without copying large data.

    Managed producers must replace files atomically, never modify published
    inodes in place. Immutable managed generations can also be referenced;
    mutable inputs are copied if the filesystem cannot pin their inode.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    source = source.resolve(strict=True)
    try:
        os.link(source, target)
    except OSError as error:
        if error.errno not in {errno.EXDEV, errno.EPERM, errno.ENOTSUP}:
            raise
        # Saved benchmark runs and content-addressed generations are immutable
        # managed inputs. Reference them on filesystems without hard links.
        if "benchmarks" in source.parts or "generations" in source.parts:
            target.symlink_to(source)
        else:
            shutil.copy2(source, target)


def prepare_map(args):
    from ark_pipeline.builders.coarse_cache import wide_aggregate_query
    from ark_pipeline.cli.serving_tiles import (
        BOUNDARIES,
        ROOT,
        BoundaryBatchIndex,
        compile_queries,
        file_record,
        publish,
        read_prepared,
        reusable,
        tool_record,
        valid_archive,
        write_receipt,
    )
    from ark_pipeline.tiles import METRIC_COLUMNS

    started = time.perf_counter()
    emit("phase", phase="Validate prepared inputs", force=True)
    prepared = read_prepared(args.prepared_inputs)
    boundaries = {key: file_record(getattr(args, key)) for key in BOUNDARIES}
    identity = {
        "serving_contract": CONTRACT,
        "schema_version": 2,
        "prepared": prepared,
        "boundaries": boundaries,
        "compiler": tool_record(args.tippecanoe),
        "code": code_fingerprint(
            [
                Path(__file__),
                ROOT / "ark_pipeline/cli/serving_tiles.py",
                ROOT / "ark_pipeline/tiles.py",
                ROOT / "ark_pipeline/spatial/boundaries.py",
            ]
        ),
    }
    generation = args.output_dir / "generations" / identity_digest(identity)
    outputs = ("priorities.pmtiles", "map-metadata.json", "serving.json")
    if reusable(generation, identity, outputs):
        validate_config(generation / "serving.json")
        publish(args.output_dir, generation)
        return {
            "status": "passed",
            "reused": True,
            "serving_contract": CONTRACT,
            "seconds": time.perf_counter() - started,
            "current": str(args.output_dir / "current"),
        }
    if generation.exists():
        generation = generation.with_name(generation.name + f"-{time.time_ns()}")
    generation.mkdir(parents=True)
    report = {"status": "building", "serving_contract": CONTRACT, "phases": {}}
    watched = [Path(r["path"]) for r in prepared["files"].values()]
    watched += [Path(r["path"]) for part in prepared["parts"].values() for r in part.values()]
    watched += [Path(r["path"]) for r in boundaries.values()]
    watched += [Path(r["path"]) for r in prepared.get("exports", {}).values()]

    def versions():
        return {str(p): (p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns) for p in watched}

    before = versions()
    try:
        phase = time.perf_counter()
        emit("phase", phase="Prepare dynamic fine data", force=True)
        for base, records in prepared["parts"].items():
            aggregate = Path(records["aggregate"]["path"])
            if not REQUIRED_PARTITION_COLUMNS <= set(pq.ParquetFile(aggregate).schema_arrow.names):
                raise ValueError(f"Incompatible fine aggregate schema: {aggregate}")
            pin(aggregate, generation / "aggregates" / f"base_{base}.parquet")
            pin(
                Path(records["source"]["path"]),
                generation / "cell-species" / f"base_{base}.parquet",
            )
        template = Path(prepared["files"]["metadata"]["path"])
        metadata = json.loads(template.read_text())
        # Preserve the current global-start domains, boundary metadata and zoom
        # transition. Fine scores are already computed from these same metrics.
        metadata.update(
            res7_delivery=CONTRACT,
            res7_base_cells=sorted(map(int, prepared["parts"])),
            available_resolutions=[3, 7],
            complete_resolutions=[3, 7],
            detail_resolutions=[3, 7],
        )
        snapshot = metadata.get("coarse_snapshot")
        if snapshot:
            pin(
                template.parent / Path(snapshot["url"]).name,
                generation / Path(snapshot["url"]).name,
            )
        atomic_json(generation / "map-metadata.json", metadata)
        environment = {
            "TILE_DIR": str(generation),
            "PMTILES_PATH": str(generation / "priorities.pmtiles"),
            "MAP_METADATA_PATH": str(generation / "map-metadata.json"),
            "RES7_PARTS_DIR": str(generation / "cell-species"),
            "RES7_AGGREGATE_PARTS_DIR": str(generation / "aggregates"),
        }
        pinned_boundaries = {}
        for key, record in boundaries.items():
            target = generation / "boundaries" / f"{key}.geojson"
            pin(Path(record["path"]), target)
            pinned_boundaries[key] = file_record(target)
            environment[BOUNDARY_SETTINGS[key]] = str(target)
        build = Path(prepared["files"]["build_duckdb"]["path"])
        # Export selection is recorded before preparation, never discovered here.
        exports = prepared.get("exports", {})
        if exports:
            required = {"species.parquet", "species_systems.parquet", "cell_species_lists.parquet"}
            if not required <= exports.keys():
                raise ValueError("Incomplete PostgreSQL export set")
            for name, record in exports.items():
                if name == "species_passage_cells_res3.parquet" and "species_passage" in prepared["files"]:
                    continue  # The receipt-verified explicit supplement is authoritative.
                pin(Path(record["path"]), generation / "exports" / name)
            if "species_passage" in prepared["files"]:
                pin(
                    Path(prepared["files"]["species_passage"]["path"]),
                    generation / "exports/species_passage_cells_res3.parquet",
                )
                if "species_passage_report" in prepared["files"]:
                    pin(Path(prepared["files"]["species_passage_report"]["path"]),
                        generation / "exports/species_passage_cells_res3.report.json")
            environment["EXPORT_DIR"] = str(generation / "exports")
        if "cell_boundaries.parquet" in exports:
            path = generation / "exports/cell_boundaries.parquet"
            coarse = set()
            with duckdb.connect(str(build), read_only=True) as connection:
                coarse = {int(row[0], 16) for row in connection.execute("SELECT h3_index FROM h3_res3_agg_all").fetchall()}
            rows = pq.read_table(path).to_pylist()
            if len(rows) != len(coarse) or {row["h3_index"] for row in rows} != coarse:
                raise ValueError("SQL boundary exports do not cover the coarse map cells")
            for framework, record in pinned_boundaries.items():
                index = load_jurisdiction_index(record["path"])
                if any(row["resolution"] != 3 or sorted(row[framework] or []) != sorted(index.codes_for_cell(h3.int_to_str(row["h3_index"]))) for row in rows):
                    raise ValueError(f"SQL boundary export differs from map boundaries: {framework}")
        report["phases"]["fine_preparation_seconds"] = time.perf_counter() - phase
        phase = time.perf_counter()
        emit("phase", phase="Compile coarse PMTiles", force=True)
        args.scratch_dir.mkdir(parents=True, exist_ok=True)
        indexes = {
            key: BoundaryBatchIndex.from_path(Path(record["path"]))
            for key, record in boundaries.items()
        }
        with duckdb.connect(str(build), read_only=True) as connection:
            connection.execute("SET threads=?", [args.threads])
            connection.execute("SET memory_limit=?", [args.memory_limit])
            count = connection.execute("SELECT count(*) FROM h3_res3_agg_all").fetchone()[0]
            names = ", ".join(f'"{name}"' for name in METRIC_COLUMNS)
            query = f"WITH metrics(h3_index, {names}) AS ({wide_aggregate_query(3)}) SELECT * FROM metrics ORDER BY h3_index"
            report["coarse"] = compile_queries(
                connection,
                [("coarse", 3, query, count)],
                generation / "priorities.pmtiles",
                indexes,
                args,
                {**os.environ, "TIPPECANOE_MAX_THREADS": str(args.tile_threads)},
            )
        report["phases"]["coarse_compilation_seconds"] = time.perf_counter() - phase
        phase = time.perf_counter()
        emit("phase", phase="Validate complete map serving output", force=True)
        if not valid_archive(generation / "priorities.pmtiles"):
            raise ValueError("Invalid coarse PMTiles archive")
        config = {
            "schema_version": 1,
            "serving_contract": CONTRACT,
            "status": "passed",
            "environment": environment,
            "boundaries": pinned_boundaries,
            "coverage": metadata["res7_base_cells"],
            "pinned_files": {
                str(p): {"bytes": p.stat().st_size, "modified_ns": p.stat().st_mtime_ns}
                for p in generation.rglob("*")
                if p.is_file() and not p.name.startswith(".")
            },
        }
        atomic_json(generation / "serving.json", config)
        validate_config(generation / "serving.json")
        read_prepared(args.prepared_inputs)
        if before != versions():
            raise ValueError("Prepared input changed during serving preparation")
        report["phases"]["validation_seconds"] = time.perf_counter() - phase
        write_receipt(generation, identity, outputs)
        report.update(
            status="passed",
            reused=False,
            seconds=time.perf_counter() - started,
            features=count,
            current=str(args.output_dir / "current"),
        )
        atomic_json(generation / "build-report.json", report)
        publish(args.output_dir, generation)
        return report
    except BaseException as error:
        report.update(status="failed", error=str(error))
        atomic_json(generation / "build-report.json", report)
        raise


def validate_config(path: Path) -> dict:
    from ark_pipeline.cli.serving_tiles import valid_archive, verify_record

    config = json.loads(path.read_text())
    if config.get("status") != "passed" or config.get("serving_contract") != CONTRACT:
        raise ValueError("No validated dynamic serving configuration; run just data-tiles")
    environment = config["environment"]
    if not valid_archive(Path(environment["PMTILES_PATH"])):
        raise ValueError("Coarse PMTiles archive is missing or invalid")
    metadata = json.loads(Path(environment["MAP_METADATA_PATH"]).read_text())
    expected = set(config["coverage"])
    if metadata.get("res7_delivery") != CONTRACT or set(metadata["res7_base_cells"]) != expected:
        raise ValueError("Metadata and fine coverage disagree")
    for setting in ("RES7_PARTS_DIR", "RES7_AGGREGATE_PARTS_DIR"):
        parts = {
            int(p.stem.removeprefix("base_")): p
            for p in Path(environment[setting]).glob("base_*.parquet")
        }
        if set(parts) != expected or not expected:
            raise ValueError(f"Incomplete serving coverage: {setting}")
        for part in parts.values():
            columns = set(pq.ParquetFile(part).schema_arrow.names)
            required = (
                REQUIRED_PARTITION_COLUMNS
                if setting == "RES7_AGGREGATE_PARTS_DIR"
                else {"h3_cell", "species_ids"}
            )
            if not required <= columns:
                raise ValueError(f"Invalid serving schema: {part}")
    for filename, expected_version in config.get("pinned_files", {}).items():
        stat = Path(filename).stat()
        if {"bytes": stat.st_size, "modified_ns": stat.st_mtime_ns} != expected_version:
            raise ValueError(f"Published serving input changed: {filename}")
    snapshot = metadata.get("coarse_snapshot")
    if snapshot and not (Path(environment["TILE_DIR"]) / Path(snapshot["url"]).name).is_file():
        raise ValueError("Coarse Arrow snapshot is missing")
    for record in config["boundaries"].values():
        verify_record(record)
    return config


def reject_benchmark_bundle(path: Path, config: dict) -> None:
    """Global startup must never select benchmark inputs, including symlinks."""

    def paths(value):
        if isinstance(value, dict):
            for key, item in value.items():
                yield from paths(key)
                yield from paths(item)
        elif isinstance(value, list):
            for item in value:
                yield from paths(item)
        elif isinstance(value, str) and value.startswith("/"):
            yield Path(value)

    for candidate in [path, *paths(config)]:
        if "benchmarks" in candidate.parts or "benchmarks" in candidate.resolve().parts:
            raise ValueError(
                "Global startup cannot use a benchmark serving bundle or benchmark inputs"
            )


def compatible_preview_metadata(record: dict) -> bool:
    return bool(
        record.get("score_domains")
        and record.get("species_normalized_score_domains")
        and record.get("tile_layout") == "wide-v2-joint-priority"
        and (
            record.get("boundary_assignment") == "cell-intersection"
            or record.get("jurisdiction_assignment") == "cell-intersection"
        )
    )


def global_preview_config(root: Path) -> dict:
    """Prefer the atomically published full build; retain the legacy preview."""
    from ark_pipeline.cli.serving_tiles import valid_archive

    root = root.resolve()
    current = root / "tiles/current"
    if current.exists() or current.is_symlink():
        path = current / "serving.json"
        config = json.loads(path.read_text())
        reject_benchmark_bundle(path, config)
        return validate_config(path)
    reject_benchmark_bundle(root, {})
    archive = root / "priorities-res3-res7.pmtiles"
    metadata = root / "map-metadata.json"
    record = json.loads(metadata.read_text()) if metadata.is_file() else {}
    if not valid_archive(archive) or not compatible_preview_metadata(record):
        root = GLOBAL_TILE_DIR
        reject_benchmark_bundle(root, {})
        archive = root / "priorities.pmtiles"
        metadata = root / "map-metadata.json"
        record = json.loads(metadata.read_text()) if metadata.is_file() else {}
        if not valid_archive(archive) or not compatible_preview_metadata(record):
            raise ValueError("No frontend-compatible global map bundle; run just data-tiles")
        snapshot = record.get("coarse_snapshot")
        if snapshot and not (root / Path(snapshot["url"]).name).is_file():
            raise ValueError("Global coarse snapshot is missing; run just data-tiles")
        print(
            "Legacy global preview is missing or out of date. Using the compatible local "
            "global map; run just data-tiles to publish the full serving bundle.",
            flush=True,
        )
    if record.get("res7_delivery") == CONTRACT:
        raise ValueError("Dynamic fine data requires a validated serving.json")
    print(
        f"Using existing global preview: {root} "
        f"(resolutions: {record.get('available_resolutions', [])})",
        flush=True,
    )
    return {
        "coverage": record.get("res7_base_cells", []),
        "environment": {
            "TILE_DIR": str(root),
            "PMTILES_PATH": str(archive),
            "MAP_METADATA_PATH": str(metadata),
            "RES7_PARTS_DIR": "",
            "RES7_AGGREGATE_PARTS_DIR": "",
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["start", "validate", "load"])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--config", type=Path)
    source.add_argument("--preview-root", type=Path)
    args = parser.parse_args()
    try:
        if args.preview_root is not None:
            config = global_preview_config(args.preview_root)
        else:
            if not args.config.is_file():
                parser.error(
                    f"Serving configuration not found: {args.config}. "
                    "Build the serving bundle with just data-tiles, or set "
                    "GLOBAL_SERVING_CONFIG to an existing validated serving.json."
                )
            path = args.config.resolve(strict=True)
            reject_benchmark_bundle(path, json.loads(path.read_text()))
            config = validate_config(path)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    if args.command == "load":
        import sys

        if "EXPORT_DIR" not in config["environment"]:
            parser.error("This serving bundle has no PostgreSQL exports")
        return subprocess.call(
            [sys.executable, "-m", "backend.load_postgres"],
            env={**os.environ, **config["environment"]},
        )
    if args.command == "start":
        return subprocess.call(["just", "serve"], env={**os.environ, **config["environment"]})
    print(json.dumps({"status": "passed", "coverage": config["coverage"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
