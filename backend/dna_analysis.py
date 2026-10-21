"""Optional DNA evidence scenarios, independent of dated-tree availability."""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

import pyarrow.parquet as pq
from litestar import get
from litestar.exceptions import HTTPException
from litestar.params import FromQuery

from ark_pipeline.builders.dna import calculate, code_identity
from ark_pipeline.dna_evidence import DnaRule
from backend.coverage_profiles import RequestSettings
from backend.spatial_analysis import InvalidAnalyticalGeneration, checked_artifact, checked_outputs


@dataclass
class DnaSpeciesRow:
    iucn_sis_id: str
    taxon_id: str | None
    qualifies: bool | None
    state: str
    sample_reported: bool
    project_reported: bool
    criteria_reported: bool
    assembly_reported: bool
    resampling_reported: bool
    family_represented: bool | None
    genus_represented: bool | None
    identity_status: str


@dataclass
class DnaPage:
    scenario_id: str
    generation: str
    rule: str
    min_busco: float
    evidence_records: int
    qualifying_records: int
    unknown_records: int
    total: int
    page: int
    per_page: int
    rows: list[DnaSpeciesRow]


@lru_cache(maxsize=2)
def load_dna_generation(path: Path):
    checked_outputs(path)
    receipt = json.loads((path / 'receipt.json').read_text())
    if receipt['identity']['code'] != code_identity():
        raise InvalidAnalyticalGeneration('DNA calculation code changed')
    return tuple(pq.read_table(checked_artifact(path, name)).to_pylist() for name in ('evidence', 'species'))


@lru_cache(maxsize=8)
def completed_dna_scenario(path: Path, rule: DnaRule):
    evidence, species = load_dna_generation(path)
    return calculate(evidence, species, rule, path.name)


scenario_lock = threading.Lock()


def dna_scenario(path: Path, rule: DnaRule):
    # Serialize cold scans as well as cache misses for different rules, bounding
    # transient full-evidence work under concurrent requests.
    with scenario_lock:
        return completed_dna_scenario(path, rule)


def dna_page(rule, min_busco, page, per_page, species_id, expected_scenario):
    if not 1 <= page <= 1_000_000 or not 1 <= per_page <= 100:
        raise ValueError('Require positive page and per_page in [1,100]')
    selected_rule = DnaRule(rule, min_busco=min_busco)
    selected = RequestSettings().dna_generation_path
    if selected is None:
        raise HTTPException(status_code=503, detail='DNA enrichment is unavailable')
    try:
        path = selected.resolve(strict=True)
        result = dna_scenario(path, selected_rule)
    except (OSError, KeyError, ValueError) as error:
        raise HTTPException(status_code=503, detail='DNA generation is unavailable or invalid') from error
    if expected_scenario and result['scenario_id'] != expected_scenario:
        raise HTTPException(status_code=409, detail='DNA scenario changed; refresh the scenario')
    rows = [r for r in result['rows'] if species_id is None or r['iucn_sis_id'] == species_id]
    return DnaPage(result['scenario_id'], path.name, rule, min_busco, result['evidence_records'],
        result['qualifying_records'], result['unknown_records'], len(rows), page, per_page,
        [DnaSpeciesRow(**row) for row in rows[(page-1)*per_page:page*per_page]])


@get('/api/analysis/dna', operation_id='getDnaScenario')
async def dna_evidence_scenario(
    rule: FromQuery[Literal['website', 'any-assembly', 'taxon-quality-proxy']] = 'website',
    min_busco: FromQuery[float] = 90, page: FromQuery[int] = 1, per_page: FromQuery[int] = 50,
    species_id: FromQuery[str | None] = None, expected_scenario: FromQuery[str | None] = None,
    coverage_profile: FromQuery[Literal['resident', 'with-passage']] = 'resident',
) -> DnaPage:
    try:
        return await asyncio.to_thread(dna_page, rule, min_busco, page, per_page, species_id, expected_scenario)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
