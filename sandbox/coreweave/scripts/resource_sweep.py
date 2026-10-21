"""Run a small explicit resource matrix; no acquisition or global coverage run."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from ark_pipeline.runtime.provenance import atomic_json

HERE = Path(__file__).resolve().parent


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('suite', choices=('pairs', 'metrics', 'reduce', 'scan'))
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--root', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--workers', default='4,8,16,16,8,4')
    p.add_argument('--memory-limits', default='2GB,4GB,8GB,4GB,2GB')
    p.add_argument('--batch-rows', type=int, default=128)
    p.add_argument('--batch-pairs', type=int, default=250000)
    p.add_argument('--max-per-bin', type=int, default=3)
    p.add_argument('--scan-cache-replay', action='store_true',
                   help='For each cold scan, populate an isolated census cache and measure its verified reuse separately')
    p.add_argument('--threads', type=int, default=8)
    p.add_argument('--timeout', type=int, default=1200)
    args = p.parse_args(argv)
    if args.output.exists():
        p.error('Use a fresh sweep root')
    args.output.mkdir(parents=True)
    report = {'suite': args.suite, 'status': 'running', 'trials': [],
              'cache_state': 'Separate processes; OS file cache retained; repeated alternating settings',
              'scope': 'Fixed bounded inputs, not a measured global runtime'}
    values = (args.memory_limits.split(',') if args.suite == 'reduce'
              else [int(value) for value in args.workers.split(',')])
    for index, value in enumerate(values):
        trial = args.output / f'{index}-{value}'
        output = trial / 'output'
        trial.mkdir()
        environment = {**os.environ, 'OMP_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1',
                       'RAYON_NUM_THREADS': '1', 'PIPELINE_WORKERS': str(value if isinstance(value, int) else args.threads)}
        if args.suite == 'scan':
            config = {'root': str(args.input), 'output': str(output),
                      'profile': str(Path('config/spatial_semantics_iucn_richness_v3_fast.toml').resolve()),
                      'scan_workers': value, 'resources': {'workers': value}}
            if args.scan_cache_replay:
                config['census_cache'] = str(trial / 'census-cache')
            atomic_json(trial / 'config.json', config)
            command = [sys.executable, '-m', 'ark_pipeline.cli.benchmark_pipeline',
                       '--stage', 'source_scan', '--config', str(trial / 'config.json')]
        else:
            command = [sys.executable, str(HERE / 'resource_workload.py'), args.suite,
                       '--input', str(args.input), '--output', str(output),
                       '--threads', str(args.threads), '--batch-rows', str(args.batch_rows),
                       '--batch-pairs', str(args.batch_pairs), '--max-per-bin', str(args.max_per_bin)]
            if args.root:
                command += ['--root', str(args.root)]
            if args.suite == 'reduce':
                command += ['--memory-limit', value]
            else:
                command += ['--workers', str(value)]
            if args.suite == 'pairs':
                command += ['--reverse']
        measured = [sys.executable, str(HERE / 'resource_probe.py'), '--output', str(trial / 'telemetry'),
                    '--scratch', str(output / 'scratch'), '--timeout', str(args.timeout),
                    '--label', f'{args.suite}-{index}-{value}', '--', *command]
        result = subprocess.run(measured, env=environment, check=False)
        telemetry = json.loads((trial / 'telemetry/report.json').read_text())
        if (output / 'result.json').is_file():
            telemetry['workload'] = json.loads((output / 'result.json').read_text())
        report['trials'].append(telemetry)
        report['status'] = 'running' if result.returncode == 0 else 'failed'
        atomic_json(args.output / 'sweep.json', report)
        if result.returncode:
            return result.returncode
        if args.suite == 'scan' and args.scan_cache_replay:
            replay = {**config, 'output': str(trial / 'cached-output')}
            atomic_json(trial / 'cached-config.json', replay)
            cached_command = [sys.executable, '-m', 'ark_pipeline.cli.benchmark_pipeline',
                              '--stage', 'source_scan', '--config', str(trial / 'cached-config.json')]
            cached_probe = [sys.executable, str(HERE / 'resource_probe.py'), '--output',
                            str(trial / 'cached-telemetry'), '--timeout', str(args.timeout),
                            '--label', f'scan-cache-{index}-{value}', '--', *cached_command]
            cached = subprocess.run(cached_probe, env=environment, check=False)
            telemetry['cached_replay'] = json.loads((trial / 'cached-telemetry/report.json').read_text())
            atomic_json(args.output / 'sweep.json', report)
            if cached.returncode:
                return cached.returncode
    if args.suite in ('metrics', 'reduce'):
        results = [json.loads((path / 'output/result.json').read_text())
                   for path in sorted(args.output.iterdir()) if path.is_dir()]
        keys = ('outputs',) if args.suite == 'metrics' else ('list_values_sha256', 'support_values_sha256')
        for key in keys:
            if any(row[key] != results[0][key] for row in results[1:]):
                raise ValueError('Resource settings changed logical outputs: ' + key)
        report['output_parity'] = 'Exact ordered Arrow-value hashes agree for every trial'
    report['status'] = 'passed'
    atomic_json(args.output / 'sweep.json', report)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
