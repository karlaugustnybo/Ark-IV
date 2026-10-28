#!/usr/bin/env python3
"""Measure how alternative H3 membership rules change IUCN range coverage.

Research diagnostic for the roadmap items "Test sensitivity of the polygon
rasterization rule" and the v3 simplification calibration (audit A01–A05).
It is not a pipeline stage and never changes the served membership rule.

One geometry pass yields every rule on the same unsimplified source geometry:

- ``any_touch``: closed contact, the intended rule and the reference;
- ``centroid``: the H3 cell centre lies inside the range;
- ``overlap_10`` / ``overlap_25``: at least that share of the cell is covered.

Two production routes are measured against the same reference:

- ``any_touch_simplified``: correct mode with the profile's 0.01° decision
  simplification at or above 100 deg² of bbox area (the precise build);
- ``fast_simplified``: FAST native overlap with the same simplification
  (the 25 October served build).

Overlap shares use planar longitude/latitude area inside a single cell, where
the distortion is negligible. Two inputs are measured:

- a size-stratified global sample of whole range polygons (per-species
  omissions/additions by size and shape);
- every eligible range polygon touching fixed regional windows, clipped with a
  margin, so complete per-cell species sets give hotspot and rank stability.

``census`` scans extracted shapefiles, ``measure`` runs bounded worker
processes with per-task timeouts, and ``analyze`` writes private summaries.
Outputs derive from restricted IUCN data and stay private.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import multiprocessing
import os
import time
import traceback
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pyogrio
import shapely
from h3ronpy.vector import cells_to_coordinates
from shapely import affinity
from shapely.geometry import box

from ark_pipeline.cli.spatial_pairs import REPOSITORY_ROOT, exclusion_reason_values
from ark_pipeline.robustness import compare_scenarios
from ark_pipeline.runtime.benchmark_estimates import SIZE_BREAKS
from ark_pipeline.spatial.census import census_bounds, iter_census_batches
from ark_pipeline.spatial.coverage import (
    GeometryCoverageError,
    _bbox_area,
    _direct_any_touch_prepared,
    _iter_candidate_cell_batches,
    _iter_longitude_normalized_pieces,
    _native_cell_polygons,
    _one_ring,
    _simplified_decision_geometry,
    _sorted_unique_chunks,
    load_spatial_profile,
    prepare_geometry,
    unwrap_antimeridian,
)

CORRECT_PROFILE = REPOSITORY_ROOT / "config/spatial_semantics_iucn_richness_v3_partial_basins.toml"
FAST_PROFILE = REPOSITORY_ROOT / "config/spatial_semantics_iucn_richness_v3_fast_partial_basins.toml"
REFERENCE = "any_touch"
RULES = ("any_touch", "centroid", "overlap_10", "overlap_25", "any_touch_simplified", "fast_simplified")
THRESHOLDS = {"overlap_10": 0.10, "overlap_25": 0.25}
# 4° x 4° windows: (west, south, east, north). Chosen for contrasting
# endemism, latitude and terrestrial/freshwater/marine mixes, not at random.
WINDOWS = {
    "madagascar_east": (46.0, -20.0, 50.0, -16.0),
    "andes_ecuador": (-80.0, -3.0, -76.0, 1.0),
    "borneo_sabah": (115.0, 3.0, 119.0, 7.0),
    "sulawesi_sea": (122.0, 0.0, 126.0, 4.0),
    "lake_malawi": (33.0, -14.0, 37.0, -10.0),
    "denmark": (8.0, 54.5, 12.0, 58.5),
}
# Clip margin: wider than a resolution-7 cell up to ~80° latitude, so every
# cell whose centre lies in a window sees the complete local range geometry.
WINDOW_MARGIN_DEGREES = 0.2
FRONTIER_CHUNK = 200_000


# --- geometry kernel ---------------------------------------------------------

def canonical_region(geometry):
    """Union of the geometry's longitude-normalized pieces within [-180, 180]."""
    pieces = list(_iter_longitude_normalized_pieces(geometry))
    if not pieces:
        return None
    return pieces[0] if len(pieces) == 1 else shapely.union_all(np.asarray(pieces, dtype=object))


def clip_to_window(geometry, window, margin=WINDOW_MARGIN_DEGREES):
    region = canonical_region(geometry)
    if region is None:
        return None
    west, south, east, north = window
    clipped = region.intersection(box(west - margin, max(-90, south - margin), east + margin, min(90, north + margin)))
    return None if clipped.is_empty else clipped


