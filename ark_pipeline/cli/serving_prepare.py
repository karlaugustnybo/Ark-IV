"""Run acquisition, spatial processing and serving preparation in one workflow."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

from rich.console import Console

from ark_pipeline.cli.serving_metadata import validate_crosswalk_sources
from ark_pipeline.cli.sources_acquire import load_manifest
from ark_pipeline.cli.spatial_analytics import boundary_catalogue
from ark_pipeline.cli.spatial_pairs import (
    DEFAULT_PROFILE,
    REPOSITORY_ROOT,
    default_duckdb_memory_limit,
)
from ark_pipeline.runtime.checkpoints import find_checkpoint, pipeline_code, source_identity
from ark_pipeline.runtime.dashboard import Dashboard, run_command
from ark_pipeline.runtime.forecasts import load_prior
from ark_pipeline.runtime.provenance import atomic_json, iso_now
from ark_pipeline.runtime.resources import positive_int, resolve_resources, worker_count
from ark_pipeline.spatial.coverage import load_spatial_profile


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--root", type=Path, default=Path(os.environ.get("GLOBAL_DATA_ROOT", "data/external")))
    result.add_argument("--profile", type=Path, default=Path(os.environ.get("SPATIAL_PROFILE", str(DEFAULT_PROFILE))))
    sources = result.add_mutually_exclusive_group()
    sources.add_argument("--spatial-root", type=Path, help="Override the profile's derived pair directory.")
    sources.add_argument("--h3-root", type=Path, help="Use an existing serving-list pack and skip spatial export.")
    result.add_argument("--crosswalk", type=Path, default=Path(os.environ.get(
        "GLOBAL_CROSSWALK_PATH", "data/exports/iucn_goat_global/iucn_goat_crosswalk.parquet"
    )))
    result.add_argument("--preview-root", type=Path, default=None)
    result.add_argument("--acquire", choices=("download", "update"), help="Acquire sources before checking the crosswalk and building.")
    result.add_argument(
        "--build-pairs",
        action="store_true",
        help="Build/resume range, point, and HydroBASINS pairs before aggregation.",
    )
    result.add_argument("--crosswalk-mode", choices=("refresh", "require-current"), default="require-current",
                        help="Rebuild matches automatically, or require the supplied crosswalk to be current.")
    result.add_argument("--iucn-details-dir", type=Path, default=os.environ.get("GLOBAL_IUCN_DETAILS_DIR"))
    result.add_argument("--iucn-metadata-mode", choices=("auto", "cache-only", "off"),
                        default=os.environ.get("GLOBAL_IUCN_METADATA_MODE", "auto"))
    result.add_argument("--reviewed-evidence-path", type=Path,
                        default=os.environ.get("GLOBAL_IUCN_REVIEWED_EVIDENCE_PATH"))
    result.add_argument("--spatial-analytics", action="store_true", help="Build separate global range denominators from the retained memberships.")
    result.add_argument("--spatial-coverage-status", choices=('complete', 'partial', 'unknown'), default='unknown')
    result.add_argument("--spatial-omissions", type=Path, help="Existing missing-basin relationship audit for per-species flags.")
    result.add_argument("--spatial-jurisdictions", type=Path, help="Checked resolution-7 jurisdiction memberships for stewardship.")
    result.add_argument("--spatial-boundary-catalogue", action="append", type=boundary_catalogue, help="Repeat framework=GeoJSON for stewardship from mapped cells.")
    result.add_argument("--spatial-weights", type=Path, help="Explicit species weights for separate range-rarity outputs.")
    result.add_argument("--dna-evidence", action='store_true', help='Build tree-independent checked DNA evidence scenarios.')
    result.add_argument("--phylogenetic", action='store_true', help="Enrich from registered tree/OTT sources and checked identity inputs.")
    result.add_argument("--phylogenetic-input-dir", type=Path, help="Optional legacy pinned tree/OTT directory; requires --phylogenetic-links-dir.")
    result.add_argument("--phylogenetic-links-dir", type=Path)
    result.add_argument("--phylogenetic-ott-index", type=Path)
    result.add_argument("--phylogenetic-tree-tips", type=Path)
    result.add_argument("--phylogenetic-baseline-review", type=Path)
    result.add_argument("--phylogenetic-config", type=Path, default=Path('config/edge_prototype.toml'))
    result.add_argument("--phylogenetic-scope", choices=('mammals', 'global'), default='mammals')
    result.add_argument("--phylogenetic-aggregates", action='store_true', help="Build separate default coarse/fine opportunity aggregates; no base-map changes.")
    result.add_argument("--tiles", action="store_true", help="Continue through validated coarse PMTiles and dynamic H3 serving.")
    result.add_argument("--ui", choices=("auto", "rich", "plain"), default="auto", help="Live Rich dashboard in a terminal; plain output when redirected.")
    result.add_argument("--fresh", action="store_true", help="Start fresh dashboard history; production outputs still undergo normal reuse validation.")
    result.add_argument("--benchmark-report", type=Path, help="ETA prior; defaults to the latest passed benchmark with matching source/profile fingerprints.")
    result.add_argument("--workers", type=worker_count, help="Shared parallelism for all compute stages: positive integer or auto (also PIPELINE_WORKERS).")
    for flag in ("spatial-workers", "metric-workers", "duckdb-threads", "metric-threads", "tile-threads", "tile-duckdb-threads"):
        result.add_argument("--" + flag, type=positive_int, help="Override the shared setting for this stage.")
    result.add_argument("--dry-run", action="store_true", help="Print paths and commands without building or verifying sources.")
    return result


def preparation_plan(args: argparse.Namespace) -> tuple[list[list[str]], dict[str, str]]:
    root = args.root.expanduser().resolve()
    phylogenetic = bool(args.phylogenetic or args.phylogenetic_input_dir or args.phylogenetic_links_dir)
    if phylogenetic and (args.phylogenetic_links_dir is None or
                        (args.phylogenetic_input_dir is None and args.phylogenetic_ott_index is None)):
        raise ValueError('Phylogenetic enrichment requires checked links and either pinned inputs or an OTT index for registered sources')
    if not phylogenetic and any((args.phylogenetic_ott_index, args.phylogenetic_tree_tips,
                                args.phylogenetic_baseline_review, args.phylogenetic_aggregates,
                                args.phylogenetic_scope != 'mammals')):
        raise ValueError('Phylogenetic options require --phylogenetic and checked identity inputs')
    if not args.spatial_analytics and any((args.spatial_omissions, args.spatial_jurisdictions, args.spatial_weights, args.spatial_boundary_catalogue)):
        raise ValueError('Spatial analytical inputs require --spatial-analytics')
    if args.spatial_jurisdictions and args.spatial_boundary_catalogue:
        raise ValueError('Choose prepared jurisdictions or boundary catalogues')
    if len(dict(args.spatial_boundary_catalogue or [])) != len(args.spatial_boundary_catalogue or []):
        raise ValueError('Duplicate boundary framework')
    resources = resolve_resources(**{name: getattr(args, name) for name in (
        "workers", "spatial_workers", "metric_workers", "duckdb_threads", "metric_threads", "tile_threads", "tile_duckdb_threads",
    )})
    # This new-pipeline entry point deliberately defaults to the selected profile,
    # even if .env still points GLOBAL_H3_ROOT at the previous data pack.
    commands = []
    if args.acquire:
        commands.append([
            sys.executable, "-m", "ark_pipeline.cli.sources_sync", args.acquire, "--root", str(root),
        ])
    crosswalk = args.crosswalk.expanduser().resolve()
    if args.crosswalk_mode == "refresh":
        crosswalk_root = root / "derived/iucn-goat-crosswalk"
        crosswalk = crosswalk_root / "current/iucn_goat_crosswalk.parquet"
        commands.append([
            sys.executable, "-m", "ark_pipeline.cli.crosswalk_refresh", "--root", str(root),
            "--output-root", str(crosswalk_root),
        ])
    if args.h3_root is not None:
        h3_root = args.h3_root.expanduser().resolve()
    else:
        profile = load_spatial_profile(args.profile)
        spatial_root = (args.spatial_root or root / "derived" / profile.profile_id).expanduser().resolve()
        h3_root = spatial_root / "serving/current"
        if args.build_pairs:
            commands.append([
                sys.executable, "-m", "ark_pipeline.cli.spatial_pairs", "build",
                "--root", str(root), "--profile", str(args.profile.expanduser().resolve()),
                "--output-root", str(spatial_root),
            ])
        commands.append([
            sys.executable, "-m", "ark_pipeline.cli.spatial_aggregate", "run",
            "--root", str(root), "--profile", str(args.profile.expanduser().resolve()),
            "--output-root", str(spatial_root),
        ])
    commands.append(["just", "global-prepare"])
    analysis_root = (args.preview_root or Path(os.environ.get("GLOBAL_PREVIEW_ROOT", str(root / "ark_iv_global_preview")))).expanduser().resolve()
    if args.spatial_analytics:
        commands.append([sys.executable, "-m", "ark_pipeline.cli.spatial_analytics",
            "--h3-root", str(h3_root), "--output-root", str(analysis_root / 'analysis/spatial'),
            "--profile", load_spatial_profile(args.profile).profile_id,
            "--coverage-status", args.spatial_coverage_status,
            *(['--omissions', str(args.spatial_omissions.expanduser().resolve())] if args.spatial_omissions else []),
            *(['--jurisdictions', str(args.spatial_jurisdictions.expanduser().resolve())] if args.spatial_jurisdictions else []),
            *[v for name, path in (args.spatial_boundary_catalogue or []) for v in ('--boundary-catalogue', f'{name}={path.expanduser().resolve()}')],
            *(['--weights', str(args.spatial_weights.expanduser().resolve())] if args.spatial_weights else []),
            "--memory-limit", os.environ.get('DUCKDB_MEMORY_LIMIT') or default_duckdb_memory_limit(),
            "--scratch-dir", str(analysis_root / 'scratch/analysis'), "--threads", str(resources.duckdb_threads)])
    if args.dna_evidence:
        commands.append([sys.executable, '-m', 'ark_pipeline.cli.dna_prepare', '--root', str(root),
            '--crosswalk', str(crosswalk), '--output-root', str(analysis_root / 'analysis/dna')])
    if phylogenetic:
        commands.append([sys.executable, "-m", "ark_pipeline.cli.phylogenetic_prepare",
            "--root", str(root), "--crosswalk", str(crosswalk),
            *(['--input-dir', str(args.phylogenetic_input_dir.expanduser().resolve())] if args.phylogenetic_input_dir else []),
            *(['--ott-index', str(args.phylogenetic_ott_index.expanduser().resolve())] if args.phylogenetic_ott_index else []),
            *(['--tree-tips', str(args.phylogenetic_tree_tips.expanduser().resolve())] if args.phylogenetic_tree_tips else []),
            "--links-dir", str(args.phylogenetic_links_dir.expanduser().resolve()),
            "--config", str(args.phylogenetic_config.expanduser().resolve()),
            "--scope", args.phylogenetic_scope,
            "--output-root", str(analysis_root / 'analysis/phylogenetic'),
            *(['--baseline-review', str(args.phylogenetic_baseline_review.expanduser().resolve())] if args.phylogenetic_baseline_review else [])])
    if args.phylogenetic_aggregates:
        commands.append([sys.executable, '-m', 'ark_pipeline.cli.analytical_prepare',
            '--phylogenetic-generation', str(analysis_root / 'analysis/phylogenetic/current'),
            '--h3-root', str(h3_root), '--profile', load_spatial_profile(args.profile).profile_id,
            '--output-root', str(analysis_root / 'analysis/opportunities'),
            '--scratch-dir', str(analysis_root / 'scratch/opportunities'),
            '--memory-limit', os.environ.get('DUCKDB_MEMORY_LIMIT') or default_duckdb_memory_limit(),
            '--threads', str(resources.duckdb_threads)])
    if args.tiles:
        commands.append(["just", "data-tiles"])
    preview_root = args.preview_root or Path(os.environ.get(
        "GLOBAL_PREVIEW_ROOT", str(root / "ark_iv_global_preview")
    ))
    environment = {
        "GLOBAL_DATA_ROOT": str(root),
        "GLOBAL_IUCN_METADATA_MODE": args.iucn_metadata_mode,
        "SPATIAL_PROFILE": str(args.profile.expanduser().resolve()),
        "GLOBAL_H3_ROOT": str(h3_root),
        "GLOBAL_CROSSWALK_PATH": str(crosswalk),
        "GLOBAL_PREVIEW_ROOT": str(preview_root.expanduser().resolve()),
        "GLOBAL_BUILD_ROOT": str(analysis_root / 'build'),
        "GLOBAL_ADM2_ROOT": str(analysis_root / 'adm2'),
        "GLOBAL_STATIC_DATA": str(analysis_root / 'static/data'),
        **resources.environment(),
        "DUCKDB_MEMORY_LIMIT": os.environ.get("DUCKDB_MEMORY_LIMIT") or default_duckdb_memory_limit(),
        "DUCKDB_SCRATCH_DIR": str(Path(os.environ.get("DUCKDB_SCRATCH_DIR") or root / "scratch/spatial").expanduser().resolve()),
    }
    if args.iucn_details_dir:
        environment["GLOBAL_IUCN_DETAILS_DIR"] = str(args.iucn_details_dir.expanduser().resolve())
    if args.reviewed_evidence_path:
        environment["GLOBAL_IUCN_REVIEWED_EVIDENCE_PATH"] = str(args.reviewed_evidence_path.expanduser().resolve())
    return commands, environment


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    report = {"status": "planned", "completed_commands": []}
    report_path = None
    dashboard = None
    try:
        commands, overrides = preparation_plan(args)
        report.update(commands=commands, environment=overrides)
        report["resources"] = resolve_resources(environ=overrides).report()
        if args.dry_run:
            print(json.dumps(report, indent=2))
            return 0
        environment = {**os.environ, **overrides}
        report_path = Path(overrides["GLOBAL_PREVIEW_ROOT"]) / "prepare-report.json"
        report["status"] = "running"
        console = Console()
        if args.ui == "rich" or (args.ui == "auto" and console.is_terminal):
            mapping = {"ark_pipeline.cli.sources_sync": ["acquisition"], "ark_pipeline.cli.crosswalk_refresh": ["crosswalk"],
                       "ark_pipeline.cli.spatial_pairs": ["pairs"], "ark_pipeline.cli.spatial_aggregate": ["lists"],
                       "ark_pipeline.cli.spatial_analytics": ["spatial_analytics"],
                       "ark_pipeline.cli.phylogenetic_prepare": ["phylogenetic"],
                       "ark_pipeline.cli.dna_prepare": ["dna_evidence"]}
            mapping['ark_pipeline.cli.analytical_prepare'] = ['opportunities']
            names = []
            for command in commands:
                names.extend(["boundaries", "metadata", "coarse_db", "coarse_cache", "fine_metrics", "prepared_inputs"]
                             if command == ["just", "global-prepare"] else ["tiles"] if command == ["just", "data-tiles"] else mapping[command[2]])
            run_output = report_path.parent / "runs" / (iso_now().replace(":", "-") + "-" + uuid.uuid4().hex[:8])
            from ark_pipeline.builders.iucn_metadata import evidence_identity

            identity = {"mode": "full", "commands": commands, "environment": overrides,
                        "profile": load_spatial_profile(args.profile).digest, "code": pipeline_code(REPOSITORY_ROOT),
                        "sources": source_identity(args.root.expanduser().resolve()),
                        "reviewed_evidence": evidence_identity(args.reviewed_evidence_path)}
            if not args.fresh:
                run_output = find_checkpoint(report_path.parent / "runs", identity) or run_output
            run_output.mkdir(parents=True, exist_ok=True)
            prior, note = load_prior(args.root.expanduser().resolve(), load_spatial_profile(args.profile).digest, args.benchmark_report)
            dashboard = Dashboard(names, run_output, report["resources"], prior=prior, note=note, mode="full", ui=args.ui, console=console, identity=identity)
            report["progress_directory"] = str(run_output)
            report["estimate_prior"] = str(prior["path"]) if prior else None
            dashboard.__enter__()
        crosswalk_checked = False
        for command in commands:
            source_preparation = command[1:3] in (["-m", "ark_pipeline.cli.sources_sync"], ["-m", "ark_pipeline.cli.crosswalk_refresh"])
            if not source_preparation and not crosswalk_checked:
                # Check the newly acquired snapshots before expensive spatial
                # filling, not the previous manifest before an update.
                crosswalk = Path(overrides["GLOBAL_CROSSWALK_PATH"])
                if not crosswalk.is_file():
                    raise ValueError("reviewed crosswalk is missing; set GLOBAL_CROSSWALK_PATH (see docs/pipeline/06_iucn_goat_global_crosswalk.md), then rerun the same command")
                crosswalk = crosswalk.resolve(strict=True)
                validate_crosswalk_sources(crosswalk, load_manifest(Path(overrides["GLOBAL_DATA_ROOT"])))
                environment["GLOBAL_CROSSWALK_PATH"] = str(crosswalk)
                report["crosswalk"] = environment["GLOBAL_CROSSWALK_PATH"]
                crosswalk_checked = True
            if command == ["just", "global-prepare"]:
                # Pin one complete generation for the whole metadata/coarse/fine
                # run, even if another export advances serving/current meanwhile.
                generation = Path(overrides["GLOBAL_H3_ROOT"]).resolve(strict=True)
                environment["GLOBAL_H3_ROOT"] = str(generation)
                report["h3_generation"] = str(generation)
            if command[1:3] in (["-m", "ark_pipeline.cli.dna_prepare"],
                                  ["-m", "ark_pipeline.cli.phylogenetic_prepare"],
                                  ["-m", "ark_pipeline.cli.analytical_prepare"],
                                  ["-m", "ark_pipeline.cli.spatial_analytics"]):
                # Explicit analytical CLI arguments must share the generations
                # pinned for metadata, even if another process advances current.
                command = list(command)
                for flag, setting in (('--crosswalk', 'GLOBAL_CROSSWALK_PATH'), ('--h3-root', 'GLOBAL_H3_ROOT')):
                    if flag in command:
                        command[command.index(flag) + 1] = environment[setting]
            if dashboard:
                name = "serving" if command == ["just", "global-prepare"] else "tiles" if command == ["just", "data-tiles"] else mapping[command[2]][0]
                measured = run_command({"name": name, "command": command}, dashboard.output, environment, dashboard, cwd=REPOSITORY_ROOT)
                if measured["exit_code"]:
                    raise subprocess.CalledProcessError(measured["exit_code"], command)
                # Acquisition may select a new source snapshot. An old baseline
                # must not survive that transition without another identity check.
                if name == "acquisition":
                    sources = source_identity(args.root.expanduser().resolve())
                    changed = sources != dashboard.identity["sources"]
                    current, note = load_prior(args.root.expanduser().resolve(), load_spatial_profile(args.profile).digest)
                    if changed or current != dashboard.forecast.prior:
                        from ark_pipeline.runtime.forecasts import Forecast

                        previous = dashboard.forecast
                        dashboard.forecast = Forecast(current, report["resources"], "full")
                        dashboard.forecast.started.update({k: v for k, v in previous.started.items() if not changed or k == "acquisition"})
                        dashboard.forecast.finished.update({k: v for k, v in previous.finished.items() if not changed or k == "acquisition"})
                        if changed:
                            for pending in dashboard.names:
                                if pending != "acquisition":
                                    dashboard.states[pending] = {"status": "pending", "phase": "Sources changed; awaiting validation"}
                            dashboard.tasks.clear()
                        dashboard.note = note
                        report["estimate_prior"] = current["path"] if current else None
                    dashboard.identity["sources"] = sources
            else:
                print("Preparing: " + " ".join(command), flush=True)
                subprocess.run(command, cwd=REPOSITORY_ROOT, env=environment, check=True)
            report["completed_commands"].append(command)
            atomic_json(report_path, report)
        report["status"] = "passed"
        report["completed_at"] = iso_now()
        atomic_json(report_path, report)
        if not dashboard:
            print(json.dumps(report, indent=2))
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError, KeyboardInterrupt) as error:
        exit_code = 130 if isinstance(error, KeyboardInterrupt) else 2 if isinstance(error, subprocess.CalledProcessError) and error.returncode == 2 else 1
        report.update(status="interrupted" if exit_code == 130 else "action-required" if exit_code == 2 else "failed", error=str(error) or "Interrupted")
        if report_path is not None:
            atomic_json(report_path, report)
        if not dashboard:
            print(json.dumps(report, indent=2))
        return exit_code
    finally:
        if dashboard:
            dashboard.status = report["status"]
            dashboard.__exit__(None, None, None)
            dashboard.console.print(f"{report['status'].upper()} · Report: {report_path}")


if __name__ == "__main__":
    raise SystemExit(main())
