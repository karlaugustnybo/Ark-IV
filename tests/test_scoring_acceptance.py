"""A synthetic FAST generation across real acquisition/identity/analysis stages."""

from __future__ import annotations

import contextlib
import csv
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import tomllib
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
from ark_pipeline.builders.fine_metrics import (
    aggregate_part,
    prepare_species,
    record_aggregate_receipt,
)
from ark_pipeline.cli.analytical_prepare import build as opportunities
from ark_pipeline.cli.analytical_prepare import parser as opportunity_parser
from ark_pipeline.cli.benchmark_pipeline import BOUNDARY_ENV
from ark_pipeline.cli.crosswalk_refresh import refresh
from ark_pipeline.cli.dna_prepare import build as dna
from ark_pipeline.cli.edge_link import run as link_tree
from ark_pipeline.cli.edge_link_expand import run as expand_links
from ark_pipeline.cli.phylogenetic_prepare import build as phylogenetic
from ark_pipeline.cli.phylogenetic_prepare import parser as phylogenetic_parser
from ark_pipeline.cli.scoring_compare import compare
from ark_pipeline.cli.serving_metadata import main as metadata
from ark_pipeline.cli.spatial_aggregate import main as aggregate_memberships
from ark_pipeline.cli.spatial_analytics import build as ranges
from ark_pipeline.runtime.provenance import atomic_json, sha256
from backend.analysis import analytical_engine, phylogenetic_scenario
from backend.dna_analysis import completed_dna_scenario, dna_evidence_scenario, load_dna_generation
from backend.spatial_analysis import (
    checked_artifact,
    checked_outputs,
    source_support_details,
    species_range_details,
)
from tests import test_refresh_crosswalk, test_spatial_pipeline
from tests.test_tile_export import boundary_fixture

HAS_ENGINE = importlib.util.find_spec('dated_complete_tree') is not None
FAST_PROFILE = Path(__file__).resolve().parents[1] / 'config/spatial_semantics_iucn_richness_v3_fast.toml'


def tree_inputs(root: Path, crosswalk: Path) -> tuple[Path, Path]:
    inputs = root / 'tree-inputs'
    inputs.mkdir()
    labels = {10: 'Panthera leo', 11: 'Panthera tigris', 12: 'Panthera ambigua',
              14: 'Panthera sherperdi', 15: 'Panthera deepwaterus', 16: 'Panthera stonewallus'}
    tree = inputs / 'tree.tre'
    tree.write_text('(' + ','.join(f'{name.replace(" ", "_")}_ott{100+i}:1' for i, name in labels.items()) + ')Mammalia_ott244265:0;')
    fields = ['uid', 'parent_uid', 'name', 'rank', 'sourceinfo', 'uniqname', 'flags']
    taxonomy = [dict(zip(fields, row)) for row in [
        ['1', '', 'Life', 'no rank', '', '', ''],
        ['2', '1', 'Animalia', 'kingdom', '', '', ''],
        ['3', '2', 'Chordata', 'phylum', '', '', ''],
        ['244265', '3', 'Mammalia', 'class', '', '', ''],
        ['5', '244265', 'Carnivora', 'order', '', '', ''],
        ['6', '5', 'Felidae', 'family', '', '', ''],
        ['7', '6', 'Panthera', 'genus', '', '', ''],
        *[[str(100+i), '7', name, 'species', '' if i == 11 else f'ncbi:{i}', '', ''] for i, name in labels.items()]]]
    raw = ('\t|\t'.join(fields) + '\t|\n' + ''.join('\t|\t'.join(row[f] for f in fields) + '\t|\n' for row in taxonomy)).encode()
    archive = inputs / 'ott.tgz'
    with tarfile.open(archive, 'w:gz') as package:
        info = tarfile.TarInfo('ott/taxonomy.tsv')
        info.size = len(raw)
        package.addfile(info, io.BytesIO(raw))
    engine_pin = tomllib.loads((FAST_PROFILE.parent / 'edge_prototype.toml').read_text())['engine_commit']
    config = inputs / 'config.toml'
    config.write_text(f'clade="Mammalia_ott244265"\nengine_commit="{engine_pin}"\n' + ''.join(
        f'\n[[sources]]\nid="{source}"\nfilename="{path.name}"\nrelease="synthetic"\nurl="https://example.invalid/unused"\nsha256="{sha256(path)}"\n'
        for source, path in [('dated-tree', tree), ('ott-taxonomy', archive)]))
    baseline = root / 'tree-links'
    link_tree(SimpleNamespace(config=config, input_dir=inputs, root=root, crosswalk=crosswalk,
        output_dir=baseline, download=False))
    expanded = root / 'expanded-links'
    expand_links(SimpleNamespace(config=config, input_dir=inputs, root=root, baseline_dir=baseline,
        output_dir=expanded))
    manifest_path = root / 'acquisition/current.json'
    manifest = json.loads(manifest_path.read_text())
    for source, path in [('dated-tree', tree), ('ott-taxonomy', archive)]:
        manifest['sources'][source] = dict(validation_status='passed', release='synthetic', files=[dict(
            logical_name=path.name, path=str(path.relative_to(root)), bytes=path.stat().st_size, sha256=sha256(path))])
    atomic_json(manifest_path, manifest)
    return config, expanded