def cell_centres(cells: np.ndarray) -> np.ndarray:
    """Return H3 cell centres as an (n, 2) longitude/latitude array."""
    if not cells.size:
        return np.empty((0, 2))
    table = pa.record_batch(cells_to_coordinates(np.asarray(cells, dtype=np.uint64)))
    return np.column_stack([table.column("lng").to_numpy(), table.column("lat").to_numpy()])


def _measure(region, polygons, centres, index, hit, share, inside, depth=0):
    """Closed contact, covered share and centre containment for indexed cells.

    Large regions are split around groups of cells. Each group is clipped to
    the union of its own cells' bounds, so the clipped piece contains all of
    the region that any of those cells can touch: results stay exact. Lower-
    dimensional clip results are kept so edge and vertex contacts survive.
    """
    if not index.size:
        return
    if depth < 16 and index.size > 256 and shapely.get_num_coordinates(region) > 4096:
        x, y = centres[index, 0], centres[index, 1]
        xm, ym = (x.min() + x.max()) / 2, (y.min() + y.max()) / 2
        for mask in ((x < xm) & (y < ym), (x >= xm) & (y < ym), (x < xm) & (y >= ym), (x >= xm) & (y >= ym)):
            part = index[mask]
            if not part.size:
                continue
            x0, y0, x1, y1 = shapely.total_bounds(polygons[part])
            piece = region.intersection(box(x0 - 1e-9, y0 - 1e-9, x1 + 1e-9, y1 + 1e-9))
            if not piece.is_empty:
                _measure(piece, polygons, centres, part, hit, share, inside, depth + 1)
        return
    shapely.prepare(region)
    touched = shapely.intersects(region, polygons[index])
    if touched.any():
        selected = index[touched]
        hit[selected] = True
        covered = shapely.area(shapely.intersection(region, polygons[selected]))
        share[selected] += covered / shapely.area(polygons[selected])
    inside[index] |= shapely.contains_xy(region, centres[index, 0], centres[index, 1])


def frontier_measures(region, cells: np.ndarray):
    """Return (hit, covered share, centre inside) for cells against a canonical region.

    Antimeridian cells arrive unwrapped beyond ±180°, so their parts are also
    measured against the region shifted by ±360°. Centres are canonical.
    """
    count = cells.size
    hit, share, inside = np.zeros(count, bool), np.zeros(count), np.zeros(count, bool)
    if not count:
        return hit, share, inside
    polygons = _native_cell_polygons(cells)
    centres = cell_centres(cells)
    bounds = shapely.bounds(polygons)
    for turn in (-1, 0, 1):
        index = np.arange(count) if turn == 0 else np.flatnonzero(bounds[:, 2] > 180 if turn == 1 else bounds[:, 0] < -180)
        if not index.size:
            continue
        shifted = region if turn == 0 else affinity.translate(region, xoff=turn * 360)
        scratch = np.zeros(count, bool)
        _measure(shifted, polygons, centres, index, hit, share, inside if turn == 0 else scratch)
    return hit, np.minimum(share, 1.0), inside


def reference_rules(region, profile) -> dict[str, np.ndarray]:
    """All unsimplified rules from one candidate pass over a canonical region."""
    overlaps, guaranteed = [], []
    for overlap, contained in _iter_candidate_cell_batches(region, profile, profile.resolution, include_guaranteed=True):
        overlaps.append(overlap)
        if contained.size:
            guaranteed.append(contained)
    overlap, full = _sorted_unique_chunks(overlaps), _sorted_unique_chunks(guaranteed)
    frontier = np.setdiff1d(_one_ring(np.setdiff1d(overlap, full, assume_unique=True)), full, assume_unique=True)
    picked = {rule: [full] for rule in ("any_touch", "centroid", *THRESHOLDS)}
    for start in range(0, frontier.size, FRONTIER_CHUNK):
        chunk = frontier[start:start + FRONTIER_CHUNK]
        hit, share, inside = frontier_measures(region, chunk)
        picked["any_touch"].append(chunk[hit])
        picked["centroid"].append(chunk[inside])
        for rule, threshold in THRESHOLDS.items():
            picked[rule].append(chunk[hit & (share >= threshold)])
    return {rule: _sorted_unique_chunks([c for c in chunks if c.size]) for rule, chunks in picked.items()}


