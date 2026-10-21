"""Measure private existing coarse/fine payloads without starting a server.

JSON/Arrow decode measurements are Python proxies. They are not browser frame
times or network latency; optional analytical outputs are not startup inputs.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import time
from pathlib import Path

import duckdb
import h3
import psutil
import pyarrow as pa

from ark_pipeline.runtime.provenance import atomic_json, sha256
from backend.res7_tiles import render_tile


def timed(function):
    started = time.perf_counter()
    value = function()
    return value, time.perf_counter() - started


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--arrow', type=Path, required=True)
    p.add_argument('--metrics', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args(argv)
    if args.output.exists():
        p.error('Use a fresh private output')
    process = psutil.Process()
    rss_before = process.memory_info().rss
    raw = args.arrow.read_bytes()
    table, decode = timed(lambda: pa.ipc.open_file(pa.BufferReader(raw)).read_all())
    report = {'scope': 'Private saved-generation payload baseline, no browser/network measurements',
              'arrow': {'sha256': sha256(args.arrow), 'file_bytes': len(raw),
                        'gzip4_bytes': len(gzip.compress(raw, compresslevel=4, mtime=0)),
                        'decoded_buffer_bytes': table.nbytes, 'rows': table.num_rows,
                        'columns': table.num_columns, 'decode_seconds': decode,
                        'rss_increase_bytes': process.memory_info().rss - rss_before}, 'fine': []}
    with duckdb.connect() as con:
        con.execute('SET threads=1')
        con.execute("SET memory_limit='2GB'")
        path = str(args.metrics / 'base_*.parquet')
        for label, order in (('dense', 'DESC'), ('sparse', 'ASC')):
            cell = con.execute(f'SELECT h3_index FROM read_parquet(?) WHERE total_species__all>0 ORDER BY total_species__all {order},h3_index LIMIT 1', [path]).fetchone()[0]
            lat, lon = h3.cell_to_latlng(cell)
            for z in (8, 10):
                x = int((lon + 180) / 360 * 2**z)
                y = int((1-math.asinh(math.tan(math.radians(lat))) / math.pi)/2*2**z)
                render_tile.cache_clear()
                payload, cold = timed(lambda: render_tile(str(args.metrics), z, x, y, 'all', 1))
                _, warm = timed(lambda: render_tile(str(args.metrics), z, x, y, 'all', 1))
                decoded, json_decode = timed(lambda: json.loads(payload))
                report['fine'].append({'case': label, 'zoom': z, 'json_bytes': len(payload),
                    'gzip4_bytes': len(gzip.compress(payload, compresslevel=4, mtime=0)),
                    'cells': len(decoded['cells']), 'cold_render_seconds': cold,
                    'cached_render_seconds': warm, 'json_decode_seconds': json_decode})
    atomic_json(args.output, report)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
