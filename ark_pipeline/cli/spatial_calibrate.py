"""Compare same-policy simplified/unsimplified coverage on an explicit sample."""

from __future__ import annotations

import argparse
import time
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import shapely

from ark_pipeline.runtime.provenance import atomic_json, runtime_identity, sha256
from ark_pipeline.spatial.coverage import (
    GeometryCoverageError,
    exact_intersecting_cells_native,
    load_spatial_profile,
    polygonal_parts,
    prepare_geometry,
)


def repair_effects(geometry, profile) -> dict:
    """Compare two repairs with simplification disabled, without adopting either.

    Invalid geometry has no authoritative original cell set. The configured
    make_valid result is compared with an explicitly labelled buffer(0)
    sensitivity alternative, never presented as a correctness reference.
    The production area-change/zero-area guards remain in force.
    """
    reference = replace(profile, decision_simplification_degrees=0.)
    if reference.coverage_mode != 'fast':
        raise ValueError('Repair sensitivity is FAST-only; correct coverage remains separately gated')
    try:
        prepared, audit = prepare_geometry(geometry, reference)
    except GeometryCoverageError as error:
        return {'status': 'production-rejected', 'reason': str(error),
                'original_valid': bool(shapely.is_valid(geometry)), 'scientific_acceptance': 'not_established'}
    alternative = polygonal_parts(geometry.buffer(0))
    if alternative is None or alternative.is_empty or not alternative.is_valid:
        return {'status': 'alternative-unavailable', 'production_repair': asdict(audit)}
    left = set(map(int, exact_intersecting_cells_native(prepared, reference).cells))
    right = set(map(int, exact_intersecting_cells_native(alternative, reference).cells))
    return {'status': 'compared', 'production_repair': asdict(audit),
            'alternative': 'GEOS buffer(0), sensitivity only; not an authoritative original',
            'production_cells': len(left), 'alternative_cells': len(right),
            'alternative_added_cells': len(right - left), 'alternative_omitted_cells': len(left - right),
            'alternative_planar_area': float(alternative.area),
            'jaccard': len(left & right) / len(left | right) if left | right else None,
            'simplification_degrees': 0., 'scientific_acceptance': 'not_established'}


def calibrate(sample: Path, profile_path: Path, output: Path, *, allow_correct: bool = False) -> dict:
    profile = load_spatial_profile(profile_path)
    if profile.coverage_mode != 'fast' and not allow_correct:
        raise ValueError('Correct-path calibration requires separate explicit authorization and --allow-correct')
    reference = replace(profile, decision_simplification_degrees=0.)
    output.mkdir(parents=True, exist_ok=False)
    # Union per species before comparison: repeated source rows are one membership.
    cells = ({}, {})
    timings = [0., 0.]
    repairs = []
    for batch in pq.ParquetFile(sample).iter_batches(batch_size=16, columns=['iucn_sis_id', 'geometry_wkb']):
        for row in batch.to_pylist():
            geometry = shapely.from_wkb(row['geometry_wkb'])
            sis = str(row['iucn_sis_id'])
            for index, selected in enumerate((profile, reference)):
                started = time.perf_counter()
                result = exact_intersecting_cells_native(geometry, selected)
                timings[index] += time.perf_counter() - started
                cells[index].setdefault(sis, set()).update(map(int, result.cells))
                if index == 1:
                    repairs.append({'iucn_sis_id': sis, 'original_valid': bool(shapely.is_valid(geometry)),
                                    'repair': asdict(result.repair)})
    species = []
    for sis in sorted(cells[0]):
        selected, baseline = cells[0][sis], cells[1][sis]
        species.append({'iucn_sis_id': sis, 'selected_cells': len(selected), 'reference_cells': len(baseline),
                        'added_cells': len(selected - baseline), 'omitted_cells': len(baseline - selected),
                        'jaccard': len(selected & baseline) / len(selected | baseline) if selected | baseline else None})
    # Cell membership is sufficient for downstream common-cohort hotspot analysis;
    # it is kept in private artifacts rather than folded into base map fields.
    import pyarrow as pa
    for name, memberships in zip(('selected', 'reference'), cells):
        rows = [{'iucn_sis_id': sis, 'h3_index': cell} for sis in sorted(memberships) for cell in sorted(memberships[sis])]
        pq.write_table(pa.Table.from_pylist(rows, schema=pa.schema([('iucn_sis_id', pa.string()), ('h3_index', pa.uint64())])), output / f'{name}.parquet')
    overlaps = [r['jaccard'] for r in species if r['jaccard'] is not None]
    report = {'sample_sha256': sha256(sample), 'profile_sha256': profile.digest,
              'coverage_mode': profile.coverage_mode, 'reference': 'same-policy-unsimplified',
              'selected_seconds': timings[0], 'reference_seconds': timings[1],
              'species': species, 'repairs': repairs, 'runtime': runtime_identity(Path(__file__).parents[2]),
              'scientific_acceptance': 'not_established',
              'mean_jaccard': float(np.mean(overlaps)) if overlaps else None}
    atomic_json(output / 'calibration.json', report)
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sample', type=Path, required=True)
    p.add_argument('--profile', type=Path, default=Path('config/spatial_semantics_iucn_richness_v3_fast.toml'))
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--allow-correct', action='store_true')
    args = p.parse_args(argv)
    calibrate(args.sample, args.profile, args.output, allow_correct=args.allow_correct)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
