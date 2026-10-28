from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import duckdb
import h3
import pyarrow as pa
import pyarrow.parquet as pq
from litestar import Litestar
from litestar.testing import TestClient

from ark_pipeline.aggregation.analytical_scores import aggregate_scores
from ark_pipeline.aggregation.membership_contract import canonical_memberships
from ark_pipeline.aggregation.species_lists import PAIR_SCHEMA, export_serving_lists, write_receipt
from ark_pipeline.builders.spatial_analytics import (
    build_jurisdictions,
    build_spatial_analytics,
    execute_cell_reduction,
    mapped_cell_geometries,
    support_summary,
)
from ark_pipeline.cli.spatial_analytics import build as publish_spatial_analytics
from ark_pipeline.runtime.provenance import sha256
from backend.spatial_analysis import (
    checked_artifact,
    checked_outputs,
    source_support_details,
    species_range_details,
)


class SpatialAnalyticsTests(unittest.TestCase):
    def test_reduction_restores_query_optimizer_settings_after_failure(self):
        with duckdb.connect() as connection:
            connection.execute("SET disabled_optimizers='filter_pushdown'")
            with self.assertRaises(duckdb.Error):
                execute_cell_reduction(connection, 'SELECT * FROM missing_table', True)
            self.assertEqual(connection.execute("SELECT current_setting('disabled_optimizers')").fetchone()[0], 'filter_pushdown')

    def test_batch_cell_geometries_preserve_exact_tile_boundaries(self):
        from ark_pipeline.tiles import geometries_for_cells

        cells = sorted({h3.latlng_to_cell(latitude, longitude, 7)
                        for latitude in (-90, -89.99, -55, 0, 55, 89.99, 90)
                        for longitude in (-180, -179.999, 0, 12, 179.999, 180)}
                       | set(h3.get_pentagons(7)))
        _, expected = geometries_for_cells(cells)
        actual = mapped_cell_geometries([int(cell, 16) for cell in cells])
        for cell, left, right in zip(cells, actual, expected, strict=True):
            self.assertTrue(left.equals_exact(right, 0), cell)

    def test_boundary_producer_preserves_overlaps_dateline_and_unique_cells(self):
        from shapely.geometry import box, mapping

        from ark_pipeline.spatial.boundaries import JurisdictionIndex

        with tempfile.TemporaryDirectory() as tmp, duckdb.connect() as c:
            root = Path(tmp)
            cells = [h3.latlng_to_cell(0, 179.999, 7), h3.latlng_to_cell(55, 12, 7)]
            path = root / 'memberships.parquet'
            pq.write_table(pa.table({'h3_cell': pa.array([int(v, 16) for v in cells * 2], type=pa.uint64()),
                                    'species_ids': [[1], [2], [1], [2]]}), path)
            boundary = root / 'boundary.geojson'
            boundary.write_text(json.dumps({'type': 'FeatureCollection', 'features': [
                {'type': 'Feature', 'properties': {'code': code, 'name': code},
                 'geometry': mapping(geometry)} for code, geometry in [
                    ('east', box(179, -1, 180, 1)), ('west', box(-180, -1, -179, 1)),
                    ('overlap', box(11, 54, 13, 56)), ('inside', box(11, 54, 13, 56))]]}))
            output = root / 'jurisdictions.parquet'
            result = build_jurisdictions(c, [path], {'country': boundary}, output, batch_cells=1)
            expected = {(int(cell, 16), 'country', code) for cell in cells
                        for code in JurisdictionIndex(boundary).codes_for_cell(cell)}
            actual = {(r['h3_index'], r['framework'], r['code']) for r in pq.read_table(output).to_pylist()}
            self.assertEqual(actual, expected)
            self.assertEqual(result['cells'], 2)
            self.assertEqual(result['jurisdiction_memberships'], len(expected))
            self.assertEqual({r[2] for r in actual}, {'east', 'west', 'overlap', 'inside'})
            parallel = root / 'parallel-jurisdictions.parquet'
            parallel_result = build_jurisdictions(c, [path], {'country': boundary}, parallel, batch_cells=1, workers=2)
            self.assertEqual(parallel_result, result)
            self.assertEqual(pq.read_table(parallel).to_pylist(), pq.read_table(output).to_pylist())
            memberships = root / 'lists'
            part = memberships / 'res7_merged_parts/part.parquet'
            part.parent.mkdir(parents=True)
            part.write_bytes(path.read_bytes())
            write_receipt(memberships / 'receipt.json', {'fixture': 1}, {'fine': part}, {})
            args = SimpleNamespace(h3_root=memberships, output_root=root / 'published',
                profile='fast-fixture', coverage_status='partial', jurisdictions=None,
                boundary_catalogue=[('country', boundary)], weights=None, omissions=None,
                threads=1, memory_limit='256MB', scratch_dir=root / 'scratch')
            published = publish_spatial_analytics(args)
            self.assertEqual(publish_spatial_analytics(args)['status'], 'reused')
            receipt = json.loads((Path(published['generation']) / 'receipt.json').read_text())
            self.assertIn('jurisdictions', receipt['outputs'])
            self.assertIn('boundary:country', receipt['identity']['optional'])
            self.assertEqual(pq.read_table(Path(published['generation']) / 'work/stewardship.parquet').num_rows, 4)
            boundary.write_text('{}')
            with self.assertRaises(ValueError):
                publish_spatial_analytics(args)
            self.assertEqual((args.output_root / 'current').resolve(), Path(published['generation']).resolve())

    def test_canonical_proof_and_generic_overlap_have_equal_results(self):
        with tempfile.TemporaryDirectory() as tmp, duckdb.connect() as c:
            root = Path(tmp)
            cells = sorted(int(v, 16) for v in h3.grid_disk(h3.latlng_to_cell(55, 12, 7), 1))
            base = (cells[0] >> 45) & 127
            canonical = root / f'base_{base}.parquet'
            table = pa.table({'h3_cell': pa.array(cells, type=pa.uint64()),
                              'species_ids': [[1, 2], [], [2], [1], [1, 3], [], [3]]})
            pq.write_table(table, canonical, row_group_size=2)
            self.assertTrue(canonical_memberships([canonical], 7))
            arbitrary = root / 'overlapping.parquet'
            pq.write_table(pa.concat_tables([table, table]), arbitrary)
            self.assertFalse(canonical_memberships([arbitrary], 7))
            self.assertFalse(canonical_memberships([canonical, canonical], 7))
            scores = root / 'scores.parquet'
            pq.write_table(pa.table({'iucn_sis_id': ['1', '2', '3'], 'priority': [1., 2., None],
                                    'baseline_incomplete': [False, False, True]}), scores)
            jurisdictions = root / 'jurisdictions.parquet'
            pq.write_table(pa.table({'h3_index': pa.array(cells + [cells[0], cells[0]], type=pa.uint64()),
                                    'framework': ['admin0'] * len(cells) + ['admin0', 'eez'],
                                    'code': ['a'] * len(cells) + ['a', 'b']}), jurisdictions)
            for label, paths in [('canonical', [canonical]), ('generic', [arbitrary])]:
                build_spatial_analytics(c, paths, root / label, profile='fast-fixture',
                    membership_generation='m', coverage_status='partial', jurisdictions=jurisdictions)
                aggregate_scores(c, paths, scores, root / f'{label}.parquet', resolution=7,
                    scenario_id='s', membership_generation='m', profile='fast-fixture')
            actual = pq.read_table(root / 'canonical/species_ranges.parquet').to_pylist()
            expected = pq.read_table(root / 'generic/species_ranges.parquet').to_pylist()
            for left, right in zip(actual, expected, strict=True):
                self.assertAlmostEqual(left.pop('global_mapped_area_km2'),
                                       right.pop('global_mapped_area_km2'), places=10)
                self.assertEqual(left, right)
            self.assertEqual(pq.read_table(root / 'canonical.parquet').to_pylist(),
                             pq.read_table(root / 'generic.parquet').to_pylist())
            for left, right in zip(pq.read_table(root / 'canonical/stewardship.parquet').to_pylist(),
                                   pq.read_table(root / 'generic/stewardship.parquet').to_pylist(), strict=True):
                self.assertAlmostEqual(left.pop('mapped_area_fraction'), right.pop('mapped_area_fraction'), places=12)
                self.assertEqual(left, right)
            for species in ([2, 1], [1, 1], [1, None]):
                pq.write_table(pa.table({'h3_cell': pa.array(cells[:1], type=pa.uint64()),
                                        'species_ids': [species]}), canonical)
                with self.assertRaisesRegex(ValueError, '[Cc]anonical'):
                    canonical_memberships([canonical], 7)
            for invalid in ([cells[0], cells[0]], list(reversed(cells)), [cells[0] ^ (1 << 45)]):
                pq.write_table(pa.table({'h3_cell': pa.array(invalid, type=pa.uint64()),
                                        'species_ids': [[1]] * len(invalid)}), canonical)
                with self.assertRaises(ValueError):
                    canonical_memberships([canonical], 7)

    def test_mixed_source_or_coarse_union_resume_ranges_and_stewardship(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cell = h3.latlng_to_cell(55, 12, 7)
            sibling = next(c for c in h3.cell_to_children(h3.cell_to_parent(cell, 6), 7) if c != cell)
            c1, c2 = (int(c, 16) for c in (cell, sibling))
            inputs = []
            for route, pairs in [('polygon', [(c1, 1), (c1, 1), (c2, 1)]), ('point', [(c1, 1), (c2, 2)]), ('hydrobasin', [(c2, 1), (c1, 3)])]:
                path = root / f'{route}.parquet'
                pq.write_table(pa.Table.from_pylist([dict(h3_index=c, iucn_sis_id=s) for c, s in pairs], schema=PAIR_SCHEMA), path)
                inputs.append(dict(logical_name=route, source_format=route, path=path, bytes=path.stat().st_size, sha256=sha256(path), rows=len(pairs)))
            opts = dict(scratch_dir=root / 'scratch', memory_limit='256MB', threads=1, archive_inputs=inputs, archive_identity={'fixture': 1})
            result = export_serving_lists(root / 'out', **opts)
            current = Path(result['root'])
            receipt = json.loads((current / 'receipt.json').read_text())
            self.assertEqual(result['res7_relationships'], 4)
            self.assertGreater(result['source_support_bytes'], 0)
            fine = list((current / 'source_support').glob('base_*.parquet'))
            coarse = list((current / 'source_support').glob('res3_base_*.parquet'))
            with duckdb.connect() as connection:
                scores = root / 'scores.parquet'
                pq.write_table(pa.table({'iucn_sis_id': ['1', '2', '3'],
                    'priority': [.125, None, .25], 'baseline_incomplete': [False, True, False]}), scores)
                for resolution, lists in ((7, list((current / 'res7_merged_parts').glob('*.parquet'))),
                                          (3, [current / 'h3_res3_species_global_merged.parquet'])):
                    aggregate_scores(connection, lists, scores, root / f'priority{resolution}.parquet',
                        resolution=resolution, scenario_id='scenario', membership_generation='m1', profile='fast-fixture')
                coarse_scores = pq.read_table(root / 'priority3.parquet').to_pylist()
                self.assertEqual(coarse_scores[0]['priority'], .375)
                self.assertEqual(coarse_scores[0]['unscored_species'], 1)
                self.assertEqual(coarse_scores[0]['baseline_incomplete_species'], 1)
                self.assertEqual(sum(r['priority'] for r in pq.read_table(root / 'priority7.parquet').to_pylist()), .5)
                summary = support_summary(connection, fine, c1)
                self.assertEqual((summary['species'], summary['polygon'], summary['point'], summary['hydrobasin'], summary['multiple']), (2, 1, 1, 1, 1))
                coarse_summary = support_summary(connection, coarse, int(h3.cell_to_parent(cell, 3), 16))
                self.assertEqual(coarse_summary['species'], 3)
                self.assertEqual(coarse_summary['multiple'], 1)
                boundary = root / 'boundaries.parquet'
                pq.write_table(pa.Table.from_pylist([dict(h3_index=c1, framework='adm0', code='X')] * 2), boundary)
                weights = root / 'weights.parquet'
                pq.write_table(pa.table({'iucn_sis_id': [1, 2, 3], 'weight': [2., 3., None]}), weights)
                omissions = root / 'omissions.parquet'
                pq.write_table(pa.table({'iucn_sis_id': [1, 1, 4], 'hybas_id': [10, 10, 11],
                    'reason': ['missing_geometry'] * 3}), omissions)
                built = build_spatial_analytics(connection, list((current / 'res7_merged_parts').glob('*.parquet')), root / 'analytical',
                    profile='fast-fixture', membership_generation='m1', coverage_status='partial', jurisdictions=boundary, weights=weights,
                    omissions=omissions)
                self.assertEqual(built['species'], 3)
                ranges = {r['iucn_sis_id']: r for r in pq.read_table(root / 'analytical/species_ranges.parquet').to_pylist()}
                self.assertEqual(ranges[1]['global_cell_count'], 2)
                self.assertEqual(ranges[1]['missing_basin_relationships'], 1)
                self.assertTrue(ranges[1]['affected_by_missing_basins'])
                self.assertFalse(ranges[2]['affected_by_missing_basins'])
                self.assertEqual({r['iucn_sis_id'] for r in pq.read_table(root / 'analytical/omitted_species.parquet').to_pylist()}, {1, 4})
                self.assertTrue(all(r['coverage_status'] == 'partial' for r in ranges.values()))
                fractions = {r['iucn_sis_id']: r for r in pq.read_table(root / 'analytical/stewardship.parquet').to_pylist()}
                self.assertEqual(fractions[1]['mapped_cell_fraction'], .5)
                for resolution in (3, 7):
                    rarity = pq.read_table(root / f'analytical/range_rarity_res{resolution}.parquet').to_pylist()
                    self.assertAlmostEqual(sum(r['range_rarity'] for r in rarity), 5.)
            analytical = root / 'analytical'
            write_receipt(analytical / 'receipt.json', {'fixture': 1},
                          {p.stem: p for p in analytical.glob('*.parquet')}, {})
            settings = SimpleNamespace(spatial_analysis_generation_path=analytical,
                                       membership_generation_path=current)
            checked_outputs.cache_clear()
            checked_artifact.cache_clear()
            with patch('backend.spatial_analysis.RequestSettings', return_value=settings), TestClient(
                Litestar([source_support_details, species_range_details]), raise_server_exceptions=False
            ) as client:
                response = client.get('/api/analysis/ranges/1')
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['global_cell_count'], 2)
                self.assertTrue(response.json()['affected_by_missing_basins'])
                self.assertEqual(response.json()['stewardship'][0]['mapped_cell_fraction'], .5)
                response = client.get(f'/api/analysis/cells/{cell}/support')
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['multiple'], 1)
                self.assertEqual(client.get('/api/analysis/ranges/1?per_page=101').status_code, 400)
                self.assertEqual(client.get('/api/analysis/cells/invalid/support').status_code, 400)
                self.assertEqual(client.get('/api/analysis/ranges/999').status_code, 404)
                settings.membership_generation_path = None
                self.assertEqual(client.get(f'/api/analysis/cells/{cell}/support').status_code, 503)
            checked_outputs.cache_clear()
            checked_artifact.cache_clear()
            self.assertEqual(export_serving_lists(root / 'out', **opts)['status'], 'reused')
            self.assertTrue(all(not name.startswith('support_') or 'source_support/' in item['filename'] for name, item in receipt['outputs'].items()))
            args = SimpleNamespace(h3_root=current, output_root=root / 'published',
                profile='fast-fixture', coverage_status='partial', jurisdictions=boundary,
                weights=weights, omissions=omissions, threads=1, memory_limit='256MB', scratch_dir=root / 'scratch2')
            first = publish_spatial_analytics(args)
            self.assertEqual(first['status'], 'built')
            self.assertEqual(publish_spatial_analytics(args)['status'], 'reused')
            published = (args.output_root / 'current').resolve()
            self.assertEqual(published, Path(first['generation']).resolve())
            boundary.write_bytes(b'broken source')
            with self.assertRaises(duckdb.Error):
                publish_spatial_analytics(args)
            self.assertEqual((args.output_root / 'current').resolve(), published)

    def test_wrong_resolution_jurisdictions_and_nullable_scores(self):
        with tempfile.TemporaryDirectory() as tmp, duckdb.connect() as c:
            root = Path(tmp)
            cell = h3.latlng_to_cell(55, 12, 7)
            memberships = root / 'lists.parquet'
            pq.write_table(pa.table({'h3_cell': [int(cell, 16)], 'species_ids': [[1, 2]]}), memberships)
            scores = root / 'scores.parquet'
            pq.write_table(pa.table({'iucn_sis_id': ['1'], 'priority': pa.array([None], type=pa.float64()),
                'baseline_incomplete': [True]}), scores)
            aggregate_scores(c, [memberships], scores, root / 'scores7.parquet', resolution=7,
                scenario_id='s', membership_generation='m', profile='fast-fixture')
            row = pq.read_table(root / 'scores7.parquet').to_pylist()[0]
            self.assertIsNone(row['priority'])
            self.assertEqual((row['scored_species'], row['unscored_species'], row['baseline_incomplete_species']), (0, 2, 1))
            boundaries = root / 'boundaries.parquet'
            pq.write_table(pa.table({'h3_index': [int(h3.cell_to_parent(cell, 3), 16)],
                'framework': ['adm0'], 'code': ['X']}), boundaries)
            with self.assertRaisesRegex(ValueError, 'resolution-7'):
                build_spatial_analytics(c, [memberships], root / 'invalid', profile='fast-fixture',
                    membership_generation='m', coverage_status='unknown', jurisdictions=boundaries)

    def test_fair_shares_sum_per_category_and_keep_unavailable_species(self):
        with tempfile.TemporaryDirectory() as tmp, duckdb.connect() as c:
            root = Path(tmp)
            cells = sorted(int(v, 16) for v in h3.grid_disk(h3.latlng_to_cell(55, 12, 7), 1))[:2]
            memberships = root / 'lists.parquet'
            pq.write_table(pa.table({'h3_cell': pa.array(cells, type=pa.uint64()),
                                     'species_ids': [[1, 2, 3, 4], [2, 5]]}), memberships)
            scores = root / 'scores.parquet'
            # '3' has no tree placement; '5' is absent from the scores.
            pq.write_table(pa.table({'iucn_sis_id': ['1', '2', '3', '4'],
                'priority': [1., None, None, 2.], 'baseline_incomplete': [False, True, False, False],
                'gap_share_upper_myr': [1.5, 2.25, None, 4.],
                'redlist_category': ['CR', 'CR', 'EN', 'LC']}), scores)
            result = aggregate_scores(c, [memberships], scores, root / 'shares7.parquet', resolution=7,
                scenario_id='s', membership_generation='m', profile='fast-fixture')
            self.assertEqual(result['identity']['aggregation'], 'distinct-individual-opportunities-v2-fair-share')
            first, second = pq.read_table(root / 'shares7.parquet').to_pylist()
            self.assertEqual((first['share_upper_cr_myr'], first['share_upper_en_myr'],
                              first['share_upper_lc_myr'], first['share_unavailable_species']), (3.75, 0, 4., 1))
            self.assertEqual((second['share_upper_cr_myr'], second['share_unavailable_species']), (2.25, 1))


if __name__ == '__main__':
    unittest.main()
