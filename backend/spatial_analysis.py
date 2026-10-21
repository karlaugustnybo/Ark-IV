"""Lazy selected-species range/stewardship and selected-cell support queries."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

import duckdb
import h3
from litestar import get
from litestar.exceptions import HTTPException
from litestar.params import FromPath, FromQuery

from ark_pipeline.builders.spatial_analytics import support_summary
from ark_pipeline.runtime.provenance import sha256
from backend.coverage_profiles import RequestSettings


@dataclass
class StewardshipRow:
    framework: str
    code: str
    jurisdiction_cells: int
    mapped_cell_fraction: float
    mapped_area_fraction: float


@dataclass
class RangeDetails:
    iucn_sis_id: str
    global_cell_count: int
    global_mapped_area_km2: float
    coverage_status: str
    spatial_profile: str
    membership_generation: str
    denominator: str
    stewardship_available: bool
    page: int
    per_page: int
    stewardship: list[StewardshipRow] = field(default_factory=list)
    missing_basin_relationships: int | None = None
    affected_by_missing_basins: bool | None = None


@dataclass
class SourceSupportDetails:
    h3_index: str
    resolution: int
    generation: str
    species: int
    polygon: int
    point: int
    hydrobasin: int
    multiple: int
    unknown: int
    fractions: dict[str, float | None]
    interpretation: str


class InvalidAnalyticalGeneration(ValueError):
    pass


@lru_cache(maxsize=4)
def checked_outputs(generation: Path) -> dict[str, Path]:
    try:
        saved = json.loads((generation / 'receipt.json').read_text())
        if saved.get('status') != 'passed':
            raise ValueError('Analytical generation is not validated')
        outputs = {}
        for name, entry in saved['outputs'].items():
            path = (generation / entry['filename']).resolve(strict=True)
            if not path.is_relative_to(generation) or path.stat().st_size != entry['bytes']:
                raise ValueError('Invalid analytical generation output')
            outputs[name] = path
        return outputs
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise InvalidAnalyticalGeneration('Invalid analytical generation') from error


@lru_cache(maxsize=128)
def checked_artifact(generation: Path, name: str) -> Path:
    # Verify only the requested partition, once per immutable generation.
    # A selected-cell request must not scan all global membership partitions.
    path = checked_outputs(generation)[name]
    saved = json.loads((generation / 'receipt.json').read_text())
    if sha256(path) != saved['outputs'][name]['sha256']:
        raise InvalidAnalyticalGeneration('Invalid analytical artifact checksum')
    return path


def range_details(species_id: str, page: int, per_page: int):
    if not species_id.isdecimal() or not 1 <= page <= 1_000_000 or not 1 <= per_page <= 100:
        raise ValueError('Require a numeric species ID, positive page and per_page in [1,100]')
    selected = RequestSettings().spatial_analysis_generation_path
    if selected is None:
        raise HTTPException(status_code=503, detail='Range analysis is unavailable')
    generation = selected.resolve(strict=True)
    outputs = checked_outputs(generation)
    checked_artifact(generation, 'species_ranges')
    if 'stewardship' in outputs:
        checked_artifact(generation, 'stewardship')
    with duckdb.connect() as c:
        c.execute("SET threads=1")
        c.execute("SET memory_limit='256MB'")
        row = c.execute('SELECT * FROM read_parquet(?) WHERE iucn_sis_id=?',
                        [str(outputs['species_ranges']), species_id]).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail='No mapped distribution for this species')
        columns = [item[0] for item in c.description]
        values = dict(zip(columns, row))
        stewardship = []
        if 'stewardship' in outputs:
            stewardship = [StewardshipRow(*r) for r in c.execute('''SELECT framework, code, jurisdiction_cells,
                mapped_cell_fraction, mapped_area_fraction FROM read_parquet(?) WHERE iucn_sis_id=?
                ORDER BY framework, code LIMIT ? OFFSET ?''', [str(outputs['stewardship']), species_id, per_page, (page-1)*per_page]).fetchall()]
    return RangeDetails(**{**values, 'iucn_sis_id': str(values['iucn_sis_id'])},
                         stewardship_available='stewardship' in outputs,
                         page=page, per_page=per_page, stewardship=stewardship)


@get('/api/analysis/ranges/{species_id:str}', operation_id='getRangeDetails')
async def species_range_details(species_id: FromPath[str], page: FromQuery[int] = 1, per_page: FromQuery[int] = 50,
    coverage_profile: FromQuery[Literal['resident', 'with-passage']] = 'resident') -> RangeDetails:
    try:
        return await asyncio.to_thread(range_details, species_id, page, per_page)
    except (OSError, KeyError, InvalidAnalyticalGeneration, duckdb.Error) as error:
        raise HTTPException(status_code=503, detail='Range generation is unavailable or invalid') from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


def cell_support(h3_index: str) -> SourceSupportDetails:
    if not h3.is_valid_cell(h3_index):
        raise ValueError('Invalid H3 cell')
    resolution = h3.get_resolution(h3_index)
    if resolution not in (3, 7):
        raise ValueError('Require a resolution-3 or resolution-7 cell')
    selected = RequestSettings().membership_generation_path
    if selected is None:
        raise HTTPException(status_code=503, detail='Spatial source support is unavailable')
    generation = selected.resolve(strict=True)
    base = h3.get_base_cell_number(h3_index)
    name = f'support_base_{base}' if resolution == 7 else f'support_res3_base_{base}'
    outputs = checked_outputs(generation)
    if name not in outputs:
        # No source-support schema is different from a known empty partition.
        if not any(k.startswith('support_') for k in outputs):
            raise HTTPException(status_code=503, detail='This membership generation does not retain source support')
        summary = dict(species=0, polygon=0, point=0, hydrobasin=0, multiple=0, unknown=0,
                       fractions={key: None for key in ('polygon', 'point', 'hydrobasin', 'multiple', 'unknown')},
                       interpretation='source support; overlapping fractions, not biological confidence')
    else:
        with duckdb.connect() as c:
            c.execute('SET threads=1')
            c.execute("SET memory_limit='256MB'")
            summary = support_summary(c, [checked_artifact(generation, name)], int(h3_index, 16))
    return SourceSupportDetails(h3_index.lower(), resolution, generation.name, **summary)


@get('/api/analysis/cells/{h3_index:str}/support', operation_id='getSourceSupportDetails')
async def source_support_details(h3_index: FromPath[str],
    coverage_profile: FromQuery[Literal['resident', 'with-passage']] = 'resident') -> SourceSupportDetails:
    try:
        return await asyncio.to_thread(cell_support, h3_index)
    except (OSError, KeyError, InvalidAnalyticalGeneration, duckdb.Error) as error:
        raise HTTPException(status_code=503, detail='Membership generation is unavailable or invalid') from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
