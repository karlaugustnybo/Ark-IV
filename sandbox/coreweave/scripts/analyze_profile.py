"""Summarize a completed private profiling run; optional local resource chart."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

GIB = 1024**3


def analyze(root):
    restored = root / "restored" if (root / "restored").is_dir() else root
    profile = json.loads((restored / "profile-report.json").read_text())
    samples = [
        json.loads(line)
        for line in (restored / "resource-samples.jsonl").read_text().splitlines()
        if line.strip()
    ]
    phases = []
    for phase in profile["measurements"]:
        points = [
            s
            for s in samples
            if phase["before"]["seconds"] <= s["seconds"] <= phase["after"]["seconds"]
        ]
        phases.append(
            {
                "stage": phase["stage"],
                "seconds": phase["seconds"],
                "average_cpu_cores": phase["average_used_cpu_cores"],
                "peak_total_gib": max(
                    (
                        s["memory_current"] / GIB
                        for s in points
                        if s.get("memory_current") is not None
                    ),
                    default=None,
                ),
                "peak_working_proxy_gib": max(
                    (
                        sum(s["memory_stat"].get(k, 0) for k in ("anon", "shmem", "kernel")) / GIB
                        for s in points
                        if s.get("memory_stat")
                    ),
                    default=None,
                ),
                "peak_process_pss_gib": max(
                    (
                        sum(p["pss"] for p in s["processes"]) / GIB
                        for s in points
                        if s["processes"] and all(p.get("pss") is not None for p in s["processes"])
                    ),
                    default=None,
                ),
            }
        )
    hotspots = defaultdict(float)
    if (root / "cpu-speedscope.json").is_file():
        stacks = json.loads((root / "cpu-speedscope.json").read_text())
        frames = stacks["shared"]["frames"]
        for trace in stacks["profiles"]:
            for stack, weight in zip(trace.get("samples", []), trace.get("weights", [])):
                if stack:
                    frame = frames[stack[-1]]
                    hotspots[
                        (frame.get("name", ""), frame.get("file", ""), frame.get("line", 0))
                    ] += weight
    summary = {
        "allocation": profile.get("allocation"),
        "stages": phases,
        "peak_cgroup_gib": int(profile["memory_peak_bytes"]) / GIB
        if profile.get("memory_peak_bytes") is not None
        else None,
        "memory_events": profile["memory_events"],
        "scope": "Measured phases only; missing counters remain unavailable. No global runtime or resource recommendation is inferred.",
        "sampled_leaf_calls": [
            {"name": n, "file": f, "line": line, "weighted_seconds": w}
            for (n, f, line), w in sorted(hotspots.items(), key=lambda item: item[1], reverse=True)[
                :25
            ]
        ],
    }
    density = restored / "density-report.json"
    if density.is_file():
        summary["density"] = json.loads(density.read_text())
    benchmark, population = restored / "benchmark-report.json", restored / "population.json"
    if benchmark.is_file() and population.is_file():
        estimate = json.loads(benchmark.read_text())["estimate"]
        calibration = json.loads(population.read_text()).get("cloud_io_calibration")
        if calibration:
            correction = calibration["projected_cloud_scan_seconds"] - next(
                p["estimated_seconds"] for p in estimate["stages"] if p["name"] == "source_scan"
            )
            summary["polygon_route_projected_hours"] = (
                estimate["total_seconds"] + correction
            ) / 3600
            summary["polygon_route_scenario_hours"] = [
                (x + correction) / 3600 for x in estimate["scenario_seconds"]
            ]
            summary["forecast_scope"] = (
                "Single-archive I/O ratio and sampled polygon-route projection; excludes unmeasured routes, setup and transfers; not a bound or global measurement"
            )
    (root / "analysis.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary, samples


def chart(root, samples):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if not samples:
        raise ValueError("No resource samples to chart")
    start = samples[0]["seconds"]
    t = [(s["seconds"] - start) / 60 for s in samples]
    cpu = [0] + [
        (b["cpu_stat"]["usage_usec"] - a["cpu_stat"]["usage_usec"])
        / 1e6
        / (b["seconds"] - a["seconds"])
        for a, b in zip(samples, samples[1:])
    ]
    fig, axes = plt.subplots(2, 1, figsize=(13, 7), sharex=True, layout="constrained")
    axes[0].plot(t, cpu, color="#2874a6", linewidth=1)
    axes[0].axhline(max(cpu), linestyle="--", color="gray", label="Observed sampled peak")
    axes[0].set(
        ylabel="CPU cores used",
        title="Recorded resource profile",
    )
    axes[0].legend(loc="upper left")
    axes[1].plot(
        t,
        [s["memory_current"] / GIB for s in samples],
        label="Total including file cache",
        color="#2874a6",
    )
    axes[1].plot(
        t,
        [
            sum(s["memory_stat"].get(k, 0) for k in ("anon", "shmem", "kernel")) / GIB
            for s in samples
        ],
        label="Working memory proxy",
        color="#b35c00",
    )

    axes[1].set(ylabel="Memory (GiB)", xlabel="Minutes since first resource sample")
    axes[1].legend(loc="upper left")
    seen = set()
    for s, x in zip(samples, t):
        if s["stage"] not in seen:
            seen.add(s["stage"])
            for ax in axes:
                ax.axvline(x, color="gray", alpha=0.18)
            if s["stage"] in {
                "source_scan",
                "crosswalk",
                "metadata",
                "coarse_db",
                "prepared_inputs",
            }:
                continue
            axes[0].text(
                x,
                max(cpu),
                s["stage"]
                .replace("high-density-metrics", "density")
                .replace("supplemental-preprocessing", "point/basin preprocessing")
                .replace("download-inputs", "download")
                .replace("prepared_inputs", "prepare"),
                rotation=90,
                fontsize=7,
                va="top",
            )
    fig.savefig(root / "resource-profile.png", dpi=170)
    fig.savefig(root / "resource-profile.svg")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--chart", action="store_true")
    args = parser.parse_args()
    summary, samples = analyze(args.root)
    if args.chart:
        if any(
            s.get("memory_current") is None or "usage_usec" not in s["cpu_stat"] for s in samples
        ):
            parser.error("Chart requires available memory and CPU counters")
        chart(args.root, samples)
    print(
        json.dumps(
            {
                k: v
                for k, v in summary.items()
                if k
                in (
                    "peak_cgroup_gib",
                    "polygon_route_projected_hours",
                    "polygon_route_scenario_hours",
                    "memory_events",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
