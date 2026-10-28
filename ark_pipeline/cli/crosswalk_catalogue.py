"""Prepare isolated matching inputs from catalogue and tabular IUCN snapshots.

Preserve the union of SIS identities. Catalogue assessment fields replace old
fields only for exact SIS identities. Old lineage is withheld for changed names;
new catalogue names without authoritative lineage remain unresolved candidates.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from ark_pipeline.cli.serving_metadata import source_file
from ark_pipeline.cli.sources_acquire import atomic_json, load_manifest, sha256
from ark_pipeline.cli.sources_download_iucn import atomic_text

ASSESSMENT_FIELDS = ('internalTaxonId', 'assessmentId', 'redlistCategory', 'yearPublished',
                     'assessmentDate', 'scopes', 'systems')
CATEGORY_LABELS = {'LC': 'Least Concern', 'NT': 'Near Threatened', 'VU': 'Vulnerable',
                   'EN': 'Endangered', 'CR': 'Critically Endangered', 'DD': 'Data Deficient',
                   'EX': 'Extinct', 'EW': 'Extinct in the Wild', 'NE': 'Not Evaluated',
                   'LR/LC': 'Lower Risk/least concern', 'LR/NT': 'Lower Risk/near threatened',
                   'LR/CD': 'Lower Risk/conservation dependent'}


def prepare(taxonomy: Path, assessments: Path, catalogue: Path, output: Path, *,
            taxon_evidence: Path | None = None, expected_release: str | None = None) -> dict:
    csv.field_size_limit(16 * 1024 * 1024)
    with taxonomy.open() as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        old = {}
        for row in reader:
            key = int(row['internalTaxonId'])
            if key in old:
                raise ValueError('Duplicate tabular SIS identity')
            old[key] = row
    current = {}
    with catalogue.open() as handle:
        for line in handle:
            row = json.loads(line)
            key = int(row['sis_taxon_id'])
            if key in current:
                raise ValueError('Duplicate catalogue SIS identity')
            if row.get('latest') is not True or not any(str(v.get('code')) == '1'
                                                       for v in row.get('scopes', [])):
                raise ValueError('Catalogue row is not a latest global assessment')
            current[key] = row
    old_assessments = {}
    with assessments.open() as handle:
        for row in csv.DictReader(handle):
            key = int(row['internalTaxonId'])
            if key in old_assessments:
                raise ValueError('Duplicate tabular assessment identity')
            old_assessments[key] = {k: row.get(k, '') for k in ASSESSMENT_FIELDS}
    evidence_inputs = {}
    if taxon_evidence is not None:
        report_path = taxon_evidence / 'report.json'
        report = json.loads(report_path.read_text())
        if not expected_release or report.get('release') != expected_release or report.get('status') != 'retrieved':
            raise ValueError('Require completed exact-ID IUCN evidence from the catalogue release')
        evidence_inputs[str(report_path.resolve())] = sha256(report_path)
    output.mkdir(parents=True, exist_ok=True)
    counts, scope = Counter(), []
    with atomic_text(output / 'taxonomy.csv') as th, atomic_text(output / 'assessments.csv') as ah:
        tw = csv.DictWriter(th, fieldnames=fields)
        aw = csv.DictWriter(ah, fieldnames=ASSESSMENT_FIELDS)
        tw.writeheader()
        aw.writeheader()
        for key in sorted(old.keys() | current.keys()):
            existing, new = old.get(key), current.get(key)
            status = ('CATALOGUE_ONLY' if existing is None else
                      'TABULAR_ONLY' if new is None else 'SHARED')
            counts[status] += 1
            same = bool(existing and new and existing['scientificName'].strip().casefold()
                        == new['taxon_scientific_name'].strip().casefold())
            row = existing.copy() if existing else dict.fromkeys(fields, '')
            previous_assessment = old_assessments.get(key)
            taxonomy_basis = 'TABULAR_UNCHANGED_NAME' if same or not new else 'CATALOGUE_NAME_ONLY'
            if new:
                if not same:
                    row = dict.fromkeys(fields, '')
                    evidence_path = taxon_evidence / 'taxa' / 'sis' / f'{key}.json' if taxon_evidence else None
                    if evidence_path and evidence_path.is_file():
                        evidence = json.loads(evidence_path.read_text())
                        evidence_inputs[str(evidence_path.resolve())] = sha256(evidence_path)
                        body = evidence.get('body', {})
                        taxon = body.get('taxon', {})
                        current_assessment = any(
                            str(a.get('assessment_id')) == str(new['assessment_id'])
                            and str(a.get('sis_taxon_id')) == str(key)
                            and a.get('latest') is True
                            and any(str(s.get('code')) == '1' for s in a.get('scopes', []))
                            for a in body.get('assessments', [])
                        )
                        if (evidence.get('status') == 'retrieved'
                                and evidence.get('release') == expected_release
                                and evidence.get('endpoint') == f'taxa/sis/{key}'
                                and str(taxon.get('sis_id')) == str(key)
                                and taxon.get('scientific_name', '').strip().casefold()
                                == new['taxon_scientific_name'].strip().casefold()
                                and taxon.get('species') is True
                                and taxon.get('infrarank') is False
                                and taxon.get('subpopulation') is False and current_assessment):
                            for field, source in (
                                ('kingdomName', 'kingdom_name'), ('phylumName', 'phylum_name'),
                                ('className', 'class_name'), ('orderName', 'order_name'),
                                ('familyName', 'family_name'), ('genusName', 'genus_name'),
                                ('speciesName', 'species_name'), ('authority', 'authority'),
                            ):
                                if field in row:
                                    row[field] = taxon.get(source) or ''
                            taxonomy_basis = 'IUCN_API_EXACT_SIS'
                            counts['IUCN_API_LINEAGE_RECOVERED'] += 1
                        else:
                            counts['IUCN_API_LINEAGE_CONFLICT'] += 1
                    if taxonomy_basis != 'IUCN_API_EXACT_SIS':
                        counts['NEW_OR_CHANGED_NAME_WITHHELD_LINEAGE'] += 1
                row.update(internalTaxonId=str(key), scientificName=new['taxon_scientific_name'])
                same_assessment = (previous_assessment and
                                   str(new['assessment_id']) == previous_assessment['assessmentId'])
                assessment = dict(zip(ASSESSMENT_FIELDS, (
                    key, new['assessment_id'], CATEGORY_LABELS.get(new['red_list_category_code'].upper(),
                                                                 new['red_list_category_code']), new['year_published'],
                    new['assessment_date'], 'Global',
                    previous_assessment.get('systems', '') if same_assessment else '',
                )))
            elif previous_assessment:
                assessment = previous_assessment
            else:
                raise ValueError(f'Tabular-only SIS {key} has no assessment')
            tw.writerow(row)
            aw.writerow(assessment)
            scope.append({'iucn_sis_id': key, 'presence': status,
                          'catalogue_category_code': new['red_list_category_code'] if new else None,
                          'taxonomy_basis': taxonomy_basis,
                          'assessment_basis': 'CATALOGUE_LATEST_GLOBAL' if new else 'TABULAR_ONLY_NOT_CONFIRMED_CURRENT'})
    result = {'counts': dict(counts), 'rows': scope,
              'inputs': {**{str(p.resolve()): sha256(p) for p in (taxonomy, assessments, catalogue)}, **evidence_inputs},
              'code_sha256': sha256(Path(__file__)),
              'outputs': {p.name: sha256(p) for p in (output / 'taxonomy.csv', output / 'assessments.csv')}}
    atomic_json(output / 'scope.json', result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--taxon-evidence', type=Path)
    args = parser.parse_args()
    manifest = load_manifest(args.root)
    paths = [source_file(args.root, manifest, key, name, args.root / 'unused') for key, name in (
        ('iucn-red-list-tabular', 'taxonomy.csv'), ('iucn-red-list-tabular', 'assessments.csv'),
        ('iucn-assessment-catalog', 'iucn_assessment_catalog.jsonl'),
    )]
    result = prepare(*paths, args.output, taxon_evidence=args.taxon_evidence,
                     expected_release=manifest['sources']['iucn-assessment-catalog']['release'])
    latest = load_manifest(args.root)
    if any(latest['sources'][key] != manifest['sources'][key]
           for key in ('iucn-red-list-tabular', 'iucn-assessment-catalog')):
        raise ValueError('Selected IUCN source changed during catalogue preparation')
    print(json.dumps(result['counts']))


if __name__ == '__main__':
    main()
