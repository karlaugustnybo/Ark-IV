"""Measure one explicitly bounded command in an isolated cloud output directory.

The report distinguishes process RSS from cgroup file cache and samples temporary
files. Sampled peaks are lower bounds; this does not predict a global runtime.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import psutil
from launch import owned_process

from ark_pipeline.runtime.provenance import atomic_json, runtime_identity
from ark_pipeline.runtime.resources import effective_limits


def counters(name):
    try:
        return {key: int(value) for key, value in
                (line.split() for line in (Path('/sys/fs/cgroup') / name).read_text().splitlines())}
    except (OSError, ValueError):
        return {}


def cgroup_text(name):
    try:
        return (Path('/sys/fs/cgroup') / name).read_text().strip()
    except OSError:
        return None


def sample(process, scratch):
    rss = cpu = count = threads = 0
    io = {}
    with contextlib.suppress(psutil.Error):
        for child in [process, *process.children(recursive=True)]:
            with contextlib.suppress(psutil.Error):
                # smaps/USS walks can consume a core with many large workers.
                # Fast RSS plus cgroup anonymous/file counters keep the observer
                # small; summed RSS includes shared pages and is labelled so.
                memory = child.memory_info()
                rss += memory.rss
                cpu += sum(child.cpu_times()[:2])
                count += 1
                threads += child.num_threads()
                with contextlib.suppress(psutil.Error, AttributeError):
                    usage = child.io_counters()
                    io[str(child.pid)] = {key: getattr(usage, key, None) for key in
                        ('read_bytes', 'write_bytes', 'read_chars', 'write_chars')}
    temporary = 0
    for root in scratch:
        if root.is_dir():
            for path in root.rglob('*'):
                with contextlib.suppress(OSError):
                    if path.is_file():
                        temporary += path.stat().st_size
    memory = counters('memory.stat')
    return {'rss_bytes': rss, 'process_cpu_seconds': cpu,
            'processes': count, 'threads': threads, 'process_io': io, 'scratch_bytes': temporary,
            'cgroup_anon_bytes': memory.get('anon'), 'cgroup_file_bytes': memory.get('file'),
            'cgroup_working_proxy_bytes': sum(memory.get(key, 0) for key in ('anon', 'shmem', 'kernel')),
            'cpu_stat': counters('cpu.stat'), 'memory_events': counters('memory.events'),
            'io_stat': cgroup_text('io.stat'), 'cpu_pressure': cgroup_text('cpu.pressure'),
            'memory_pressure': cgroup_text('memory.pressure'), 'io_pressure': cgroup_text('io.pressure')}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--scratch', type=Path, action='append', default=[])
    parser.add_argument('--timeout', type=int, required=True)
    parser.add_argument('--label', required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if args.output.exists() or args.timeout <= 0 or not command:
        parser.error('Use a fresh report directory, positive timeout and explicit command')
    args.output.mkdir(parents=True)
    report = {'label': args.label, 'command': command, 'status': 'running',
              'disk_free_before_bytes': shutil.disk_usage(args.output).free,
              'effective_limits': effective_limits(), 'runtime': runtime_identity(Path.cwd()),
              'cpu_affinity': sorted(os.sched_getaffinity(0)) if hasattr(os, 'sched_getaffinity') else None,
              'cpu_model': next((line.split(':', 1)[1].strip()
                                 for line in Path('/proc/cpuinfo').read_text().splitlines()
                                 if line.startswith('model name')), None) if Path('/proc/cpuinfo').is_file() else None,
              'environment': {key: os.environ.get(key) for key in
                  ('PIPELINE_WORKERS', 'SPATIAL_WORKERS', 'RES7_WORKERS', 'RES7_THREADS',
                   'DUCKDB_THREADS', 'DUCKDB_MEMORY_LIMIT', 'OMP_NUM_THREADS',
                   'OPENBLAS_NUM_THREADS', 'RAYON_NUM_THREADS')},
              'before': sample(psutil.Process(), args.scratch)}
    atomic_json(args.output / 'report.json', report)
    stop = threading.Event()
    observations = []
    started = time.monotonic()
    with (args.output / 'command.log').open('w') as log:
        with owned_process(command, stdout=log, stderr=subprocess.STDOUT) as child:
            process = psutil.Process(child.pid)

            def monitor():
                with (args.output / 'samples.jsonl').open('w') as samples:
                    while not stop.is_set():
                        row = sample(process, args.scratch)
                        row['seconds'] = time.monotonic() - started
                        observations.append(row)
                        samples.write(json.dumps(row) + '\n')
                        samples.flush()
                        if row['cgroup_working_proxy_bytes'] > report['effective_limits']['memory_bytes'] * .9:
                            # Match the full-job guard: leave room for diagnostics
                            # and persistence. Sampling cannot prevent every OOM.
                            report['memory_headroom_stop'] = True
                            with contextlib.suppress(ProcessLookupError):
                                child.terminate()
                            return
                        stop.wait(.25)

            thread = threading.Thread(target=monitor, daemon=True)
            thread.start()
            try:
                child.wait(timeout=args.timeout)
                report['exit_code'] = child.returncode
                report['status'] = ('memory-headroom-stop' if report.get('memory_headroom_stop')
                                    else 'passed' if child.returncode == 0 else 'failed')
            except subprocess.TimeoutExpired:
                report['status'] = 'timeout'
            finally:
                stop.set()
                thread.join(timeout=5)
                report['seconds'] = time.monotonic() - started
                report['after'] = sample(psutil.Process(), args.scratch)
                report['disk_free_after_bytes'] = shutil.disk_usage(args.output).free
                report['sample_interval_seconds'] = .25
                report['samples'] = len(observations)
                report['peaks'] = {key: max((row[key] or 0 for row in observations), default=0)
                    for key in ('rss_bytes', 'scratch_bytes', 'processes', 'threads',
                                'cgroup_anon_bytes', 'cgroup_file_bytes', 'cgroup_working_proxy_bytes')}
                per_process = {}
                for row in observations:
                    for pid, values in row['process_io'].items():
                        current = per_process.setdefault(pid, {})
                        for key, value in values.items():
                            if value is not None:
                                current[key] = max(current.get(key, 0), value)
                report['sampled_process_io_lower_bound'] = {
                    key: sum(values.get(key, 0) for values in per_process.values())
                    for key in ('read_bytes', 'write_bytes', 'read_chars', 'write_chars')}
                before_cpu = report['before']['cpu_stat'].get('usage_usec')
                after_cpu = report['after']['cpu_stat'].get('usage_usec')
                report['cgroup_cpu_seconds'] = ((after_cpu-before_cpu)/1e6
                    if before_cpu is not None and after_cpu is not None else None)
                atomic_json(args.output / 'report.json', report)
    print(json.dumps({key: report[key] for key in ('label', 'status', 'seconds', 'peaks')}))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
