from __future__ import annotations

import csv
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ark_pipeline.cli.crosswalk_catalogue import prepare
from ark_pipeline.cli.crosswalk_evidence import (
    canonical_id,
    ott_links,
    scoped_tree_tips,
    screen_crosswalk,
)
from ark_pipeline.cli.sources_fetch_goat_provenance import fetch, normalize_batch


def record(taxid='10', name='Panthera leo'):
    return {'recordId': 'positional-label-is-not-identity', 'record': {
        'taxon_id': taxid, 'scientific_name': name, 'taxon_rank': 'species',
        'attributes': [{'key': 'redlist_category', 'keyword_value': ['VU'],
                        'aggregation_source': 'direct', 'values': [
                            {'metadata': {'source': {'slug': '101/1101'}}}
                        ]}],
    }}


class CrosswalkInvestigationTests(unittest.TestCase):
    def test_col_conflict_withholds_transfer_without_inventing_matches(self):
        import duckdb
        import pyarrow as pa

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            crosswalk = root / 'input.parquet'
            with duckdb.connect() as con:
                table = pa.Table.from_pylist([
                    {'iucn_sis_id': 1, 'match_status': 'MATCHED', 'matched_ncbi_species_taxid': '10', 'review_candidate_ncbi_taxid': None, 'iucn_redlist_category': 'VU', 'safe_for_automatic_species_trait_transfer': True},
                    {'iucn_sis_id': 2, 'match_status': 'REVIEW_UNRESOLVED', 'matched_ncbi_species_taxid': None, 'review_candidate_ncbi_taxid': '20', 'iucn_redlist_category': 'EN', 'safe_for_automatic_species_trait_transfer': False},
                ])
                con.from_arrow(table).write_parquet(str(crosswalk))
            evidence = root / 'evidence.jsonl'
            evidence.write_text(''.join(json.dumps({
                'iucn_sis_id': key, 'ncbi_taxid': '10' if key == 1 else '20', 'col_concept_conflict': key == 1,
                'col_xr': {'status': 'concept_conflict' if key == 1 else 'shared_name_concept',
                           'iucn_name_concepts': ['a'], 'goat_name_concepts': ['b' if key == 1 else 'a']},
                'tree_status': 'UNIQUE_SPECIES_TIP',
            }) + '\n' for key in (1, 2)))
            result = screen_crosswalk(crosswalk, evidence, root / 'screened.parquet')
            self.assertEqual(result['transfer_eligible'], 0)
            self.assertEqual(result['withheld_for_col_conflict'], 1)
            with duckdb.connect() as con:
                self.assertEqual(con.execute('SELECT match_status FROM read_parquet(?) ORDER BY iucn_sis_id',
                                            [str(root / 'screened.parquet')]).fetchall(),
                                 [('MATCHED',), ('REVIEW_UNRESOLVED',)])
                self.assertEqual(con.execute('SELECT iucn_redlist_category FROM read_parquet(?) WHERE iucn_sis_id=1',
                                            [str(root / 'screened.parquet')]).fetchone()[0], 'Vulnerable')
            rows = [json.loads(line) for line in evidence.read_text().splitlines()]
            rows[0]['col_concept_conflict'] = False
            rows[0]['col_xr'].update(status='shared_name_concept', goat_name_concepts=['a'])
            evidence.write_text(''.join(json.dumps(row) + '\n' for row in rows))
            scope = root / 'scope.json'
            scope.write_text(json.dumps({'outputs': {'taxonomy.csv': 'a', 'assessments.csv': 'b'},
                                         'rows': [{'iucn_sis_id': 1, 'presence': 'TABULAR_ONLY'},
                                                  {'iucn_sis_id': 2, 'presence': 'SHARED'}]}))
            (root / 'match_summary.json').write_text(json.dumps({'sources': {
                'iucn_taxonomy': {'sha256': 'a'}, 'iucn_assessments': {'sha256': 'b'},
            }}))
            scoped = screen_crosswalk(crosswalk, evidence, root / 'scoped.parquet', scope=scope)
            self.assertEqual(scoped['withheld_for_catalogue_scope'], 1)
            self.assertEqual(scoped['transfer_eligible'], 0)
            rows[0]['ncbi_taxid'] = '999'
            evidence.write_text(''.join(json.dumps(row) + '\n' for row in rows))
            with self.assertRaisesRegex(ValueError, 'does not reconcile'):
                screen_crosswalk(crosswalk, evidence, root / 'invalid.parquet')

    def test_catalogue_union_preserves_ids_and_withholds_changed_lineage(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            taxonomy, assessments, catalogue = [root / name for name in ('tax.csv', 'ass.csv', 'cat.jsonl')]
            taxonomy.write_text('internalTaxonId,scientificName,familyName,kingdomName\n'
                                '1,Panthera leo,Felidae,Animalia\n'
                                '2,Panthera tigris,Felidae,Animalia\n'
                                '3,Old species,Oldidae,Animalia\n')
            assessments.write_text('internalTaxonId,assessmentId,redlistCategory,systems\n'
                                   '1,11,VU,Terrestrial\n2,22,EN,Terrestrial\n3,33,DD,Freshwater\n')
            rows = [{'sis_taxon_id': key, 'assessment_id': aid, 'taxon_scientific_name': name,
                     'red_list_category_code': 'VU', 'year_published': '2026',
                     'assessment_date': '2026-01-01', 'latest': True, 'scopes': [{'code': '1'}]}
                    for key, aid, name in ((1, 11, 'Panthera leo'), (2, 23, 'Changed name'), (4, 44, 'New species'))]
            catalogue.write_text(''.join(json.dumps(row) + '\n' for row in rows))
            result = prepare(taxonomy, assessments, catalogue, root / 'out')
            with (root / 'out/taxonomy.csv').open() as handle:
                taxa = {row['internalTaxonId']: row for row in csv.DictReader(handle)}
            with (root / 'out/assessments.csv').open() as handle:
                ass = {row['internalTaxonId']: row for row in csv.DictReader(handle)}
            self.assertEqual(set(taxa), {'1', '2', '3', '4'})
            self.assertEqual(taxa['1']['familyName'], 'Felidae')
            self.assertEqual(taxa['2']['familyName'], '')
            self.assertEqual(taxa['4']['kingdomName'], '')
            self.assertEqual(ass['1']['systems'], 'Terrestrial')
            self.assertEqual(ass['1']['redlistCategory'], 'Vulnerable')
            self.assertEqual(ass['2']['systems'], '')
            self.assertEqual(ass['3']['assessmentId'], '33')
            self.assertEqual(result['counts']['NEW_OR_CHANGED_NAME_WITHHELD_LINEAGE'], 2)
            api = root / 'api'
            (api / 'taxa/sis').mkdir(parents=True)
            (api / 'report.json').write_text(json.dumps({'status': 'retrieved', 'release': '2026-1'}))
            detail = {'status': 'retrieved', 'release': '2026-1', 'endpoint': 'taxa/sis/4',
                      'body': {'taxon': {'sis_id': 4, 'scientific_name': 'New species',
                                         'species': True, 'infrarank': False, 'subpopulation': False,
                                         'family_name': 'Newidae', 'kingdom_name': 'ANIMALIA'},
                               'assessments': [rows[2]]}}
            (api / 'taxa/sis/4.json').write_text(json.dumps(detail))
            recovered = prepare(taxonomy, assessments, catalogue, root / 'recovered',
                                taxon_evidence=api, expected_release='2026-1')
            self.assertEqual(recovered['counts']['IUCN_API_LINEAGE_RECOVERED'], 1)
            with (root / 'recovered/taxonomy.csv').open() as handle:
                recovered_taxa = {row['internalTaxonId']: row for row in csv.DictReader(handle)}
            self.assertEqual(recovered_taxa['4']['familyName'], 'Newidae')
            detail['body']['taxon']['sis_id'] = 999
            (api / 'taxa/sis/4.json').write_text(json.dumps(detail))
            conflict = prepare(taxonomy, assessments, catalogue, root / 'conflict',
                               taxon_evidence=api, expected_release='2026-1')
            self.assertEqual(conflict['counts']['IUCN_API_LINEAGE_CONFLICT'], 1)
            catalogue.write_text(json.dumps(rows[0]) + '\n' + json.dumps(rows[0]) + '\n')
            with self.assertRaisesRegex(ValueError, 'Duplicate catalogue'):
                prepare(taxonomy, assessments, catalogue, root / 'invalid')

    def test_batch_identity_uses_document_and_rejects_missing_duplicate_foreign(self):
        body = {'status': {'success': True}, 'records': [record('11'), record('10')]}
        rows = normalize_batch(body, ['10', '11'])
        self.assertEqual([row['ncbi_taxid'] for row in rows], ['11', '10'])
        self.assertEqual(json.loads(rows[0]['field_provenance_json'])['redlist_category']['rawValues'][0]
                         ['metadata']['source']['slug'], '101/1101')
        for records in ([record()], [record(), record()], [record('999')]):
            with self.subTest(records=records), self.assertRaises(ValueError):
                normalize_batch({'status': {'success': True}, 'records': records}, ['10', '11'])

    def test_provenance_resume_reuses_validated_batch(self):
        body = {'status': {'success': True}, 'records': [record()]}
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'raw.json'
            with patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps(body).encode())) as request:
                first = fetch(['10'], output, 'a' * 64)
                self.assertEqual(request.call_count, 1)
            with patch('urllib.request.urlopen', side_effect=AssertionError('cached batch requested')):
                second = fetch(['10'], output, 'a' * 64)
            self.assertEqual(first['rows'], second['rows'])

    def test_ott_links_preserve_competing_species_and_merge_cycles(self):
        self.assertEqual(canonical_id('1', {'1': '2', '2': '3'}), '3')
        self.assertIsNone(canonical_id('1', {'1': '2', '2': '1'}))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'ott.tgz'
            text = ('uid|parent_uid|name|rank|sourceinfo|uniqname|flags|\n'
                    '1|0|First|species|ncbi:99||extinct|\n'
                    '2|0|Second|species|ncbi:10|||\n')
            with tarfile.open(path, 'w:gz') as tar:
                entry = tarfile.TarInfo('ott/taxonomy.tsv')
                data = text.encode()
                entry.size = len(data)
                tar.addfile(entry, io.BytesIO(data))
            links = ott_links(path, {'10'}, {'99': '10'})
            self.assertEqual(len(links['10']), 2)
            self.assertEqual(links['10'][0]['flags'], 'extinct')

    def test_tree_parser_excludes_internal_ids_and_preserves_duplicate_tips(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'tree.tre'
            path.write_text('((A_ott10:1,B_ott10:1)Internal_ott99:1,C_ott0:1,D_ott12:1);')
            tips, summary = scoped_tree_tips(path, {'10', '12', '99'})
            self.assertEqual(tips['10']['count'], 2)
            self.assertNotIn('99', tips)
            self.assertEqual(summary['parsed_leaf_labels'], 4)
            self.assertEqual(summary['placeholder_leaf_labels'], 1)


if __name__ == '__main__':
    unittest.main()
