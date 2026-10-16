"""Bounded parallel feature producer, isolated from the native tile compiler."""

from __future__ import annotations

import json
import multiprocessing as mp
import sys
import time
from collections import deque
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from ark_pipeline.runtime.progress import emit
from ark_pipeline.runtime.provenance import atomic_json

_encoder_key = None
_encoder = None


def encode(job):
    from ark_pipeline.tile_worker_state import INDEXES
    from ark_pipeline.tiles import batch_encoder

    global _encoder_key, _encoder
    resolution, keys, rows, binary = job
    if binary:
        import pyarrow as pa
        import pyarrow.compute as pc
        import shapely

        from ark_pipeline.tiles import BOUNDARY_TILE_PROPERTIES, geometries_for_cells

        started = time.perf_counter()
        columns = list(zip(*rows, strict=True))
        geometry, polygons = geometries_for_cells(columns[0])
        arrays = {"h3_index": pa.array(columns[0], type=pa.string()),
                  "resolution": pa.array([resolution] * len(rows), type=pa.int64())}
        for framework, name in BOUNDARY_TILE_PROPERTIES.items():
            codes = INDEXES[framework].codes_for_geometries(polygons) if framework in INDEXES else [()] * len(rows)
            arrays[name] = pa.array(["|".join(value) for value in codes], type=pa.string())
        for key, values in zip(keys, columns[1:], strict=True):
            values = pa.array(values, type=pa.int64())
            if values.null_count:
                raise ValueError(f"Null metric in tile input: {key}")
            arrays[key] = pc.if_else(pc.equal(values, 0), None, values)
        # Boundary lookup deliberately uses the legacy clipping geometry; export
        # uses the original GeoJSON ring/split, including its dateline vertices.
        for index, value in enumerate(geometry):
            if value["type"] != "Polygon":
                polygons[index] = shapely.from_geojson(json.dumps(value))
        arrays["geometry"] = pa.array(shapely.to_wkb(polygons), type=pa.binary())
        return pa.record_batch(arrays), len(rows), time.perf_counter() - started, 0, 0
    key = (resolution, keys)
    if key != _encoder_key:
        _encoder = batch_encoder(resolution, INDEXES, keys, True)
        _encoder_key = key
    before = _encoder.cache_info()
    started = time.perf_counter()
    output = _encoder(rows).encode("utf-8")
    after = _encoder.cache_info()
    return output, len(rows), time.perf_counter() - started, after.hits - before.hits, after.misses - before.misses


def main():
    # Only the coordinator and already-forked workers import database libraries;
    # the dedicated fork server preloads spatial state without their threads.
    import duckdb
    import pyarrow as pa
    import pyogrio

    from ark_pipeline.tiles import BOUNDARY_TILE_PROPERTIES, METRIC_COLUMNS, PROPERTY_KEYS

    config = json.loads(Path(sys.argv[1]).read_text())
    mp.set_forkserver_preload(["ark_pipeline.tile_worker_state"])
    names = dict(zip(METRIC_COLUMNS, PROPERTY_KEYS, strict=True))
    report = {}
    offset = config["overall_offset"]
    with ProcessPoolExecutor(max_workers=config["workers"], mp_context=mp.get_context("forkserver")) as pool, \
         duckdb.connect(config["database"], read_only=True) as connection:
        connection.execute("SET threads=?", [config["threads"]])
        connection.execute("SET memory_limit=?", [config["memory_limit"]])
        connection.execute("SET temp_directory=?", [config["scratch_dir"]])
        for name, resolution, query, expected in config["jobs"]:
            started = time.perf_counter()
            query_started = time.perf_counter()
            cursor = connection.execute(query)
            keys = tuple(names[column[0]] for column in cursor.description[1:])
            profile = {"query_seconds": time.perf_counter() - query_started,
                       "feature_encoding_seconds": 0.0, "pipe_write_seconds": 0.0,
                       "geojson_bytes": 0, "flatgeobuf_bytes": 0,
                       "metric_cache_hits": 0, "metric_cache_misses": 0}
            count = 0
            binary = config.get("format") == "fgb"
            def batches():
                nonlocal count
                pending = deque()
                exhausted = False
                while pending or not exhausted:
                    while not exhausted and len(pending) < config["workers"] * 2:
                        query_started = time.perf_counter()
                        rows = cursor.fetchmany(config["batch_size"])
                        profile["query_seconds"] += time.perf_counter() - query_started
                        if not rows:
                            exhausted = True
                            break
                        pending.append(pool.submit(encode, (resolution, keys, rows, binary)))
                    if not pending:
                        break
                    # Consume in submission order regardless of worker completion.
                    data, size, seconds, hits, misses = pending.popleft().result()
                    write_started = time.perf_counter()
                    if binary:
                        yield data
                    else:
                        sys.stdout.buffer.write(data)
                    profile["pipe_write_seconds"] += time.perf_counter() - write_started
                    profile["feature_encoding_seconds"] += seconds
                    if not binary:
                        profile["geojson_bytes"] += len(data)
                    profile["metric_cache_hits"] += hits
                    profile["metric_cache_misses"] += misses
                    count += size
                    emit(task="features", phase=f"Stream {name}", completed=count, total=expected,
                         fraction=count / max(1, expected), unit="partition features")
                    emit("work", task="tile-stream", phase="Stream map features", overall=True, scope="phase",
                         completed=offset + count, total=config["overall_total"], unit="features streamed")

            if binary:
                fields = [("h3_index", pa.string()), ("resolution", pa.int64())]
                fields += [(key, pa.string()) for key in BOUNDARY_TILE_PROPERTIES.values()]
                fields += [(key, pa.int64()) for key in keys]
                fields += [("geometry", pa.binary())]
                path = Path(config["report"]).parent / f"input-{name}.fgb"
                path.unlink(missing_ok=True)
                reader = pa.RecordBatchReader.from_batches(pa.schema(fields), batches())
                pyogrio.write_arrow(reader, path, driver="FlatGeobuf", layer=f"res{resolution}",
                                    geometry_name="geometry", geometry_type="Unknown", crs="EPSG:4326",
                                    layer_options={"SPATIAL_INDEX": "NO"})
                profile["flatgeobuf_bytes"] = path.stat().st_size
                profile["input_path"] = str(path)
            else:
                for _ in batches():
                    pass
            if count != expected:
                raise ValueError(f"Expected {expected} features, emitted {count}")
            profile.update(features=count, stream_with_backpressure_seconds=time.perf_counter() - started)
            report[name] = profile
            atomic_json(Path(config["report"]), report)
            print(f"Streamed {name}: {count:,} features", file=sys.stderr, flush=True)
            offset += count
    sys.stdout.buffer.flush()
    # Process exit also releases the fork server and its shared boundary pages.


if __name__ == "__main__":
    main()
