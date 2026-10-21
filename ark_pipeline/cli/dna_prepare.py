"""Prepare private DNA scenarios from registered sources without a dated tree."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

import duckdb

from ark_pipeline.builders.dna import build_generation, current_lineage_ids
from ark_pipeline.cli.serving_metadata import source_file, validate_crosswalk_sources
from ark_pipeline.cli.sources_acquire import load_manifest
from ark_pipeline.dna_evidence import FIELDS
from ark_pipeline.edge import canonical_taxid, load_ncbi_merges
from ark_pipeline.runtime.provenance import sha256
from ark_pipeline.runtime.resources import configure_duckdb


def build(root: Path, crosswalk: Path, output_root: Path) -> dict:
    root = root.resolve(strict=True)
    manifest = load_manifest(root)
    validate_crosswalk_sources(crosswalk, manifest)
    paths = {'crosswalk': crosswalk, 'crosswalk_review': crosswalk.with_name('match_summary.json')}
    for key, source, name in (
        ('goat', 'goat-species', 'tol_species_all_ranks.tsv'),
        ('nodes', 'ncbi-taxonomy', 'nodes.dmp'), ('merges', 'ncbi-taxonomy', 'merged.dmp')):
        if source not in manifest.get('sources', {}):
            raise ValueError('DNA enrichment requires registered source receipts')
        paths[key] = source_file(root, manifest, source, name, root / name)
    sources = {name: {'path': str(path.resolve(strict=True)), 'sha256': sha256(path)} for name, path in paths.items()}
    review = json.loads(paths['crosswalk_review'].read_text())
    for key, reviewed_name in (('nodes', 'ncbi_nodes'), ('merges', 'ncbi_merged')):
        if review.get('sources', {}).get(reviewed_name, {}).get('sha256') != sources[key]['sha256']:
            raise ValueError('NCBI snapshot differs from the reviewed crosswalk')
    merges = load_ncbi_merges(paths['merges'])
    # The export has many unrelated taxon attributes. Keep only the shared
    # evidence contract before crossing Arrow into Python; absent fields retain
    # exactly the same unknown values as row.get() in older snapshots.
    with paths['goat'].open(encoding='utf-8-sig', newline='') as stream:
        available = set(next(csv.reader(stream, delimiter='\t')))
    if not {'taxon_id', 'taxon_rank'} <= available:
        raise ValueError('GoaT evidence requires taxon_id and taxon_rank columns')
    projection = ', '.join(f'"{key}"' if key in available else f'NULL::VARCHAR AS "{key}"'
                           for key in ('taxon_id', *FIELDS))
    with duckdb.connect() as c:
        configure_duckdb(c, threads=1, memory_limit='512MB')
        raw = c.execute(f"SELECT {projection} FROM read_csv(?, delim='\t', header=true, all_varchar=true, sample_size=-1) WHERE lower(taxon_rank)='species'",
                        [str(paths['goat'])]).to_arrow_table().to_pylist()
        targets = c.execute('''SELECT iucn_sis_id, matched_ncbi_species_taxid,
            safe_for_automatic_species_trait_transfer FROM read_parquet(?)''', [str(crosswalk)]).to_arrow_table().to_pylist()
    def canonical(value):
        return canonical_taxid(str(value), merges) if value is not None else None
    ids = [canonical(row['taxon_id']) for row in raw]
    counts = Counter(ids)
    lineages = current_lineage_ids(paths['nodes'], (set(ids) | {canonical(r['matched_ncbi_species_taxid']) for r in targets}) - {None})
    evidence = [{**{key: row.get(key) for key in FIELDS}, 'taxon_id': taxid,
        **lineages.get(taxid, {}), 'genomic_transfer_safe': counts[taxid] == 1 and lineages.get(taxid, {}).get('current_species') is True}
        for row, taxid in zip(raw, ids)]
    species = []
    for row in targets:
        taxid = canonical(row['matched_ncbi_species_taxid'])
        safe = row['safe_for_automatic_species_trait_transfer'] is True and lineages.get(taxid, {}).get('current_species') is True
        species.append({'iucn_sis_id': str(row['iucn_sis_id']), 'taxon_id': taxid,
                        **(lineages.get(taxid, {}) if safe else {}), 'genomic_transfer_safe': safe})
    validate_crosswalk_sources(crosswalk, load_manifest(root))
    return build_generation(evidence, species, output_root, sources=sources)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--crosswalk', type=Path, required=True)
    p.add_argument('--output-root', type=Path, required=True)
    print(json.dumps(build(**vars(p.parse_args(argv))), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
