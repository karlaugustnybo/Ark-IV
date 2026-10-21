from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from shapely.geometry import MultiPolygon, Polygon, box

from ark_pipeline.cli.scoring_compare import compare
from ark_pipeline.cli.scoring_rehearsal import run as robustness_rehearsal
from ark_pipeline.cli.spatial_calibrate import calibrate, repair_effects
from ark_pipeline.spatial.coverage import load_spatial_profile

ROOT = Path(__file__).parents[1]
FAST = ROOT / 'config/spatial_semantics_iucn_richness_v3_fast.toml'
CORRECT = ROOT / 'config/spatial_semantics_iucn_richness_v3.toml'


class ScoringReportTests(unittest.TestCase):
    def test_repair_sensitivity_is_separate_and_preserves_production_rejection(self):
        profile = load_spatial_profile(FAST)
        control = repair_effects(box(12, 55, 12.02, 55.02), profile)
        self.assertEqual(control['status'], 'compared')
        self.assertEqual(control['jaccard'], 1)
        overlapping = MultiPolygon([box(12, 55, 13, 56), box(12.92, 55.3, 13.5, 55.6)])
        result = repair_effects(overlapping, profile)
        self.assertEqual(result['status'], 'compared')
        self.assertFalse(result['production_repair']['original_valid'])
        self.assertEqual(result['simplification_degrees'], 0)
        self.assertGreater(result['alternative_added_cells'], 0)
        rejected = repair_effects(Polygon([(12, 55), (13, 56), (12, 56), (13, 55)]), profile)
        self.assertEqual(rejected['status'], 'production-rejected')
        with self.assertRaisesRegex(ValueError, 'FAST-only'):
            repair_effects(overlapping, load_spatial_profile(CORRECT))

    def test_rehearsal_covers_nonconstant_ranks_source_subsets_and_unmapped_species(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = robustness_rehearsal(Path(tmp) / 'matrix')
            self.assertEqual(result['status'], 'passed')
            self.assertEqual(result['species'], 96)
            self.assertGreater(result['fine_cells'], 500)
            self.assertGreater(result['unmapped_assessed_species'], 0)
            self.assertIn('polygon-only', result['scenarios'])
            self.assertIn('range-rarity', result['scenarios'])
            self.assertTrue(any(cohorts['all']['spearman_rank_correlation'] is not None
                                for cohorts in result['comparisons'].values()))
            self.assertTrue(any(cohorts['all']['top_percent_overlap']['10']['fraction'] < 1
                                for cohorts in result['comparisons'].values()))

    def test_common_cohorts_strata_ties_and_duplicate_guards(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            left, right = (root / f'{name}.parquet' for name in ('left', 'right'))
            pq.write_table(pa.table({'id': ['a', 'b', 'c'], 'priority': [2., 2., None], 'scope': ['land'] * 3}), left)
            pq.write_table(pa.table({'id': ['a', 'b', 'd'], 'priority': [2., 2., 8.], 'scope': ['land', 'land', 'sea']}), right)
            options = dict(identifier='id', score='priority', stratum='scope', reference_generation='r', alternative_generation='a')
            report = compare(left, right, root / 'comparison.json', **options)
            self.assertEqual(report['cohorts']['all']['common_cohort'], 2)
            self.assertEqual(report['cohorts']['stratum:sea']['common_cohort'], 0)
            self.assertIsNone(report['cohorts']['all']['spearman_rank_correlation'])
            self.assertEqual(report['cohorts']['all']['top_percent_overlap']['1']['fraction'], 1)
            with self.assertRaisesRegex(ValueError, 'row budget'):
                compare(left, right, root / 'bounded.json', max_rows=2, **options)
            pq.write_table(pa.table({'id': ['a', 'a'], 'priority': [1., 2.], 'scope': ['land'] * 2}), right)
            with self.assertRaisesRegex(ValueError, 'unique'):
                compare(left, right, root / 'duplicates.json', **options)

    def test_fast_calibration_and_correct_path_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # The guard must fail before reading inputs or creating outputs.
            with self.assertRaisesRegex(ValueError, 'explicit authorization'):
                calibrate(root / 'missing.parquet', CORRECT, root / 'forbidden')
            self.assertFalse((root / 'forbidden').exists())
            sample = root / 'sample.parquet'
            shapes = [box(12, 55, 12.02, 55.02),
                MultiPolygon([box(12, 55, 12.005, 55.005), box(12.01, 55.01, 12.015, 55.015)]),
                Polygon([(12, 55), (12.02, 55), (12.02, 55.02), (12, 55.02)],
                    holes=[[(12.005, 55.005), (12.015, 55.005), (12.015, 55.015), (12.005, 55.015)]]),
                Polygon([(179.99, 10), (-179.99, 10), (-179.99, 10.01), (179.99, 10.01)])]
            pq.write_table(pa.table({'iucn_sis_id': [1, 2, 3, 4], 'geometry_wkb': [s.wkb for s in shapes]}), sample)
            report = calibrate(sample, FAST, root / 'fast')
            self.assertEqual(report['coverage_mode'], 'fast')
            self.assertEqual(len(report['species']), 4)
            self.assertEqual(report['scientific_acceptance'], 'not_established')
            self.assertTrue(all(r['selected_cells'] > 0 for r in report['species']))
            self.assertEqual(pq.read_schema(root / 'fast/selected.parquet').names, ['iucn_sis_id', 'h3_index'])


if __name__ == '__main__':
    unittest.main()
