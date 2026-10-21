"""Load build-pipeline Parquet artifacts into PostgreSQL."""

import asyncio
from collections.abc import Iterator
from pathlib import Path

import asyncpg
import pyarrow.parquet as pq

from backend.config import ROOT, get_settings


def rows(path: Path, columns: list[str]) -> Iterator[tuple]:
    """Yield bounded Parquet batches directly into Postgres COPY."""
    parquet = pq.ParquetFile(path)
    for batch in parquet.iter_batches(batch_size=65_536, columns=columns):
        values = [batch.column(index).to_pylist() for index in range(len(columns))]
        yield from zip(*values, strict=True)


def partition_row_count(parts_dir: Path | None) -> int | None:
    """Return the total rows in partitioned Parquet data without scanning it."""
    if parts_dir is None:
        return None
    parts = sorted(parts_dir.glob("base_*.parquet"))
    if not parts:
        return None
    return sum(pq.ParquetFile(path).metadata.num_rows for path in parts)


async def load() -> None:
    settings = get_settings()
    external_res7_cells = partition_row_count(settings.res7_parts_dir)
    phylogenetic_path = None
    if settings.phylogenetic_generation_path is not None:
        from ark_pipeline.builders.phylogenetic import load_generation
        pinned = settings.phylogenetic_generation_path.resolve(strict=True)
        load_generation(pinned)  # Verify the generation before the SQL transaction.
        phylogenetic_path = pinned / 'species_phylogenetic.parquet'

    connection = await asyncpg.connect(settings.database_url)
    try:
        await connection.execute((ROOT / "backend" / "schema.sql").read_text())
        async with connection.transaction():
            await connection.execute(
                "TRUNCATE species_passage_cells, cell_species, cell_species_lists, species_cells, cell_boundaries, species_systems, "
                "species, species_phylogenetic_scores, app_stats CASCADE"
            )
            species_columns = [
                "gbif_accepted_id",
                "iucn_sis_id",
                "iucn_assessment_id",
                "gbif_taxon_id",
                "goat_taxon_id",
                "species_name",
                "family",
                "redlist_category",
                "has_dna_species_level",
                "genus_has_dna",
                "family_has_dna",
                "goat_data_deficient",
                "edge_group_name",
                "has_ebp_criteria_evidence",
            ]
            available = set(
                pq.ParquetFile(settings.export_dir / "species.parquet").schema_arrow.names
            )
            species_columns += [
                column
                for column in (
                    "metadata_status",
                    "spatial_redlist_category",
                    "iucn_assessment_latest",
                    "iucn_population_trend", "iucn_assessment_date", "iucn_assessment_scope",
                    "iucn_threat_classifications", "iucn_attributes_source_sha256",
                )
                if column in available
            ]
            await connection.copy_records_to_table(
                "species",
                records=rows(settings.export_dir / "species.parquet", species_columns),
                columns=species_columns,
            )
            if phylogenetic_path is not None:
                columns = ['generation_id', 'iucn_sis_id', 'tree_tip', 'ed_myr', 'edge1',
                           'edge2_myr_point', 'edge2_myr_median', 'edge2_myr_p025', 'edge2_myr_p975',
                           'score_status', 'gap_myr', 'represented', 'baseline_incomplete',
                           'availability_reason', 'nearest_tip', 'nearest_distance_myr',
                           'evidence_state', 'qualifying_evidence', 'sample_reported', 'project_reported',
                           'criteria_reported', 'assembly_reported', 'resampling_reported']
                await connection.copy_records_to_table('species_phylogenetic_scores',
                    records=rows(phylogenetic_path, columns), columns=columns)
            system_columns = ["gbif_accepted_id", "system"]
            await connection.copy_records_to_table(
                "species_systems",
                records=rows(settings.export_dir / "species_systems.parquet", system_columns),
                columns=system_columns,
            )
            list_columns = ["h3_index", "resolution", "species_ids"]
            await connection.copy_records_to_table(
                "cell_species_lists",
                records=rows(settings.export_dir / "cell_species_lists.parquet", list_columns),
                columns=list_columns,
            )
            species_cells_path = settings.export_dir / "species_cells_res3.parquet"
            if species_cells_path.is_file():
                species_cell_columns = ["gbif_accepted_id", "resolution", "h3_indexes"]
                await connection.copy_records_to_table(
                    "species_cells",
                    records=rows(species_cells_path, species_cell_columns),
                    columns=species_cell_columns,
                )
            passage_path = settings.export_dir / "species_passage_cells_res3.parquet"
            if passage_path.is_file():
                columns = ["gbif_accepted_id", "resolution", "h3_indexes"]
                await connection.copy_records_to_table(
                    "species_passage_cells",
                    records=rows(passage_path, columns),
                    columns=columns,
                )
            # HTTP cell details use the compact species_ids arrays above. The
            # expanded relation can contain millions to billions of rows and is
            # retained only as an opt-in compatibility load.
            expanded_path = settings.export_dir / "cell_species.parquet"
            if settings.load_expanded_cell_species and expanded_path.is_file():
                cell_columns = ["h3_index", "resolution", "gbif_accepted_id"]
                await connection.copy_records_to_table(
                    "cell_species",
                    records=rows(expanded_path, cell_columns),
                    columns=cell_columns,
                )
            boundaries_path = settings.export_dir / "cell_boundaries.parquet"
            if boundaries_path.is_file():
                source_boundary_columns = [
                    "h3_index",
                    "resolution",
                    "admin0",
                    "admin1",
                    "municipality",
                    "eez",
                    "conservation_framework",
                ]
                target_boundary_columns = [
                    "h3_index",
                    "resolution",
                    "admin0_codes",
                    "admin1_codes",
                    "municipality_codes",
                    "eez_codes",
                    "conservation_framework_codes",
                ]
                await connection.copy_records_to_table(
                    "cell_boundaries",
                    records=rows(boundaries_path, source_boundary_columns),
                    columns=target_boundary_columns,
                )
            await connection.execute(
                """
                INSERT INTO app_stats (
                    singleton, total, critically_endangered, edge_species,
                    needs_dna_sampling, res3_cells, res7_cells
                )
                SELECT TRUE,
                    COUNT(*)::int,
                    COUNT(*) FILTER (WHERE redlist_category = 'Critically Endangered')::int,
                    COUNT(*) FILTER (WHERE edge_group_name IS NOT NULL)::int,
                    COUNT(*) FILTER (WHERE has_dna_species_level = false)::int,
                    (SELECT COUNT(*)::int FROM cell_species_lists WHERE resolution = 3),
                    COALESCE(
                        $1::int,
                        (SELECT COUNT(*)::int FROM cell_species_lists WHERE resolution = 7)
                    )
                FROM species
                """,
                external_res7_cells,
            )
            await connection.execute("ANALYZE")
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(load())
