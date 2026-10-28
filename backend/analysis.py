"""Optional private analytical endpoints; no eager map payload or tree download."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

from asyncpg import UndefinedColumnError
from litestar import get
from litestar.datastructures import State
from litestar.exceptions import HTTPException
from litestar.params import FromPath, FromQuery

from ark_pipeline.builders.phylogenetic import load_generation
from ark_pipeline.dna_evidence import DnaRule
from backend.coverage_profiles import RequestSettings
from backend.db import get_pool


@dataclass
class PhylogeneticSpeciesRow:
    iucn_sis_id: str
    tree_tip: str | None
    gap_myr: float | None
    priority: float | None
    represented: bool | None
    baseline_incomplete: bool
    availability_reason: str | None
    # Bounds over every placed species, withheld ones included; see methodology.
    gap_upper_myr: float | None = None
    gap_share_upper_myr: float | None = None
    redlist_category: str | None = None
    evidence_state: str = 'unknown'
    qualifying_evidence: bool | None = None
    sample_reported: bool | None = None
    project_reported: bool | None = None
    criteria_reported: bool | None = None
    assembly_reported: bool | None = None
    resampling_reported: bool | None = None
    nearest_tip: str | None = None
    nearest_distance_myr: float | None = None
    ed_myr: float | None = None
    edge1: float | None = None
    edge2_myr_point: float | None = None
    edge2_myr_median: float | None = None
    edge2_myr_p025: float | None = None
    edge2_myr_p975: float | None = None
    score_status: str = 'unavailable'


@dataclass
class ScenarioMetadata:
    tree_generation: str
    evidence_generation: str
    identity_generation: str
    scope: str
    root: str
    stem: str
    units: str
    rule: str
    rule_version: int
    min_busco: float
    review_policy: str


@dataclass
class PhylogeneticPage:
    scenario_id: str
    weighted_scenario_id: str
    metadata: ScenarioMetadata
    total: int
    page: int
    per_page: int
    represented_tips: int
    tree_tips: int
    unresolved_positive_records: int
    unknown_evidence_records: int
    unresolved_without_genus: bool
    rows: list[PhylogeneticSpeciesRow] = field(default_factory=list)


@dataclass
class AssessmentDetails:
    iucn_sis_id: str | None
    iucn_assessment_id: str | None
    iucn_population_trend: str | None
    iucn_assessment_date: str | None
    iucn_assessment_scope: str | None
    iucn_threat_classifications: str | None
    iucn_attributes_source_sha256: str | None


@lru_cache(maxsize=2)
def analytical_engine(path: Path):
    return load_generation(path)


def scenario_page(rule, min_busco, weights, page, per_page, species_id, nearest, expected_scenario):
    if not 1 <= page <= 1_000_000 or not 1 <= per_page <= 100:
        raise ValueError('page must be positive and per_page in [1,100]')
    selected_rule = DnaRule(rule, min_busco=min_busco)
    if any(not math.isfinite(v) or v < 0 for v in weights.values()):
        raise ValueError('Threat weights must be finite and nonnegative')
    selected = RequestSettings().phylogenetic_generation_path
    if selected is None:
        raise HTTPException(status_code=503, detail='Phylogenetic enrichment is unavailable')
    try:
        engine, scores = analytical_engine(selected.resolve(strict=True))
    except (OSError, ValueError, KeyError) as error:
        raise HTTPException(status_code=503, detail='Phylogenetic generation is unavailable or invalid') from error
    result = engine.rank(selected_rule, weights,
                         species_ids={species_id} if species_id else None, nearest=nearest)
    if expected_scenario and expected_scenario != result['scenario_id']:
        raise HTTPException(status_code=409, detail='Scenario generation changed; refresh the scenario')
    identity = result['identity']
    rows = result['rows'][(page - 1) * per_page:page * per_page]
    return PhylogeneticPage(result['scenario_id'], result['weighted_scenario_id'],
        ScenarioMetadata(identity['tree'], identity['evidence'], identity['identities'],
            identity['scope'], identity['root'], identity['stem'], identity['units'],
            rule, 1, min_busco, identity['review_policy']),
        len(result['rows']), page, per_page, result['represented_tips'], result['tree_tips'],
        result['unresolved_positive_records'], result['unknown_evidence_records'], result['unresolved_without_genus'],
        [PhylogeneticSpeciesRow(**{**scores.get(row['iucn_sis_id'], {}), **row}) for row in rows])


@get('/api/analysis/phylogenetic', operation_id='getPhylogeneticScenario')
async def phylogenetic_scenario(
    rule: FromQuery[Literal['website', 'any-assembly', 'taxon-quality-proxy']] = 'website',
    min_busco: FromQuery[float] = 90, page: FromQuery[int] = 1, per_page: FromQuery[int] = 50,
    species_id: FromQuery[str | None] = None, nearest: FromQuery[bool] = False,
    expected_scenario: FromQuery[str | None] = None,
    cr: FromQuery[float] = 4, en: FromQuery[float] = 3, vu: FromQuery[float] = 2, nt: FromQuery[float] = 1, dd: FromQuery[float] = 2, lc: FromQuery[float] = .1,
    coverage_profile: FromQuery[Literal['resident', 'with-passage']] = 'resident',
) -> PhylogeneticPage:
    try:
        return await asyncio.to_thread(scenario_page, rule, min_busco,
            dict(CR=cr, EN=en, VU=vu, NT=nt, DD=dd, LC=lc), page, per_page,
            species_id, nearest, expected_scenario)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@get('/api/species/{gbif_accepted_id:str}/assessment', operation_id='getAssessmentDetails')
async def assessment_details(state: State, gbif_accepted_id: FromPath[str],
                             coverage_profile: FromQuery[Literal['resident', 'with-passage']] = 'resident') -> AssessmentDetails:
    try:
        row = await get_pool(state).fetchrow('''SELECT iucn_sis_id, iucn_assessment_id,
            iucn_population_trend, iucn_assessment_date, iucn_assessment_scope,
            iucn_threat_classifications, iucn_attributes_source_sha256
            FROM species WHERE gbif_accepted_id=$1''', gbif_accepted_id)
    except UndefinedColumnError as error:
        raise HTTPException(status_code=503, detail='Assessment detail fields have not been loaded') from error
    if row is None:
        raise HTTPException(status_code=404, detail='Species not found')
    return AssessmentDetails(**dict(row))
