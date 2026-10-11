from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from ark_pipeline.builders.spatial_metadata import collect_spatial_metadata
from ark_pipeline.runtime.provenance import sha256


class SpatialMetadataTests(unittest.TestCase):
    def source(self, root, text):
        path = root / 'points_Terrestrial.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('points.csv', text)
        (root / 'acquisition').mkdir(exist_ok=True)
        (root / 'acquisition/current.json').write_text(json.dumps({'sources': {
            'iucn-spatial-tables': {'files': [dict(path=path.name, logical_name=path.name,
                                                  bytes=path.stat().st_size, sha256=sha256(path))]}
        }}))
        return path

    def test_conflicts_are_audited_and_realms_require_affirmative_attributes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.source(root, 'id_no,sci_name,family,category,marine,terrestria,freshwater\n'
                '9,First name,FAMILY,EN,0,0,1\n'
                '9,Second name,FAMILY,VU,1,0,0\n'
                '10,Point name,FAMILY,LC,,,\n'
                '11,Ignore me,FAMILY,LC,1,1,1\n')
            rows = collect_spatial_metadata(root, {'9', '10'})
            by_id = {row['gbif_accepted_id']: row for row in rows}
            self.assertEqual(set(by_id), {'9', '10'})
            self.assertIsNone(by_id['9']['species_name'])
            self.assertIsNone(by_id['9']['source_category'])
            self.assertEqual(json.loads(by_id['9']['conflicts']), ['sci_name', 'category'])
            self.assertEqual(by_id['9']['systems'], ['Freshwater', 'Marine'])
            # Terrestrial in a file title must not fill absent point attributes.
            self.assertEqual(by_id['10']['systems'], [])
            self.assertIn('points.csv', by_id['10']['provenance'])

    def test_registered_source_change_fails_instead_of_silently_recovering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.source(root, 'id_no,sci_name\n9,First name\n')
            path.write_bytes(path.read_bytes() + b'changed')
            with self.assertRaisesRegex(ValueError, 'source changed'):
                collect_spatial_metadata(root, {'9'})
