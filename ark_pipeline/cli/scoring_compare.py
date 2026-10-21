"""Write a private common-cohort species-rank or cell-hotspot comparison."""

from __future__ import annotations

import argparse
from pathlib import Path

import pyarrow.parquet as pq

from ark_pipeline.robustness import compare_scenarios
from ark_pipeline.runtime.provenance import atomic_json, sha256


def load_scores(path: Path, identifier: str, score: str, stratum: str | None, max_rows: int):
    table = pq.ParquetFile(path)
    if not 1 <= max_rows <= 5_000_000 or table.metadata.num_rows > max_rows:
        raise ValueError('Comparison exceeds the explicit bounded row budget')
    groups: dict[str, dict[str, float | None]] = {'all': {}}
    columns = list(dict.fromkeys([identifier, score, *([stratum] if stratum else [])]))
    for batch in table.iter_batches(columns=columns, batch_size=65536):
        for row in batch.to_pylist():
            key = row[identifier]
            if key is None or str(key) in groups['all']:
                raise ValueError('Comparison identifiers must be nonnull and unique')
            key = str(key)
            value = float(row[score]) if row[score] is not None else None
            groups['all'][key] = value
            if stratum:
                # Prefix distinguishes a named stratum from the aggregate cohort.
                label = f'stratum:{row[stratum]}' if row[stratum] is not None else 'stratum:<unknown>'
                groups.setdefault(label, {})[key] = value
    return groups


def compare(reference: Path, alternative: Path, output: Path, *, identifier: str,
            score: str, reference_generation: str, alternative_generation: str,
            stratum: str | None = None, max_rows: int = 1_000_000) -> dict:
    if output.exists() or not reference_generation or not alternative_generation:
        raise ValueError('Require a new output and both scenario/generation identities')
    inputs = {name: {'path': str(path.resolve(strict=True)), 'sha256': sha256(path),
                     'generation': generation} for name, path, generation in (
        ('reference', reference, reference_generation), ('alternative', alternative, alternative_generation))}
    left, right = (load_scores(path, identifier, score, stratum, max_rows) for path in (reference, alternative))
    result = {'schema_version': 1, 'inputs': inputs, 'identifier': identifier, 'score': score,
              'stratum': stratum, 'cohorts': {key: compare_scenarios(left.get(key, {}), right.get(key, {}))
                  for key in sorted(left.keys() | right.keys())},
              'interpretation': 'same-identifier finite cohorts; changes are not biological trends'}
    for item in inputs.values():
        if sha256(Path(item['path'])) != item['sha256']:
            raise ValueError('Comparison input changed during calculation')
    atomic_json(output, result)
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--reference', type=Path, required=True)
    p.add_argument('--alternative', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--identifier', required=True, help='iucn_sis_id or h3_index, as stored')
    p.add_argument('--score', default='priority')
    p.add_argument('--reference-generation', required=True)
    p.add_argument('--alternative-generation', required=True)
    p.add_argument('--stratum', help='Optional ecosystem/status column; no inferred strata')
    p.add_argument('--max-rows', type=int, default=1_000_000)
    args = p.parse_args(argv)
    compare(**vars(args))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
