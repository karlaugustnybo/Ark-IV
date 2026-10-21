from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Literal

import duckdb
import h3
import pyarrow.parquet as pq
from litestar import Litestar, Request, Response, get
from litestar.config.compression import CompressionConfig
from litestar.config.cors import CORSConfig
from litestar.datastructures import State
from litestar.exceptions import HTTPException, NotFoundException
from litestar.openapi.config import OpenAPIConfig
from litestar.params import Parameter
from litestar.response import File, Stream

from ark_pipeline.spatial.boundaries import load_jurisdiction_index
from backend.analysis import assessment_details, phylogenetic_scenario
from backend.config import get_settings
from backend.coverage_profiles import CoverageProfileMiddleware, RequestSettings, current_profile
from backend.db import database_lifespan, get_pool
from backend.dna_analysis import dna_evidence_scenario
from backend.models import (
    CellBoundaryMembership,
    CellDetailsResponse,
    CellSpeciesRow,
    CellStats,
    CoverageProfile,
    CoverageProfilesResponse,
    HealthResponse,
    SpeciesCellsResponse,
    SpeciesPage,
    SpeciesRow,
    SpeciesSuggestion,
    SpeciesSuggestions,
    StatsResponse,
)
from backend.res7_tiles import SYSTEM_NAMES, aggregate_coverage, render_tile
from backend.spatial_analysis import source_support_details, species_range_details

settings = RequestSettings()
CELL_BOUNDARY_FRAMEWORKS = (
    ("admin0", "Countries & territories", "jurisdictions_path"),
    ("admin1", "States, regions & provinces", "admin1_boundaries_path"),
    ("municipality", "Municipalities & local areas", "municipality_boundaries_path"),
    ("eez", "Exclusive economic zones", "eez_boundaries_path"),
    (
        "conservation_framework",
        "Conservation frameworks",
        "conservation_boundaries_path",
    ),
)
VALID_SYSTEMS = {"Terrestrial", "Freshwater", "Marine"}
VALID_REDLIST_FILTERS = {
    "Critically Endangered",
    "Endangered",
    "Vulnerable",
    "Near Threatened",
    "Data Deficient",
    "Least Concern",
    "Not Assessed",
}
VALID_DNA_FILTERS = {"missing_family", "missing_genus", "missing_species", "sampled"}
VALID_SORTS = {
    "species_name": "species_name",
    "family": "family",
    "redlist_category": "CASE redlist_category WHEN 'Critically Endangered' THEN 1 WHEN 'Endangered' THEN 2 WHEN 'Vulnerable' THEN 3 WHEN 'Near Threatened' THEN 4 WHEN 'Data Deficient' THEN 5 WHEN 'Least Concern' THEN 6 ELSE 7 END",
    "threat_score": "threat_score",
    "dna_level": "dna_level_score",
    "priority": "priority",
}
VALID_DNA_FILTERS.add("goat_data_deficient")
MAX_SEARCH_LENGTH = 120


def _clean_search(value: str) -> str:
    """Normalize user text without treating it as SQL or regular-expression syntax."""
    query = " ".join(value.split())
    if len(query) > MAX_SEARCH_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Search text must be {MAX_SEARCH_LENGTH} characters or fewer",
        )
    if any(unicodedata.category(character) == "Cc" for character in query):
        raise HTTPException(
            status_code=400,
            detail="Search text contains unsupported control characters",
        )
    return query


def _comma_values(value: str, allowed: set[str]) -> list[str] | None:
    values = list(dict.fromkeys(item.strip() for item in value.split(",") if item.strip()))
    return values if values and set(values) <= allowed else None


