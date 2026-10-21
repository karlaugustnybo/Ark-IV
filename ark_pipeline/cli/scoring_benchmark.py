"""Bounded synthetic evidence/tree/serialization/membership performance probe."""

from __future__ import annotations

import argparse
import concurrent.futures
import platform
import resource
import sys
import threading
import time
from pathlib import Path

import duckdb
import h3
import numpy as np
import orjson
import psutil
import pyarrow as pa
import pyarrow.parquet as pq

from ark_pipeline.aggregation.analytical_scores import aggregate_scores
from ark_pipeline.aggregation.metrics import METRIC_NAMES
from ark_pipeline.aggregation.pairs import LIST_SCHEMA
from ark_pipeline.dna_evidence import DnaRule, evaluate_evidence
from ark_pipeline.phylogenetic_scenarios import CompactTree, ScenarioEngine
from ark_pipeline.runtime.provenance import atomic_json, runtime_identity
from ark_pipeline.runtime.resources import effective_limits


def benchmark(output: Path, tips: int = 32768, cells: int = 100, richness: int = 2000,
              memory_limit: str = '256MB') -> dict:
    if not 2 <= tips <= 131072 or not 1 <= cells <= 1000 or not 1 <= richness <= tips:
        raise ValueError('Synthetic fixture exceeds the bounded probe limits')
    if memory_limit not in ('128MB', '256MB', '512MB', '1GB'):
        raise ValueError('Require a bounded benchmark DuckDB memory limit')
    output.mkdir(parents=True, exist_ok=False)
    times = {}

    def timed(name, fn):
        started = time.perf_counter()
        value = fn()
        times[name] = time.perf_counter() - started
        return value

    n = 2*tips - 1
    tree = CompactTree(np.concatenate(([-1], (np.arange(1, n)-1)//2)),
        np.concatenate(([0.], np.ones(n-1))), np.arange(tips-1, n), tuple(f'tip{i}' for i in range(tips)))
    evidence = [dict(taxon_id=str(i), tree_tip=f'tip{i}', genomic_transfer_safe=True,
                     assembly_level='Chromosome' if i % 10 == 0 else '',
                     busco_completeness='95' if i % 10 == 0 else '',
                     sample_acquired='yes' if i % 17 == 0 else '') for i in range(tips)]
    species = [dict(iucn_sis_id=str(i), tree_tip=f'tip{i}', tree_identity_verified=True,
                    genomic_transfer_safe=True, redlist_category='EN') for i in range(tips)]
    rule = DnaRule()
    represented = timed('evidence_seconds', lambda: {r['tree_tip'] for r in evidence if evaluate_evidence(r, rule).qualifies is True})
    timed('gap_kernel_seconds', lambda: tree.gaps(represented))
    timed('nearest_diagnostic_seconds', lambda: tree.nearest(represented))
    engine = ScenarioEngine(tree, evidence, species, dict(tree='synthetic-binary-v1', root='stem-free',
        scope='synthetic', units='Myr', stem='excluded', evidence='deterministic-v1', identities='one-to-one-v1'))
    default = timed('scenario_cold_seconds', lambda: engine.rank(rule, {'EN': 3.}))
    warm = timed('scenario_warm_seconds', lambda: engine.rank(rule, {'EN': 3.}))
    if warm != default:
        raise ValueError('Cached/dynamic scenario mismatch')
    encoded = timed('serialization_seconds', lambda: orjson.dumps(default))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        concurrent_results = timed('four_warm_requests_seconds', lambda: list(pool.map(
            lambda _: engine.rank(rule, {'EN': 3.})['weighted_scenario_id'], range(4))))
    if set(concurrent_results) != {default['weighted_scenario_id']}:
        raise ValueError('Concurrent scenario identity mismatch')
    scores = output / 'scores.parquet'
    pq.write_table(pa.Table.from_pylist(default['rows']), scores, compression='zstd')
    h3_cells = sorted(h3.grid_disk(h3.latlng_to_cell(55, 12, 7), 30))[:cells]
    lists = output / 'memberships.parquet'
    pq.write_table(pa.table({'h3_cell': pa.array([int(c,16) for c in h3_cells], type=pa.uint64()),
                            'species_ids': [[(i + j*17) % tips for i in range(richness)] for j, _ in enumerate(h3_cells)]}, schema=LIST_SCHEMA), lists, compression='zstd')
    with duckdb.connect() as c:
        c.execute("SET threads=1")
        c.execute('SET memory_limit=?', [memory_limit])
        scratch = output / 'scratch'
        scratch.mkdir()
        c.execute('SET temp_directory=?', [str(scratch.resolve())])
        stop = threading.Event()
        observations = {'scratch_allocated_peak_bytes': 0, 'process_rss_peak_bytes': 0, 'samples': 0}
        def cgroup_snapshot():
            result = {}
            for name in ('memory.current', 'memory.peak', 'cpu.stat', 'cpu.pressure', 'memory.pressure', 'io.stat'):
                try:
                    result[name] = (Path('/sys/fs/cgroup') / name).read_text().strip()
                except OSError:
                    result[name] = None
            return result
        observations['cgroup_before'] = cgroup_snapshot()
        process = psutil.Process()
        io_before = process.io_counters() if hasattr(process, 'io_counters') else None
        def observe():
            while not stop.is_set():
                allocated = 0
                for path in scratch.rglob('*'):
                    try:
                        allocated += path.stat().st_blocks * 512 if path.is_file() else 0
                    except FileNotFoundError:
                        pass
                observations['scratch_allocated_peak_bytes'] = max(observations['scratch_allocated_peak_bytes'], allocated)
                observations['process_rss_peak_bytes'] = max(observations['process_rss_peak_bytes'], process.memory_info().rss)
                observations['samples'] += 1
                stop.wait(.01)
        monitor = threading.Thread(target=observe, daemon=True)
        monitor.start()
        cpu_before = time.process_time()
        try:
            spatial = timed('dense_aggregation_seconds', lambda: aggregate_scores(c, [lists], scores, output / 'cell_scores.parquet',
                resolution=7, scenario_id=default['weighted_scenario_id'], membership_generation='synthetic-memberships-v1', profile='synthetic'))
        finally:
            observations['cpu_seconds'] = time.process_time() - cpu_before
            stop.set()
            monitor.join()
        io_after = process.io_counters() if io_before is not None else None
        observations['io_bytes'] = {key: getattr(io_after, key) - getattr(io_before, key)
            for key in ('read_bytes', 'write_bytes')} if io_after else None
        observations['sampling_interval_seconds'] = .01
        observations['interpretation'] = 'sampled scratch allocation is a lower bound, not proof of no spill'
        observations['cgroup_after'] = cgroup_snapshot()
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report = {'scope': 'synthetic only; no global interactive latency claim', 'tree_nodes': n, 'tree_tips': tips,
        'represented_tips': len(represented), 'memberships': cells * richness, 'cells': cells,
        'workers': 1, 'concurrent_warm_requests': 4, 'duckdb_threads': 1, 'duckdb_memory_limit': memory_limit,
        'timings': times, 'full_scenario_json_bytes': len(encoded), 'spatial_output': spatial,
        'process_peak_rss_bytes': peak if sys.platform == 'darwin' else peak * 1024,
        'aggregation_resources': observations,
        'effective_limits': effective_limits(), 'runtime': runtime_identity(Path(__file__).parents[2]),
        'hardware': platform.processor(), 'base_metric_names': list(METRIC_NAMES),
        'new_base_map_properties': 0, 'optional_map_modes_enabled': 0}
    atomic_json(output / 'benchmark.json', report)
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--tips', type=int, default=32768)
    p.add_argument('--cells', type=int, default=100)
    p.add_argument('--richness', type=int, default=2000)
    p.add_argument('--memory-limit', choices=('128MB', '256MB', '512MB', '1GB'), default='256MB')
    args = p.parse_args(argv)
    report = benchmark(args.output, args.tips, args.cells, args.richness, args.memory_limit)
    print(orjson.dumps(report).decode())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