def _production(decision, profile, repair) -> np.ndarray:
    try:
        return _direct_any_touch_prepared(decision, profile, repair).cells
    except GeometryCoverageError:
        # A clipped sliver may have no native overlap in FAST mode.
        return np.empty(0, dtype=np.uint64)


def _in_window(cells: np.ndarray, window) -> np.ndarray:
    if not cells.size:
        return cells
    centres = cell_centres(cells)
    west, south, east, north = window
    keep = (centres[:, 0] >= west) & (centres[:, 0] < east) & (centres[:, 1] >= south) & (centres[:, 1] < north)
    return cells[keep]


def geometry_rules(geometry, correct, fast, windows=None, *, check=False) -> tuple[dict, dict]:
    """Return {window or "": {rule: cells}} and geometry/timing facts.

    ``windows`` maps names to boxes; without it the whole geometry is measured.
    With ``check`` an unsimplified whole geometry also runs the production
    correct kernel, which must reproduce the reference ``any_touch`` cells.
    """
    timings, started = {}, time.perf_counter()
    unsimplified = replace(correct, decision_simplification_degrees=0.0)
    prepared, repair = prepare_geometry(geometry, correct)
    unwrapped = unwrap_antimeridian(prepared)
    simplify = _bbox_area(unwrapped) >= correct.decision_simplification_min_bbox_degrees2
    decision = (_simplified_decision_geometry(unwrapped, correct.decision_simplification_degrees, source_validated=True)
                if simplify else unwrapped)
    timings["prepare"] = time.perf_counter() - started
    facts = {
        "bbox_area_degrees2": _bbox_area(unwrapped), "planar_area_degrees2": float(prepared.area),
        "perimeter_degrees": float(prepared.length), "coordinates": int(shapely.get_num_coordinates(prepared)),
        "components": len(getattr(prepared, "geoms", [prepared])),
        "holes": int(sum(len(p.interiors) for p in getattr(prepared, "geoms", [prepared]))),
        "repaired": not repair.original_valid, "simplification_routed": simplify,
        "simplification_applied": decision is not unwrapped,
    }
    results = {}
    targets = windows.items() if windows else [("", None)]
    for name, window in targets:
        tick = time.perf_counter()
        if window is None:
            region, routed = canonical_region(unwrapped), decision
        else:
            region = clip_to_window(unwrapped, window)
            routed = clip_to_window(decision, window) if decision is not unwrapped else region
        if region is None:
            results[name] = {rule: np.empty(0, dtype=np.uint64) for rule in RULES}
            continue
        rules = reference_rules(region, unsimplified)
        timings[f"{name or 'whole'}:reference"] = time.perf_counter() - tick
        tick = time.perf_counter()
        if decision is unwrapped and not check:
            rules["any_touch_simplified"] = rules["any_touch"]
        elif routed is None:
            rules["any_touch_simplified"] = np.empty(0, dtype=np.uint64)
        else:
            rules["any_touch_simplified"] = _production(routed, correct, repair)
        rules["fast_simplified"] = np.empty(0, dtype=np.uint64) if routed is None else _production(routed, fast, repair)
        timings[f"{name or 'whole'}:production"] = time.perf_counter() - tick
        if window is not None:
            rules = {rule: _in_window(cells, window) for rule, cells in rules.items()}
        if check and decision is unwrapped:
            facts["kernel_check"] = bool(np.array_equal(rules["any_touch"], rules["any_touch_simplified"]))
        results[name] = rules
    facts["seconds"] = timings
    return results, facts


# --- census ------------------------------------------------------------------

ATTRIBUTES = ("id_no", "presence", "origin", "seasonal")


def _census_file(path: str, profile_path: str) -> list[dict]:
    profile = load_spatial_profile(Path(profile_path))
    rows = []
    for layer_name, _ in pyogrio.list_layers(path):
        layer = str(layer_name)
        for batch in iter_census_batches(Path(path), layer, 2048):
            for row, wkb, envelope in batch:
                # The only non-attribute key is the source FID column.
                fid = next(value for key, value in row.items() if key not in ATTRIBUTES)
                reason = exclusion_reason_values(row["id_no"], wkb, row["presence"], row["origin"], profile,
                                                 seasonal=row["seasonal"])
                minx, miny, maxx, maxy = census_bounds(wkb, envelope) if reason is None else (math.nan,) * 4
                rows.append({"path": path, "layer": layer, "fid": int(fid), "iucn_sis_id": row["id_no"],
                             "excluded": reason, "minx": minx, "miny": miny, "maxx": maxx, "maxy": maxy})
    return rows