def _filtered_species_cte(start: int, *, include_fuzzy: bool) -> str:
    search, redlist, dna, systems, admin0, admin1, municipality, eez, conservation = range(
        start, start + 9
    )
    fuzzy_clause = (
        """
              OR (length(search_query.value) >= 3 AND (
                  normalize_species_search(source.species_name) % search_query.value
                  OR normalize_species_search(source.family) % search_query.value
              ))"""
        if include_fuzzy
        else ""
    )
    return f"""
        normalized_query AS (
            SELECT normalize_species_search(${search}::text) AS value
        ),
        search_query AS (
            SELECT value, escape_species_like(value) AS pattern
            FROM normalized_query
        ),
        boundary_species AS (
            SELECT DISTINCT unnest(lists.species_ids) AS gbif_accepted_id
            FROM cell_boundaries boundaries
            JOIN cell_species_lists lists USING (resolution, h3_index)
            WHERE boundaries.resolution = 3
              AND (${admin0}::text[] IS NULL OR boundaries.admin0_codes && ${admin0}::text[])
              AND (${admin1}::text[] IS NULL OR boundaries.admin1_codes && ${admin1}::text[])
              AND (${municipality}::text[] IS NULL OR boundaries.municipality_codes && ${municipality}::text[])
              AND (${eez}::text[] IS NULL OR boundaries.eez_codes && ${eez}::text[])
              AND (${conservation}::text[] IS NULL OR boundaries.conservation_framework_codes && ${conservation}::text[])
        ),
        filtered_species AS (
            SELECT source.*, CASE
                WHEN search_query.value = '' THEN 0
                WHEN normalize_species_search(source.species_name) = search_query.value
                  OR normalize_species_search(source.family) = search_query.value THEN 1
                WHEN normalize_species_search(source.species_name) LIKE search_query.pattern || '%' ESCAPE E'\\\\'
                  OR normalize_species_search(source.family) LIKE search_query.pattern || '%' ESCAPE E'\\\\' THEN 2
                WHEN normalize_species_search(source.species_name) LIKE '%' || search_query.pattern || '%' ESCAPE E'\\\\'
                  OR normalize_species_search(source.family) LIKE '%' || search_query.pattern || '%' ESCAPE E'\\\\' THEN 3
                ELSE 4
            END AS match_rank
            FROM species source, search_query
            WHERE (search_query.value = ''
              OR normalize_species_search(source.species_name) LIKE '%' || search_query.pattern || '%' ESCAPE E'\\\\'
              OR normalize_species_search(source.family) LIKE '%' || search_query.pattern || '%' ESCAPE E'\\\\'
              {fuzzy_clause})
              AND (${redlist}::text[] IS NULL OR COALESCE(source.redlist_category, 'Not Assessed') = ANY(${redlist}::text[]))
              AND (${dna}::text[] IS NULL OR CASE
                    WHEN source.goat_data_deficient = true THEN 'goat_data_deficient'
                    WHEN source.family_has_dna = false THEN 'missing_family'
                    WHEN source.genus_has_dna = false THEN 'missing_genus'
                    WHEN source.has_dna_species_level = false THEN 'missing_species'
                    ELSE 'sampled' END = ANY(${dna}::text[]))
              AND (${systems}::text[] IS NULL OR EXISTS (
                    SELECT 1 FROM species_systems ss
                    WHERE ss.gbif_accepted_id = source.gbif_accepted_id
                      AND ss.system = ANY(${systems}::text[])
              ))
              AND ((${admin0}::text[] IS NULL AND ${admin1}::text[] IS NULL
                    AND ${municipality}::text[] IS NULL AND ${eez}::text[] IS NULL
                    AND ${conservation}::text[] IS NULL)
                   OR source.gbif_accepted_id IN (SELECT gbif_accepted_id FROM boundary_species))
        )
    """


def _h3_to_bigint(value: str) -> int:
    try:
        if not re.fullmatch(r"[0-9a-fA-F]{15}", value) or not h3.is_valid_cell(value.lower()):
            raise ValueError(value)
        return h3.str_to_int(value.lower())
    except ValueError as exc:
        raise NotFoundException(detail="Invalid H3 index") from exc


def _res7_partition_path(h3_value: int) -> Path | None:
    """Resolve the base-cell partition file for one fine cell, or None."""
    if settings.res7_parts_dir is None:
        return None
    base_cell = (h3_value >> 45) & 127
    partition = settings.res7_parts_dir / f"base_{base_cell}.parquet"
    return partition if partition.is_file() else None


def _external_res7_species_ids(h3_value: int) -> list[str] | None:
    """Read one res-7 list from its base-cell Parquet partition.

    These globally large lists stay on the mounted data disk; Parquet row-group
    statistics make a selected-cell lookup cheap without importing 96 million
    rows into PostgreSQL.
    """
    partition = _res7_partition_path(h3_value)
    if partition is None:
        return None
    connection = duckdb.connect()
    try:
        row = connection.execute(
            "SELECT species_ids FROM read_parquet(?) WHERE h3_cell = ? LIMIT 1",
            [str(partition), h3_value],
        ).fetchone()
    finally:
        connection.close()
    return [str(species_id) for species_id in row[0]] if row else None


