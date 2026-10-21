"""Optional continuous scores from distinct memberships, outside base metrics."""

from __future__ import annotations

from pathlib import Path

import pyarrow.parquet as pq

from ark_pipeline.aggregation.membership_contract import canonical_memberships
from ark_pipeline.aggregation.species_lists import sql_path
from ark_pipeline.runtime.provenance import identity_digest


def aggregate_scores(connection, membership_paths: list[Path], species_scores: Path,
                     output: Path, *, resolution: int, scenario_id: str,
                     membership_generation: str, profile: str, ecosystem: str = 'all') -> dict:
    """Sum individual opportunities, not joint branch-union benefit.

    Coarse input must contain distinct coarse membership. Never sum fine gaps
    to obtain coarse values. Ecosystem-specific callers must supply memberships
    and species scores already restricted to that named profile/ecosystem;
    represented-baseline calculation remains global to the accepted tree scope.
    """
    if resolution not in (3, 7) or not membership_paths or not all((scenario_id, membership_generation, profile)):
        raise ValueError('Require resolution, memberships and complete aggregation identity')
    if ecosystem not in ('all', 'Terrestrial', 'Freshwater', 'Marine'):
        raise ValueError('Require an explicit supported ecosystem scope')
    source = '[' + ','.join(sql_path(p) for p in membership_paths) + ']'
    canonical = canonical_memberships(membership_paths, resolution)
    connection.execute(f"""CREATE OR REPLACE TEMP VIEW scenario_memberships AS
        SELECT {'' if canonical else 'DISTINCT'} h3_cell::UBIGINT AS h3_index,
            unnest(species_ids)::VARCHAR AS iucn_sis_id
        FROM read_parquet({source}, hive_partitioning=false)""")
    if not canonical and connection.execute('SELECT count(*) FROM scenario_memberships WHERE ((h3_index >> 52) & 15) != ? OR h3_index IS NULL OR iucn_sis_id IS NULL', [resolution]).fetchone()[0]:
        raise ValueError('Membership resolution/identity mismatch')
    connection.execute(f'CREATE OR REPLACE TEMP VIEW scenario_scores AS SELECT * FROM read_parquet({sql_path(species_scores)})')
    if connection.execute('''SELECT count(*) FROM (SELECT iucn_sis_id FROM scenario_scores GROUP BY 1 HAVING count(*)>1)''').fetchone()[0]:
        raise ValueError('Duplicate species scores')
    if connection.execute('SELECT count(*) FROM scenario_scores WHERE priority < 0 OR NOT isfinite(priority)').fetchone()[0]:
        raise ValueError('Scores must be finite and nonnegative or null')
    identity = dict(scenario_id=scenario_id, memberships=membership_generation, profile=profile,
                    ecosystem=ecosystem, resolution=resolution, aggregation='distinct-individual-opportunities-v1')
    spatial_id = identity_digest(identity)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValueError('Analytical score output already exists')
    temporary = output.with_suffix('.tmp')
    try:
        connection.execute(f"""COPY (SELECT m.h3_index,
            sum(s.priority)::DOUBLE AS priority,
            count(s.priority)::BIGINT AS scored_species,
            count(*) - count(s.priority) AS unscored_species,
            count(*) FILTER(WHERE s.baseline_incomplete) AS baseline_incomplete_species
            FROM scenario_memberships m LEFT JOIN scenario_scores s USING(iucn_sis_id)
            GROUP BY m.h3_index ORDER BY m.h3_index)
            TO {sql_path(temporary)} (FORMAT PARQUET, COMPRESSION ZSTD)""")
        if connection.execute('SELECT count(*) FROM read_parquet(?) WHERE NOT isfinite(priority)', [str(temporary)]).fetchone()[0]:
            raise ValueError('Cell priority overflows')
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return {'spatial_scenario_id': spatial_id, 'identity': identity,
            'cells': pq.read_metadata(output).num_rows, 'bytes': output.stat().st_size}
