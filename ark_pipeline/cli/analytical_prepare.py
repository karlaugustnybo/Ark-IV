"""Publish separate coarse/fine opportunity presets from retained memberships.

This is downstream-only: no geometry, acquisition, SQL load or base-map change.
Unscored species, including unsupported clades, remain explicit in every cell.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from ark_pipeline.aggregation.analytical_scores import aggregate_scores
from ark_pipeline.aggregation.species_lists import (
    RES3_FILENAME,
    _publish_current,
    signature,
    write_receipt,
)
from ark_pipeline.builders.phylogenetic import load_generation
from ark_pipeline.cli.spatial_analytics import checked_memberships
from ark_pipeline.dna_evidence import DnaRule
from ark_pipeline.runtime.progress import emit
from ark_pipeline.runtime.provenance import (
    code_fingerprint,
    dependency_identity,
    identity_digest,
    receipt_is_current,
    sha256,
)
from ark_pipeline.runtime.resources import positive_int


def build(args):
    rule = DnaRule(args.rule, min_busco=args.min_busco)
    weights = {category.upper(): getattr(args, category) for category in ('cr', 'en', 'vu', 'nt', 'dd', 'lc')}
    tree = args.phylogenetic_generation.resolve(strict=True)
    tree_hash = sha256(tree / 'receipt.json')
    emit('phase', phase='Rank phylogenetic scenario', force=True)
    engine, _ = load_generation(tree)
    scenario = engine.rank(rule, weights)
    memberships, membership_hash, fine = checked_memberships(args.h3_root, args.profile)
    coarse = memberships / RES3_FILENAME
    receipt_record = json.loads((memberships / 'receipt.json').read_text())
    if not any(item['filename'] == RES3_FILENAME for item in receipt_record['outputs'].values()):
        raise ValueError('Membership receipt lacks the distinct coarse list')
    identity = dict(schema_version=1, phylogenetic=tree_hash, memberships=membership_hash,
        scenario=scenario['weighted_scenario_id'], profile=args.profile, ecosystem='all',
        code=code_fingerprint([Path(__file__), Path(__file__).with_name('spatial_analytics.py'),
            Path(__file__).parents[1] / 'aggregation/analytical_scores.py',
            Path(__file__).parents[1] / 'aggregation/membership_contract.py']),
        dependencies=dependency_identity())
    if not args.profile:
        raise ValueError('Require the selected membership profile')
    target = args.output_root / 'generations' / identity_digest(identity)
    receipt = target / 'receipt.json'
    if receipt.is_file():
        saved = json.loads(receipt.read_text())
        outputs = {name: target / item['filename'] for name, item in saved['outputs'].items()}
        if receipt_is_current(receipt, identity, outputs, saved['totals']['schemas']):
            _publish_current(args.output_root, target)
            return dict(status='reused', generation=str(target), **saved['totals'])
    if (args.output_root / 'current').resolve() == target.resolve():
        raise ValueError('Published opportunity generation is corrupt; choose a new output root')
    target.mkdir(parents=True, exist_ok=True)
    species = target / 'species_priorities.parquet'
    pq.write_table(pa.Table.from_pylist(scenario['rows']), species.with_suffix('.tmp'), compression='zstd')
    species.with_suffix('.tmp').replace(species)
    outputs = {'species_priorities': species}
    aggregates = {}
    with duckdb.connect() as connection:
        connection.execute('SET memory_limit=?', [args.memory_limit])
        connection.execute('SET threads=?', [args.threads])
        args.scratch_dir.mkdir(parents=True, exist_ok=True)
        connection.execute('SET temp_directory=?', [str(args.scratch_dir.resolve())])
        for resolution, paths in ((3, [coarse]), (7, fine)):
            emit('phase', phase=f'Resolution-{resolution} opportunity aggregation', force=True)
            output = target / f'opportunities_res{resolution}.parquet'
            # Only an unpublished, interrupted generation may be resumed here.
            output.unlink(missing_ok=True)
            aggregates[str(resolution)] = aggregate_scores(connection, paths, species, output,
                resolution=resolution, scenario_id=scenario['weighted_scenario_id'],
                membership_generation=membership_hash, profile=args.profile)
            outputs[f'opportunities_res{resolution}'] = output
    if sha256(tree / 'receipt.json') != tree_hash or sha256(memberships / 'receipt.json') != membership_hash:
        raise ValueError('Pinned analytical inputs changed during aggregation')
    totals = dict(scenario_id=scenario['scenario_id'], weighted_scenario_id=scenario['weighted_scenario_id'],
        scope=engine.generation['scope'], rule=rule.identity(), weights=weights,
        represented_tips=scenario['represented_tips'], tree_tips=scenario['tree_tips'],
        unresolved_positive_records=scenario['unresolved_positive_records'],
        aggregates=aggregates, schemas={name: signature(pq.read_schema(path)) for name, path in outputs.items()},
        output_bytes=sum(path.stat().st_size for path in outputs.values()))
    write_receipt(receipt, identity, outputs, totals)
    _publish_current(args.output_root, target)
    return dict(status='built', generation=str(target), **totals)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phylogenetic-generation', type=Path, required=True)
    p.add_argument('--h3-root', type=Path, required=True)
    p.add_argument('--output-root', type=Path, required=True)
    p.add_argument('--profile', required=True)
    p.add_argument('--rule', choices=('website', 'any-assembly', 'taxon-quality-proxy'), default='website')
    p.add_argument('--min-busco', type=float, default=90)
    for category, weight in (('cr', 4), ('en', 3), ('vu', 2), ('nt', 1), ('dd', 2), ('lc', .1)):
        p.add_argument('--' + category, type=float, default=weight)
    p.add_argument('--memory-limit', default='256MB')
    p.add_argument('--threads', type=positive_int, default=1)
    p.add_argument('--scratch-dir', type=Path, required=True)
    return p


def main(argv=None):
    print(json.dumps(build(parser().parse_args(argv)), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