def _external_res7_cell_count(parts_dir: Path | None) -> int | None:
    """Reuse footer counts until the consumed files or selected generation change."""
    if parts_dir is None:
        return None
    selected = parts_dir.resolve()
    records = []
    for path in sorted(selected.glob("base_*.parquet")):
        stat = path.stat()
        records.append(
            (str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        )
    return _partition_count(tuple(records))


@lru_cache(maxsize=16)
def _partition_count(records: tuple[tuple, ...]) -> int | None:
    return (
        sum(pq.ParquetFile(record[0]).metadata.num_rows for record in records) if records else None
    )


@get("/api/coverage-profiles", operation_id="getCoverageProfiles")
async def coverage_profiles(state: State, coverage_profile: Literal["resident", "with-passage"] = "resident") -> CoverageProfilesResponse:
    from backend.coverage_profiles import LABELS, PROFILE_IDS

    registry = getattr(state, "coverage_settings", {"resident": get_settings()})
    return CoverageProfilesResponse(
        selected=current_profile.get(),
        profiles=[
            CoverageProfile(id=key, label=LABELS[key], available=key in registry)
            for key in PROFILE_IDS
        ],
    )


@get("/api/health", operation_id="getHealth")
async def health(state: State, coverage_profile: Literal["resident", "with-passage"] = "resident") -> HealthResponse:
    pool = get_pool(state)
    database = "ok"
    try:
        await pool.fetchval("SELECT 1")
    except Exception:
        database = "unavailable"
    tiles = "ok" if settings.pmtiles_path.is_file() else "missing"
    return HealthResponse(
        status="ok" if database == tiles == "ok" else "degraded", database=database, tiles=tiles
    )


@get("/api/stats", operation_id="getStats")
async def stats(state: State, coverage_profile: Literal["resident", "with-passage"] = "resident") -> StatsResponse:
    pool = get_pool(state)
    row = await pool.fetchrow(
        """
        SELECT total, critically_endangered, edge_species, needs_dna_sampling,
               res3_cells, res7_cells
        FROM app_stats WHERE singleton = TRUE
        """
    )
    values = dict(row)
    external_res7_cells = _external_res7_cell_count(settings.res7_parts_dir)
    if external_res7_cells is not None:
        values["res7_cells"] = external_res7_cells
    return StatsResponse(**values)


@get("/api/species", operation_id="getSpecies")
async def species_page(
    state: State,
    search: str = "",
    sort: str = "priority",
    order: str = "desc",
    page: int = Parameter(default=1, ge=1),
    per_page: int = Parameter(default=10, ge=1, le=100),
    cr: float = 4.0,
    en: float = 3.0,
    vu: float = 2.0,
    nt: float = 1.0,
    dd: float = 2.0,
    lc: float = 0.1,
    sp: float = 2.0,
    gen: float = 3.0,
    fam: float = 4.0,
    gdd: float = 4.0,
    samp: float = 0.0,
    cov: float = 0.0,
    redlist: str = "",
    dna: str = "",
    systems: str = "",
    admin0: str = "",
    admin1: str = "",
    municipality: str = "",
    eez: str = "",
    conservation_framework: str = "",
    coverage_profile: Literal["resident", "with-passage"] = "resident",
) -> SpeciesPage:
    if not all(
        math.isfinite(value) for value in (cr, en, vu, nt, dd, lc, sp, gen, fam, gdd, samp, cov)
    ):
        raise HTTPException(status_code=400, detail="Score weights must be finite")
    if any(
        not math.isfinite(threat * dna_weight)
        for threat in (cr, en, vu, nt, dd, lc)
        for dna_weight in (sp, gen, fam, gdd, samp)
    ):
        raise HTTPException(status_code=400, detail="Score products must fit finite numbers")
    pool = get_pool(state)
    sort_key = sort if sort in VALID_SORTS else "priority"
    sort_sql = VALID_SORTS[sort_key]
    dna_tie_break_sql = ", dna_level_rank ASC" if sort_key in {"dna_level", "priority"} else ""
    order_sql = "ASC" if order.lower() == "asc" else "DESC"
    query = _clean_search(search)
    redlist_values = _comma_values(redlist, VALID_REDLIST_FILTERS)
    dna_values = _comma_values(dna, VALID_DNA_FILTERS)
    system_values = _comma_values(systems, VALID_SYSTEMS)
    admin0_values = list(dict.fromkeys(item.strip() for item in admin0.split(",") if item.strip()))
    admin1_values = list(dict.fromkeys(item.strip() for item in admin1.split(",") if item.strip()))
    municipality_values = list(
        dict.fromkeys(item.strip() for item in municipality.split(",") if item.strip())
    )
    eez_values = list(
        dict.fromkeys(item.strip().upper() for item in eez.split(",") if item.strip())
    )
    conservation_values = list(
        dict.fromkeys(item.strip() for item in conservation_framework.split(",") if item.strip())
    )
    if redlist.strip() and redlist_values is None:
        raise HTTPException(status_code=400, detail="Invalid Red List filter")
    if dna.strip() and dna_values is None:
        raise HTTPException(status_code=400, detail="Invalid DNA filter")
    if systems.strip() and system_values is None:
        raise HTTPException(status_code=400, detail="Invalid ecosystem filter")
    if any(
        len(values) > 30
        for values in (
            admin0_values,
            admin1_values,
            municipality_values,
            eez_values,
            conservation_values,
        )
    ):
        raise HTTPException(status_code=400, detail="Select at most 30 boundaries per framework")
    filter_values = (
        query,
        redlist_values,
        dna_values,
        system_values,
        admin0_values or None,
        admin1_values or None,
        municipality_values or None,
        eez_values or None,
        conservation_values or None,
    )
    summary = await pool.fetchrow(
        f"WITH {_filtered_species_cte(1, include_fuzzy=False)} "
        "SELECT COUNT(*)::int AS total, MIN(match_rank)::int AS best_rank "
        "FROM filtered_species",
        *filter_values,
    )
    use_fuzzy = bool(query) and len(query) >= 3 and summary["total"] == 0
    if use_fuzzy:
        summary = await pool.fetchrow(
            f"WITH {_filtered_species_cte(1, include_fuzzy=True)} "
            "SELECT COUNT(*)::int AS total, MIN(match_rank)::int AS best_rank "
            "FROM filtered_species",
            *filter_values,
        )
    total = summary["total"]
    suggested = use_fuzzy and total > 0
    total_pages = max(1, math.ceil(total / per_page))
    page = min(page, total_pages)
    values = [
        cr,
        en,
        vu,
        nt,
        dd,
        lc,
        fam,
        gen,
        sp,
        samp,
        gdd,
        query,
        redlist_values,
        dna_values,
        system_values,
        admin0_values or None,
        admin1_values or None,
        municipality_values or None,
        eez_values or None,
        conservation_values or None,
        per_page,
        (page - 1) * per_page,
    ]
    rows = await pool.fetch(
        f"""
        WITH {_filtered_species_cte(12, include_fuzzy=use_fuzzy)}, scored AS (
            SELECT gbif_accepted_id, iucn_sis_id, iucn_assessment_id,
                gbif_taxon_id, goat_taxon_id,
                COALESCE(species_name, '') AS species_name, COALESCE(family, '') AS family,
                COALESCE(redlist_category, 'Not Assessed') AS redlist_category,
                match_rank, metadata_status, spatial_redlist_category, iucn_assessment_latest,
                CASE redlist_category
                    WHEN 'Critically Endangered' THEN $1::double precision WHEN 'Endangered' THEN $2::double precision
                    WHEN 'Vulnerable' THEN $3::double precision WHEN 'Near Threatened' THEN $4::double precision
                    WHEN 'Data Deficient' THEN $5::double precision WHEN 'Least Concern' THEN $6::double precision ELSE 0
                END::float AS threat_score,
                CASE WHEN goat_data_deficient = true THEN 'GoaT Data Deficient (' || $11::double precision::text || ')'
                    WHEN family_has_dna = false THEN 'Missing Family (' || $7::double precision::text || ')'
                    WHEN genus_has_dna = false THEN 'Missing Genus (' || $8::double precision::text || ')'
                    WHEN has_dna_species_level = false THEN 'Missing Species (' || $9::double precision::text || ')'
                    ELSE 'Already Sampled' END AS dna_level,
                CASE WHEN goat_data_deficient = true THEN $11::double precision
                    WHEN family_has_dna = false THEN $7::double precision WHEN genus_has_dna = false THEN $8::double precision
                    WHEN has_dna_species_level = false THEN $9::double precision ELSE $10::double precision END::float AS dna_level_score,
                CASE WHEN goat_data_deficient = true THEN 2
                    WHEN family_has_dna = false THEN 1 WHEN genus_has_dna = false THEN 3
                    WHEN has_dna_species_level = false THEN 4 ELSE 5 END AS dna_level_rank
            FROM filtered_species
        )
        SELECT gbif_accepted_id, iucn_sis_id, iucn_assessment_id,
               gbif_taxon_id, goat_taxon_id,
               species_name, family, redlist_category, threat_score, dna_level,
               metadata_status, spatial_redlist_category, iucn_assessment_latest,
               (threat_score * dna_level_score)::float AS priority
        FROM scored
        ORDER BY match_rank ASC,
                 CASE WHEN match_rank = 4 THEN GREATEST(
                     similarity(normalize_species_search(species_name), normalize_species_search($12::text)),
                     similarity(normalize_species_search(family), normalize_species_search($12::text))
                 ) ELSE 1 END DESC,
                 {sort_sql} {order_sql}{dna_tie_break_sql}, species_name ASC
        LIMIT $21::int OFFSET $22::int
        """,
        *values,
    )
    return SpeciesPage(
        rows=[SpeciesRow(**dict(row)) for row in rows],
        page=page,
        total_pages=total_pages,
        total=total,
        suggested=suggested,
    )


@get("/api/species/suggestions", operation_id="getSpeciesSuggestions")
async def species_suggestions(
    state: State,
    search: str = "",
    limit: int = Parameter(default=8, ge=1, le=20),
    coverage_profile: Literal["resident", "with-passage"] = "resident",
) -> SpeciesSuggestions:
    """Return compact, ranked autocomplete matches without table scoring or counts."""
    pool = get_pool(state)
    query = _clean_search(search)
    if len(query) < 2:
        return SpeciesSuggestions()

    literal_rows = await pool.fetch(
        """
        WITH normalized_query AS (
            SELECT normalize_species_search($1::text) AS value
        ), search_query AS (
            SELECT value, escape_species_like(value) AS pattern
            FROM normalized_query
        )
        SELECT source.gbif_accepted_id,
               COALESCE(source.species_name, '') AS species_name,
               COALESCE(source.family, '') AS family
        FROM species source, search_query
        WHERE normalize_species_search(source.species_name)
                  LIKE search_query.pattern || '%' ESCAPE E'\\\\'
           OR normalize_species_search(source.family)
                  LIKE search_query.pattern || '%' ESCAPE E'\\\\'
           OR (length(search_query.value) >= 3 AND (
               normalize_species_search(source.species_name)
                   LIKE '%' || search_query.pattern || '%' ESCAPE E'\\\\'
               OR normalize_species_search(source.family)
                   LIKE '%' || search_query.pattern || '%' ESCAPE E'\\\\'
           ))
        ORDER BY CASE
            WHEN normalize_species_search(source.species_name) = search_query.value THEN 1
            WHEN normalize_species_search(source.family) = search_query.value THEN 2
            WHEN normalize_species_search(source.species_name)
                     LIKE search_query.pattern || '%' ESCAPE E'\\\\' THEN 3
            WHEN normalize_species_search(source.family)
                     LIKE search_query.pattern || '%' ESCAPE E'\\\\' THEN 4
            WHEN normalize_species_search(source.species_name)
                     LIKE '%' || search_query.pattern || '%' ESCAPE E'\\\\' THEN 5
            ELSE 6
        END,
        CASE WHEN source.iucn_assessment_latest IS TRUE THEN 0
             WHEN source.iucn_assessment_latest IS NULL THEN 1 ELSE 2 END,
        source.species_name ASC
        LIMIT $2::int
        """,
        query,
        limit,
    )
    if literal_rows:
        return SpeciesSuggestions(rows=[SpeciesSuggestion(**dict(row)) for row in literal_rows])

    if len(query) < 3:
        return SpeciesSuggestions()

    fuzzy_rows = await pool.fetch(
        """
        WITH search_query AS (
            SELECT normalize_species_search($1::text) AS value
        )
        SELECT source.gbif_accepted_id,
               COALESCE(source.species_name, '') AS species_name,
               COALESCE(source.family, '') AS family
        FROM species source, search_query
        WHERE normalize_species_search(source.species_name) % search_query.value
           OR normalize_species_search(source.family) % search_query.value
        ORDER BY GREATEST(
            similarity(normalize_species_search(source.species_name), search_query.value),
            similarity(normalize_species_search(source.family), search_query.value)
        ) DESC,
        CASE WHEN source.iucn_assessment_latest IS TRUE THEN 0
             WHEN source.iucn_assessment_latest IS NULL THEN 1 ELSE 2 END,
        source.species_name ASC
        LIMIT $2::int
        """,
        query,
        limit,
    )
    return SpeciesSuggestions(
        rows=[SpeciesSuggestion(**dict(row)) for row in fuzzy_rows],
        suggested=bool(fuzzy_rows),
    )


@get("/api/species/{gbif_accepted_id:str}/cells", operation_id="getSpeciesCells")
async def species_cells(
    state: State,
    gbif_accepted_id: str,
    resolution: int = 3,
    include_passage: bool = False,
    coverage_profile: Literal["resident", "with-passage"] = "resident",
) -> SpeciesCellsResponse:
    if resolution not in (3, 7):
        raise NotFoundException(detail="Resolution must be 3 or 7")
    passage_available = (settings.export_dir / "species_passage_cells_res3.parquet").is_file()
    if include_passage and not passage_available:
        raise HTTPException(
            status_code=503, detail="Passage coverage has not been built for this dataset"
        )
    pool = get_pool(state)
    row = await pool.fetchrow(
        """
        SELECT s.species_name, coverage.resolution,
               ARRAY(
                   SELECT DISTINCT to_hex(value)
                   FROM unnest(coalesce(coverage.h3_indexes, '{}'::bigint[]) ||
                       CASE WHEN $3 THEN coalesce(passage.h3_indexes, '{}'::bigint[])
                            ELSE '{}'::bigint[] END) AS cells(value)
                   ORDER BY 1
               ) AS cells
        FROM species s
        LEFT JOIN LATERAL (
            SELECT resolution, h3_indexes
            FROM species_cells
            WHERE gbif_accepted_id = s.gbif_accepted_id
              AND resolution <= CASE WHEN $3 THEN 3 ELSE $2 END
            ORDER BY resolution DESC
            LIMIT 1
        ) coverage ON true
        LEFT JOIN species_passage_cells passage
          ON passage.gbif_accepted_id = s.gbif_accepted_id AND passage.resolution = 3
        WHERE s.gbif_accepted_id = $1
        """,
        gbif_accepted_id,
        resolution,
        include_passage,
    )
    if row is None:
        raise NotFoundException(detail="Species not found")
    actual_resolution = row["resolution"] or 3
    return SpeciesCellsResponse(
        gbif_accepted_id=gbif_accepted_id,
        species_name=row["species_name"] or "",
        resolution=actual_resolution,
        cells=list(row["cells"] or []),
        include_passage=include_passage,
        passage_available=passage_available,
    )


@get("/api/cells/{h3_index:str}/species", operation_id="getCellSpecies")
async def cell_species(
    state: State,
    h3_index: str,
    resolution: int = 3,
    system: str = "",
    coverage_profile: Literal["resident", "with-passage"] = "resident",
) -> CellDetailsResponse:
    if resolution not in (3, 7):
        raise NotFoundException(detail="Resolution must be 3 or 7")
    h3_value = _h3_to_bigint(h3_index)
    h3_index = h3.int_to_str(h3_value)
    if h3.get_resolution(h3_index) != resolution:
        raise HTTPException(status_code=400, detail="H3 cell resolution does not match resolution")
    if system and system not in VALID_SYSTEMS:
        raise HTTPException(status_code=400, detail="Invalid ecosystem system")
    system_value = system or None
    pool = get_pool(state)
    species_ids, boundaries = await asyncio.gather(
        pool.fetchval(
            "SELECT species_ids FROM cell_species_lists WHERE h3_index = $1 AND resolution = $2",
            h3_value,
            resolution,
        ),
        asyncio.to_thread(_cell_boundary_memberships, h3_index),
    )
    partition = None
    if species_ids is None and resolution == 7:
        partition = await asyncio.to_thread(_res7_partition_path, h3_value)
        if partition is not None:
            species_ids = await asyncio.to_thread(_external_res7_species_ids, h3_value)
    # Only a missing generated partition makes the empty list a data gap; a
    # configured partition simply not containing this cell is a real answer.
    species_data_unavailable = resolution == 7 and species_ids is None and partition is None
    if not species_ids:
        return CellDetailsResponse(
            h3_index=h3_index,
            resolution=resolution,
            boundaries=boundaries,
            species=[],
            stats=None if species_data_unavailable else CellStats(),
            species_data_unavailable=species_data_unavailable,
        )
    rows = await pool.fetch(
        """
        SELECT s.gbif_accepted_id, s.iucn_sis_id, s.iucn_assessment_id,
               s.gbif_taxon_id, s.goat_taxon_id,
               s.species_name, COALESCE(s.family, '') AS family,
               COALESCE(s.redlist_category, 'Not Assessed') AS redlist_category,
               s.has_dna_species_level, s.genus_has_dna, s.family_has_dna,
               s.goat_data_deficient, s.metadata_status, s.spatial_redlist_category, s.iucn_assessment_latest
        FROM species s
        WHERE s.gbif_accepted_id = ANY($1::text[])
          AND ($2::text IS NULL OR EXISTS (
              SELECT 1 FROM species_systems ss
              WHERE ss.gbif_accepted_id = s.gbif_accepted_id AND ss.system = $2
          ))
        ORDER BY CASE s.redlist_category
            WHEN 'Critically Endangered' THEN 1 WHEN 'Endangered' THEN 2
            WHEN 'Vulnerable' THEN 3 WHEN 'Near Threatened' THEN 4
            WHEN 'Data Deficient' THEN 5 WHEN 'Least Concern' THEN 6 ELSE 7 END,
            s.species_name
        """,
        [str(species_id) for species_id in species_ids],
        system_value,
    )
    stats = CellStats()
    result: list[CellSpeciesRow] = []
    categories = {
        "Critically Endangered": "CR",
        "Endangered": "EN",
        "Vulnerable": "VU",
        "Near Threatened": "NT",
        "Data Deficient": "DD",
        "Least Concern": "LC",
    }
    for row in rows:
        stats.total += 1
        if category := categories.get(row["redlist_category"]):
            setattr(stats, category, getattr(stats, category) + 1)
        if row["goat_data_deficient"]:
            stats.goat_data_deficient += 1
            dna_level = "GoaT Data Deficient"
        elif not row["family_has_dna"]:
            stats.missing_family_dna += 1
            dna_level = "Missing Family"
        elif not row["genus_has_dna"]:
            stats.missing_genus_dna += 1
            dna_level = "Missing Genus"
        elif not row["has_dna_species_level"]:
            stats.missing_species_dna += 1
            dna_level = "Missing Species"
        else:
            dna_level = "Sampled"
        result.append(
            CellSpeciesRow(
                gbif_accepted_id=row["gbif_accepted_id"],
                iucn_sis_id=row["iucn_sis_id"],
                iucn_assessment_id=row["iucn_assessment_id"],
                gbif_taxon_id=row["gbif_taxon_id"],
                goat_taxon_id=row["goat_taxon_id"],
                species_name=row["species_name"] or "",
                family=row["family"],
                redlist_category=row["redlist_category"],
                dna_level=dna_level,
                metadata_status=row.get("metadata_status", "assessed"),
                spatial_redlist_category=row.get("spatial_redlist_category"),
                iucn_assessment_latest=row.get("iucn_assessment_latest"),
            )
        )
    return CellDetailsResponse(
        h3_index=h3_index,
        resolution=resolution,
        boundaries=boundaries,
        species=result,
        stats=stats,
    )


def _cell_boundary_memberships(h3_index: str) -> list[CellBoundaryMembership]:
    memberships: list[CellBoundaryMembership] = []
    for framework, framework_name, setting_name in CELL_BOUNDARY_FRAMEWORKS:
        path = Path(getattr(settings, setting_name))
        if not path.is_file():
            continue
        index = load_jurisdiction_index(str(path))
        codes = sorted(
            set(index.codes_for_cell(h3_index)),
            key=lambda code: (index.names.get(code, code), code),
        )
        memberships.extend(
            CellBoundaryMembership(
                framework=framework,
                framework_name=framework_name,
                code=code,
                name=index.names.get(code, code),
            )
            for code in codes
        )
    return memberships


def _aggregate_coverage() -> tuple[tuple[int, ...], int]:
    return aggregate_coverage(settings.res7_aggregate_parts_dir)


def _validate_tile_boundary_filters(
    filters: tuple[tuple[tuple[str, ...], Path, str], ...],
) -> None:
    """Validate only boundary catalogues that participate in this tile request."""
    for selected_codes, path, error_detail in filters:
        invalid = set(selected_codes) - set(load_jurisdiction_index(str(path)).codes)
        if invalid:
            raise HTTPException(status_code=400, detail=error_detail)


@get("/api/tiles/res7/{z:int}/{x:int}/{y:int}", include_in_schema=False)
async def resolution7_tile(
    z: int,
    x: int,
    y: int,
    system: str = "all",
    jurisdictions: str = "",
    admin0: str = "",
    admin1: str = "",
    municipality: str = "",
    eez: str = "",
    conservation_framework: str = "",
    v: str = "",
) -> Response[bytes]:
    del v  # Browser cache-busting token supplied by map metadata.
    if settings.res7_aggregate_parts_dir is None:
        raise NotFoundException(detail="Resolution-7 aggregate directory is not configured")
    if z < 8 or z > 12 or x < 0 or y < 0 or x >= 2**z or y >= 2**z:
        raise NotFoundException(detail="Invalid resolution-7 tile")
    normalized_system = system.lower()
    if normalized_system not in SYSTEM_NAMES:
        raise HTTPException(status_code=400, detail="Invalid ecosystem system")
    selected_jurisdictions = tuple(
        dict.fromkeys(
            code.strip().upper() for code in f"{jurisdictions},{admin0}".split(",") if code.strip()
        )
    )
    selected_admin1 = tuple(
        dict.fromkeys(code.strip() for code in admin1.split(",") if code.strip())
    )
    selected_municipalities = tuple(
        dict.fromkeys(code.strip() for code in municipality.split(",") if code.strip())
    )
    selected_eezs = tuple(
        dict.fromkeys(code.strip().upper() for code in eez.split(",") if code.strip())
    )
    selected_conservation = tuple(
        dict.fromkeys(code.strip() for code in conservation_framework.split(",") if code.strip())
    )
    if any(
        len(values) > 30
        for values in (
            selected_jurisdictions,
            selected_admin1,
            selected_municipalities,
            selected_eezs,
            selected_conservation,
        )
    ):
        raise HTTPException(status_code=400, detail="Select at most 30 boundaries per framework")
    active_boundary_filters = tuple(
        (selected_codes, path, error_detail)
        for selected_codes, path, error_detail in (
            (
                selected_jurisdictions,
                settings.jurisdictions_path,
                "Invalid jurisdiction code",
            ),
            (
                selected_admin1,
                settings.admin1_boundaries_path,
                "Invalid state or province code",
            ),
            (
                selected_municipalities,
                settings.municipality_boundaries_path,
                "Invalid municipality code",
            ),
            (
                selected_eezs,
                getattr(settings, "eez_boundaries_path", Path()),
                "Invalid EEZ code",
            ),
            (
                selected_conservation,
                settings.conservation_boundaries_path,
                "Invalid conservation framework code",
            ),
        )
        if selected_codes
    )
    if active_boundary_filters:
        await asyncio.to_thread(
            _validate_tile_boundary_filters,
            active_boundary_filters,
        )
    base_cells, coverage_version = _aggregate_coverage()
    content = await asyncio.to_thread(
        render_tile,
        str(settings.res7_aggregate_parts_dir),
        z,
        x,
        y,
        normalized_system,
        coverage_version,
        str(settings.jurisdictions_path) if selected_jurisdictions else "",
        selected_jurisdictions,
        str(settings.admin1_boundaries_path) if selected_admin1 else "",
        selected_admin1,
        str(settings.municipality_boundaries_path) if selected_municipalities else "",
        selected_municipalities,
        str(getattr(settings, "eez_boundaries_path", Path())) if selected_eezs else "",
        selected_eezs,
        str(settings.conservation_boundaries_path) if selected_conservation else "",
        selected_conservation,
    )
    return Response(
        content=content,
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@get("/tiles/{filename:str}", include_in_schema=False)
async def tiles(request: Request, filename: str) -> File | Response[bytes] | Stream:
    if filename == settings.map_metadata_path.name:
        if not settings.map_metadata_path.is_file():
            raise NotFoundException(detail="Map metadata build artifact not found")
        metadata = json.loads(settings.map_metadata_path.read_text())
        metadata["coverage_profile"] = current_profile.get()
        if settings.pmtiles_path.is_file():
            metadata["archive_version"] = _archive_version(settings.pmtiles_path.stat())
        aggregate_cells, aggregate_version = _aggregate_coverage()
        if settings.res7_aggregate_parts_dir is not None:
            metadata["res7_delivery"] = "dynamic-h3-v1"
            metadata["res7_base_cells"] = list(aggregate_cells)
            metadata["res7_coverage_version"] = aggregate_version
            available = set(metadata.get("available_resolutions", [])) - {7}
            if aggregate_cells:
                available.add(7)
            metadata["available_resolutions"] = sorted(available)
            source_cells = []
            if settings.res7_parts_dir is not None:
                source_cells = [
                    int(match.group(1))
                    for path in settings.res7_parts_dir.glob("base_*.parquet")
                    if (match := re.fullmatch(r"base_(\d+)\.parquet", path.name))
                ]
            complete = set(metadata.get("complete_resolutions", [3])) - {7}
            if source_cells and set(source_cells) == set(aggregate_cells):
                complete.add(7)
            metadata["complete_resolutions"] = sorted(complete)
        return Response(
            content=json.dumps(metadata, separators=(",", ":")).encode(),
            media_type="application/json",
            headers={"Cache-Control": "no-cache"},
        )
    if re.fullmatch(r"res3-priorities-[0-9a-f]{16}\.arrow", filename):
        path = settings.tile_dir / filename
        if not path.is_file():
            raise NotFoundException(detail="Coarse map snapshot not found")
        return File(
            path=path,
            media_type="application/vnd.apache.arrow.file",
            content_disposition_type="inline",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )
    # Keep the browser-facing URL stable when a preview archive has a more
    # descriptive on-disk filename (for example priorities-res3-res7.pmtiles).
    if filename not in {"priorities.pmtiles", settings.pmtiles_path.name}:
        raise NotFoundException()
    path = settings.pmtiles_path
    if not path.is_file():
        raise NotFoundException(detail="PMTiles build artifact not found")
    return await asyncio.to_thread(
        _archive_response, path, request.headers, request.query_params.get("v")
    )


def _archive_version(stat: os.stat_result) -> str:
    """Opaque representation identity under ordinary filesystem publication."""
    identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    return hashlib.sha256(repr(identity).encode()).hexdigest()[:32]


def _archive_response(path: Path, headers, version: str | None) -> Response | Stream:
    # Open once so validators and range bytes refer to one inode even if current
    # switches while the response is being prepared. Managed producers replace
    # published files atomically; in-place concurrent writes are unsupported.
    stream = path.open("rb")
    stat = os.fstat(stream.fileno())
    token = _archive_version(stat)
    etag = f'"{token}"'
    common = {"ETag": etag, "Accept-Ranges": "bytes", "Cache-Control": "no-cache"}
    if version and version != token:
        stream.close()
        raise HTTPException(
            status_code=409, detail="Map archive generation changed; reload map metadata"
        )
    if headers.get("if-match") not in (None, "*", etag):
        stream.close()
        return Response(content=b"", status_code=412, headers=common)
    if headers.get("if-none-match") in ("*", etag):
        stream.close()
        return Response(content=b"", status_code=304, headers=common)
    size = stat.st_size
    range_header = headers.get("range")
    if headers.get("if-range") not in (None, etag):
        range_header = None
    if range_header:
        match = re.fullmatch(r"bytes=(\d+)-(\d*)", range_header.strip())
        if not match:
            stream.close()
            return Response(
                content=b"", status_code=416, headers={**common, "Content-Range": f"bytes */{size}"}
            )
        start = int(match.group(1))
        end = min(int(match.group(2)) if match.group(2) else size - 1, size - 1)
        if start > end or start >= size:
            stream.close()
            return Response(
                content=b"", status_code=416, headers={**common, "Content-Range": f"bytes */{size}"}
            )
        try:
            stream.seek(start)
            content = stream.read(end - start + 1)
        finally:
            stream.close()
        return Response(
            content=content,
            status_code=206,
            media_type="application/vnd.pmtiles",
            headers={
                **common,
                "Content-Range": f"bytes {start}-{end}/{size}",
                "Content-Length": str(len(content)),
            },
        )

    def chunks():
        try:
            while chunk := stream.read(1024 * 1024):
                yield chunk
        finally:
            stream.close()

    return Stream(
        chunks(),
        media_type="application/vnd.pmtiles",
        headers={**common, "Content-Length": str(size)},
    )


app = Litestar(
    route_handlers=[
        dna_evidence_scenario,
        phylogenetic_scenario,
        source_support_details,
        species_range_details,
        assessment_details,
        coverage_profiles,
        health,
        stats,
        species_page,
        species_suggestions,
        species_cells,
        cell_species,
        resolution7_tile,
        tiles,
    ],
    lifespan=[database_lifespan],
    middleware=[CoverageProfileMiddleware],
    cors_config=CORSConfig(allow_origins=[settings.frontend_origin]),
    compression_config=CompressionConfig(backend="gzip", minimum_size=1_024, gzip_compress_level=4),
    openapi_config=OpenAPIConfig(
        title="Ark-IV API",
        version="1.0.0",
        description=(
            "Typed serving API for species and H3 cell details. Coarse map data "
            "uses Arrow/PMTiles and fine global cells use cached on-demand tiles. "
            "coverage_profile selects matching SQL and map artifacts. Score weights "
            "and threat/DNA products must be finite. Cell IDs must be valid H3 cells "
            "matching resolution; nonempty ecosystem filters must be canonical."
        ),
        path="/schema",
    ),
)
