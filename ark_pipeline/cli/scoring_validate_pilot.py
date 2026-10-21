"""Compare the compact kernel with an explicit receipt-verified mammal pilot."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from ark_pipeline.cli.phylogenetic_prepare import normalize_inputs
from ark_pipeline.dna_evidence import DnaRule, evaluate_evidence
from ark_pipeline.edge import load_clade
from ark_pipeline.phylogenetic_scenarios import CompactTree, ScenarioEngine
from ark_pipeline.runtime.provenance import atomic_json, sha256


def validate(directory: Path, output: Path) -> dict:
    report = json.loads((directory / 'report.json').read_text())
    names = ('goat_mammal_tree_audit.parquet', 'species_gap_pilot.parquet', 'complete_crown_gaps.parquet')
    for name in names:
        if sha256(directory / name) != report['outputs'][name]['sha256']:
            raise ValueError(f'Pilot output checksum changed: {name}')
    tree_path = directory / 'equal_splits_median_tree.tre'
    if sha256(tree_path) != report['sources']['dated-tree']['sha256']:
        raise ValueError('Pilot tree checksum changed')
    tree = CompactTree.from_tree(load_clade(tree_path, report['clade']))
    original = pd.read_parquet(directory / 'species_gap_pilot.parquet')
    assessed, evidence = normalize_inputs(original, pd.read_parquet(directory / 'goat_mammal_tree_audit.parquet'))
    rule = DnaRule()
    represented = {r['tree_tip'] for r in evidence if r['genomic_transfer_safe'] and evaluate_evidence(r, rule).qualifies is True}
    engine = ScenarioEngine(tree, evidence, assessed,
        dict(tree=sha256(tree_path), root=report['clade'], scope='Mammalia', units='Myr', stem='excluded',
             evidence=report['outputs'][names[0]]['sha256'], identities=report['outputs'][names[1]]['sha256']))
    gaps = dict(zip(tree.tip_labels, map(float, tree.gaps(represented))))
    nearest = dict(zip(tree.tip_labels, tree.nearest(represented)))
    expected_gaps = pd.read_parquet(directory / 'complete_crown_gaps.parquet')
    if set(gaps) != set(expected_gaps.tree_tip):
        raise ValueError('Pilot and compact kernel retain different complete trees')
    maximum = 0.
    for row in expected_gaps.itertuples():
        maximum = max(maximum, abs(gaps[row.tree_tip] - row.genomic_gap_myr))
        if not math.isclose(gaps[row.tree_tip], row.genomic_gap_myr, abs_tol=1e-10, rel_tol=1e-10):
            raise ValueError('Compact/pilot marginal gap mismatch')
        actual = nearest[row.tree_tip]
        if actual is None or actual[1] != row.nearest_dna_tip or not math.isclose(actual[0], row.nearest_dna_distance_myr, abs_tol=1e-10, rel_tol=1e-10):
            raise ValueError('Compact/pilot nearest-relative mismatch')
    result = engine.rank(rule, {k.upper(): v for k,v in report['weights'].items() if k in ('cr','en','vu','nt','dd','lc')})
    actual = {r['iucn_sis_id']: r for r in result['rows']}
    priorities = 0
    mismatches = []
    for row in original.itertuples():
        score = actual[str(row.iucn_sis_id)]['priority']
        expected = row.ark_gap_priority
        if pd.isna(expected):
            if score is not None:
                mismatches.append(str(row.iucn_sis_id))
        else:
            priorities += 1
            if score is None or not np.isclose(score, expected, atol=1e-10, rtol=1e-10):
                mismatches.append(str(row.iucn_sis_id))
    summary = {'status': 'passed' if not mismatches else 'failed', 'source_report_sha256': sha256(directory / 'report.json'),
        'tree_tips': len(gaps), 'assessed_species': len(assessed), 'baseline_evidence_records': len(evidence),
        'represented_tips': len(represented), 'expected_represented_tips': report['website_dna_baseline_tips'],
        'priorities_compared': priorities, 'priority_mismatches': len(mismatches),
        'maximum_gap_absolute_error_myr': maximum, 'unresolved_positive_records': result['unresolved_positive_records'],
        'scenario_id': result['scenario_id'], 'scientific_acceptance': 'not_established_by_arithmetic_parity'}
    if len(represented) != report['website_dna_baseline_tips']:
        summary['status'] = 'failed'
    atomic_json(output, summary)
    if summary['status'] != 'passed':
        raise ValueError(f"Pilot parity failed; {len(mismatches)} priority mismatches")
    return summary


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input-dir', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args(argv)
    print(json.dumps(validate(args.input_dir, args.output), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