def census(source_dir: Path, output: Path, workers: int) -> dict:
    files = sorted(str(p) for p in source_dir.rglob("*.shp"))
    if not files:
        raise ValueError(f"no shapefiles under {source_dir}")
    rows = []
    with ProcessPoolExecutor(workers, mp_context=multiprocessing.get_context("spawn")) as pool:
        for result in pool.map(_census_file, files, [str(CORRECT_PROFILE)] * len(files)):
            rows.extend(result)
    table = pa.Table.from_pylist(rows)
    pq.write_table(table, output)
    eligible = sum(r["excluded"] is None for r in rows)
    return {"files": len(files), "rows": len(rows), "eligible": eligible}


# --- task selection and execution ---------------------------------------------

def size_bin(area: float) -> int:
    return next((index for index, edge in enumerate(SIZE_BREAKS) if area < edge), len(SIZE_BREAKS))


def _hash(row) -> int:
    key = f"{Path(row['path']).name}|{row['layer']}|{row['fid']}|{row['iucn_sis_id']}".encode()
    return int.from_bytes(hashlib.sha256(key).digest()[:8], "big")


def select_tasks(rows: list[dict], per_bin: int, large_per_bin: int, windows: dict) -> list[dict]:
    """Stratified whole-polygon sample plus every polygon touching a window.

    Bins at or above 100 deg² take ``large_per_bin`` polygons because their
    unsimplified reference is expensive. Tasks run cheapest-first.
    """
    eligible = [r for r in rows if r["excluded"] is None]
    bins: dict[int, list] = {}
    for row in eligible:
        area = max(0.0, row["maxx"] - row["minx"]) * max(0.0, row["maxy"] - row["miny"])
        bins.setdefault(size_bin(area), []).append((_hash(row), area, row))
    tasks = []
    for index, members in bins.items():
        limit = large_per_bin if index >= SIZE_BREAKS.index(100.0) + 1 else per_bin
        for _, area, row in sorted(members, key=lambda item: item[0])[:limit]:
            tasks.append({"kind": "sample", "row": row, "bbox_area": area, "size_bin": index,
                          "bin_population": len(members)})
    for row in eligible:
        touched = {name: w for name, w in windows.items()
                   if row["minx"] <= w[2] + WINDOW_MARGIN_DEGREES and row["maxx"] >= w[0] - WINDOW_MARGIN_DEGREES
                   and row["miny"] <= w[3] + WINDOW_MARGIN_DEGREES and row["maxy"] >= w[1] - WINDOW_MARGIN_DEGREES}
        if touched:
            area = max(0.0, row["maxx"] - row["minx"]) * max(0.0, row["maxy"] - row["miny"])
            tasks.append({"kind": "window", "row": row, "bbox_area": area, "windows": sorted(touched)})
    tasks.sort(key=lambda t: (t["bbox_area"], t["kind"]))
    for number, task in enumerate(tasks):
        task["id"] = f"{task['kind']}-{number:06d}"
    return tasks


def _read_geometry(row):
    metadata, table = pyogrio.read_arrow(row["path"], layer=row["layer"], fids=[row["fid"]], columns=[])
    return shapely.from_wkb(table.column(metadata["geometry_name"] or "wkb_geometry")[0].as_py())


def run_task(task: dict, output: Path) -> None:
    correct, fast = load_spatial_profile(CORRECT_PROFILE), load_spatial_profile(FAST_PROFILE)
    started = time.perf_counter()
    record = {key: value for key, value in task.items() if key != "row"} | {
        key: task["row"][key] for key in ("path", "layer", "fid", "iucn_sis_id")}
    try:
        geometry = _read_geometry(task["row"])
        windows = {name: WINDOWS[name] for name in task["windows"]} if task["kind"] == "window" else None
        results, facts = geometry_rules(geometry, correct, fast, windows, check=task["kind"] == "sample")
        arrays = {f"{name}/{rule}": cells for name, rules in results.items() for rule, cells in rules.items()}
        np.savez_compressed(output / f"{task['id']}.npz", **arrays)
        record.update(status="passed", **facts)
    except Exception as error:  # recorded per task; the run continues
        record.update(status="failed", error=f"{type(error).__name__}: {error}"[:500],
                      trace=traceback.format_exc()[-2000:])
    record["wall_seconds"] = time.perf_counter() - started
    (output / f"{task['id']}.json").write_text(json.dumps(record, default=float))


