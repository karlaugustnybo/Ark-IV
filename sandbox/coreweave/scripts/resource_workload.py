"""Bounded FAST/real-partition workloads using production pipeline functions."""

from __future__ import annotations

import argparse
import json
import multiprocessing
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from ark_pipeline.aggregation.metrics import aggregate_species_lists
from ark_pipeline.aggregation.pairs import aggregate_sorted_pairs
from ark_pipeline.builders.coarse_cache import sql_path
from ark_pipeline.builders.fine_metrics import (
    aggregate_worker,
    initialize_aggregate_worker,
    prepare_species,
    validate_aggregate_part,
)
from ark_pipeline.builders.spatial_analytics import build_spatial_analytics
from ark_pipeline.cli import benchmark_pipeline as bp
from ark_pipeline.runtime.provenance import atomic_json, sha256
from ark_pipeline.spatial.coverage import load_spatial_profile


def fingerprint(path):
    """Hash logical ordered Arrow values, independent of Parquet row groups."""
    import hashlib
    digest = hashlib.sha256()
    with pq.ParquetFile(path) as reader:
        for batch in reader.iter_batches(batch_size=65536):
            sink = pa.BufferOutputStream()
            with pa.ipc.new_stream(sink, batch.schema) as writer:
                writer.write_batch(batch)
            digest.update(sink.getvalue())
    return digest.hexdigest()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage', choices=('pairs', 'partition', 'reduce', 'metrics', 'compare-pairs', 'metric-batch', 'ranges'))
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--root', type=Path)
    p.add_argument('--profile', type=Path, default=Path('config/spatial_semantics_iucn_richness_v3_fast.toml'))
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--workers', type=int, default=8)
    p.add_argument('--threads', type=int, default=1)
    p.add_argument('--memory-limit', default='2GB')
    p.add_argument('--max-per-bin', type=int, default=3)
    p.add_argument('--batch-rows', type=int, default=128)
    p.add_argument('--batch-pairs', type=int, default=250000)
    p.add_argument('--batch-cells', type=int, default=16384)
    p.add_argument('--relationship-chunk', type=int, default=250000)
    p.add_argument('--reverse', action='store_true')
    args = p.parse_args(argv)
    if args.output.exists() or min(args.workers, args.threads, args.batch_rows, args.batch_pairs) < 1:
        p.error('Fresh outputs and positive resource settings required')
    args.output.mkdir(parents=True)
    if args.stage == 'pairs':
        profile = load_spatial_profile(args.profile)
        if profile.coverage_mode != 'fast':
            p.error('Resource investigation permits FAST coverage only')
        profile_path = args.output / 'profile.toml'
        profile_path.write_text(args.profile.read_text().replace(
            'geometry_batch_rows = 128', f'geometry_batch_rows = {args.batch_rows}'))
        profile = load_spatial_profile(profile_path)
        selection = bp.select_sample(args.input, args.root, profile, args.max_per_bin)
        atomic_json(args.output / 'selection.json', selection)
        bp.build_pairs({'output': str(args.output), 'profile': str(profile_path),
                        'resources': {'spatial_workers': args.workers},
                        'reverse_bands': args.reverse})
        return 0
    with duckdb.connect() as con:
        con.execute('SET threads=?', [args.threads])
        con.execute('SET memory_limit=?', [args.memory_limit])
        con.execute('SET preserve_insertion_order=false')
        scratch = args.output / 'scratch'
        scratch.mkdir()
        con.execute('SET temp_directory=?', [str(scratch.resolve())])
        settings = dict(con.execute("SELECT name,value FROM duckdb_settings() WHERE name IN ('threads','memory_limit','temp_directory')").fetchall())
        query_profile = args.output / 'duckdb-profile.json'
        con.execute("SET enable_profiling='json'")
        con.execute("SET profiling_output=?", [str(query_profile.resolve())])

        def profile_peaks():
            if not query_profile.is_file():
                return None
            data = json.loads(query_profile.read_text())
            return {name: data.get(name) for name in
                    ('system_peak_buffer_memory', 'system_peak_temp_dir_size',
                     'total_bytes_read', 'total_bytes_written', 'cpu_time', 'latency')}
        if args.stage == 'metric-batch':
            pa.set_cpu_count(args.threads)
            prepare_species(con, args.root / 'species/species.parquet', args.root / 'species/species_systems.parquet')
            target = args.output / 'metrics.parquet'
            aggregate_species_lists(con, args.input, target, batch_cells=args.batch_cells,
                                    relationship_chunk=args.relationship_chunk)
            result = validate_aggregate_part(con, args.input, target)
            result.update(effective_settings=settings, values_sha256=fingerprint(target),
                          duckdb_profile=profile_peaks())
            atomic_json(args.output / 'result.json', result)
            return 0
        if args.stage == 'ranges':
            # This isolated test generation is geographically incomplete. It is
            # never selected by serving or presented as global coverage.
            paths = [args.input] if args.input.is_file() else sorted(args.input.glob('base_*.parquet'))
            result = build_spatial_analytics(con, paths, args.output / 'ranges',
                profile='bounded-historical-partition-probe', membership_generation='bounded-probe',
                coverage_status='partial')
            result.update(effective_settings=settings, duckdb_profile=profile_peaks(),
                          values_sha256=fingerprint(args.output / 'ranges/species_ranges.parquet'))
            atomic_json(args.output / 'result.json', result)
            return 0
        if args.stage == 'partition':
            sources = sorted(args.input.glob('spatial/archives/*/res7_pairs.parquet'))
            if not sources:
                p.error('No restored pair inputs')
            paths = '[' + ','.join(sql_path(path) for path in sources) + ']'
            con.execute(f"COPY (SELECT *, ((h3_index >> 45) & 127)::INTEGER AS base_cell FROM read_parquet({paths})) TO {sql_path(args.output / 'parts')} (FORMAT PARQUET, COMPRESSION ZSTD, PARTITION_BY(base_cell))")
            sizes = sorted([{'base': int(path.name.split('=')[1]),
                             'rows': sum(pq.read_metadata(f).num_rows for f in path.glob('*.parquet')),
                             'bytes': sum(f.stat().st_size for f in path.glob('*.parquet'))}
                            for path in (args.output / 'parts').iterdir()], key=lambda row:-row['rows'])
            atomic_json(args.output / 'partitions.json', {'inputs': [str(x) for x in sources], 'parts': sizes})
            return 0
        if args.stage == 'reduce':
            result = aggregate_sorted_pairs(con, sql_path(args.input), args.output / 'lists.parquet',
                                            resolution=7, deduplicate=True,
                                            batch_pairs=args.batch_pairs)
            result['list_duckdb_profile'] = profile_peaks()
            # Use the identical production provenance reduction as list export.
            con.execute(f"COPY (SELECT h3_index,iucn_sis_id,1::UTINYINT AS source_support FROM read_parquet({sql_path(args.input)}) GROUP BY 1,2 ORDER BY 1,2) TO {sql_path(args.output / 'support.parquet')} (FORMAT PARQUET, COMPRESSION ZSTD)")
            result.update(effective_settings=settings, support_duckdb_profile=profile_peaks(),
                          input_sha256=sha256(args.input),
                          list_values_sha256=fingerprint(args.output / 'lists.parquet'),
                          support_values_sha256=fingerprint(args.output / 'support.parquet'))
            atomic_json(args.output / 'result.json', result)
            return 0
        if args.stage == 'compare-pairs':
            con.execute(f"COPY (SELECT h3_index,iucn_sis_id FROM read_parquet({sql_path(args.input)}) ORDER BY 1,2) TO {sql_path(args.output / 'ordered.parquet')} (FORMAT PARQUET)")
            atomic_json(args.output / 'result.json', {'ordered_values_sha256': fingerprint(args.output / 'ordered.parquet'),
                'rows': pq.read_metadata(args.input).num_rows})
            return 0
    species, systems = args.root / 'species/species.parquet', args.root / 'species/species_systems.parquet'
    parts = sorted(args.input.glob('base_*.parquet'))
    if not parts:
        p.error('No checked metric list partitions')
    started = time.monotonic()
    with ProcessPoolExecutor(max_workers=args.workers, mp_context=multiprocessing.get_context('spawn'),
        initializer=initialize_aggregate_worker,
        initargs=(species, systems, args.output / 'scratch', args.memory_limit, args.threads)) as pool:
        futures = [pool.submit(aggregate_worker, int(path.stem.removeprefix('base_')), path,
                              args.output / path.name, sha256(path)) for path in parts]
        results = [future.result() for future in futures]
    workload_seconds = time.monotonic() - started
    started = time.monotonic()
    outputs = {path.name: fingerprint(args.output / path.name) for path in parts}
    atomic_json(args.output / 'result.json', {'parts': results, 'outputs': outputs,
                'production_worker_seconds': workload_seconds,
                'comparison_hash_seconds': time.monotonic() - started})
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
