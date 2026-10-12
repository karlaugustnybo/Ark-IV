from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import geopandas as gpd
import pyarrow as pa
import pyarrow.parquet as pq
from shapely.geometry import box

from ark_pipeline.cli.species_passage import build
from ark_pipeline.runtime.provenance import sha256


class PassageTests(unittest.TestCase):
    def test_explicit_sis_mapping_policy_and_coarse_supplement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'acquisition').mkdir()
            source = root / 'synthetic.zip'
            source.write_bytes(b'synthetic source, mocked GDAL intake')
            (root / 'acquisition/current.json').write_text(json.dumps({'sources': {'iucn-spatial': {'files': [{
                'path': source.name, 'bytes': source.stat().st_size, 'sha256': sha256(source), 'logical_name': 'synthetic',
            }]}}}))
            species = root / 'species.parquet'
            pq.write_table(pa.table({'gbif_accepted_id':['serving-101','serving-102'], 'iucn_sis_id':['101','102']}), species)
            frame = gpd.GeoDataFrame({'id_no':[101,102,999,101.5], 'presence':[1,9,1,1], 'origin':[1,1,1,1], 'seasonal':[4]*4}, geometry=[box(11.99,54.99,12.01,55.01)]*4, crs='EPSG:4326')
            output = root / 'passage.parquet'
            with patch('ark_pipeline.cli.species_passage.pyogrio.list_layers', return_value=[('ranges','Polygon')]), patch('ark_pipeline.cli.species_passage.pyogrio.read_info', return_value={'fields':['id_no','presence','origin','seasonal']}), patch('ark_pipeline.cli.species_passage.pyogrio.read_dataframe', return_value=frame):
                result = build(root, Path('config/spatial_semantics_iucn_richness_v3.toml'), species, output)
            rows = pq.read_table(output).to_pylist()
            self.assertEqual([row['gbif_accepted_id'] for row in rows], ['serving-101'])
            self.assertEqual(rows[0]['resolution'], 3)
            self.assertGreater(len(rows[0]['h3_indexes']), 0)
            self.assertEqual(result['accepted_rows'], 1)
            self.assertEqual(result['species_sha256'], sha256(species))
            self.assertEqual(result['scope'], 'species-range-overlay')
            # No name inference or numeric equality fallback if SIS evidence is absent.
            pq.write_table(pa.table({'gbif_accepted_id':['101']}), species)
            with self.assertRaisesRegex(ValueError, 'explicit iucn_sis_id'):
                build(root, Path('config/spatial_semantics_iucn_richness_v3.toml'), species, output)
            pq.write_table(pa.table({'gbif_accepted_id':['a','b'], 'iucn_sis_id':['101','101']}), species)
            with self.assertRaisesRegex(ValueError, 'Ambiguous'):
                build(root, Path('config/spatial_semantics_iucn_richness_v3.toml'), species, output)

    def test_source_mutation_never_replaces_previous_supplement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'acquisition').mkdir()
            source = root/'synthetic.zip'
            source.write_bytes(b'original')
            (root/'acquisition/current.json').write_text(json.dumps({'sources':{'iucn-spatial':{'files':[{'path':source.name,'bytes':8,'sha256':sha256(source),'logical_name':'fixture'}]}}}))
            species = root/'species.parquet'
            pq.write_table(pa.table({'gbif_accepted_id':['app'], 'iucn_sis_id':['101']}),species)
            output = root/'passage.parquet'
            output.write_bytes(b'previous selected supplement')
            with patch('ark_pipeline.cli.species_passage.pyogrio.list_layers', return_value=[('ranges','Polygon')]), patch('ark_pipeline.cli.species_passage.pyogrio.read_info', return_value={'fields':['id_no','presence','origin','seasonal']}), patch('ark_pipeline.cli.species_passage.pyogrio.read_dataframe', side_effect=lambda *a, **k: (source.write_bytes(b'replaced'), gpd.GeoDataFrame({'id_no':[], 'presence':[], 'origin':[], 'seasonal':[]},geometry=[],crs='EPSG:4326'))[1]):
                with self.assertRaisesRegex(ValueError,'changed during scan'):
                    build(root, Path('config/spatial_semantics_iucn_richness_v3.toml'), species, output)
            self.assertEqual(output.read_bytes(),b'previous selected supplement')