def measure(census_path: Path, output: Path, workers: int, per_bin: int, large_per_bin: int,
            task_timeout: float, deadline: float | None) -> dict:
    """Run tasks in killable processes; finished task files make reruns resume."""
    output.mkdir(parents=True, exist_ok=True)
    tasks = select_tasks(pq.read_table(census_path).to_pylist(), per_bin, large_per_bin, WINDOWS)
    (output / "tasks.json").write_text(json.dumps([{k: v for k, v in t.items()} for t in tasks], default=float))
    pending = [t for t in tasks if not (output / f"{t['id']}.json").exists()]
    context, running, counts = multiprocessing.get_context("fork"), {}, {"timeout": 0, "skipped": 0}
    print(f"tasks {len(tasks)} pending {len(pending)}", flush=True)
    while pending or running:
        while pending and len(running) < workers:
            if deadline and time.time() > deadline:
                counts["skipped"] = len(pending)
                pending = []
                break
            task = pending.pop(0)
            process = context.Process(target=run_task, args=(task, output))
            process.start()
            running[task["id"]] = (process, time.monotonic(), task)
        time.sleep(0.2)
        for task_id, (process, began, task) in list(running.items()):
            late = time.monotonic() - began > task_timeout or (deadline and time.time() > deadline)
            if process.is_alive() and not late:
                continue
            if process.is_alive():
                process.kill()
                process.join()
                counts["timeout"] += 1
                record = {key: value for key, value in task.items() if key != "row"} | {
                    "status": "timeout", "iucn_sis_id": task["row"]["iucn_sis_id"], "wall_seconds": time.monotonic() - began}
                (output / f"{task_id}.json").write_text(json.dumps(record, default=float))
            process.join()
            del running[task_id]
    done = sum(1 for t in tasks if (output / f"{t['id']}.json").exists())
    return {"tasks": len(tasks), "recorded": done, **counts}


# --- analysis ----------------------------------------------------------------

def _difference(reference: np.ndarray, alternative: np.ndarray) -> dict:
    common = np.intersect1d(reference, alternative, assume_unique=True).size
    union = reference.size + alternative.size - common
    return {"reference_cells": int(reference.size), "cells": int(alternative.size),
            "omitted": int(reference.size - common), "added": int(alternative.size - common),
            "jaccard": common / union if union else 1.0}


def _quantiles(values) -> dict:
    values = np.asarray([v for v in values if v is not None and math.isfinite(v)], dtype=float)
    if not values.size:
        return {"n": 0}
    return {"n": int(values.size), "mean": float(values.mean()), "median": float(np.median(values)),
            "p90": float(np.quantile(values, 0.9)), "max": float(values.max())}


SIZE_CLASSES = ((1, "1 cell"), (10, "2-10"), (100, "11-100"), (1_000, "101-1k"), (10_000, "1k-10k"),
                (100_000, "10k-100k"), (math.inf, ">100k"))


def size_class(cells: int) -> str:
    return next(label for limit, label in SIZE_CLASSES if cells <= limit)


