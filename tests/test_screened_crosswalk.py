from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb
import pyarrow as pa

from ark_pipeline.cli.crosswalk_refresh import refresh
from ark_pipeline.cli.screened_crosswalk import (
    BINDINGS,
    FILES,
    POLICY,
    SOURCE,
    counts,
    source_bindings,
)
from ark_pipeline.cli.serving_metadata import validate_crosswalk_sources
from ark_pipeline.cli.sources_acquire import atomic_json, load_manifest, sha256


def fixture(root: Path):
    manifest = {'schema_version': 1, 'sources': {}}
    for source_id, names in BINDINGS.items():
        record = {'validation_status': 'passed', 'release': 'fixture', 'files': []}
        for name in names:
            path = root / source_id / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f'synthetic {source_id} {name}')
            record['files'].append({'logical_name': name, 'path': str(path.relative_to(root)),
                                    'sha256': sha256(path), 'bytes': path.stat().st_size})
        manifest['sources'][source_id] = record
    package = root / 'screened-package'
    package.mkdir()
    artifact = package / FILES[0]
    with duckdb.connect() as con:
        con.from_arrow(pa.Table.from_pylist([{
            'iucn_sis_id': 101, 'match_status': 'MATCHED', 'matched_ncbi_species_taxid': '10',
            'safe_for_automatic_species_trait_transfer': True, 'ncbi_identifier_status': 'CURRENT',
            'current_ncbi_rank': 'species', 'col_xr_concept_status': 'shared_name_concept',
            'iucn_catalogue_presence': 'SHARED', 'goat_iucn_sis_ids': [101],
        }])).write_parquet(str(artifact))
    summary = {'screening_policy': POLICY, 'crosswalk_sha256': sha256(artifact),
               'registered_sources': source_bindings(manifest), 'counts': counts(artifact)}
    atomic_json(package / 'match_summary.json', summary)
    atomic_json(package / 'screening_receipt.json', {
        'status': 'passed', 'crosswalk_sha256': sha256(artifact),
        'summary_sha256': sha256(package / 'match_summary.json'),
    })
    atomic_json(package / 'provenance.json', {'policy': 'synthetic fixture'})
    manifest['sources'][SOURCE] = {'validation_status': 'passed', 'release': 'fixture', 'files': [
        {'logical_name': name, 'path': str((package / name).relative_to(root)),
         'sha256': sha256(package / name), 'bytes': (package / name).stat().st_size}
        for name in FILES
    ]}
    atomic_json(root / 'acquisition/current.json', manifest)
    return package


class ScreenedCrosswalkTests(unittest.TestCase):
    def test_rebuild_selects_and_reuses_screened_product_without_matching(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            with patch('ark_pipeline.cli.crosswalk_refresh.subprocess.run', side_effect=AssertionError('old matcher ran')):
                result = refresh(root, root / 'derived', memory_limit='256MB', threads=1)
                self.assertEqual(result['status'], 'selected-screened')
                path = Path(result['crosswalk'])
                validate_crosswalk_sources(path, load_manifest(root))
                reused = refresh(root, root / 'derived', memory_limit='256MB', threads=1)
                self.assertEqual(reused['status'], 'reused-screened')
                self.assertEqual(reused['crosswalk'], result['crosswalk'])

    def test_changed_sources_stop_refresh_and_serving_without_fallback(self):
        for source_id in BINDINGS:
            with self.subTest(source=source_id), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                fixture(root)
                result = refresh(root, root / 'derived', memory_limit='256MB', threads=1)
                current = (root / 'derived/current').resolve()
                manifest = load_manifest(root)
                manifest['sources'][source_id]['files'][0]['sha256'] = 'changed'
                atomic_json(root / 'acquisition/current.json', manifest)
                with patch('ark_pipeline.cli.crosswalk_refresh.subprocess.run', side_effect=AssertionError('fallback ran')):
                    with self.assertRaisesRegex(ValueError, 'sources changed'):
                        refresh(root, root / 'derived', memory_limit='256MB', threads=1)
                with self.assertRaisesRegex(ValueError, 'sources changed'):
                    validate_crosswalk_sources(Path(result['crosswalk']), manifest)
                self.assertEqual((root / 'derived/current').resolve(), current)

    def test_damaged_generation_is_repaired_once_and_then_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            first = refresh(root, root / 'derived', memory_limit='256MB', threads=1)
            Path(first['crosswalk']).with_name('provenance.json').write_text('damaged')
            repaired = refresh(root, root / 'derived', memory_limit='256MB', threads=1)
            self.assertNotEqual(first['crosswalk'], repaired['crosswalk'])
            reused = refresh(root, root / 'derived', memory_limit='256MB', threads=1)
            self.assertEqual(repaired['crosswalk'], reused['crosswalk'])

    def test_explicit_supplement_cannot_be_silently_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            with self.assertRaisesRegex(ValueError, 'newly screened'):
                refresh(root, root / 'derived', memory_limit='256MB', threads=1,
                        goat_provenance=root / 'other.json')

    def test_receipt_tampering_is_rejected_by_serving_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package = fixture(root)
            (package / 'screening_receipt.json').write_text(json.dumps({'status': 'failed'}))
            with self.assertRaisesRegex(ValueError, 'receipt/output mismatch'):
                validate_crosswalk_sources(package / FILES[0], load_manifest(root))

    def test_changed_raw_file_is_detected_even_without_manifest_update(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            refresh(root, root / 'derived', memory_limit='256MB', threads=1)
            current = (root / 'derived/current').resolve()
            (root / 'col-xr/col-xr-dwca.zip').write_text('changed raw input')
            with self.assertRaises(ValueError):
                refresh(root, root / 'derived', memory_limit='256MB', threads=1)
            self.assertEqual((root / 'derived/current').resolve(), current)

    def test_unscreened_override_cannot_bypass_registered_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            legacy = root / 'legacy'
            legacy.mkdir()
            (legacy / FILES[0]).write_bytes(b'synthetic')
            atomic_json(legacy / 'match_summary.json', {'sources': {}})
            with self.assertRaisesRegex(ValueError, 'screened crosswalk is registered'):
                validate_crosswalk_sources(legacy / FILES[0], load_manifest(root))

    def test_private_cloud_staging_includes_the_registered_pack(self):
        module_path = Path(__file__).resolve().parents[1] / 'sandbox/coreweave/scripts/cloud_storage.py'
        spec = importlib.util.spec_from_file_location('screened_storage_test', module_path)
        storage = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(storage)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package = fixture(root)
            staged = {item['path'] for item in storage.input_plan(root)}
            self.assertTrue({str((package / name).relative_to(root)) for name in FILES}.issubset(staged))


if __name__ == '__main__':
    unittest.main()
