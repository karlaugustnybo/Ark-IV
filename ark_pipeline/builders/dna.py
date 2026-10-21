"""Tree-independent evidence and checked NCBI lineage scenario generations."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from ark_pipeline.aggregation.species_lists import _publish_current, signature, write_receipt
from ark_pipeline.dna_evidence import (
    DnaRule,
    evaluate_evidence,
    evidence_table,
)
from ark_pipeline.runtime.provenance import (
    code_fingerprint,
    identity_digest,
    receipt_is_current,
    sha256,
)


def code_identity():
    return code_fingerprint([Path(__file__), Path(__file__).parents[1] / 'dna_evidence.py'])


def current_lineage_ids(nodes: Path, taxids: set[str]) -> dict[str, dict]:
    """Resolve rank IDs directly from the pinned parent graph, never names.

    Unavailable ancestors remain null. Cycles and incomplete parent chains
    invalidate the lineage instead of granting permission to transfer evidence.
    """
    parents = {}
    with nodes.open() as handle:
        for line in handle:
            fields = [v.strip() for v in line.split('|')[:3]]
            if len(fields) != 3 or fields[0] in parents:
                raise ValueError('Malformed or duplicate NCBI node')
            parents[fields[0]] = fields[1], fields[2]
    result = {}
    for taxid in taxids:
        current, seen, lineage = taxid, set(), {}
        while current in parents:
            if current in seen:
                raise ValueError('Cycle in NCBI lineage')
            seen.add(current)
            parent, rank = parents[current]
            if rank in ('kingdom', 'family', 'genus'):
                lineage.setdefault(f'{rank}_taxid', current)
            if parent == current:
                break
            current = parent
        else:
            lineage = {}
        result[taxid] = {**lineage, 'current_species': parents.get(taxid, (None, None))[1] == 'species'}
    return result


def calculate(evidence: list[dict], species: list[dict], rule: DnaRule, generation: str) -> dict:
    counts = Counter(str(r['taxon_id']) for r in evidence)
    wanted = {str(r.get('taxon_id')) for r in species if r.get('genomic_transfer_safe') is True}
    states, families, genera = {}, set(), set()
    qualifying = unknown = 0
    # Scan every relative once. Retain focal states only, rather than another
    # full-evidence dictionary, and count qualification/unknowns in this pass.
    for row in evidence:
        state = evaluate_evidence(row, rule)
        qualifying += state.qualifies is True
        unknown += state.qualifies is None
        safe = row.get('genomic_transfer_safe') is True
        taxid = str(row['taxon_id'])
        if safe and taxid in wanted and counts[taxid] == 1:
            states[taxid] = state
        if safe and state.qualifies is True:
            kingdom, family, genus = (row.get(k) for k in ('kingdom_taxid', 'family_taxid', 'genus_taxid'))
            if kingdom and family:
                families.add((kingdom, family))
                if genus:
                    genera.add((kingdom, family, genus))
    rows = []
    unavailable = evaluate_evidence({}, rule)
    for row in species:
        safe = row.get('genomic_transfer_safe') is True
        state = states.get(str(row.get('taxon_id')), unavailable) if safe else unavailable
        kingdom, family, genus = (row.get(k) for k in ('kingdom_taxid', 'family_taxid', 'genus_taxid'))
        rows.append({'iucn_sis_id': str(row['iucn_sis_id']),
            'taxon_id': str(row['taxon_id']) if row.get('taxon_id') is not None else None,
            **asdict(state), 'family_represented': (kingdom, family) in families if kingdom and family else None,
            'genus_represented': (kingdom, family, genus) in genera if kingdom and family and genus else None,
            'identity_status': 'checked' if safe else 'unresolved_taxonomy'})
    identity = {'generation': generation, 'rule': rule.identity(), 'method': 'checked-ncbi-rank-representation-v1'}
    return {'scenario_id': identity_digest(identity), 'identity': identity, 'rows': rows,
            'evidence_records': len(evidence),
            'qualifying_records': qualifying, 'unknown_records': unknown}


def build_generation(evidence: list[dict], species: list[dict], output: Path, *, sources: dict) -> dict:
    if not sources or not evidence or not species:
        raise ValueError('Require pinned evidence, species and sources')
    if len({str(r['iucn_sis_id']) for r in species}) != len(species):
        raise ValueError('Duplicate assessed species')

    def validate_sources():
        for name, source in sources.items():
            if sha256(Path(source['path'])) != source['sha256']:
                raise ValueError(f'DNA source changed: {name}')

    validate_sources()
    identity = {'schema_version': 1, 'sources': sources, 'code': code_identity(),
                'evidence': identity_digest({'rows': evidence}), 'species': identity_digest({'rows': species})}
    target = output / 'generations' / identity_digest(identity)
    receipt = target / 'receipt.json'
    if receipt.exists():
        saved = json.loads(receipt.read_text())
        outputs = {name: target / item['filename'] for name, item in saved['outputs'].items()}
        if receipt_is_current(receipt, identity, outputs, saved['totals']['schemas']):
            validate_sources()
            _publish_current(output, target)
            return {'status': 'reused', 'generation': str(target)}
    if (output / 'current').resolve() == target.resolve():
        raise ValueError('Published DNA generation is corrupt; choose a new output root')
    target.mkdir(parents=True, exist_ok=True)
    tables = {'evidence': evidence_table(evidence), 'species': evidence_table(species),
              'default': pa.Table.from_pylist(calculate(evidence, species, DnaRule(), target.name)['rows'])}
    outputs = {}
    for name, table in tables.items():
        path = target / f'{name}.parquet'
        temporary = path.with_suffix('.tmp')
        pq.write_table(table, temporary, compression='zstd')
        temporary.replace(path)
        outputs[name] = path
    totals = {'schemas': {name: signature(table.schema) for name, table in tables.items()},
              'evidence_records': len(evidence), 'assessed_species': len(species)}
    validate_sources()
    write_receipt(receipt, identity, outputs, totals)
    _publish_current(output, target)
    return {'status': 'built', 'generation': str(target), **totals}
