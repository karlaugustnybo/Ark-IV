"""Reproducible synthetic robustness matrix through production list/reducers.

This bounded scientific rehearsal compares descriptive quantities; it does not
select a default score or substitute synthetic outcomes for biological evidence.
"""

from __future__ import annotations

import argparse
import math
from collections import Counter, defaultdict
from pathlib import Path

import duckdb
import h3
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from ark_pipeline.aggregation.analytical_scores import aggregate_scores
from ark_pipeline.aggregation.species_lists import PAIR_SCHEMA, export_serving_lists, sql_path
from ark_pipeline.builders.coarse_cache import DEFAULT_WEIGHTS, DNA_SCORE_PREDICATES
from ark_pipeline.builders.spatial_analytics import build_spatial_analytics
from ark_pipeline.cli.scoring_compare import compare
from ark_pipeline.dna_evidence import DnaRule, evaluate_evidence, lineage_representation
from ark_pipeline.phylogenetic_scenarios import CompactTree, ScenarioEngine
from ark_pipeline.runtime.provenance import atomic_json, identity_digest, sha256


def fixture():
    parent, length, tips, labels = [], [], [], []

    def branch(start, count, up=-1, depth=0):
        node = len(parent)
        parent.append(up)
        length.append(0. if up < 0 else 1. + (start % 11) / (depth + 1))
        if count == 1:
            tips.append(node)
            labels.append(f'tip_{start:03d}')
        else:
            branch(start, count // 2, node, depth + 1)
            branch(start + count // 2, count // 2, node, depth + 1)

    branch(0, 128)
    tree = CompactTree(np.array(parent), np.array(length), np.array(tips), tuple(labels))
    evidence, species = [], []
    categories = ['CR', 'EN', 'VU', 'NT', 'DD', 'LC']
    for index, label in enumerate(labels):
        row = dict(taxon_id=str(index), tree_tip=label, genomic_transfer_safe=True,
                   tree_identity_verified=True, kingdom_taxid=f'k{index // 32}',
                   family_taxid=f'f{index // 16}', genus_taxid=f'g{index // 4}')
        # All six fields are present, including explicit unknowns; field order
        # cannot cause sparse-column inference to drop a later witness.
        row.update(sample_acquired=None, in_progress=None, ebp_standard_criteria=None,
                   assembly_level=None, busco_completeness=None, resampling_required=None)
        mode = index % 8
        if mode == 0:
            row['sample_acquired'] = 'voucher'
        elif mode == 1:
            row['assembly_level'] = ''
        elif mode == 2:
            row['assembly_level'] = 'Contig'
        elif mode in (3, 4):
            row.update(assembly_level='Chromosome', busco_completeness='96' if mode == 3 else '85')
        elif mode == 6:
            row.update(assembly_level='Scaffold', resampling_required='yes')
        elif mode == 7:
            row['in_progress'] = 'project'
        evidence.append(row)
        if index < 96:
            species.append({**row, 'iucn_sis_id': str(index + 1),
                            'redlist_category': categories[index % len(categories)],
                            'ecosystem': ('Terrestrial', 'Freshwater', 'Marine')[index % 3]})
    # Preserve unresolved positive relatives and their rule-dependent withholding.
    evidence.append(dict(taxon_id='unresolved', genomic_transfer_safe=False,
                         genus_taxid='g1', sample_acquired='voucher'))
    return tree, evidence, species


def run(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    tree, evidence, species = fixture()
    identity = dict(tree='synthetic-complete-128', root='synthetic-global-root',
                    scope='synthetic-four-clades', stem='excluded', units='Myr',
                    evidence=identity_digest({'rows': evidence}), identities=identity_digest({'rows': species}))
    engine = ScenarioEngine(tree, evidence, species, identity)
    weights = {key.upper(): DEFAULT_WEIGHTS[key] for key in ('cr', 'en', 'vu', 'nt', 'dd', 'lc')}
    scores = {}
    for name, rule, selected in [('website-gap', DnaRule(), weights),
                                 ('assembly-gap', DnaRule('any-assembly'), weights),
                                 ('quality-gap', DnaRule('taxon-quality-proxy'), weights),
                                 ('flat-threat-gap', DnaRule(), dict.fromkeys(weights, 1.))]:
        result = engine.rank(rule, selected)
        by_id = {row['iucn_sis_id']: row for row in species}
        rows = [{**row, 'ecosystem': by_id[row['iucn_sis_id']]['ecosystem'],
                 'status': by_id[row['iucn_sis_id']]['redlist_category']} for row in result['rows']]
        scores[name] = output / f'{name}.parquet'
        pq.write_table(pa.Table.from_pylist(rows), scores[name])
    metadata = []
    for row in lineage_representation(evidence, species, DnaRule()):
        state = evaluate_evidence(row, DnaRule())
        metadata.append(dict(iucn_sis_id=row['iucn_sis_id'], ecosystem=row['ecosystem'], status=row['redlist_category'],
            threat=weights[row['redlist_category']], has_dna_species_level=state.qualifies,
            genus_has_dna=row['genus_represented'], family_has_dna=row['family_represented'],
            goat_data_deficient=state.qualifies is None))
    with duckdb.connect() as c:
        c.execute("SET threads=1; SET memory_limit='256MB'")
        c.register('fixture_species', pa.Table.from_pylist(metadata))
        cases = ' '.join(f'WHEN {predicate} THEN {DEFAULT_WEIGHTS[name]}' for name, predicate in DNA_SCORE_PREDICATES.items())
        scores['categorical'] = output / 'categorical.parquet'
        c.execute(f'''COPY (SELECT iucn_sis_id, threat*(CASE {cases} ELSE 0 END)::DOUBLE AS priority,
            false AS baseline_incomplete, ecosystem, status FROM fixture_species ORDER BY iucn_sis_id)
            TO ? (FORMAT PARQUET)''', [str(scores['categorical'])])
    # Three disjoint geographic patches and deterministic overlapping ranges,
    # including repeated raw pairs and multi-route support.
    all_cells = sorted({int(cell, 16) for lat, lon in ((55, 12), (0, 36), (-30, 150))
                        for cell in h3.grid_disk(h3.latlng_to_cell(lat, lon, 7), 10)})
    routes = ('polygon', 'point', 'hydrobasin')
    masks = defaultdict(int)
    for index, cell in enumerate(all_cells):
        for species_id in range(1, 97):
            if (index * 17 + species_id * 23) % (7 + species_id % 19) < 3:
                masks[cell, species_id] |= 1 << ((index + species_id) % 3)
                if (index + species_id) % 7 == 0:
                    masks[cell, species_id] |= 1 << ((index + species_id + 1) % 3)
    inputs = []
    for bit, route in enumerate(routes):
        pairs = [dict(h3_index=cell, iucn_sis_id=sis) for (cell, sis), mask in masks.items() if mask & (1 << bit)]
        path = output / f'{route}.parquet'
        pq.write_table(pa.Table.from_pylist(pairs + pairs[:20], schema=PAIR_SCHEMA), path)
        inputs.append(dict(logical_name=route, source_format=route, path=path, bytes=path.stat().st_size,
                           sha256=sha256(path), rows=len(pairs) + min(20, len(pairs))))
    published = export_serving_lists(output / 'memberships', scratch_dir=output / 'scratch', memory_limit='256MB',
        threads=1, archive_inputs=inputs, archive_identity={'fixture': 'robustness-128-v1'})
    current = Path(published['root'])
    actual_support = {(row['h3_index'], row['iucn_sis_id']): row['source_support']
        for path in sorted((current / 'source_support').glob('base_*.parquet'))
        for row in pq.read_table(path).to_pylist()}
    assert actual_support == masks
    expected_coarse = defaultdict(int)
    for (cell, sis), mask in masks.items():
        expected_coarse[int(h3.cell_to_parent(format(cell, 'x'), 3), 16), sis] |= mask
    actual_coarse = {(row['h3_index'], row['iucn_sis_id']): row['source_support']
        for path in sorted((current / 'source_support').glob('res3_base_*.parquet'))
        for row in pq.read_table(path).to_pylist()}
    assert actual_coarse == expected_coarse
    fine = sorted((current / 'res7_merged_parts').glob('*.parquet'))
    coarse = [current / 'h3_res3_species_global_merged.parquet']
    oracle = defaultdict(set)
    for cell, sis in masks:
        oracle[cell].add(str(sis))
    reports, artifacts = {}, {}
    with duckdb.connect() as c:
        c.execute("SET threads=1; SET memory_limit='256MB'")
        for name, path in scores.items():
            values = {row['iucn_sis_id']: row['priority'] for row in pq.read_table(path).to_pylist()}
            for resolution, memberships in ((7, fine), (3, coarse)):
                target = output / f'{name}-res{resolution}.parquet'
                aggregate_scores(c, memberships, path, target, resolution=resolution,
                    scenario_id=name, membership_generation=current.name, profile='synthetic-fast')
                grouped = oracle if resolution == 7 else defaultdict(set)
                if resolution == 3:
                    for cell, ids in oracle.items():
                        grouped[int(h3.cell_to_parent(format(cell, 'x'), 3), 16)].update(ids)
                for row in pq.read_table(target).to_pylist():
                    selected = [values[sis] for sis in grouped[row['h3_index']] if values.get(sis) is not None]
                    expected = math.fsum(selected) if selected else None
                    if expected is None:
                        assert row['priority'] is None
                    else:
                        assert math.isclose(row['priority'], expected, rel_tol=1e-12, abs_tol=1e-12)
                    assert row['scored_species'] == len(selected)
                artifacts[name, resolution] = target
        threat = output / 'range-weights.parquet'
        pq.write_table(pa.table({'iucn_sis_id': list(range(1, 97)), 'weight': [weights[row['redlist_category']] for row in species]}), threat)
        build_spatial_analytics(c, fine, output / 'rarity', profile='synthetic-fast',
            membership_generation=current.name, coverage_status='complete', weights=threat)
        rarity = pq.read_table(output / 'rarity/range_rarity_res7.parquet').to_pylist()
        mapped_ids = set().union(*oracle.values())
        mapped_weight = sum(weights[row['redlist_category']] for row in species if row['iucn_sis_id'] in mapped_ids)
        assert math.isclose(math.fsum(row['range_rarity'] for row in rarity), mapped_weight, rel_tol=1e-12)
        for resolution in (3, 7):
            path = output / f'rarity-res{resolution}.parquet'
            source = output / f'rarity/range_rarity_res{resolution}.parquet'
            c.execute(f'COPY (SELECT h3_index,range_rarity AS priority FROM read_parquet({sql_path(source)})) TO {sql_path(path)} (FORMAT PARQUET)')
            artifacts['range-rarity', resolution] = path
        website = {r['iucn_sis_id']: r for r in pq.read_table(scores['website-gap']).to_pylist()}
        for bit, route in enumerate(routes):
            grouped = defaultdict(set)
            for (cell, sis), mask in masks.items():
                if mask & (1 << bit):
                    grouped[cell].add(str(sis))
            membership = output / f'{route}-lists.parquet'
            pq.write_table(pa.table({'h3_cell': pa.array(sorted(grouped), type=pa.uint64()),
                'species_ids': [[int(s) for s in sorted(grouped[cell], key=int)] for cell in sorted(grouped)]}), membership)
            target = output / f'{route}-only-res7.parquet'
            aggregate_scores(c, [membership], scores['website-gap'], target, resolution=7,
                scenario_id='website-gap', membership_generation=f'{current.name}:{route}-only', profile='synthetic-fast')
            for row in pq.read_table(target).to_pylist():
                selected = [website[sis]['priority'] for sis in grouped[row['h3_index']] if website[sis]['priority'] is not None]
                expected = math.fsum(selected) if selected else None
                assert row['priority'] is None if expected is None else math.isclose(row['priority'], expected, rel_tol=1e-12)
            artifacts[f'{route}-only', 7] = target
    for name, path in scores.items():
        if name != 'categorical':
            for stratum in ('ecosystem', 'status'):
                reports[f'species:categorical:{name}:{stratum}'] = compare(scores['categorical'], path,
                    output / f'species-{name}-{stratum}.json', identifier='iucn_sis_id', score='priority',
                    reference_generation='categorical', alternative_generation=name, stratum=stratum)['cohorts']
    for (name, resolution), path in artifacts.items():
        reference_name = 'website-gap' if name.endswith('-only') else 'categorical'
        if name == reference_name:
            continue
        reports[f'cells:res{resolution}:{reference_name}:{name}'] = compare(artifacts[reference_name, resolution], path,
            output / f'cells-{name}-res{resolution}.json', identifier='h3_index', score='priority',
            reference_generation=reference_name, alternative_generation=name)['cohorts']
    result = {'status': 'passed', 'scope': 'synthetic representative arithmetic and robustness rehearsal',
        'species': len(species), 'tree_tips': len(tree.tip_labels), 'evidence_records': len(evidence),
        'fine_cells': len(oracle), 'memberships': len(masks), 'mapped_species': len(mapped_ids),
        'unmapped_assessed_species': len(species) - len(mapped_ids), 'source_masks': dict(Counter(masks.values())),
        'scenarios': list(scores) + ['range-rarity', *[route + '-only' for route in routes]],
        'checks': ['production three-route OR/deduplication', 'independent distinct-cell score oracle',
                   'distinct coarse oracle', 'global range-rarity weight conservation',
                   'rule and weight comparisons', 'source-route subsets', 'status/ecosystem cohorts'],
        'comparisons': reports, 'scientific_acceptance': 'descriptive comparisons; no default adopted'}
    atomic_json(output / 'report.json', result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    run(args.output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