@unittest.skipUnless(HAS_ENGINE, 'Optional engine: uv sync --group edge')
@unittest.skipUnless(shutil.which('tippecanoe'), 'Tippecanoe required for full acceptance artifacts')
class ScoringAcceptanceTests(unittest.TestCase):
    def test_mixed_fast_generation_to_metadata_scenarios_ranges_and_presets(self):
        with contextlib.redirect_stdout(io.StringIO()):
            test_spatial_pipeline.SpatialBuildTests()._mixed_source_contract(FAST_PROFILE, self.verify_generation)

    def verify_generation(self, root: Path, spatial: Path, profile):
        started = time.monotonic()
        # Retain all registered spatial routes while adding tiny authoritative
        # assessment/taxonomy inputs through the existing matcher fixture.
        manifest_path = root / 'acquisition/current.json'
        spatial_sources = json.loads(manifest_path.read_text())['sources']
        test_refresh_crosswalk.fixture(root)
        assessment = root / 'sources/assessments.csv'
        with assessment.open() as handle:
            reader = csv.DictReader(handle)
            assessment_fields, assessment_rows = reader.fieldnames, list(reader)
        with assessment.open('w') as handle:
            writer = csv.DictWriter(handle, assessment_fields + ['possiblyExtinct', 'possiblyExtinctInTheWild'])
            writer.writeheader()
            writer.writerows({**row, 'possiblyExtinct': 'false', 'possiblyExtinctInTheWild': 'false'} for row in assessment_rows)
        goat = root / 'sources/tol_species_all_ranks.tsv'
        with goat.open() as handle:
            reader = csv.DictReader(handle, delimiter='\t')
            fields, rows = reader.fieldnames + ['class', 'family', 'genus'], list(reader)
        for row in rows:
            row.update({'class': 'Mammalia', 'family': 'Felidae', 'genus': 'Panthera'})
            if row['taxon_id'] == '10':
                row['sample_acquired'] = 'voucher'
            if row['taxon_id'] in ('11', '16'):
                row['assembly_level'] = 'Contig'
        with goat.open('w') as handle:
            writer = csv.DictWriter(handle, fields, delimiter='\t')
            writer.writeheader()
            writer.writerows(rows)
        test_refresh_crosswalk.register(root)
        manifest = json.loads(manifest_path.read_text())
        manifest['sources'].update(spatial_sources)
        atomic_json(manifest_path, manifest)
        crosswalk = Path(refresh(root, root / 'crosswalk', memory_limit='256MB', threads=1)['crosswalk'])
        with contextlib.redirect_stdout(io.StringIO()) as diagnostics:
            status = aggregate_memberships(['run', '--root', str(root), '--output-root', str(spatial),
                '--profile', str(FAST_PROFILE), '--scratch-dir', str(root / 'scratch'),
                '--memory-limit', '256MB', '--threads', '1'])
        self.assertEqual(status, 0, diagnostics.getvalue())
        memberships = (spatial / 'serving/current').resolve()
        protected = {p: sha256(p) for p in memberships.rglob('*') if p.is_file()}
        self.assertEqual(metadata(['--root', str(root), '--h3-root', str(memberships),
            '--crosswalk', str(crosswalk), '--output-dir', str(root / 'metadata'),
            '--profile', str(FAST_PROFILE), '--iucn-metadata-mode', 'off']), 0)
        species_path = root / 'metadata/species.parquet'
        species = pq.read_table(species_path)
        self.assertTrue({'iucn_assessment_date', 'iucn_attributes_source_sha256'}.issubset(species.column_names))
        self.assertTrue({'201', '204'}.issubset(set(species['iucn_sis_id'].to_pylist())))
        protected[species_path] = sha256(species_path)
        # Build the actual categorical fine path before optional enrichment.
        # Its files and metric projection must survive downstream processing.
        base_metrics = root / 'base-metrics'
        base_metrics.mkdir()
        with duckdb.connect() as connection:
            connection.execute("SET threads=1; SET memory_limit='256MB'")
            prepare_species(connection, species_path, root / 'metadata/species_systems.parquet')
            for part in sorted((memberships / 'res7_merged_parts').glob('*.parquet')):
                target = base_metrics / part.name
                aggregate_part(connection, part, target)
                record_aggregate_receipt(SimpleNamespace(species=species_path,
                    species_systems=root / 'metadata/species_systems.parquet'), part, target)
                protected[target] = sha256(target)
        for path in self.base_artifacts(root, memberships, crosswalk, base_metrics):
            protected[path] = sha256(path)
        dna_result = dna(root, crosswalk, root / 'dna')
        config, links = tree_inputs(root, crosswalk)
        tree_args = phylogenetic_parser().parse_args(['--root', str(root), '--crosswalk', str(crosswalk),
            '--ott-index', str(root / 'tree-inputs/ott_index.parquet'), '--links-dir', str(links),
            '--tree-tips', str(root / 'tree-links/global_tree_tips.parquet'), '--config', str(config),
            '--risk-samples', '2', '--output-root', str(root / 'phylogenetic')])
        tree_result = phylogenetic(tree_args)
        self.assertEqual(tree_result['tree_tips'], 6)
        self.assertEqual(phylogenetic(tree_args)['status'], 'reused')
        preset_args = opportunity_parser().parse_args(['--phylogenetic-generation', tree_result['generation'],
            '--h3-root', str(memberships), '--profile', profile.profile_id,
            '--output-root', str(root / 'presets'), '--scratch-dir', str(root / 'scratch/presets')])
        preset = opportunities(preset_args)
        self.assertEqual(opportunities(preset_args)['status'], 'reused')
        default_current = (root / 'presets/current').resolve()
        preset_args.rule = 'any-assembly'
        def interrupt_fine(*args, **kwargs):
            if kwargs['resolution'] == 7:
                raise RuntimeError('interrupted fine aggregate')
            return aggregate_scores(*args, **kwargs)
        with patch('ark_pipeline.cli.analytical_prepare.aggregate_scores', side_effect=interrupt_fine):
            with self.assertRaisesRegex(RuntimeError, 'interrupted fine'):
                opportunities(preset_args)
        self.assertEqual((root / 'presets/current').resolve(), default_current)
        alternative = opportunities(preset_args)
        self.assertNotEqual(alternative['weighted_scenario_id'], preset['weighted_scenario_id'])
        comparison = {}
        for name, identifier in [('species_priorities', 'iucn_sis_id'), ('opportunities_res3', 'h3_index')]:
            comparison[name] = compare(Path(preset['generation']) / f'{name}.parquet',
                Path(alternative['generation']) / f'{name}.parquet', root / f'{name}-comparison.json',
                identifier=identifier, score='priority', reference_generation=preset['weighted_scenario_id'],
                alternative_generation=alternative['weighted_scenario_id'])['cohorts']['all']
        preset_args.profile = 'wrong-profile'
        with self.assertRaisesRegex(ValueError, 'profile differs'):
            opportunities(preset_args)
        preset_args.profile = profile.profile_id
        fine_paths = sorted((memberships / 'res7_merged_parts').glob('*.parquet'))
        with duckdb.connect() as connection:
            cell = connection.execute('SELECT h3_cell FROM read_parquet(?) LIMIT 1', [list(map(str, fine_paths))]).fetchone()[0]
        boundary = root / 'jurisdictions.parquet'
        pq.write_table(pa.table({'h3_index': pa.array([cell], type=pa.uint64()), 'framework': ['adm0'], 'code': ['SYN']}), boundary)
        range_result = ranges(SimpleNamespace(h3_root=memberships, output_root=root / 'ranges',
            profile=profile.profile_id, coverage_status='partial', jurisdictions=boundary, weights=None,
            omissions=None, threads=1, memory_limit='256MB', scratch_dir=root / 'scratch/ranges'))
        settings = SimpleNamespace(phylogenetic_generation_path=Path(tree_result['generation']),
            dna_generation_path=Path(dna_result['generation']), spatial_analysis_generation_path=Path(range_result['generation']),
            membership_generation_path=memberships)
        caches = (analytical_engine, completed_dna_scenario, load_dna_generation, checked_outputs, checked_artifact)
        for cache in caches:
            cache.cache_clear()
        try:
            with patch('backend.analysis.RequestSettings', return_value=settings), \
                 patch('backend.dna_analysis.RequestSettings', return_value=settings), \
                 patch('backend.spatial_analysis.RequestSettings', return_value=settings), \
                 TestClient(Litestar([phylogenetic_scenario, dna_evidence_scenario,
                     source_support_details, species_range_details]), raise_server_exceptions=False) as client:
                default = client.get('/api/analysis/phylogenetic').json()
                assembly_response = client.get('/api/analysis/phylogenetic?rule=any-assembly&species_id=102')
                self.assertEqual(assembly_response.status_code, 200, assembly_response.text)
                assembly = assembly_response.json()
                # This assessed Contig-only relative has a reviewed name bridge,
                # no direct NCBI tree ID and no website-qualifying evidence.
                self.assertEqual(default['represented_tips'], 1)
                self.assertEqual(assembly['represented_tips'], 2)
                self.assertTrue(assembly['rows'][0]['represented'])
                self.assertEqual(assembly['rows'][0]['priority'], 0.)
                dna_page = client.get('/api/analysis/dna?rule=any-assembly&species_id=102').json()
                self.assertTrue(dna_page['rows'][0]['qualifies'])
                self.assertEqual(client.get('/api/analysis/ranges/101').status_code, 200)
                support = client.get(f'/api/analysis/cells/{h3.int_to_str(cell)}/support')
                self.assertEqual(support.status_code, 200, support.text)
                self.assertGreater(support.json()['species'], 0)
                disk = pq.read_table(Path(preset['generation']) / 'species_priorities.parquet').to_pylist()
                self.assertEqual({r['iucn_sis_id']: r['priority'] for r in disk},
                                 {r['iucn_sis_id']: r['priority'] for r in default['rows']})
        finally:
            for cache in caches:
                cache.cache_clear()
        self.assertEqual(protected, {p: sha256(p) for p in protected})
        # Input tampering must fail before moving either downstream current.
        before = (root / 'presets/current').resolve()
        extra = memberships / 'res7_merged_parts/unreceipted.parquet'
        pq.write_table(pq.read_table(fine_paths[0]), extra)
        with self.assertRaisesRegex(ValueError, 'unreceipted'):
            opportunities(preset_args)
        self.assertEqual((root / 'presets/current').resolve(), before)
        if report := os.environ.get('SCORING_ACCEPTANCE_REPORT'):
            atomic_json(Path(report), dict(status='passed', coverage_mode='fast', synthetic=True,
                wall_seconds=time.monotonic() - started, assessed_species=6,
                metadata_species=species.num_rows, tree_tips=tree_result['tree_tips'],
                represented_tips={'website': preset['represented_tips'], 'any-assembly': alternative['represented_tips']},
                protected_files=len(protected), default_preset=preset, alternative_preset=alternative,
                comparisons=comparison, checks=['real polygon/point/basin routes', 'registered taxonomy matcher',
                    'metadata categorical metrics coarse Arrow and PMTiles', 'registered checked tree enrichment reuse',
                    'tree-independent DNA', 'stewardship', 'scenario APIs', 'coarse/fine opportunity presets',
                    'interruption and resume', 'wrong-profile rejection', 'unreceipted partition rejection',
                    'base-membership metadata and fine-output hashes unchanged']))

    def base_artifacts(self, root, memberships, crosswalk, base_metrics):
        """Use the same production commands as the existing full replay."""
        base = root / 'base'
        base.mkdir()
        boundary = base / 'boundaries.json'
        boundary_fixture(boundary)
        paths = dict(DATA_DIR=base, SOURCE_DUCKDB_PATH=base / 'source.duckdb',
            BUILD_DUCKDB_PATH=base / 'build.duckdb', EXPORT_DIR=base / 'exports',
            TILE_DIR=base / 'coarse-tiles', PMTILES_PATH=base / 'coarse-tiles/priorities.pmtiles',
            MAP_METADATA_PATH=base / 'coarse-tiles/map-metadata.json',
            VALIDATION_REPORT_PATH=base / 'build-validation.json',
            SOURCE_VALIDATION_REPORT_PATH=base / 'source-validation.json',
            DUCKDB_SCRATCH_DIR=base / 'scratch', SPATIAL_PROFILE=FAST_PROFILE,
            GLOBAL_H3_ROOT=memberships, H3_ID_CROSSWALK_PATH=crosswalk)
        environment = {**os.environ, **{k: str(v) for k, v in paths.items()},
            **{name: str(boundary) for name in BOUNDARY_ENV.values()},
            'PIPELINE_WORKERS': '1', 'DUCKDB_THREADS': '1', 'DUCKDB_MEMORY_LIMIT': '256MB',
            'TIPPECANOE_MAX_THREADS': '1', 'TILE_THREADS': '1', 'TILE_DUCKDB_THREADS': '1'}
        commands = [
            [sys.executable, 'ark_pipeline/builders/source_database.py', '--target', str(paths['SOURCE_DUCKDB_PATH']),
                '--overwrite', '--resolutions', '3', '--species-parquet', str(root / 'metadata/species.parquet'),
                '--species-systems-parquet', str(root / 'metadata/species_systems.parquet'),
                '--h3-res3', str(memberships / 'h3_res3_species_global_merged.parquet'),
                '--crosswalk', str(crosswalk), '--defer-lossless-validation'],
            [sys.executable, 'ark_pipeline/builders/coarse_cache.py', '--rebuild-aggregates', '--resolutions', '3',
                '--skip-expanded-cell-species', '--defer-lossless-validation'],
            [sys.executable, '-m', 'ark_pipeline.cli.serving_tiles', 'record', '--h3-root', str(memberships),
                '--parts-dir', str(base_metrics), '--build-duckdb', str(paths['BUILD_DUCKDB_PATH']),
                '--species', str(root / 'metadata/species.parquet'), '--species-systems', str(root / 'metadata/species_systems.parquet'),
                '--crosswalk', str(crosswalk), '--metadata-template', str(paths['MAP_METADATA_PATH']),
                '--output', str(base / 'prepared.json')],
            [sys.executable, '-m', 'ark_pipeline.cli.serving_tiles', 'build', '--prepared-inputs', str(base / 'prepared.json'),
                '--output-dir', str(base / 'tiles'), '--scratch-dir', str(base / 'scratch/tiles'),
                *[value for key in BOUNDARY_ENV for value in ('--' + key.replace('_', '-'), str(boundary))]],
        ]
        for command in commands:
            result = subprocess.run(command, cwd=FAST_PROFILE.parents[1], env=environment,
                text=True, capture_output=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout[-4000:] + result.stderr[-4000:])
        self.assertTrue((base / 'tiles/current/priorities.pmtiles').is_file())
        arrows = list((base / 'coarse-tiles').glob('*.arrow'))
        self.assertTrue(arrows, 'Coarse Arrow snapshot must be produced')
        publication = json.loads((base / 'tiles/current/receipt.json').read_text())
        self.assertEqual(publication['status'], 'passed')
        return [*arrows, base / 'tiles/current/priorities.pmtiles']
