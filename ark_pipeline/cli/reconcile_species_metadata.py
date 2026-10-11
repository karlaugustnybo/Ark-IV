"""Repair species metadata only; never compute H3 membership, scores or tiles."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import duckdb

from ark_pipeline.builders.iucn_metadata import apply_iucn_metadata, validate_evidence
from ark_pipeline.cli.sources_download_iucn import atomic_json
from ark_pipeline.runtime.metadata_publication import publish_metadata


def reconcile(
    input_dir: Path, details_dir: Path, output_dir: Path, *, expected_release: str | None = None,
    reviewed_evidence_path: Path | None = None
) -> dict:
    if input_dir.resolve() == output_dir.resolve():
        raise ValueError("Keep the input dimension intact; choose a separate output directory")
    with duckdb.connect() as connection:
        connection.execute("SET memory_limit='1GB'")
        connection.execute("SET threads=1")
        connection.execute(
            "CREATE TABLE species AS SELECT * FROM read_parquet(?)",
            [str(input_dir / "species.parquet")],
        )
        connection.execute(
            "CREATE TABLE species_systems AS SELECT * FROM read_parquet(?)",
            [str(input_dir / "species_systems.parquet")],
        )
        connection.execute("CREATE TABLE baseline AS SELECT * FROM species")
        connection.execute("CREATE TABLE baseline_systems AS SELECT * FROM species_systems")
        report = apply_iucn_metadata(connection, details_dir, expected_release=expected_release,
                                     reviewed_evidence_path=reviewed_evidence_path)
        mutable = {"species_name", "family", "iucn_assessment_id", "redlist_category", "metadata_status",
                   "iucn_metadata_provenance", "iucn_ecosystem_evidence", "iucn_assessment_latest"}
        immutable = [row[1] for row in connection.execute("PRAGMA table_info('baseline')").fetchall()
                     if row[1] not in mutable]
        differences = " OR ".join(
            'b."' + column.replace('"', '""') + '" IS DISTINCT FROM s."' + column.replace('"', '""') + '"'
            for column in immutable
        )
        checks = {
            "non_metadata_fields_preserved": f"SELECT count(*)=0 FROM baseline b JOIN species s USING(gbif_accepted_id) WHERE {differences}",
            "identities_preserved": """SELECT count(*)=0 FROM ((SELECT gbif_accepted_id FROM baseline EXCEPT SELECT gbif_accepted_id FROM species) UNION ALL (SELECT gbif_accepted_id FROM species EXCEPT SELECT gbif_accepted_id FROM baseline))""",
            "unique_species_ids": "SELECT count(*)=count(DISTINCT gbif_accepted_id) FROM species",
            "dna_traits_preserved": """SELECT count(*)=0 FROM baseline b JOIN species s USING(gbif_accepted_id) WHERE b.has_dna_species_level IS DISTINCT FROM s.has_dna_species_level OR b.genus_has_dna IS DISTINCT FROM s.genus_has_dna OR b.family_has_dna IS DISTINCT FROM s.family_has_dna OR b.goat_data_deficient IS DISTINCT FROM s.goat_data_deficient""",
            "pinned_categories_and_assessments_preserved": """SELECT count(*)=0 FROM baseline b JOIN species s USING(gbif_accepted_id) WHERE b.iucn_assessment_id IS NOT NULL AND (b.iucn_assessment_id IS DISTINCT FROM s.iucn_assessment_id OR b.redlist_category IS DISTINCT FROM s.redlist_category)""",
            "previous_realms_preserved": "SELECT count(*)=0 FROM (SELECT * FROM baseline_systems EXCEPT SELECT gbif_accepted_id,system FROM species_systems)",
            "unique_ecosystem_relationships": "SELECT count(*)=count(DISTINCT (gbif_accepted_id,system)) FROM species_systems",
        }
        report["checks"] = {
            name: bool(connection.execute(sql).fetchone()[0]) for name, sql in checks.items()
        }
        if not all(report["checks"].values()):
            raise ValueError("Authoritative enrichment failed preservation checks")
        report["ecosystem_gaps_before"] = connection.execute(
            "SELECT count(*) FROM baseline ANTI JOIN baseline_systems USING(gbif_accepted_id)"
        ).fetchone()[0]
        columns = ["gbif_accepted_id", "species_name", "metadata_status"]
        report["ecosystem_gaps_remaining"] = [
            dict(zip(columns, row, strict=True))
            for row in connection.execute(
                "SELECT s.gbif_accepted_id,s.species_name,s.metadata_status FROM species s ANTI JOIN species_systems e USING(gbif_accepted_id) ORDER BY s.gbif_accepted_id"
            ).fetchall()
        ]
        report["ecosystem_gaps_after"] = len(report["ecosystem_gaps_remaining"])
        report["species_count"] = connection.execute("SELECT count(*) FROM species").fetchone()[0]
        report["spatial_only_before"] = connection.execute(
            "SELECT count(*) FROM baseline WHERE iucn_assessment_id IS NULL"
        ).fetchone()[0]
        report["spatial_only_after"] = connection.execute(
            "SELECT count(*) FROM species WHERE iucn_assessment_id IS NULL"
        ).fetchone()[0]
        report.update(
            status="passed",
            input_dir=str(input_dir),
            details_dir=str(details_dir),
            output_dir=str(output_dir),
            scope="metadata-only-no-serving-build",
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".metadata-repair-", dir=output_dir.parent) as temporary:
            staged = Path(temporary)
            for table in ("species", "species_systems"):
                connection.execute(
                    f"COPY (SELECT * FROM {table} ORDER BY gbif_accepted_id) TO ? (FORMAT PARQUET,COMPRESSION ZSTD)",
                    [str(staged / f"{table}.parquet")],
                )
            atomic_json(staged / "build-report.json", report)
            publish_metadata(staged, output_dir, validate=lambda: validate_evidence(report))
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--details-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--reviewed-evidence-path", type=Path)
    parser.add_argument("--expected-release", help="Require the spatial source's IUCN release")
    args = parser.parse_args()
    report = reconcile(
        args.input_dir, args.details_dir, args.output_dir, expected_release=args.expected_release,
        reviewed_evidence_path=args.reviewed_evidence_path
    )
    print(
        json.dumps(
            {
                k: report[k]
                for k in [
                    "status",
                    "species_count",
                    "ecosystem_gaps_before",
                    "ecosystem_gaps_after",
                    "spatial_only_assessments_recovered",
                ]
            }
        )
    )


if __name__ == "__main__":
    main()
