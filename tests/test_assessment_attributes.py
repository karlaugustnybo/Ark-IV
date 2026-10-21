from __future__ import annotations

import contextlib
import csv
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import duckdb
import pyarrow.parquet as pq
from asyncpg import UndefinedColumnError
from litestar import Litestar
from litestar.testing import TestClient

from ark_pipeline.builders.coarse_cache import export_parquet
from ark_pipeline.builders.species_metadata import build_global_species
from ark_pipeline.runtime.provenance import sha256
from backend.analysis import assessment_details
from backend.load_postgres import rows as export_rows
from tests import test_refresh_crosswalk


class AssessmentAttributesTests(unittest.TestCase):
    def test_exact_assessment_attributes_survive_detail_exports_outside_metric_schema(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp)
            test_refresh_crosswalk.fixture(root)
            source = root / 'sources/assessments.csv'
            with source.open() as handle:
                reader = csv.DictReader(handle)
                fields, records = reader.fieldnames + ['populationTrend', 'threats'], list(reader)
            for row in records:
                row.update(populationTrend='Increasing' if row['internalTaxonId'] == '101' else '', threats='habitat alteration')
            # A different SIS using the same assessment ID must never supply a
            # species' attributes. A different assessment is also ignored.
            unrelated = [{**records[0], 'internalTaxonId': '999', 'populationTrend': 'WRONG SIS'},
                        {**records[0], 'assessmentId': '9999', 'yearPublished': '2000',
                         'populationTrend': 'WRONG ASSESSMENT'}]
            with source.open('w') as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(records)
            test_refresh_crosswalk.register(root)
            crosswalk = test_refresh_crosswalk.RefreshCrosswalkTests().run_matcher(root)
            # Freeze the focal assessment IDs before adding unrelated records
            # to the descriptive attribute lookup input.
            with source.open('a') as handle:
                csv.DictWriter(handle, fieldnames=fields).writerows(unrelated)
            build_global_species(crosswalk_path=crosswalk, assessments_path=source,
                goat_species_path=root / 'sources/tol_species_all_ranks.tsv', h3_paths=[], output_dir=root / 'species')
            with duckdb.connect() as c:
                c.execute('CREATE TABLE SpecInfo AS SELECT * FROM read_parquet(?)', [str(root / 'species/species.parquet')])
                c.execute('CREATE TABLE SpecSystems AS SELECT * FROM read_parquet(?)', [str(root / 'species/species_systems.parquet')])
                c.execute('CREATE TABLE H3Res3Species(h3_index VARCHAR, gbif_ids VARCHAR[])')
                export_parquet(c, root / 'exports', resolutions=(3,), include_expanded_cell_species=False)
            exported = pq.read_table(root / 'exports/species.parquet').to_pylist()
            target = next(r for r in exported if r['iucn_sis_id'] == '101')
            self.assertEqual(target['iucn_population_trend'], 'Increasing')
            self.assertEqual(target['iucn_assessment_date'], '2026-01-01')
            self.assertEqual(target['iucn_attributes_source_sha256'], sha256(source))
            self.assertIsNone(next(r for r in exported if r['iucn_sis_id'] == '102')['iucn_population_trend'])
            columns = ['iucn_sis_id', 'iucn_assessment_id', 'iucn_population_trend', 'iucn_assessment_date',
                'iucn_assessment_scope', 'iucn_threat_classifications', 'iucn_attributes_source_sha256']
            projected = list(export_rows(root / 'exports/species.parquet', columns))
            self.assertIn(tuple(target[k] for k in columns), projected)
            pool = SimpleNamespace(fetchrow=AsyncMock(return_value={k: target[k] for k in columns}))
            with patch('backend.analysis.get_pool', return_value=pool), TestClient(
                Litestar([assessment_details]), raise_server_exceptions=False
            ) as client:
                response = client.get('/api/species/101/assessment')
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['iucn_population_trend'], 'Increasing')
                pool.fetchrow.return_value = None
                self.assertEqual(client.get('/api/species/999/assessment').status_code, 404)
                pool.fetchrow.side_effect = UndefinedColumnError('legacy schema')
                self.assertEqual(client.get('/api/species/101/assessment').status_code, 503)
            previous = (root / 'species/species.parquet').read_bytes()
            with source.open('a') as handle:
                csv.DictWriter(handle, fieldnames=fields).writerow({**records[0], 'populationTrend': 'conflicting value'})
            with self.assertRaisesRegex(ValueError, 'Conflicting assessment attribute'):
                build_global_species(crosswalk_path=crosswalk, assessments_path=source,
                    goat_species_path=root / 'sources/tol_species_all_ranks.tsv', h3_paths=[], output_dir=root / 'species')
            self.assertEqual((root / 'species/species.parquet').read_bytes(), previous)


if __name__ == '__main__':
    unittest.main()
