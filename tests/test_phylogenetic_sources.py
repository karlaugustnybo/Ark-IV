from __future__ import annotations

import tempfile
import tomllib
import unittest
from pathlib import Path

from ark_pipeline.cli.sources_acquire import (
    Source,
    is_due,
    load_catalogue,
    request_headers,
    validate_input_files,
)
from ark_pipeline.runtime.provenance import sha256


class PhylogeneticSourceTests(unittest.TestCase):
    def test_pinned_text_downloads_accept_provider_mime_type_and_keep_conditionals(self):
        headers = request_headers({'remote': {'etag': 'pinned', 'last_modified': 'yesterday'}})
        self.assertEqual(headers['Accept'], '*/*')
        self.assertEqual(headers['If-None-Match'], 'pinned')
        self.assertEqual(headers['If-Modified-Since'], 'yesterday')

    def test_optional_registered_pins_match_existing_reviewed_prototype_inputs(self):
        root = Path(__file__).resolve().parents[1]
        catalogue = load_catalogue(root / 'config/data_sources.toml')
        prototype = tomllib.loads((root / 'config/edge_prototype.toml').read_text())
        for source in prototype['sources']:
            if source['id'] not in ('dated-tree', 'ott-taxonomy'):
                continue
            registered = catalogue.sources[source['id']]
            self.assertFalse(registered.get('required'))
            self.assertEqual(registered.get('file_sha256')[source['filename']], source['sha256'])
            self.assertEqual(registered.get('download_url'), source['url'])
            self.assertNotIn(source['id'], catalogue.profiles['authorized']['required_sources'])

    def test_checksum_is_verified_before_registration_and_changed_pins_are_due(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'tree.tre'
            path.write_text('(a:1,b:1):0;')
            digest = sha256(path)
            source = Source(dict(id='tree', required_files=['tree.tre'], update_policy='pinned',
                                 release='r1', file_sha256={'tree.tre': digest}))
            self.assertEqual(validate_input_files(source, {'tree.tre': path}), [])
            current = dict(release='r1', files=[dict(logical_name='tree.tre', sha256=digest)])
            self.assertFalse(is_due(source, current, False))
            current['files'][0]['sha256'] = 'changed'
            self.assertTrue(is_due(source, current, False))
            path.write_text('(a:2,b:2):0;')
            self.assertTrue(any('pinned checksum' in s for s in validate_input_files(source, {'tree.tre': path})))


if __name__ == '__main__':
    unittest.main()
