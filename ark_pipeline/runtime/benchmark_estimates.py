"""Explicit workload models for the post-acquisition pipeline benchmark."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

import h3

from ark_pipeline.runtime.provenance import code_fingerprint

SIZE_BREAKS = (1e-10, 1e-9, 1e-8, 1e-7, 1e-6, 1e-5, 1e-4, 3e-4,
               1e-3, 3e-3, 1e-2, 3e-2, 1e-1, 3e-1, 1., 3., 10., 30.,
               100., 300., 1000., 3000., 10000.)


def estimate(stages: list[dict], population: dict, pairs: dict, lists: dict, *, serving_contract: str | None = None) -> dict:
    """Project measured work; scenario ranges are not statistical confidence intervals.

    Forced minimum/maximum polygons exercise the kernel and downstream stages,
    but are not random observations. Use them only in a completely sampled band.
    """
    if serving_contract is None:
        serving_contract = next((stage.get("serving_contract", "static-pmtiles-v1")
                                 for stage in stages if stage["name"] == "tiles"), "static-pmtiles-v1")
    if serving_contract not in {"dynamic-h3-v1", "static-pmtiles-v1"}:
        raise ValueError(f"Unsupported tile estimate contract: {serving_contract}")
    groups = defaultdict(list)
    for row in pairs["observations"]:
        if not 0 <= row["size_bin"] < len(population["size_bin_counts"]) or population["size_bin_counts"][row["size_bin"]] == 0:
            raise ValueError("Sample contains an observation outside the current population")
        groups[row["size_bin"]].append(row)
    bands = []
    global_kernel = global_pairs = 0.0
    missing = []
    for band, count in enumerate(population["size_bin_counts"]):
        if not count:
            continue
        all_rows = groups[band]
        if len(all_rows) > count:
            raise ValueError(f"Sample exceeds the current population in band {band}")
        rows = all_rows if len(all_rows) == count else [r for r in all_rows if not r["forced_extreme"]]
        if not rows:
            missing.append(band)
            bands.append({"size_bin": band, "population": count, "observations": 0})
            continue
        seconds = sum(r["kernel_seconds"] for r in rows) / len(rows)
        cells = sum(r["output_pairs"] for r in rows) / len(rows)
        global_kernel += seconds * count
        global_pairs += cells * count
        bands.append({"size_bin": band, "population": count, "observations": len(rows),
                      "mean_kernel_seconds": seconds, "mean_output_pairs": cells})
    result = {
        "serving_contract": serving_contract,
        "status": "incomplete" if missing else "planning-estimate",
        "missing_size_bins": missing,
        "bands": bands,
        "assumptions": [
            "Size-band means represent other eligible polygons in that band; forced extremes are excluded unless the entire band was sampled.",
            "Pair generation retains measured worker utilization and write throughput. The full source scan is added separately, including checksum verification and WKB decoding.",
            "The sample duplicate rate and species-per-cell density are extrapolated; distinct cell counts are capped by the number of H3 cells on Earth. Global overlap may differ substantially.",
            "Crosswalk, boundary preparation and metadata use full reference data: their measured costs are added once.",
            "Sorting and tile compilation scenarios span linear and N log N work; metrics span cell-count and relationship-count scaling. These are sensitivity scenarios, not confidence intervals or guaranteed bounds.",
            "Disk spilling, OS caches, memory pressure, spatial overlap and changes in worker utilization can invalidate these projections. Acquisition and database loading are outside this command's scope.",
            "Spatial analytics measures boundary intersections and range/stewardship reductions over the sampled polygon memberships, not complete global denominators. Not benchmarked: DNA evidence, phylogenetic scores and opportunity aggregates, point-range archives, HydroBASINS basin coverage and joins, and cloud restore/retention. Fresh previous-run timings can fill these gaps; a reused pairs stage cannot calibrate point/HydroBASINS work.",
            "Tile time is mostly fixed validation of the complete serving output, so scaling the fixture's tile stage understates it.",
        ],
        "stages": [],
        "total_seconds": None,
        "scenario_seconds": None,
    }
    fixed = {"source_scan", "crosswalk", "boundaries", "metadata"}
    if missing:
        result["stages"] = [{"name": s["name"], "estimated_seconds": s["wall_seconds"] if s["name"] in fixed else None}
                            for s in stages if s["name"] != "sample_setup"]
        return result
    raw = pairs["pair_rows"]
    if not raw or not global_pairs:
        raise ValueError("No spatial relationships available to estimate downstream work")
    raw_scale = global_pairs / raw
    projected = {"raw_pairs": global_pairs}
    for resolution in (3, 7):
        for unit in ("cells", "relationships"):
            key = f"res{resolution}_{unit}"
            projected[key] = lists[key] * raw_scale
            if unit == "cells":
                projected[key] = min(h3.get_num_cells(resolution), projected[key])
    result["projected_workload"] = projected
    kernel_sum = sum(r["kernel_seconds"] for r in pairs["observations"])
    if kernel_sum <= 0:
        raise ValueError("No positive kernel timing measurements")
    for stage in stages:
        name, elapsed = stage["name"], stage["wall_seconds"]
        if name == "sample_setup":
            continue
        if name in fixed:
            low = point = high = elapsed
            model = "measured full reference/source workload; no scaling"
        elif name == "pairs":
            write = min(elapsed, pairs["write_seconds"])
            low = point = high = (elapsed - write) * global_kernel / kernel_sum + write * raw_scale
            model = "stratified kernel time at observed utilization + raw-pair write throughput"
        elif name in {"coarse_db", "coarse_cache", "fine_metrics", "spatial_analytics"}:
            resolution = 7 if name in {"fine_metrics", "spatial_analytics"} else 3
            scales = [projected[f"res{resolution}_{unit}"] / lists[f"res{resolution}_{unit}"]
                      for unit in ("cells", "relationships")]
            low, high = elapsed * min(scales), elapsed * max(scales)
            point = (low + high) / 2
            model = f"midpoint of res{resolution} cell and relationship throughput scenarios"
        else:
            if name == "tiles":
                resolutions = (3,) if serving_contract == "dynamic-h3-v1" else (3, 7)
                sample_work = sum(lists[f"res{resolution}_cells"] for resolution in resolutions)
                full_work = sum(projected[f"res{resolution}_cells"] for resolution in resolutions)
                unit = "coarse tile features" if resolutions == (3,) else "coarse and fine tile features"
            else:
                sample_work, full_work, unit = raw, global_pairs, "raw pairs"
            scale = full_work / sample_work
            scenarios = [elapsed * scale]
            if name in {"lists", "tiles"}:
                scenarios.append(elapsed * scale * math.log2(max(2, full_work)) / math.log2(max(2, sample_work)))
            low, high = min(scenarios), max(scenarios)
            point = scenarios[0]
            model = f"{unit} throughput" + ("; N log N sensitivity" if len(scenarios) > 1 else "")
        result["stages"].append({"name": name, "estimated_seconds": point,
                                 "scenario_seconds": [low, high], "model": model})
    result["total_seconds"] = sum(s["estimated_seconds"] for s in result["stages"])
    result["scenario_seconds"] = [sum(s["scenario_seconds"][i] for s in result["stages"]) for i in (0, 1)]
    return result


# Passed cloud runs save their stage timings here (see sandbox/coreweave/scripts/watch_run.py).
RUN_BASELINES = Path(__file__).resolve().parents[2] / "sandbox/coreweave/run-baselines"
# Profile coverage_mode -> cloud run mode.
RUN_MODES = {"fast": "fast", "correct": "precise"}
CLOUD_OVERHEAD = ("restore", "retention")
ANALYTICAL_STAGES = ("spatial_analytics", "dna_evidence", "phylogenetic", "opportunities")
COVERAGE_STAGES = {"pairs", "lists", "coarse_db", "coarse_cache", "fine_metrics", "spatial_analytics"}


def spatial_calibration(baseline: dict, mode: str | None) -> dict | None:
    """Use a full-input stage measurement only for its mode and producer code."""
    prior = baseline.get('spatial_calibration')
    if mode not in {'fast', 'precise'} or not isinstance(prior, dict) or prior.get('mode') != mode or baseline.get('mode') != mode:
        return None
    seconds = prior.get('estimated_seconds')
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
        return None
    if not all(isinstance(prior.get(key), str) for key in ('source', 'limitations')):
        return None
    root = Path(__file__).resolve().parents[1]
    paths = [root / name for name in ('cli/spatial_analytics.py', 'builders/spatial_analytics.py',
        'aggregation/membership_contract.py', 'tile_spatial.py', 'tiles.py',
        'spatial/boundaries.py', 'builders/coarse_cache.py')]
    if prior.get('code_sha256') != code_fingerprint(paths):
        return None
    return prior


def previous_full_run(mode: str, directory: Path = RUN_BASELINES) -> dict | None:
    """Newest saved full-run baseline, preferring the same coverage mode."""
    runs = []
    for path in directory.glob("*.json"):
        try:
            run = json.loads(path.read_text())
        except (OSError, ValueError):
            # A watcher may still be writing its exclusively created baseline.
            continue
        if not isinstance(run, dict) or not isinstance(run.get("created_at"), str) or not isinstance(run.get("stages"), dict):
            continue
        if any(not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 for value in run["stages"].values()):
            continue
        runs.append({**run, "path": str(path)})
    runs.sort(key=lambda run: run["created_at"])
    same = [run for run in runs if run.get("mode") == mode]
    return (same or runs or [None])[-1]


def full_run_estimate(projection: dict, baseline: dict | None, mode: str, resources: dict | None = None) -> dict | None:
    """Combine the benchmark projection with a previous full run's measured stages.

    A stage the previous run computed freshly uses its measured time; a stage it
    reused (continuation) uses a recorded reconstruction of its original work
    when available, otherwise the benchmark projection. Stages the benchmark
    does not run come only from the previous run. With a different coverage
    mode, coverage-dependent stages keep their benchmark projection and spatial
    analytics scales by the projected/observed fine-relationship ratio. No
    resource scaling is applied. Missing stages prevent a complete total.
    """
    if baseline is None or projection.get("total_seconds") is None:
        return None
    measured, reused = baseline["stages"], set(baseline.get("reused", []))
    unknown_reuse = bool(baseline.get("continued_from") and "reused" not in baseline)
    same_mode = baseline.get("mode") == mode
    observed = baseline.get("workload", {}).get("res7_relationships")
    projected = projection["projected_workload"]["res7_relationships"]
    ratio = 1.0 if same_mode or not observed else projected / observed
    benchmarked = {stage["name"] for stage in projection["stages"]}
    stages = []
    reconstructed = baseline.get("reconstructed_stages", {}) if same_mode else {}
    used_reconstructions = []
    calibration = spatial_calibration(baseline, mode)
    for stage in projection["stages"]:
        name = stage["name"]
        if name == 'spatial_analytics' and calibration:
            stages.append({'name': name, 'estimated_seconds': calibration['estimated_seconds'],
                           'source': calibration['source'], 'calibration': calibration})
        elif name in measured and name not in reused and not unknown_reuse and (same_mode or name not in COVERAGE_STAGES):
            stages.append({"name": name, "estimated_seconds": measured[name], "source": "previous full run"})
        elif name in reconstructed:
            prior = reconstructed[name]
            stages.append({"name": name, "estimated_seconds": prior["estimated_seconds"],
                           "source": prior["source"], "reconstruction": prior})
            used_reconstructions.append(name)
        else:
            reason = ("previous run reused this stage" if name in reused else
                      "previous continuation has unknown reuse" if unknown_reuse else
                      "different coverage mode" if not same_mode and name in COVERAGE_STAGES else "")
            source = "benchmark projection" + (f"; {reason}" if reason else "")
            stages.append({"name": name, "estimated_seconds": stage["estimated_seconds"], "source": source})
    for name in dict.fromkeys([*baseline.get("order", []), *measured, *ANALYTICAL_STAGES]):
        if name in benchmarked or name in CLOUD_OVERHEAD:
            continue
        if name == 'spatial_analytics' and calibration:
            stages.append({'name': name, 'estimated_seconds': calibration['estimated_seconds'],
                           'source': calibration['source'], 'calibration': calibration})
        elif name not in measured or name in reused or unknown_reuse:
            stages.append({"name": name, "estimated_seconds": None,
                           "source": "not benchmarked; no fresh previous-run timing"})
        elif name == "spatial_analytics" and ratio != 1.0:
            stages.append({"name": name, "estimated_seconds": measured[name] * ratio,
                           "source": f"previous full run x {ratio:.2f} fine relationships; not benchmarked"})
        else:
            stages.append({"name": name, "estimated_seconds": measured[name], "source": "previous full run; not benchmarked"})
    missing = [stage["name"] for stage in stages if stage["estimated_seconds"] is None]
    total = None if missing else sum(stage["estimated_seconds"] for stage in stages)
    overhead = sum(measured[name] for name in CLOUD_OVERHEAD) if all(name in measured for name in CLOUD_OVERHEAD) else None
    gaps = []
    if not same_mode:
        gaps.append(f"The previous full run used {baseline.get('mode') or 'an unknown'} coverage; this benchmark uses {mode}.")
    if resources is not None and resources != baseline.get("resources"):
        gaps.append(f"Benchmark resources `{resources}` differ from the previous run's `{baseline.get('resources')}`; "
                    "the total mixes timings from both machines.")
    if unknown_reuse:
        gaps.append("The previous full run was a continuation without a reused-stage list; some measured stages may be receipt reuse.")
    if missing:
        gaps.append(f"No complete full-run ETA: missing fresh timings for {', '.join(missing)}.")
    if overhead is None:
        gaps.append("Cloud restore/retention timings are incomplete; no cloud total is available.")
    if not same_mode and not observed:
        gaps.append("Previous fine-relationship count is unavailable; spatial analytics could not be adjusted for coverage mode.")
    if reused:
        gaps.append(f"The previous full run reused {', '.join(sorted(reused))}; original-work reconstructions are used when available in the same mode, otherwise benchmark projections are kept where available.")
    for name in used_reconstructions:
        gaps.append(f"{name}: {reconstructed[name]['limitations']}")
    if calibration:
        gaps.append(calibration['limitations'])
    if any(stage["name"] == "pairs" and stage["source"].startswith("benchmark projection") for stage in stages):
        gaps.append("The pair projection covers polygons only; point archives and HydroBASINS routes remain unestimated.")
    if observed and same_mode:
        gaps.append(f"Projected fine relationships are {projected / observed:.2f}x the previous run's observed count.")
    return {"baseline": baseline["path"], "label": baseline.get("label"), "mode": baseline.get("mode"),
            "stages": stages, "total_seconds": total, "cloud_overhead_seconds": overhead, "gaps": gaps}


def duration(seconds: float | None) -> str:
    if seconds is None:
        return "unavailable"
    return f"{seconds / 3600:.2f} h" if seconds >= 3600 else f"{seconds / 60:.2f} min" if seconds >= 60 else f"{seconds:.2f} s"


def markdown_report(report: dict) -> str:
    projection = report.get("estimate", {})
    by_name = {s["name"]: s for s in projection.get("stages", [])}
    lines = ["# Pipeline benchmark", "", f"Status: **{report['status']}**", "",
             f"Output: `{report['output']}`", "",
             f"Workers: `{report['resources']}`", ""]
    if report.get("scope") == "stage-replay":
        lines += [f"Stage replay: **{report['only_stage']}**. Inputs reused from `{report['from_run']}`.",
                  "This measures one stage against saved inputs, not a full pipeline build.", ""]
        reference = report.get("reference_stage")
        if reference and reference.get("status") == "passed":
            lines += [f"Previous run's stage time: **{duration(reference.get('wall_seconds'))}**.", ""]
            if report.get("reference_resources") != report["resources"]:
                lines += ["Worker settings differ from the reference run; timings are not a controlled speedup comparison.", ""]
        if report.get("reference_runtime") != report.get("runtime"):
            lines += ["Runtime environments differ or the reference environment is unknown; times are not a controlled speedup comparison.", ""]
    if report.get("selection"):
        selection = report["selection"]
        lines += [f"Sample: **{selection['selected_rows']:,} selected polygons** from the {selection['original_rows']:,}-row fixture. "
                  f"Current-policy exclusions: `{selection['excluded_by_current_policy']}`.", ""]
    if report.get("workload"):
        work = report["workload"]
        lines += [f"Observed output: **{work['raw_pairs']:,} raw pairs**, **{work['res7_relationships']:,} distinct fine relationships**, "
                  f"**{work['res7_cells']:,} fine cells** and **{work['res3_cells']:,} coarse cells**. "
                  f"Current full population: **{work['eligible_polygons']:,} eligible polygons**.", ""]
    lines += ["| Stage | Measured wall time | Estimated full build | Model |",
             "| --- | ---: | ---: | --- |"]
    for stage in report["stages"]:
        projected = by_name.get(stage["name"], {})
        lines.append(f"| {stage['name']} ({stage['status']}) | {duration(stage.get('wall_seconds'))} | "
                     f"{duration(projected.get('estimated_seconds'))} | {projected.get('model', 'not projected')} |")
    for stage in report['stages']:
        if stage.get('phase_seconds'):
            lines += ['', f"### {stage['name']} phases", '', '| Phase | Wall time |', '| --- | ---: |',
                      *[f'| {name} | {duration(seconds)} |' for name, seconds in stage['phase_seconds'].items()]]
    lines += ["", f"Measured benchmark total (including setup): **{duration(report.get('wall_seconds'))}**.",
              f"Projected benchmarked stages: **{duration(projection.get('total_seconds'))}**."]
    if projection.get("scenario_seconds"):
        lines += [f"Model sensitivity: **{duration(projection['scenario_seconds'][0])}–{duration(projection['scenario_seconds'][1])}** (not a confidence interval)."]
    full_run = projection.get("full_run")
    if full_run:
        lines += ["", "## Full-run ETA", "",
                  f"Benchmark projection combined with the previous full run: {full_run['label']} (`{full_run['baseline']}`).", "",
                  "| Stage | Estimate | Source |", "| --- | ---: | --- |",
                  *[f"| {s['name']} | {duration(s['estimated_seconds'])} | {s['source']} |" for s in full_run["stages"]],
                  "", f"Estimated full build: **{duration(full_run['total_seconds'])}**."]
        if full_run["cloud_overhead_seconds"] is not None and full_run["total_seconds"] is not None:
            lines.append(f"Cloud run including restore and retention: **{duration(full_run['total_seconds'] + full_run['cloud_overhead_seconds'])}**.")
        lines += [f"- {gap}" for gap in full_run["gaps"]]
    if projection.get("missing_size_bins"):
        lines += [f"No full estimate: missing representative observations in size bands {projection['missing_size_bins']}."]
    lines += ["", *[f"- {message}" for message in report.get("warnings", []) + projection.get("assumptions", [])]]
    if report.get("error"):
        lines += ["", f"Failure: {report['error']}"]
    return "\n".join(lines) + "\n"