def analyze_sample(records: list[dict], output: Path) -> dict:
    rows = []
    for record in records:
        if record["kind"] != "sample" or record["status"] != "passed":
            continue
        with np.load(output / f"{record['id']}.npz") as arrays:
            reference = arrays[f"/{REFERENCE}"]
            compactness = (4 * math.pi * record["planar_area_degrees2"] / record["perimeter_degrees"] ** 2
                           if record["perimeter_degrees"] else None)
            for rule in RULES[1:]:
                rows.append({"task": record["id"], "iucn_sis_id": record["iucn_sis_id"], "rule": rule,
                             "size_bin": record["size_bin"], "bin_population": record["bin_population"],
                             "bbox_area_degrees2": record["bbox_area_degrees2"], "compactness": compactness,
                             "simplification_applied": record["simplification_applied"],
                             "size_class": size_class(reference.size),
                             **_difference(reference, arrays[f"/{rule}"])})
    pq.write_table(pa.Table.from_pylist(rows), output.parent / "sample_metrics.parquet")
    summary = {}
    for rule in RULES[1:]:
        subset = [r for r in rows if r["rule"] == rule]
        share = lambda r, key: r[key] / r["reference_cells"] if r["reference_cells"] else None  # noqa: E731
        by_class = {}
        for _, label in SIZE_CLASSES:
            members = [r for r in subset if r["size_class"] == label]
            if members:
                by_class[label] = {"polygons": len(members),
                                   "omitted_share": _quantiles(share(r, "omitted") for r in members),
                                   "added_share": _quantiles(share(r, "added") for r in members),
                                   "lost_entirely": sum(r["cells"] == 0 for r in members)}
        # Reweight the stratified sample by each bin's census population.
        weights = {}
        for r in subset:
            weights.setdefault(r["size_bin"], []).append(r)
        total = sum(members[0]["bin_population"] for members in weights.values())
        weighted = {key: sum(members[0]["bin_population"] / total * np.mean([share(r, key) for r in members])
                             for members in weights.values()) for key in ("omitted", "added")}
        summary[rule] = {"polygons": len(subset), "by_reference_size": by_class,
                         "census_weighted_mean_share": weighted,
                         "lost_entirely": sum(r["cells"] == 0 for r in subset)}
    simplified = [r for r in rows if r["simplification_applied"]]
    summary["simplification_calibration"] = {
        rule: {"polygons": len([r for r in simplified if r["rule"] == rule]),
               "omitted_share": _quantiles(r["omitted"] / r["reference_cells"] for r in simplified if r["rule"] == rule),
               "added_share": _quantiles(r["added"] / r["reference_cells"] for r in simplified if r["rule"] == rule),
               "jaccard": _quantiles(r["jaccard"] for r in simplified if r["rule"] == rule)}
        for rule in ("any_touch_simplified", "fast_simplified")}
    checks = [r.get("kernel_check") for r in records if r["kind"] == "sample" and "kernel_check" in r]
    summary["kernel_check"] = {"checked": len(checks), "matched": sum(bool(c) for c in checks)}
    return summary


def _cell_totals(sets: dict, rule: str, ids: list[int], weights: dict[int, float]):
    """Return (cells, weighted priority, richness) for one rule's species sets."""
    chunks = [sets[(rule, sis)] for sis in ids if (rule, sis) in sets and sets[(rule, sis)].size]
    if not chunks:
        return np.empty(0, dtype=np.uint64), np.empty(0), np.empty(0, dtype=np.int64)
    pair_weights = np.concatenate([np.full(sets[(rule, sis)].size, weights.get(sis, 0.0))
                                   for sis in ids if (rule, sis) in sets and sets[(rule, sis)].size])
    cells, inverse = np.unique(np.concatenate(chunks), return_inverse=True)
    return cells, np.bincount(inverse, weights=pair_weights), np.bincount(inverse)


