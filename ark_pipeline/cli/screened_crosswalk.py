"""Register and consume a screened crosswalk without losing its source bindings."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

import duckdb

from ark_pipeline.cli.sources_acquire import (
    DEFAULT_CONFIG,
    atomic_json,
    load_catalogue,
    load_manifest,
    register_manual,
    sha256,
)

SOURCE = 'screened-crosswalk'
POLICY = 'screened-crosswalk-v1'
FILES = ('iucn_goat_crosswalk.parquet', 'match_summary.json', 'screening_receipt.json', 'provenance.json')
BINDINGS = {
    'iucn-red-list-tabular': ('taxonomy.csv', 'assessments.csv'),
    'iucn-assessment-catalog': ('iucn_assessment_catalog.jsonl',),
    'goat-species': ('tol_species_all_ranks.tsv',),
    'ncbi-taxonomy': ('names.dmp', 'nodes.dmp', 'merged.dmp', 'delnodes.dmp'),
    'col-xr': ('col-xr-dwca.zip',),
    'gbif-backbone': ('backbone.zip',),
    'ott-taxonomy': ('ott3.7.3.tgz',),
    'dated-tree': ('equal_splits_median_tree.tre',),
}


def source_bindings(manifest: dict) -> dict:
    result = {}
    for source_id, names in BINDINGS.items():
        record = manifest.get('sources', {}).get(source_id)
        if record is None or record.get('validation_status') != 'passed':
            raise ValueError(f'{source_id}: validated source required by screened crosswalk')
        files = {item['logical_name']: item['sha256'] for item in record['files']}
        if any(name not in files for name in names):
            raise ValueError(f'{source_id}: screened crosswalk source files missing')
        result[source_id] = {'release': record.get('release'), 'files': {name: files[name] for name in names}}
    return result


def validate(crosswalk: Path, summary: dict, manifest: dict) -> None:
    if summary.get('screening_policy') != POLICY:
        raise ValueError('Unsupported screened crosswalk policy; re-screen before rebuilding')
    if summary.get('registered_sources') != source_bindings(manifest):
        raise ValueError('Screened crosswalk sources changed; re-screen the new snapshots before rebuilding')
    digest = sha256(crosswalk)
    receipt = json.loads(crosswalk.with_name('screening_receipt.json').read_text())
    if (digest != summary.get('crosswalk_sha256') or receipt.get('status') != 'passed'
            or receipt.get('crosswalk_sha256') != digest
            or receipt.get('summary_sha256') != sha256(crosswalk.with_name('match_summary.json'))):
        raise ValueError('Screened crosswalk receipt/output mismatch')


def counts(crosswalk: Path) -> dict:
    with duckdb.connect() as con:
        con.execute("SET threads=1; SET memory_limit='512MB'")
        con.read_parquet(str(crosswalk)).create_view('cw')
        total, unique, nulls, invalid = con.execute('''
            SELECT count(*),count(DISTINCT iucn_sis_id),count(*) FILTER(WHERE iucn_sis_id IS NULL),
                count(*) FILTER(WHERE match_status NOT IN ('MATCHED','REVIEW_UNRESOLVED','NO_GOAT_NCBI_CANDIDATE')
                    OR match_status IS NULL
                    OR (match_status='MATCHED' AND matched_ncbi_species_taxid IS NULL)
                    OR (match_status<>'MATCHED' AND matched_ncbi_species_taxid IS NOT NULL)
                    OR safe_for_automatic_species_trait_transfer IS NULL
                    OR (safe_for_automatic_species_trait_transfer AND
                        (match_status<>'MATCHED' OR coalesce(ncbi_identifier_status,'')<>'CURRENT'
                         OR coalesce(current_ncbi_rank,'')<>'species'
                         OR coalesce(col_xr_concept_status,'')='concept_conflict'
                         OR coalesce(iucn_catalogue_presence,'') NOT IN ('SHARED','CATALOGUE_ONLY')
                         OR coalesce(len(goat_iucn_sis_ids),0)>1
                         OR (len(goat_iucn_sis_ids)=1 AND NOT list_contains(goat_iucn_sis_ids,iucn_sis_id)))))
            FROM cw
        ''').fetchone()
        if not total or total != unique or nulls or invalid:
            raise ValueError('Screened crosswalk identity/transfer reconciliation failed')
        statuses = dict(con.execute('SELECT match_status,count(*) FROM cw GROUP BY 1').fetchall())
        eligible = con.execute('SELECT count(*) FROM cw WHERE safe_for_automatic_species_trait_transfer').fetchone()[0]
    return {'iucn_taxa': total, 'matched': statuses.get('MATCHED', 0),
            'safe_for_automatic_species_trait_transfer': eligible, **statuses}


def register(root: Path, investigation: Path, release: str) -> None:
    from ark_pipeline.cli.serving_metadata import source_file

    manifest = load_manifest(root)
    bindings = source_bindings(manifest)
    # Check the actual selected files, not just the manifest's claims.
    for source_id, names in BINDINGS.items():
        for name in names:
            source_file(root, manifest, source_id, name, root / 'unused')
    artifact = investigation / 'crosswalk-reviewed.parquet'
    review = json.loads((investigation / 'review-receipt.json').read_text())
    matcher = json.loads((investigation / 'reviewed-candidate/match_summary.json').read_text())
    external = json.loads((investigation / 'reviewed-evidence-clean/summary.json').read_text())
    scope = json.loads((investigation / 'catalogue-api-inputs/scope.json').read_text())
    digest = sha256(artifact)
    if (review.get('status') != 'passed' or not review.get('violations')
            or any(review['violations'].values()) or review['artifact']['sha256'] != digest
            or external['screened_crosswalk_sha256'] != digest
            or external['catalogue_scope_sha256'] != sha256(investigation / 'catalogue-api-inputs/scope.json')):
        raise ValueError('Investigation screening receipts do not reconcile')
    for path, expected in review['inputs'].items():
        if sha256(Path(path)) != expected:
            raise ValueError('Investigation evidence changed after screening')
    for source_id in ('iucn-red-list-tabular', 'iucn-assessment-catalog'):
        if not set(bindings[source_id]['files'].values()).issubset(scope['inputs'].values()):
            raise ValueError('IUCN catalogue preparation belongs to different registered inputs')
    for key, source_id, filename in (
        ('goat_species', 'goat-species', 'tol_species_all_ranks.tsv'),
        ('ncbi_names', 'ncbi-taxonomy', 'names.dmp'), ('ncbi_nodes', 'ncbi-taxonomy', 'nodes.dmp'),
        ('ncbi_merged', 'ncbi-taxonomy', 'merged.dmp'), ('ncbi_deleted', 'ncbi-taxonomy', 'delnodes.dmp'),
    ):
        if matcher['sources'][key]['sha256'] != bindings[source_id]['files'][filename]:
            raise ValueError(f'{key}: matcher used different source content')
    for source_id in ('col-xr', 'gbif-backbone', 'ott-taxonomy', 'dated-tree'):
        if external['inputs'][source_id]['sha256'] not in bindings[source_id]['files'].values():
            raise ValueError(f'{source_id}: external audit used different source content')
    if external['crosswalk_sha256'] != sha256(investigation / 'reviewed-candidate/iucn_goat_crosswalk.parquet'):
        raise ValueError('External audit used a different matcher generation')
    actual_counts = counts(artifact)
    staging = root / 'acquisition/.staging'
    staging.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='screened-crosswalk-', dir=staging) as temporary:
        package = Path(temporary)
        shutil.copy2(artifact, package / FILES[0])
        summary = {'schema_version': 2, 'screening_policy': POLICY, 'crosswalk_sha256': digest,
                   'registered_sources': bindings, 'sources': matcher['sources'], 'counts': actual_counts,
                   'validation_limit': 'structural screening; biological precision not independently adjudicated'}
        atomic_json(package / 'match_summary.json', summary)
        atomic_json(package / 'provenance.json', {
            'review': review, 'matcher': matcher, 'external_audit': external,
            'catalogue_preparation': {key: value for key, value in scope.items() if key != 'rows'},
        })
        atomic_json(package / 'screening_receipt.json', {
            'status': 'passed', 'crosswalk_sha256': digest,
            'summary_sha256': sha256(package / 'match_summary.json'), 'counts': actual_counts,
        })
        validate(package / FILES[0], summary, load_manifest(root))
        register_manual(argparse.Namespace(root=root, source=SOURCE, release=release,
                                          file=[f'{name}={package / name}' for name in FILES],
                                          inventory_dir=None, authorized=True, reference=False),
                        load_catalogue(DEFAULT_CONFIG))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--investigation', type=Path, required=True)
    parser.add_argument('--release', required=True)
    args = parser.parse_args()
    register(args.root.resolve(), args.investigation.resolve(), args.release)


if __name__ == '__main__':
    main()
