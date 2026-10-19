"""Convert saved cloud timings to transparent, conditional compute-cost scenarios."""

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np


def stratified_work(pairs, population):
    groups = defaultdict(list)
    for row in pairs["observations"]:
        groups[row["size_bin"]].append(row)
    kernel = count = 0.0
    arrays = []
    for band, size in enumerate(population["size_bin_counts"]):
        if not size:
            continue
        rows = groups[band]
        if len(rows) != size:
            rows = [r for r in rows if not r.get("forced_extreme", False)]
        if not rows:
            raise ValueError(f"Missing representative band {band}")
        values = np.array([[r["kernel_seconds"], r["output_pairs"]] for r in rows])
        mean = values.mean(axis=0)
        kernel += mean[0] * size
        count += mean[1] * size
        arrays.append((size, values))
    rng = np.random.default_rng(0)
    draws = np.zeros((1000, 2))
    for size, values in arrays:
        sampled = rng.integers(0, len(values), size=(1000, len(values)))
        draws += values[sampled].mean(axis=1) * size
    return kernel, count, np.quantile(draws, [0.1, 0.9], axis=0).tolist()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument(
        "--pairs", type=Path, help="Explicit actual-main pairs-report.json companion"
    )
    parser.add_argument(
        "--population", type=Path, help="Explicit calibrated population.json companion"
    )
    parser.add_argument("--cpu", type=int, required=True)
    parser.add_argument("--memory-gib", type=int, required=True)
    parser.add_argument("--cpu-rate", type=float, required=True)
    parser.add_argument("--memory-rate", type=float, required=True)
    parser.add_argument(
        "--reserve-hours", type=float, nargs=3, required=True, metavar=("LOW", "CENTRAL", "HIGH")
    )
    args = parser.parse_args()
    from launch import cost_plan

    rate = cost_plan(args.cpu, args.memory_gib, 3600, args.cpu_rate, args.memory_rate)[
        "rate_usd_per_hour"
    ]
    if (
        not all(np.isfinite(v) and v >= 0 for v in args.reserve_hours)
        or sorted(args.reserve_hours) != args.reserve_hours
    ):
        parser.error("Reserve hours must be finite, nonnegative and ordered")
    reserve_low, reserve_central, reserve_high = args.reserve_hours
    report = json.loads(args.report.read_text())
    if report.get("status") != "passed":
        raise ValueError("Completed complete-fixture report required")
    if bool(args.pairs) != bool(args.population):
        parser.error("Supply --pairs and --population together")
    output = args.report.parent
    stages = {s["name"]: s for s in report["stages"]}
    projection = report["estimate"]
    pairs = json.loads(args.pairs.read_text()) if args.pairs else report["full_pairs"]
    population = (
        json.loads(args.population.read_text()) if args.population else report["full_population"]
    )
    global_kernel, global_pairs, sampling = stratified_work(pairs, population)
    source = population["cloud_io_calibration"]["projected_cloud_scan_seconds"]
    point = projection["total_seconds"] - stages["source_scan"]["wall_seconds"] + source
    low, high = projection["scenario_seconds"]
    low += source * 0.5 - stages["source_scan"]["wall_seconds"]
    high += source * 2 - stages["source_scan"]["wall_seconds"]
    # The paired bootstrap preserves per-band cell/time covariance, but these
    # deterministic old-policy samples do not support statistical confidence bounds.
    kernel_low, raw_low = sampling[0]
    kernel_high, raw_high = sampling[1]
    if global_kernel <= 0 or global_pairs <= 0:
        raise ValueError("Positive observed kernel work and pairs required")
    low_ratio = min(1.0, kernel_low / global_kernel, raw_low / global_pairs)
    high_ratio = max(1.0, kernel_high / global_kernel, raw_high / global_pairs)
    low *= low_ratio
    high *= high_ratio
    polygon = {
        "point_hours": point / 3600,
        "scenario_hours": [low / 3600, high / 3600],
        "point_compute_usd": point / 3600 * rate,
        "scenario_compute_usd": [low / 3600 * rate, high / 3600 * rate],
    }
    basin = report.get("basin_kernel")
    basin_model = None
    if basin:
        kernel, cells, uncertainty = stratified_work(basin["pairs"], basin["population"])
        count = basin["population"]["matching_basin_count"]
        # Kernel-only arithmetic excludes source extraction and the final join.
        # A fixed cold-launch cost must not be multiplied by half a million basins.
        ideal = kernel / args.cpu
        basin_model = {
            "matching_basins": count,
            "stratified_kernel_core_seconds": kernel,
            "projected_basin_cell_rows": cells,
            "matched_kernel_lower_hours": ideal / 3600,
            "scope": "Measured kernel, extrapolated matched basins; unresolved geometries and join costs need explicit assumptions",
        }
    reserves = {
        "optimistic_extra_hours": reserve_low,
        "central_extra_hours": reserve_central,
        "pessimistic_extra_hours": reserve_high,
        "pessimistic_contingency_factor": 1.0,
        "scope": "Explicit planning allowances for setup, points, geometry extraction, basin index and basin/species join; these phases have not been timed end-to-end on cloud",
        "condition": "Unresolved basin geometries are fixed and have similar complexity; no guarantee of the upper cost",
    }
    full_hours = [low / 3600 + reserve_low, high / 3600 + reserve_high]
    conditional_full = {
        "central_hours": point / 3600 + reserve_central,
        "central_compute_usd": (point / 3600 + reserve_central) * rate,
        "scenario_hours": full_hours,
        "scenario_compute_usd": [h * rate for h in full_hours],
        "reserves": reserves,
    }
    result = {
        "rate_usd_per_hour": rate,
        "polygon_post_acquisition": polygon,
        "conditional_full_build": conditional_full,
        "sampling_sensitivity": {
            "kernel_core_seconds": global_kernel,
            "raw_pairs": global_pairs,
            "bootstrap_10_90_kernel_and_rows": sampling,
            "label": "sensitivity scenarios, not confidence intervals",
        },
        "cloud_source_scan_estimated_seconds": source,
        "basin_model": basin_model,
        "scaling": report.get("scaling"),
        "max_measured_stage_rss_bytes": max(
            (
                s["peak_process_tree_rss_bytes"]
                for s in stages.values()
                if s.get("peak_process_tree_rss_bytes") is not None
            ),
            default=None,
        ),
        "full_release_status": "Conditional model only; source completeness and all-route validation must be established separately",
        "exclusions": [
            "Persistent storage/snapshots without a verified tariff",
            "Initial provider acquisition",
            "Database deployment/loading",
            "Retries or reruns beyond the stated scenario",
        ],
        "tariff_source": "User-supplied rates; verify current provider terms separately",
    }
    (output / "cost-model.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
