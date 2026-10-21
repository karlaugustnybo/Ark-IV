"""Publish checked clade evidence and descriptive scores as private generations."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from ark_pipeline.aggregation.species_lists import _publish_current, signature, write_receipt
from ark_pipeline.dna_evidence import DnaRule, evidence_table
from ark_pipeline.edge import score_clade
from ark_pipeline.phylogenetic_scenarios import CompactTree, ScenarioEngine
from ark_pipeline.runtime.provenance import (
    code_fingerprint,
    dependency_identity,
    identity_digest,
    receipt_is_current,
    sha256,
)


def calculation_code_identity() -> str:
    return code_fingerprint([Path(__file__), *(Path(__file__).parents[1] / name for name in (
        'dna_evidence.py', 'phylogenetic_scenarios.py', 'compact_phylogeny.py', 'edge.py', 'extinction_status.py'))])


def build_generation(tree, evidence: list[dict], species: list[dict], output: Path, *,
                     sources: dict[str, dict], scope: str, root: str,
                     risk_samples: int = 128, seed: int = 42, include_edge: bool = True) -> dict:
    """All sources are content-pinned; no acquisition, spatial work or SQL load.

    The complete crown is retained for gaps. EDGE uses the existing independent
    extinction policy and risk-only uncertainty implementation, on a tree copy.
    """
    if not sources or any(not entry.get('sha256') or not entry.get('path') for entry in sources.values()):
        raise ValueError('Require content-pinned sources for phylogenetic enrichment')

    def validate_sources():
        for name, entry in sources.items():
            if sha256(Path(entry['path'])) != entry['sha256']:
                raise ValueError(f'Phylogenetic source changed: {name}')

    validate_sources()
    if not species:
        raise ValueError('No assessed species in the selected clade scope')
    compact = tree if isinstance(tree, CompactTree) else CompactTree.from_tree(tree)
    engine_origin = None
    if include_edge:
        from importlib.metadata import distribution
        package = distribution('dated-complete-tree')
        engine_origin = {'version': package.version,
                         'origin': json.loads(package.read_text('direct_url.json') or '{}')}
    identity = {'schema_version': 1, 'sources': sources, 'scope': scope, 'root': root,
                'stem': 'excluded', 'units': 'Myr', 'risk_samples': risk_samples,
                'seed': seed, 'include_edge': include_edge,
                'ed_policy': 'extant-edge-crown' if include_edge else 'complete-gap-crown',
                'method': 'compact-complete-tree-risk-only-v1' if isinstance(tree, CompactTree) else 'existing-edge-risk-only-v1', 'default_rule': DnaRule().identity(),
                'code': calculation_code_identity(), 'dependencies': dependency_identity(),
                'numpy': np.__version__, 'engine': engine_origin,
                'prepared_evidence': identity_digest({'rows': evidence}),
                'prepared_species': identity_digest({'rows': species})}
    generation = output / 'generations' / identity_digest(identity)
    receipt = generation / 'receipt.json'
    if receipt.is_file():
        saved = json.loads(receipt.read_text())
        outputs = {name: generation / item['filename'] for name, item in saved['outputs'].items()}
        if receipt_is_current(receipt, identity, outputs, saved['totals']['schemas']):
            validate_sources()
            _publish_current(output, generation)
            return {'status': 'reused', 'generation': str(generation), **saved['totals']}
    if (output / 'current').resolve() == generation.resolve():
        raise ValueError('Published phylogenetic generation is corrupt; use a new output root')
    generation.mkdir(parents=True, exist_ok=True)
    generation_identity = dict(tree=identity_digest({'sources': sources, 'root': root}), root=root,
        scope=scope, stem='excluded', units='Myr',
        evidence=identity['prepared_evidence'], identities=identity['prepared_species'])
    engine = ScenarioEngine(compact, evidence, species, generation_identity)
    # Reuse existing arithmetic implementation and keep its risk uncertainty
    # distinct from tree/date uncertainty and official EDGE-list membership.
    edge_rows, diagnostics = ([], {})
    if include_edge:
        if isinstance(tree, CompactTree):
            from ark_pipeline.compact_phylogeny import score_complete_tree

            edge_rows, diagnostics = score_complete_tree(tree, species, risk_samples, seed)
        else:
            joined = [{**row, 'join_status': 'joined' if row.get('tree_identity_verified') is True else 'unresolved'} for row in species]
            edge_rows, diagnostics = score_clade(tree.copy(), joined, risk_samples, seed, stable_risk_order=True)
    by_tip = {row['tree_tip']: row for row in edge_rows}
    ed = dict(zip(compact.tip_labels, compact.fair_proportion()))
    scores = []
    for row in species:
        verified = row.get('tree_identity_verified') is True and row.get('tree_tip') in ed
        score = by_tip.get(row.get('tree_tip'), {}) if verified else {}
        scores.append({'iucn_sis_id': str(row['iucn_sis_id']),
            'tree_tip': row.get('tree_tip'), 'ed_myr': score.get('ark_ed_myr') if include_edge else float(ed[row['tree_tip']]) if verified else None,
            'edge1': score.get('ark_edge1'), 'edge2_myr_point': score.get('ark_edge2_myr_point'),
            'edge2_myr_median': score.get('ark_edge2_myr_median'),
            'edge2_myr_p025': score.get('ark_edge2_myr_p025'), 'edge2_myr_p975': score.get('ark_edge2_myr_p975'),
            'score_status': 'descriptive_risk_only_uncertainty' if score else 'excluded_extinct_or_unavailable_edge' if verified and include_edge else 'ed_only' if verified else 'unavailable_tree_identity'})
    labels = dict(zip(compact.tip_nodes, compact.tip_labels))
    default_rows = engine.calculate(DnaRule(), nearest=True)['rows']
    gap_by_id = {row['iucn_sis_id']: row for row in default_rows}
    tables = {
        'tree': pa.table({'parent': compact.parent, 'length': compact.length,
                          'tip_label': pa.array([labels.get(i) for i in range(len(compact.parent))], type=pa.string())}),
        'evidence': evidence_table(evidence) if evidence else pa.table({
            'taxon_id': pa.array([], type=pa.string()), 'tree_tip': pa.array([], type=pa.string()),
            'genomic_transfer_safe': pa.array([], type=pa.bool_())}),
        'species': evidence_table(species),
        'scores': pa.Table.from_pylist(scores),
        'default_gaps': pa.Table.from_pylist(default_rows),
    }
    tables['species_phylogenetic'] = pa.Table.from_pylist([
        {**row, **gap_by_id[row['iucn_sis_id']], 'generation_id': generation.name}
        for row in scores])
    outputs = {}
    for name, table in tables.items():
        target = generation / f'{name}.parquet'
        temporary = target.with_suffix('.tmp')
        pq.write_table(table, temporary, compression='zstd')
        temporary.replace(target)
        outputs[name] = target
    totals = {'schemas': {name: signature(table.schema) for name, table in tables.items()},
              'generation_identity': generation_identity, 'tree_tips': len(compact.tip_labels),
              'species': len(species), 'baseline_records': len(evidence), 'edge_diagnostics': diagnostics,
              'output_bytes': sum(p.stat().st_size for p in outputs.values())}
    validate_sources()
    write_receipt(receipt, identity, outputs, totals)
    _publish_current(output, generation)
    return {'status': 'built', 'generation': str(generation), **totals}


def load_generation(path: Path) -> tuple[ScenarioEngine, dict[str, dict]]:
    """Resolve current once; validate every artifact before admitting scenarios."""
    path = path.resolve(strict=True)
    receipt = path / 'receipt.json'
    saved = json.loads(receipt.read_text())
    if saved.get('identity', {}).get('code') != calculation_code_identity():
        raise ValueError('Stale phylogenetic calculation code; rebuild enrichment')
    outputs = {name: (path / item['filename']).resolve(strict=True) for name, item in saved['outputs'].items()}
    if any(not output.is_relative_to(path) for output in outputs.values()):
        raise ValueError('Invalid phylogenetic generation output path')
    if not receipt_is_current(receipt, saved['identity'], outputs, saved['totals']['schemas']):
        raise ValueError('Stale/corrupt phylogenetic generation')
    nodes = pq.read_table(outputs['tree']).to_pydict()
    tips = [i for i, label in enumerate(nodes['tip_label']) if label is not None]
    tree = CompactTree(np.array(nodes['parent']), np.array(nodes['length']), np.array(tips),
                       tuple(nodes['tip_label'][i] for i in tips))
    engine = ScenarioEngine(tree, pq.read_table(outputs['evidence']).to_pylist(),
        pq.read_table(outputs['species']).to_pylist(), saved['totals']['generation_identity'])
    scores = {row['iucn_sis_id']: row for row in pq.read_table(outputs['scores']).to_pylist()}
    return engine, scores
