"""Publish range/stewardship analyses from a pinned serving-list generation."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import duckdb
import pyarrow.parquet as pq

from ark_pipeline.aggregation.species_lists import _publish_current, signature, write_receipt
from ark_pipeline.builders.spatial_analytics import build_jurisdictions, build_spatial_analytics
from ark_pipeline.runtime.provenance import (
    code_fingerprint,
    dependency_identity,
    identity_digest,
    receipt_is_current,
    sha256,
)


def checked_memberships(path: Path, profile: str | None = None) -> tuple[Path, str, list[Path]]:
    """Pin a complete publication and reject unreceipted fine partitions."""
    source = path.resolve(strict=True)
    source_receipt = source / 'receipt.json'
    receipt_hash = sha256(source_receipt)
    saved = json.loads(source_receipt.read_text())
    relation = saved.get('identity', {}).get('relations', {})
    stages = relation.get('archive_stages', {})
    profile_ids = {stage.get('semantic_profile', {}).get('id') for stage in stages.values()}
    if relation.get('semantic_profile', {}).get('id'):
        profile_ids.add(relation['semantic_profile']['id'])
    profile_ids.discard(None)
    if profile is not None and profile_ids and profile_ids != {profile}:
        raise ValueError('Selected profile differs from the pinned memberships')
    inputs = {name: source / entry['filename'] for name, entry in saved['outputs'].items()}
    # Verify the complete membership publication, including retained support.
    for name, path in inputs.items():
        if not path.resolve().is_relative_to(source) or not path.is_file() or sha256(path) != saved['outputs'][name]['sha256']:
            raise ValueError('Membership generation is incomplete or corrupt')
    if saved.get('status') != 'passed':
        raise ValueError('Membership generation has not passed validation')
    fine = sorted((source / 'res7_merged_parts').glob('*.parquet'))
    recorded_fine = {p.resolve() for p in inputs.values() if p.parent == source / 'res7_merged_parts'}
    if not fine or {p.resolve() for p in fine} != recorded_fine:
        raise ValueError('Membership generation contains missing or unreceipted fine partitions')
    if sha256(source_receipt) != receipt_hash:
        raise ValueError('Membership receipt changed during validation')
    return source, receipt_hash, fine


def build(args):
    source, membership_hash, fine = checked_memberships(args.h3_root, args.profile)
    catalogues = getattr(args, 'boundary_catalogue', None) or []
    boundaries = dict(catalogues)
    if len(boundaries) != len(catalogues):
        raise ValueError('Duplicate boundary framework')
    if boundaries and args.jurisdictions:
        raise ValueError('Choose boundary catalogues or prepared jurisdiction memberships')
    optional = {key: {'path': str(path.resolve()), 'sha256': sha256(path)}
                for key in ('jurisdictions', 'weights', 'omissions') if (path := getattr(args, key, None)) is not None}
    optional.update({f'boundary:{key}': {'path': str(path.resolve()), 'sha256': sha256(path)}
                     for key, path in boundaries.items()})
    identity = {'schema_version': 1, 'memberships': membership_hash,
        'profile': args.profile, 'coverage_status': args.coverage_status, 'optional': optional,
        'dependencies': dependency_identity(),
        'code': code_fingerprint([Path(__file__), Path(__file__).parents[1] / 'builders/spatial_analytics.py',
                                  Path(__file__).parents[1] / 'aggregation/membership_contract.py',
                                  Path(__file__).parents[1] / 'tile_spatial.py',
                                  Path(__file__).parents[1] / 'tiles.py',
                                  Path(__file__).parents[1] / 'spatial/boundaries.py',
                                  Path(__file__).parents[1] / 'builders/coarse_cache.py'])}
    target = args.output_root / 'generations' / identity_digest(identity)
    receipt = target / 'receipt.json'
    if receipt.is_file():
        current = json.loads(receipt.read_text())
        outputs = {name: target / item['filename'] for name, item in current['outputs'].items()}
        if receipt_is_current(receipt, identity, outputs, current['totals']['schemas']):
            _publish_current(args.output_root, target)
            return {'status': 'reused', 'generation': str(target)}
    if target.exists():
        # Keep prior interrupted outputs intact; builders stage into a fresh child.
        import shutil
        if (args.output_root / 'current').resolve() == target.resolve():
            raise ValueError('Published analytical generation is corrupt; choose a new output root')
        scratch = target / 'work'
        if scratch.exists():
            shutil.rmtree(scratch)
    target.mkdir(parents=True, exist_ok=True)
    with duckdb.connect() as connection:
        connection.execute('SET memory_limit=?', [args.memory_limit])
        connection.execute('SET threads=?', [args.threads])
        args.scratch_dir.mkdir(parents=True, exist_ok=True)
        connection.execute('SET temp_directory=?', [str(args.scratch_dir.resolve())])
        jurisdictions = args.jurisdictions
        boundary_result = {}
        if boundaries:
            jurisdictions = target / 'jurisdictions.parquet'
            boundary_result = build_jurisdictions(connection, fine, boundaries, jurisdictions)
        result = build_spatial_analytics(connection, fine,
            target / 'work', profile=args.profile, membership_generation=identity['memberships'],
            coverage_status=args.coverage_status, jurisdictions=jurisdictions, weights=args.weights,
            omissions=getattr(args, 'omissions', None))
    result.update(boundary_result)
    outputs = {p.stem: p for p in (target / 'work').glob('*.parquet')}
    if boundaries:
        outputs['jurisdictions'] = jurisdictions
    for key, path in optional.items():
        if sha256(Path(path['path'])) != path['sha256']:
            raise ValueError(f'Analytical source changed: {key}')
    if sha256(source / 'receipt.json') != identity['memberships']:
        raise ValueError('Membership receipt changed during analysis')
    result['schemas'] = {name: signature(pq.read_schema(path)) for name, path in outputs.items()}
    write_receipt(receipt, identity, outputs, result)
    _publish_current(args.output_root, target)
    return {'status': 'built', 'generation': str(target), **result}


def boundary_catalogue(value: str) -> tuple[str, Path]:
    name, separator, path = value.partition('=')
    if not separator or not re.fullmatch(r'[a-z][a-z0-9_-]*', name) or not path:
        raise argparse.ArgumentTypeError('Expected framework=path')
    return name, Path(path)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--h3-root', type=Path, required=True)
    p.add_argument('--output-root', type=Path, required=True)
    p.add_argument('--profile', required=True)
    p.add_argument('--coverage-status', choices=('complete', 'partial', 'unknown'), default='unknown')
    p.add_argument('--jurisdictions', type=Path)
    p.add_argument('--boundary-catalogue', action='append', type=boundary_catalogue, help='Repeat framework=GeoJSON to build stewardship memberships from mapped cells')
    p.add_argument('--weights', type=Path)
    p.add_argument('--omissions', type=Path, help='Pinned missing_basin_relationships.parquet audit')
    p.add_argument('--memory-limit', default='1GB')
    p.add_argument('--threads', type=int, default=1)
    p.add_argument('--scratch-dir', type=Path, required=True)
    print(json.dumps(build(p.parse_args(argv)), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
