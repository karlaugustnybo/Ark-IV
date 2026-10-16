"""Exercise the real Bun acquisition CLI with fully offline search responses."""
from __future__ import annotations

import csv
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

HARNESS = r'''
const destination = process.argv[1];
const stop = process.env.FIXTURE_STOP === '1';
const onePage = process.env.FIXTURE_ONE_PAGE === '1';
process.argv = ['bun', 'sources_download_goat.mjs', destination];
const item = (id) => ({ result: { taxon_id: id, taxon_rank: 'species',
  scientific_name: 'Fixture ' + id,
  lineage: [{taxon_rank: 'family', scientific_name: 'Fixturefamily'}],
  fields: { sample_acquired: {value: 'fixture-voucher', aggregation_source: 'direct'},
    redlist_category: {value: 'VU', aggregation_source: 'direct',
      rawValues: [{metadata: {source: {slug: '101/1101', date: '2099-01-01'}}}]} } }});
globalThis.fetch = async (url, options) => {
  const request = JSON.parse(options.body);
  const params = JSON.parse(request.params_yaml);
  const query = JSON.parse(request.query_yaml);
  if (!query.fields.some((field) => field.name === 'redlist_category')) throw Error('Missing field');
  if (!params.include_estimates || !params.include_lineage) throw Error('Missing provenance settings');
  if (params.search_after && stop) return new Response('fixture interruption', {status: 400});
  const results = params.size === 1 ? [] :
    onePage ? [item('fixtureA'), item('fixtureB')] :
    [item(params.search_after ? 'fixtureB' : 'fixtureA')];
  return new Response(JSON.stringify({status: {success: true, hits: 2}, results,
    search_after: ['fixtureA']}), {headers: {'content-type': 'application/json'}});
};
await import('./ark_pipeline/cli/sources_download_goat.mjs');
'''


class GoatDownloadTests(unittest.TestCase):
    def run_download(self, output: Path, *, stop: bool = False, one_page: bool = False):
        return subprocess.run(
            ['bun', '-e', HARNESS, str(output)], capture_output=True, text=True,
            env={**os.environ, 'GOAT_PAGE_SIZE': '2', 'GOAT_MAX_ATTEMPTS': '1',
                 'FIXTURE_STOP': '1' if stop else '0',
                 'FIXTURE_ONE_PAGE': '1' if one_page else '0'},
        )

    def assert_output(self, output):
        with output.open() as handle:
            rows = list(csv.DictReader(handle, delimiter='\t'))
        self.assertEqual([row['taxon_id'] for row in rows], ['fixtureA', 'fixtureB'])
        self.assertEqual(rows[0]['family'], 'Fixturefamily')
        self.assertEqual(rows[0]['redlist_category'], 'VU')
        provenance = json.loads(rows[0]['field_provenance_json'])
        self.assertEqual(provenance['sample_acquired']['aggregation_source'], 'direct')
        self.assertNotIn('value', provenance['sample_acquired'])
        self.assertEqual(provenance['redlist_category']['rawValues'][0]['metadata']['source']['slug'], '101/1101')
        self.assertFalse(Path(str(output) + '.checkpoint.json').exists())
        self.assertFalse(Path(str(output) + '.lock').exists())

    def test_fresh_cli_completes_and_exports_field_provenance(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'goat.tsv'
            result = self.run_download(output, one_page=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['rows'], 2)
            self.assert_output(output)

    def test_cli_resumes_committed_page_and_preserves_previous_output_on_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'goat.tsv'
            output.write_text('previous selected snapshot')
            first = self.run_download(output, stop=True)
            self.assertNotEqual(first.returncode, 0)
            self.assertEqual(output.read_text(), 'previous selected snapshot')
            second = self.run_download(output)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn('Resuming after 1 rows', second.stderr)
            self.assert_output(output)
