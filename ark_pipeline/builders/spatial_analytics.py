"""Range denominators and stewardship from retained global memberships.

Outputs are analytical Parquet, never extra base-map metrics. Counts describe
mapped any-touch cells. Jurisdictions may overlap and their fractions need not
sum to one; boundary-touch cell membership is not an exact polygon intersection.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import h3ronpy
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from ark_pipeline.aggregation.membership_contract import canonical_memberships
from ark_pipeline.aggregation.pairs import RES3_PARENT_SQL
from ark_pipeline.aggregation.species_lists import sql_path


def build_jurisdictions(connection: duckdb.DuckDBPyConnection, list_paths: list[Path],
                        boundaries: dict[str, Path], output: Path, *, batch_cells: int = 4096) -> dict:
    """Intersect mapped cells with pinned catalogues using the tile boundary policy.

    Scan cell IDs without expanding species lists. Overlapping jurisdictions are
    retained independently; this measures cell membership, not land-area shares.
    """
    from ark_pipeline.tile_spatial import BoundaryBatchIndex
    from ark_pipeline.tiles import geometries_for_cells

    if not boundaries or not list_paths or batch_cells < 1:
        raise ValueError('Require boundary catalogues, memberships and a positive batch size')
    indexes = {name: BoundaryBatchIndex.from_path(path) for name, path in boundaries.items()}
    if any(not name or not index.codes or any(not code for code in index.codes) or len(set(index.codes)) != len(index.codes) for name, index in indexes.items()):
        raise ValueError('Boundary frameworks and codes must be nonempty and unique')
    schema = pa.schema([('h3_index', pa.uint64()), ('framework', pa.string()), ('code', pa.string())])
    source = '[' + ','.join(sql_path(p) for p in list_paths) + ']'
    query = f'SELECT DISTINCT h3_cell FROM read_parquet({source}, hive_partitioning=false) WHERE len(species_ids)>0 ORDER BY h3_cell'
    temporary = output.with_suffix('.tmp')
    cells = relationships = 0
    with connection.execute(query).to_arrow_reader(batch_cells) as reader, pq.ParquetWriter(temporary, schema, compression='zstd') as writer:
        for batch in reader:
            ids = batch.column(0).to_pylist()
            if any(v is None or ((v >> 52) & 15) != 7 for v in ids):
                raise ValueError('Jurisdictions require nonnull resolution-7 cells')
            _, geometries = geometries_for_cells([format(v, 'x') for v in ids])
            for framework, index in indexes.items():
                hits = index.codes_for_geometries(geometries)
                rows = [{'h3_index': cell, 'framework': framework, 'code': code}
                        for cell, codes in zip(ids, hits, strict=True) for code in codes]
                writer.write_table(pa.Table.from_pylist(rows, schema=schema))
                relationships += len(rows)
            cells += len(ids)
    temporary.replace(output)
    return {'cells': cells, 'jurisdiction_memberships': relationships,
            'boundary_policy': 'tile-any-intersection-antimeridian-safe-v1'}


def build_spatial_analytics(connection: duckdb.DuckDBPyConnection, list_paths: list[Path],
                            output: Path, *, profile: str, membership_generation: str,
                            coverage_status: str, jurisdictions: Path | None = None,
                            weights: Path | None = None,
                            omissions: Path | None = None) -> dict:
    """Caller pins complete inputs; DuckDB sort/group work can spill to scratch.

    coverage_status is explicit (complete/partial/unknown). It must describe the
    supplied global generation, never the currently selected viewport.
    """
    if coverage_status not in ('complete', 'partial', 'unknown') or not profile or not membership_generation:
        raise ValueError('Require explicit profile, generation and coverage status')
    if not list_paths:
        raise ValueError('No global membership inputs')
    output.mkdir(parents=True, exist_ok=False)
    source = '[' + ','.join(sql_path(p) for p in list_paths) + ']'
    canonical = canonical_memberships(list_paths, 7)
    connection.execute(f"""CREATE OR REPLACE TEMP VIEW analytical_pairs AS
        SELECT {'' if canonical else 'DISTINCT'} h3_cell::UBIGINT AS h3_index, unnest(species_ids)::BIGINT AS iucn_sis_id
        FROM read_parquet({source}, hive_partitioning=false)""")
    invalid = 0 if canonical else connection.execute('SELECT count(*) FROM analytical_pairs WHERE ((h3_index >> 52) & 15) != 7 OR h3_index IS NULL OR iucn_sis_id IS NULL').fetchone()[0]
    if invalid:
        raise ValueError('Global denominators require nonnull resolution-7 memberships')
    areas_path = output / 'cell_areas.parquet'
    area_query = (f'SELECT h3_cell AS h3_index FROM read_parquet({source}, hive_partitioning=false) WHERE len(species_ids)>0 ORDER BY h3_cell'
                  if canonical else 'SELECT DISTINCT h3_index FROM analytical_pairs ORDER BY h3_index')
    reader = connection.execute(area_query).to_arrow_reader(65536)
    schema = pa.schema([('h3_index', pa.uint64()), ('cell_area_km2', pa.float64())])
    with reader, pq.ParquetWriter(areas_path, schema, compression='zstd') as writer:
        for batch in reader:
            areas = np.asarray(h3ronpy.cells_area_km2(batch['h3_index']))
            writer.write_table(pa.Table.from_arrays([batch['h3_index'], pa.array(areas)], schema=schema))
    connection.execute(f"CREATE OR REPLACE TEMP VIEW analytical_areas AS SELECT * FROM read_parquet({sql_path(areas_path)})")
    metadata = ', '.join(f"'{v.replace(chr(39), chr(39)*2)}'::VARCHAR AS {k}" for k, v in {
        'spatial_profile': profile, 'membership_generation': membership_generation,
        'coverage_status': coverage_status, 'denominator': 'global-res7-distinct-mapped-cells-v1'}.items())
    omission_columns = 'NULL::BIGINT AS missing_basin_relationships, NULL::BOOLEAN AS affected_by_missing_basins'
    omission_join = ''
    if omissions:
        # Preserve affected species even when none of their basins were mapped.
        omitted = output / 'omitted_species.parquet'
        connection.execute(f"""COPY (SELECT iucn_sis_id::BIGINT AS iucn_sis_id,
            count(DISTINCT hybas_id)::BIGINT AS missing_basin_relationships
            FROM read_parquet({sql_path(omissions)})
            WHERE reason='missing_geometry' GROUP BY 1 ORDER BY 1)
            TO {sql_path(omitted)} (FORMAT PARQUET, COMPRESSION ZSTD)""")
        omission_join = f'LEFT JOIN read_parquet({sql_path(omitted)}) o USING(iucn_sis_id)'
        omission_columns = '''coalesce(o.missing_basin_relationships, 0)::BIGINT AS missing_basin_relationships,
            (o.missing_basin_relationships IS NOT NULL) AS affected_by_missing_basins'''
    ranges = output / 'species_ranges.parquet'
    connection.execute(f"""COPY (WITH mapped AS (SELECT iucn_sis_id, count(*)::BIGINT AS global_cell_count,
        sum(cell_area_km2)::DOUBLE AS global_mapped_area_km2, {metadata}
        FROM analytical_pairs JOIN analytical_areas USING(h3_index)
        GROUP BY iucn_sis_id)
        SELECT mapped.*, {omission_columns} FROM mapped {omission_join} ORDER BY iucn_sis_id)
        TO {sql_path(ranges)} (FORMAT PARQUET, COMPRESSION ZSTD)""")
    result = {'species': pq.read_metadata(ranges).num_rows, 'range_bytes': ranges.stat().st_size,
              'membership_strategy': 'validated-canonical' if canonical else 'distinct',
              'coverage_status': coverage_status, 'spatial_profile': profile,
              'membership_generation': membership_generation}
    if jurisdictions:
        # Input: distinct-cell jurisdiction catalogue, not range geometry.
        if connection.execute(f'''SELECT count(*) FROM read_parquet({sql_path(jurisdictions)})
            WHERE h3_index IS NULL OR ((h3_index::UBIGINT >> 52) & 15) != 7
                OR framework IS NULL OR framework='' OR code IS NULL OR code='' ''').fetchone()[0]:
            raise ValueError('Jurisdictions require nonnull resolution-7 cells, frameworks and codes')
        connection.execute(f"""COPY (WITH boundaries AS (
            SELECT DISTINCT h3_index::UBIGINT AS h3_index, framework, code
            FROM read_parquet({sql_path(jurisdictions)}))
            SELECT p.iucn_sis_id, b.framework, b.code,
                count(*)::BIGINT AS jurisdiction_cells,
                count(*)::DOUBLE / r.global_cell_count AS mapped_cell_fraction,
                sum(a.cell_area_km2) / r.global_mapped_area_km2 AS mapped_area_fraction,
                r.global_cell_count, r.coverage_status, r.spatial_profile, r.membership_generation
            FROM analytical_pairs p JOIN boundaries b USING(h3_index)
            JOIN analytical_areas a USING(h3_index)
            JOIN read_parquet({sql_path(ranges)}) r USING(iucn_sis_id)
            GROUP BY p.iucn_sis_id, b.framework, b.code, r.global_cell_count,
                     r.global_mapped_area_km2, r.coverage_status, r.spatial_profile, r.membership_generation
            ORDER BY p.iucn_sis_id, b.framework, b.code)
            TO {sql_path(output / 'stewardship.parquet')} (FORMAT PARQUET, COMPRESSION ZSTD)""")
    if weights:
        connection.execute(f"CREATE OR REPLACE TEMP VIEW analytical_weights AS SELECT * FROM read_parquet({sql_path(weights)})")
        if connection.execute('SELECT count(*) FROM (SELECT iucn_sis_id FROM analytical_weights GROUP BY 1 HAVING count(*)>1)').fetchone()[0]:
            raise ValueError('Duplicate species weights')
        if connection.execute('SELECT count(*) FROM analytical_weights WHERE weight < 0 OR NOT isfinite(weight)').fetchone()[0]:
            raise ValueError('Weights must be finite and nonnegative')
        fine = output / 'range_rarity_res7.parquet'
        connection.execute(f"""COPY (SELECT p.h3_index,
            sum(w.weight / r.global_cell_count)::DOUBLE AS range_rarity,
            count(w.weight)::BIGINT AS scored_species,
            count(*) - count(w.weight) AS unscored_species, {metadata}
            FROM analytical_pairs p JOIN read_parquet({sql_path(ranges)}) r USING(iucn_sis_id)
            LEFT JOIN analytical_weights w USING(iucn_sis_id)
            GROUP BY p.h3_index ORDER BY p.h3_index)
            TO {sql_path(fine)} (FORMAT PARQUET, COMPRESSION ZSTD)""")
        # Deliberately sum fine contributions: one global unit budget per species.
        # This differs from coarse distinct-species DNA-gap aggregation.
        connection.execute(f"""COPY (SELECT {RES3_PARENT_SQL} AS h3_index,
            sum(range_rarity)::DOUBLE AS range_rarity,
            sum(scored_species)::BIGINT AS scored_memberships,
            sum(unscored_species)::BIGINT AS unscored_memberships, {metadata}
            FROM read_parquet({sql_path(fine)}) GROUP BY 1 ORDER BY 1)
            TO {sql_path(output / 'range_rarity_res3.parquet')} (FORMAT PARQUET, COMPRESSION ZSTD)""")
    areas_path.unlink()
    return result


def support_summary(connection, support_paths: list[Path], h3_index: int) -> dict:
    """Overlapping source-route fractions over distinct selected-cell species."""
    source = '[' + ','.join(sql_path(p) for p in support_paths) + ']'
    rows = connection.execute(f"""WITH species AS (
        SELECT iucn_sis_id, bit_or(source_support) AS mask
        FROM read_parquet({source}) WHERE h3_index=? GROUP BY 1)
        SELECT count(*), count(*) FILTER(WHERE mask & 1 != 0),
        count(*) FILTER(WHERE mask & 2 != 0), count(*) FILTER(WHERE mask & 4 != 0),
        count(*) FILTER(WHERE bit_count(mask & 7)>1), count(*) FILTER(WHERE mask=0 OR mask & 8 != 0)
        FROM species""", [h3_index]).fetchone()
    counts = dict(zip(('species', 'polygon', 'point', 'hydrobasin', 'multiple', 'unknown'), rows))
    return {**counts, 'fractions': {k: v / rows[0] if rows[0] else None for k, v in counts.items() if k != 'species'},
            'interpretation': 'source support; overlapping fractions, not biological confidence'}
