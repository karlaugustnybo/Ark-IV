"""Prepare complete released-tree scope without widening identity permissions."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq

from ark_pipeline.builders.phylogenetic import build_generation
from ark_pipeline.cli.dna_prepare import build as prepare_dna
from ark_pipeline.cli.phylogenetic_gap import run as prepare_mammals
from ark_pipeline.cli.phylogenetic_prepare import normalize_inputs
from ark_pipeline.compact_phylogeny import read_complete_tree
from ark_pipeline.edge import load_ncbi_merges
from ark_pipeline.edge_links import direct_identifiers
from ark_pipeline.edge_matching import normalize_name
from ark_pipeline.runtime.provenance import sha256


def validate_link_crosswalk(links_dir: Path, crosswalk: Path):
    """Reconcile all assessment identities across independently reviewed joins.

    Genomic permissions may be re-reviewed without changing an IUCN identity.
    The caller first checks both receipts against the same pinned IUCN sources;
    matching a SIS ID alone is insufficient to bridge two reviewed generations.
    """
    columns = ['iucn_sis_id', 'iucn_assessment_id', 'iucn_scientific_name']
    current = pq.read_table(crosswalk, columns=columns).to_pylist()
    checked = pq.read_table(links_dir / 'species_tree_links.parquet', columns=columns).to_pylist()

    def identities(rows):
        result = {str(row['iucn_sis_id']): (str(row['iucn_assessment_id']), normalize_name(row['iucn_scientific_name'])) for row in rows}
        if len(result) != len(rows) or any(row['iucn_sis_id'] is None for row in rows):
            raise ValueError('Global identity reconciliation requires unique nonnull SIS IDs')
        return result

    if identities(current) != identities(checked):
        raise ValueError('Global tree identities differ from current assessment/name identities')


def attach_identities(evidence, species, links, mapping, repeated, mammal_evidence, mammal_species):
    """Direct species IDs plus already checked bridges; conflicts never transfer.

    All relatives remain in the baseline audit, including ambiguous records.
    Multiple source records sharing a canonical ID cannot represent a tip.
    The historically reviewed mammal witnesses remain scoped to that review.
    """
    counts = Counter(row.get('taxon_id') for row in evidence)
    by_id = {row['taxon_id']: row for row in evidence if counts[row['taxon_id']] == 1}
    eligible_ids = {row['taxon_id'] for row in evidence if row.get('genomic_transfer_safe') is True and counts[row['taxon_id']] == 1}
    by_sis = {str(row['iucn_sis_id']): row for row in links}
    if len(by_sis) != len(links):
        raise ValueError('Duplicate assessed identity links')
    reviewed = {row.get('canonical_ncbi_taxid', row['taxon_id']): row for row in mammal_evidence}
    assessed_review = {str(row['iucn_sis_id']): row for row in mammal_species}
    for row in evidence:
        taxid = row.get('taxon_id')
        candidates = mapping.get(taxid, set())
        permitted = row.get('genomic_transfer_safe') is True and counts[taxid] == 1
        safe = permitted and taxid not in repeated and len(candidates) == 1
        row.update(tree_tip=next(iter(candidates)) if safe else None,
                   genomic_transfer_safe=safe, tree_join_status='linked' if safe else 'unresolved_tree_identity',
                   tree_join_route='ncbi_source_identifier' if safe else None)
        old = reviewed.get(taxid)
        if permitted and old and old.get('genomic_transfer_safe') is True:
            if candidates and candidates != {old['tree_tip']}:
                raise ValueError('Global direct identity conflicts with reviewed mammal witness')
            row.update(tree_tip=old['tree_tip'], genomic_transfer_safe=True,
                       tree_join_status='linked', tree_join_route='reviewed_mammal_identity')
    # Gather all bridge claims before applying any, so input ordering cannot
    # choose a winner when two assessments disagree about one evidence record.
    claims = {}
    for target in species:
        sis = str(target['iucn_sis_id'])
        link = by_sis.get(sis)
        if link is None:
            raise ValueError('Assessed target lacks an identity audit row')
        verified = link.get('tree_identity_verified') is True and link.get('tree_tip') is not None
        witness = by_id.get(target.get('taxon_id'))
        original_safe = target.get('genomic_transfer_safe') is True
        permitted = original_safe and witness is not None and target['taxon_id'] in eligible_ids and target['taxon_id'] not in repeated
        candidates = mapping.get(target.get('taxon_id'), set())
        consistent = len(candidates) <= 1 and (not candidates or candidates == {link.get('tree_tip')})
        old = assessed_review.get(sis)
        if old is not None and old.get('genomic_transfer_safe') is not True:
            permitted = False
        safe = verified and permitted and consistent
        # Assessment/name identities are reconciled across the reviewed joins.
        # A tree-only match never makes an unsafe target safe.
        target.update(tree_tip=link.get('tree_tip') if verified else None,
            tree_identity_verified=verified, genomic_transfer_safe=safe,
            redlist_category=link.get('redlist_category'), iucn_class=link.get('iucn_class'),
            ott_flags=link.get('ott_flags'), iucn_assessment_id=link.get('iucn_assessment_id'))
        if safe:
            claims.setdefault(target['taxon_id'], set()).add(target['tree_tip'])
    for taxid, tips in claims.items():
        witness = by_id[taxid]
        if len(tips) != 1 or (witness.get('tree_tip') and witness['tree_tip'] not in tips):
            witness.update(tree_tip=None, genomic_transfer_safe=False, tree_join_status='conflicting_identity_bridges')
            for target in species:
                if target.get('taxon_id') == taxid:
                    target['genomic_transfer_safe'] = False
            continue
        witness.update(tree_tip=next(iter(tips)), genomic_transfer_safe=True,
                       tree_join_status='linked', tree_join_route='checked_iucn_species_bridge')
    tip_counts = Counter(row['tree_tip'] for row in evidence if row.get('genomic_transfer_safe') is True)
    for row in evidence:
        if row.get('genomic_transfer_safe') is True and tip_counts[row['tree_tip']] != 1:
            row.update(tree_tip=None, genomic_transfer_safe=False, tree_join_status='competing_evidence_identity')
    for target in species:
        witness = by_id.get(target.get('taxon_id'))
        if witness is None or witness.get('genomic_transfer_safe') is not True:
            target['genomic_transfer_safe'] = False
    return evidence, species


def build_global(args):
    # Reuse the original checksum/snapshot guards and historical review rather
    # than inventing a broader permission for its recovered mammal witnesses.
    args.prepare_only, args.skip_cached_review = True, True
    if args.input_dir is None and args.ott_index is None:
        raise ValueError('Registered-input mode requires --ott-index')
    _, mammals, mammal_baseline, sources = prepare_mammals(args)
    validate_link_crosswalk(args.links_dir, args.crosswalk)
    mammal_species, mammal_evidence = normalize_inputs(mammals, mammal_baseline)
    dna = prepare_dna(args.root, args.crosswalk, args.output_root.parent / 'dna')
    generation = Path(dna['generation'])
    saved = json.loads((generation / 'receipt.json').read_text())
    for name in ('evidence', 'species'):
        path = generation / saved['outputs'][name]['filename']
        if sha256(path) != saved['outputs'][name]['sha256']:
            raise ValueError('Prepared DNA output changed')
        sources['dna_' + name] = {'path': str(path), 'sha256': sha256(path)}
    evidence = pq.read_table(Path(sources['dna_evidence']['path'])).to_pylist()
    species = pq.read_table(Path(sources['dna_species']['path'])).to_pylist()
    links = pq.read_table(args.links_dir / 'species_tree_links.parquet').to_pylist()
    ott = args.ott_index or args.input_dir / 'ott_index.parquet'
    tips = args.tree_tips or Path(sources['global_tree_tips']['path'])
    mapping, repeated = direct_identifiers(tips, ott, {r['taxon_id'] for r in evidence if r['taxon_id']},
                                           load_ncbi_merges(Path(sources['ncbi_merged']['path'])))
    evidence, species = attach_identities(evidence, species, links, mapping, repeated, mammal_evidence, mammal_species)
    tree, audit = read_complete_tree(Path(sources['dated-tree']['path']))
    sources['global_preparation_code'] = {'path': str(Path(__file__).resolve()), 'sha256': sha256(Path(__file__))}
    result = build_generation(tree, evidence, species, args.output_root, sources=sources,
        scope='all-acquired-clades', root=audit['root_label'], risk_samples=args.risk_samples, seed=args.seed)
    return {**result, 'tree_audit': audit}
