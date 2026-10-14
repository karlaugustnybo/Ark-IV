"""Explicit, read-only inputs for the exploratory serving notebooks.

Paths are escaped once for DuckDB's SQL file literals. The identity covers the
consumed bytes, including the crosswalk, and is not a provenance receipt or a
claim about compatible provider vintages. No input is acquired or rebuilt here.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

SCHEMAS = {
    "species.parquet": {
        "gbif_accepted_id",
        "iucn_sis_id",
        "species_name",
        "family",
        "redlist_category",
        "has_dna_species_level",
        "genus_has_dna",
        "family_has_dna",
        "goat_data_deficient",
        "has_ebp_criteria_evidence",
        "edge_group_name",
        "iucn_assessment_id",
        "gbif_taxon_id",
        "goat_taxon_id",
    },
    "species_systems.parquet": {"gbif_accepted_id", "system"},
    "species_cells_res3.parquet": {"gbif_accepted_id", "h3_indexes"},
    "cell_species_lists.parquet": {"h3_index", "species_ids"},
    "cell_boundaries.parquet": {
        "h3_index",
        "admin0",
        "admin1",
        "municipality",
        "conservation_framework",
    },
}
CROSSWALK_SCHEMA = {
    "iucn_sis_id",
    "ncbi_class",
    "iucn_class",
    "iucn_assessment_date",
    "iucn_year_published",
    "taxonomic_concept_relation",
    "safe_for_automatic_species_trait_transfer",
}


@dataclass(frozen=True)
class NotebookInputs:
    exports: str
    crosswalk: str
    identity: str


def configured_inputs(files: tuple[str, ...] = tuple(SCHEMAS)) -> NotebookInputs:
    """Resolve and validate explicit files before any dependent cell runs.

    Empty datasets and missing/schema-invalid files fail with a named input,
    rather than masquerading as biological zeroes or rendering partial results.
    """
    import duckdb

    exports = Path(os.environ.get("ARK_NOTEBOOK_EXPORTS", "data/global/exports"))
    crosswalk = Path(
        os.environ.get(
            "ARK_NOTEBOOK_CROSSWALK", "data/exports/iucn_goat_global/iucn_goat_crosswalk.parquet"
        )
    )
    paths = [(exports / name, SCHEMAS[name]) for name in files]
    paths.append((crosswalk, CROSSWALK_SCHEMA))
    identity = hashlib.sha256()
    with duckdb.connect() as connection:
        for path, required in paths:
            if not path.is_file():
                raise FileNotFoundError(
                    f"Notebook input not found: {path}; configure ARK_NOTEBOOK_EXPORTS and ARK_NOTEBOOK_CROSSWALK"
                )
            relation = connection.read_parquet(str(path))
            missing = required - set(relation.columns)
            if missing:
                raise ValueError(
                    f"Notebook input {path.name} lacks columns: {', '.join(sorted(missing))}"
                )
            if relation.count("*").fetchone()[0] == 0:
                raise ValueError(f"Notebook input {path.name} is empty; no analysis denominator")
            key = "iucn_sis_id" if path == crosswalk else "gbif_accepted_id"
            if path.name in {"species.parquet", "species_cells_res3.parquet"} or path == crosswalk:
                if relation.aggregate(f"count(*) - count(DISTINCT {key})").fetchone()[0]:
                    raise ValueError(
                        f"Notebook input {path.name} has duplicate or null species keys"
                    )
            if path.name == "species_cells_res3.parquet":
                if (
                    relation.filter("h3_indexes IS NULL OR len(h3_indexes) = 0")
                    .count("*")
                    .fetchone()[0]
                ):
                    raise ValueError(
                        "Mapped ranges must contain at least one cell; missing coverage belongs outside this table"
                    )
            identity.update(path.name.encode())
            with path.open("rb") as stream:
                while block := stream.read(1024 * 1024):
                    identity.update(block)
    return NotebookInputs(
        str(exports).replace("'", "''"), str(crosswalk).replace("'", "''"), identity.hexdigest()
    )
