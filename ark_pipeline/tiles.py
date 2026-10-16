"""Bounded feature batches with prepared, shared boundary intersections."""

from __future__ import annotations

import json
import time
from collections import defaultdict
from collections.abc import Iterable
from functools import lru_cache
from typing import TextIO

import h3
import numpy as np
import orjson
import shapely

from ark_pipeline.builders.coarse_cache import (
    BOUNDARY_TILE_PROPERTIES,
    METRIC_TILE_NAMES,
    METRICS,
    SYSTEM_TILE_PREFIXES,
    SYSTEMS,
    TILE_ZOOM_RANGES,
    antimeridian_safe_polygon,
)
from ark_pipeline.spatial.boundaries import cell_geometry
from ark_pipeline.tile_spatial import BoundaryBatchIndex

METRIC_COLUMNS = tuple(f"{metric}__{system.lower()}" for system in SYSTEMS for metric in METRICS)
PROPERTY_KEYS = tuple(
    f"{SYSTEM_TILE_PREFIXES[system]}_{METRIC_TILE_NAMES[metric]}"
    for system in SYSTEMS for metric in METRICS
)



def geometries_for_cells(ids: list[str]) -> tuple[list[dict], np.ndarray]:
    """Build each ring once; preserve the established dateline lookup policy."""
    geojson = []
    groups: dict[int, list[int]] = defaultdict(list)
    polygons = np.empty(len(ids), dtype=object)
    for index, cell in enumerate(ids):
        ring = [[lon, lat] for lat, lon in h3.cell_to_boundary(cell)]
        geometry = antimeridian_safe_polygon(ring)
        geojson.append(geometry)
        if geometry["type"] == "Polygon":
            groups[len(geometry["coordinates"][0])].append(index)
        else:
            # Dateline and polar cells use the shared canonical clipping contract.
            polygons[index] = cell_geometry(cell)
    for indexes in groups.values():
        polygons[indexes] = shapely.polygons([geojson[i]["coordinates"][0] for i in indexes])
    return geojson, polygons


def feature_batch(
    rows: list[tuple], resolution: int, indexes: dict[str, BoundaryBatchIndex],
    *, include_metrics: bool = True,
) -> Iterable[dict]:
    ids = [row[0] for row in rows]
    geometry, polygons = geometries_for_cells(ids)
    codes = {key: index.codes_for_geometries(polygons) for key, index in indexes.items()}
    zoom = TILE_ZOOM_RANGES[resolution]
    for offset, row in enumerate(rows):
        properties = {"h3_index": row[0], "resolution": resolution}
        for framework, key in BOUNDARY_TILE_PROPERTIES.items():
            properties[key] = "|".join(codes[framework][offset]) if framework in codes else ""
        if include_metrics:
            properties.update(zip(PROPERTY_KEYS, (int(value) for value in row[1:]), strict=True))
        yield {
            "type": "Feature",
            "tippecanoe": {"layer": f"res{resolution}", "minzoom": zoom["min"], "maxzoom": zoom["max"]},
            "properties": properties,
            "geometry": geometry[offset],
        }


def batch_encoder(resolution, indexes, property_keys, omit_zero_metrics):
    encode = json.JSONEncoder(separators=(",", ":")).encode
    if omit_zero_metrics:
        # Native float formatting avoids Python's per-coordinate conversion.
        # Dense diagnostic output retains its historical byte representation.
        def encode(value):
            return orjson.dumps(value).decode("utf-8")

    # Adjacent cells commonly share species lists and therefore all 164 metrics.
    # Cache only their encoded properties, never cell geometry or whole features.
    # Scope the bounded cache to this query so partitions cannot accumulate RAM.
    @lru_cache(maxsize=4096)
    def metric_json(values):
        properties = dict(zip(property_keys, map(int, values), strict=True))
        if omit_zero_metrics:
            properties = {key: value for key, value in properties.items() if value != 0}
        fragment = encode(properties)[1:-1]
        return ',' + fragment if fragment else ''

    def encode_rows(rows):
        lines = []
        for row, feature in zip(rows, feature_batch(rows, resolution, indexes, include_metrics=False), strict=True):
            # Assemble encoded objects at explicit JSON boundaries; never replace
            # text inside strings. This retains the original key and feature order.
            lines.append('{"type":"Feature","tippecanoe":' + encode(feature["tippecanoe"])
                         + ',"properties":' + encode(feature["properties"])[:-1]
                         + metric_json(tuple(row[1:])) + '},"geometry":'
                         + encode(feature["geometry"]) + '}\n')
        return "".join(lines)

    encode_rows.cache_info = metric_json.cache_info
    return encode_rows


def stream_query(connection, query: str, stream: TextIO, resolution: int,
                 indexes: dict[str, BoundaryBatchIndex], batch_size: int = 2048, progress=None,
                 *, omit_zero_metrics: bool = False, profile: dict | None = None) -> int:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    count = 0
    property_keys = PROPERTY_KEYS
    query_seconds = encoding_seconds = write_seconds = 0.0
    encoded_bytes = 0
    started = time.perf_counter()
    cursor = connection.execute(query)
    if omit_zero_metrics:
        # Managed queries may leave out columns proven all-zero by Parquet
        # statistics. Match by name so values cannot shift to a different metric.
        names = dict(zip(METRIC_COLUMNS, PROPERTY_KEYS, strict=True))
        property_keys = tuple(names[column[0]] for column in cursor.description[1:])
    query_seconds += time.perf_counter() - started
    encode_rows = batch_encoder(resolution, indexes, property_keys, omit_zero_metrics)
    while True:
        started = time.perf_counter()
        rows = cursor.fetchmany(batch_size)
        query_seconds += time.perf_counter() - started
        if not rows:
            break
        started = time.perf_counter()
        batch = encode_rows(rows)
        encoded_bytes += len(batch.encode("utf-8"))
        encoding_seconds += time.perf_counter() - started
        started = time.perf_counter()
        stream.write(batch)
        write_seconds += time.perf_counter() - started
        count += len(rows)
        if progress:
            progress(count)
    if profile is not None:
        profile.update(query_seconds=query_seconds, feature_encoding_seconds=encoding_seconds,
                       pipe_write_seconds=write_seconds, geojson_bytes=encoded_bytes,
                       metric_cache_hits=encode_rows.cache_info().hits,
                       metric_cache_misses=encode_rows.cache_info().misses)
    return count