def analyze_windows(records: list[dict], output: Path, weights: dict[int, float]) -> dict:
    """Union rows per species, then compare per-cell score and richness by rule.

    Cells occupied under any rule form the common universe; a cell a rule
    leaves empty scores zero, so omissions lower its rank instead of vanishing.
    """
    species: dict[tuple[str, str, int], list[np.ndarray]] = {}
    for record in records:
        if record["kind"] != "window" or record["status"] != "passed":
            continue
        with np.load(output / f"{record['id']}.npz") as arrays:
            for key in arrays.files:
                window, rule = key.split("/")
                species.setdefault((window, rule, record["iucn_sis_id"]), []).append(arrays[key])
    cell_tables, species_rows, summary = [], [], {}
    empty = np.empty(0, dtype=np.uint64)
    for window in WINDOWS:
        sets = {(rule, sis): _sorted_unique_chunks([c for c in chunks if c.size])
                for (w, rule, sis), chunks in species.items() if w == window}
        if not sets:
            continue
        ids = sorted({sis for _, sis in sets})
        totals = {rule: _cell_totals(sets, rule, ids, weights) for rule in RULES}
        universe = _sorted_unique_chunks([cells for cells, _, _ in totals.values() if cells.size])
        dense = {}
        for rule, (cells, priority, richness) in totals.items():
            position = np.searchsorted(universe, cells)
            filled_priority, filled_richness = np.zeros(universe.size), np.zeros(universe.size)
            filled_priority[position], filled_richness[position] = priority, richness
            dense[rule] = (filled_priority, filled_richness)
            cell_tables.append(pa.table({"window": [window] * universe.size, "rule": [rule] * universe.size,
                                         "cell": universe, "priority": filled_priority,
                                         "richness": filled_richness.astype(np.int64)}))
        keys = universe.tolist()
        window_summary = {"species": len(ids), "unweighted_species": sum(sis not in weights for sis in ids),
                          "cells": int(universe.size), "rules": {}}
        for rule in RULES:
            entry = {}
            for metric, column in (("priority", 0), ("richness", 1)):
                entry[metric] = compare_scenarios(dict(zip(keys, dense[REFERENCE][column].tolist())),
                                                  dict(zip(keys, dense[rule][column].tolist())))
                for item in entry[metric]["top_percent_overlap"].values():
                    size, common = item["selected"], item["common"]
                    item["jaccard"] = common / (2 * size - common) if size else None
            entry["occupied_cells"] = int(totals[rule][0].size)
            entry["species_cell_pairs"] = int(totals[rule][2].sum())
            entry["species_lost_from_window"] = sum(
                1 for sis in ids if sets.get((REFERENCE, sis), empty).size and not sets.get((rule, sis), empty).size)
            window_summary["rules"][rule] = entry
        for sis in ids:
            reference = sets.get((REFERENCE, sis), empty)
            for rule in RULES[1:]:
                species_rows.append({"window": window, "iucn_sis_id": sis, "rule": rule,
                                     "weight": weights.get(sis), **_difference(reference, sets.get((rule, sis), empty))})
        summary[window] = window_summary
    if cell_tables:
        pq.write_table(pa.concat_tables(cell_tables), output.parent / "window_cells.parquet")
    pq.write_table(pa.Table.from_pylist(species_rows), output.parent / "window_species.parquet")
    return summary


def analyze(measure_dir: Path, weights_path: Path, summary_path: Path) -> dict:
    records = [json.loads(p.read_text()) for p in sorted(measure_dir.glob("*-*.json"))]
    weights = {int(r["iucn_sis_id"]): float(r["priority_weight"])
               for r in pq.read_table(weights_path).to_pylist() if r["priority_weight"] is not None}
    statuses = {}
    for record in records:
        key = f"{record['kind']}:{record['status']}"
        statuses[key] = statuses.get(key, 0) + 1
    failures = [{k: r.get(k) for k in ("id", "iucn_sis_id", "bbox_area", "status", "error")}
                for r in records if r["status"] != "passed"]
    summary = {"rules": RULES, "reference": REFERENCE, "windows": WINDOWS, "task_status": statuses,
               "unfinished": failures[:200], "sample": analyze_sample(records, measure_dir),
               "windows_result": analyze_windows(records, measure_dir, weights),
               "timing": {"sample_seconds_by_bin": _timing(records)}}
    summary_path.write_text(json.dumps(summary, indent=2, default=float))
    return summary


def _timing(records):
    by_bin = {}
    for r in records:
        if r["kind"] == "sample":
            by_bin.setdefault(r["size_bin"], []).append(r.get("wall_seconds"))
    return {str(k): _quantiles(v) for k, v in sorted(by_bin.items())}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("census", help="Bounds/attribute census of extracted IUCN shapefiles.")
    scan.add_argument("--source-dir", type=Path, required=True)
    scan.add_argument("--output", type=Path, required=True)
    scan.add_argument("--workers", type=int, default=os.cpu_count())
    run = commands.add_parser("measure", help="Compute every rule for selected polygons.")
    run.add_argument("--census", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--workers", type=int, default=os.cpu_count())
    run.add_argument("--per-bin", type=int, default=40)
    run.add_argument("--large-per-bin", type=int, default=12)
    run.add_argument("--task-timeout", type=float, default=2400)
    run.add_argument("--deadline", type=float, help="Epoch seconds after which no task starts or continues.")
    report = commands.add_parser("analyze", help="Summarize measured rules.")
    report.add_argument("--measure-dir", type=Path, required=True)
    report.add_argument("--weights", type=Path, required=True)
    report.add_argument("--summary", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "census":
        result = census(args.source_dir, args.output, args.workers)
    elif args.command == "measure":
        result = measure(args.census, args.output, args.workers, args.per_bin, args.large_per_bin,
                         args.task_timeout, args.deadline)
    else:
        result = analyze(args.measure_dir, args.weights, args.summary)
        result = result["task_status"]
    print(json.dumps(result, default=float), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
