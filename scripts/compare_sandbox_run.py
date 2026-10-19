"""Compare the old global website exports with a verified sandbox download."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old", type=Path, required=True)
    parser.add_argument("--new", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    inventory = json.loads((args.new / "_cloud_storage/inventory.json").read_text())
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sandbox/coreweave/scripts"))
    from cloud_storage import verify_inventory

    verify_inventory(args.new, inventory)
    con = duckdb.connect()
    con.execute("SET memory_limit='750MB'")
    con.execute("SET threads=2")
    scratch = args.new / "_transfer" / "analysis-scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    con.execute("SET temp_directory=?", [str(scratch)])

    def rows(sql):
        result = con.execute(sql)
        names = [column[0] for column in result.description]
        return [dict(zip(names, row, strict=True)) for row in result.fetchall()]

    baseline_hashes = {}
    from cloud_storage import digest

    listed = {item["path"] for item in inventory["files"]}
    for label, root in [("old", args.old), ("new", args.new / "exports")]:
        for table, filename in [
            ("species", "species.parquet"),
            ("systems", "species_systems.parquet"),
            ("cells", "cell_species_lists.parquet"),
            ("ranges", "species_cells_res3.parquet"),
        ]:
            if label == "new" and "exports/" + filename not in listed:
                raise ValueError("Compared export is not covered by verified inventory")
            if label == "old":
                baseline_hashes[filename] = digest(root / filename)
            con.read_parquet(str(root / filename)).create_view(f"{label}_{table}")
    flags = ["has_dna_species_level", "genus_has_dna", "family_has_dna", "goat_data_deficient"]
    report = {
        "sources": {
            "old": str(args.old.resolve()),
            "baseline_sha256": baseline_hashes,
            "new": str(args.new.resolve()),
            "inventory_sha256": hashlib.sha256(
                (args.new / "_cloud_storage/inventory.json").read_bytes()
            ).hexdigest(),
        }
    }
    for label in ["old", "new"]:
        report[label] = {
            "species": rows(f"""SELECT count(*) AS rows, count(DISTINCT gbif_accepted_id) AS distinct_ids,
                count(*) FILTER (WHERE gbif_accepted_id IS NULL) AS null_ids,
                count(*) FILTER (WHERE species_name IS NULL) AS missing_names,
                count(*) FILTER (WHERE species_name LIKE 'IUCN taxon %') AS placeholder_species,
                count(*) FILTER (WHERE family IS NULL OR family='') AS missing_families,
                count(*) FILTER (WHERE edge_group_name IS NOT NULL) AS edge_species,
                {", ".join(f"count(*) FILTER (WHERE {f}) AS {f}, count(*) FILTER (WHERE {f} IS NULL) AS {f}_null" for f in flags)}
                FROM {label}_species""")[0],
            "redlist": rows(
                f"SELECT redlist_category, count(*) AS species FROM {label}_species GROUP BY 1 ORDER BY 2 DESC"
            ),
            "dna_priority": rows(f"""SELECT CASE WHEN goat_data_deficient THEN 'GoaT data deficient'
                WHEN NOT family_has_dna THEN 'Missing family' WHEN NOT genus_has_dna THEN 'Missing genus'
                WHEN NOT has_dna_species_level THEN 'Missing species' ELSE 'Covered' END AS priority,
                count(*) AS species FROM {label}_species GROUP BY 1 ORDER BY 2 DESC"""),
            "systems": rows(f"""SELECT system, count(*) AS relationships, count(DISTINCT gbif_accepted_id) AS species
                FROM {label}_systems GROUP BY 1 ORDER BY 1"""),
            "system_gaps": rows(f"""SELECT count(*) AS species_without_system FROM {label}_species s
                WHERE NOT EXISTS (SELECT 1 FROM {label}_systems t WHERE t.gbif_accepted_id=s.gbif_accepted_id)""")[
                0
            ],
            "coarse_cells": rows(f"""SELECT count(*) AS cells, sum(len(species_ids)) AS relationships,
                avg(len(species_ids)) AS mean_richness, median(len(species_ids)) AS median_richness,
                quantile_cont(len(species_ids), .95) AS p95_richness, max(len(species_ids)) AS max_richness,
                count(*) FILTER (WHERE species_ids IS NULL) AS null_lists FROM {label}_cells WHERE resolution=3""")[
                0
            ],
            "mapped_species": rows(
                f"SELECT count(DISTINCT gbif_accepted_id) AS species FROM {label}_ranges WHERE resolution=3"
            )[0],
        }
    report["membership"] = rows("""SELECT
        (SELECT count(*) FROM old_species o JOIN new_species n USING(gbif_accepted_id)) AS shared,
        (SELECT count(*) FROM new_species n ANTI JOIN old_species o USING(gbif_accepted_id)) AS added,
        (SELECT count(*) FROM old_species o ANTI JOIN new_species n USING(gbif_accepted_id)) AS removed""")[
        0
    ]
    report["shared_changes"] = (
        rows(f"""SELECT {", ".join(f"count(*) FILTER (WHERE o.{f} IS DISTINCT FROM n.{f}) AS {f}" for f in ["species_name", "family", "redlist_category", *flags, "edge_group_name"])}
        FROM old_species o JOIN new_species n USING(gbif_accepted_id)""")[0]
    )
    report["flag_transitions"] = {
        f: rows(f"""SELECT o.{f} AS old_value, n.{f} AS new_value, count(*) AS species
            FROM old_species o JOIN new_species n USING(gbif_accepted_id)
            GROUP BY 1,2 ORDER BY 1,2""")
        for f in flags
    }
    report["added_species"] = rows("""SELECT count(*) AS total,
        count(*) FILTER (WHERE n.species_name LIKE 'IUCN taxon %') AS placeholders
        FROM new_species n ANTI JOIN old_species o USING(gbif_accepted_id)""")[0]
    report["redlist_transitions"] = rows("""SELECT o.redlist_category AS old_category,
        n.redlist_category AS new_category, count(*) AS species FROM old_species o JOIN new_species n USING(gbif_accepted_id)
        WHERE o.redlist_category IS DISTINCT FROM n.redlist_category GROUP BY 1,2 ORDER BY 3 DESC""")
    report["coarse_membership"] = rows("""SELECT
        count(*) FILTER (WHERE o.h3_index IS NOT NULL AND n.h3_index IS NOT NULL) AS shared_cells,
        count(*) FILTER (WHERE o.h3_index IS NULL) AS added_cells,
        count(*) FILTER (WHERE n.h3_index IS NULL) AS removed_cells,
        avg(len(n.species_ids)-len(o.species_ids)) FILTER (WHERE o.h3_index IS NOT NULL AND n.h3_index IS NOT NULL) AS mean_shared_richness_delta,
        count(*) FILTER (WHERE len(n.species_ids)>len(o.species_ids)) AS richer_cells,
        count(*) FILTER (WHERE len(n.species_ids)<len(o.species_ids)) AS poorer_cells,
        count(*) FILTER (WHERE len(n.species_ids)=len(o.species_ids)) AS equal_richness_cells
        FROM old_cells o FULL JOIN new_cells n USING(h3_index,resolution)""")[0]
    report["largest_range_changes"] = (
        rows("""SELECT coalesce(n.gbif_accepted_id,o.gbif_accepted_id) AS species_id,
        s.species_name, coalesce(len(o.h3_indexes),0) AS old_cells, coalesce(len(n.h3_indexes),0) AS new_cells,
        coalesce(len(n.h3_indexes),0)-coalesce(len(o.h3_indexes),0) AS delta
        FROM old_ranges o FULL JOIN new_ranges n USING(gbif_accepted_id,resolution)
        LEFT JOIN new_species s ON s.gbif_accepted_id=coalesce(n.gbif_accepted_id,o.gbif_accepted_id)
        ORDER BY abs(delta) DESC LIMIT 20""")
    )
    import pyarrow.parquet as pq

    parts = [
        args.new / name
        for name in sorted(listed)
        if name.startswith("tiles/generations/")
        and "/cell-species/base_" in name
        and name.endswith(".parquet")
    ]
    report["new_fine"] = {
        "partitions": len(parts),
        "cells": sum(pq.ParquetFile(p).metadata.num_rows for p in parts),
        "bytes": sum(p.stat().st_size for p in parts),
    }
    report["scope"] = (
        "Output processing comparison; biological changes and causal explanations are not inferred"
    )
    report["coverage_audit"] = None
    audit_path = args.new / "coverage-audit/coverage-summary.json"
    if audit_path.is_file() and "coverage-audit/coverage-summary.json" in listed:
        report["coverage_audit"] = json.loads(audit_path.read_text())
    (args.output / "comparison.json").write_text(json.dumps(report, indent=2) + "\n")
    old, new = report["old"], report["new"]
    lines = [
        "# Pipeline output comparison",
        "",
        report["scope"],
        "",
        "| Measure | Baseline | Candidate |",
        "|---|---:|---:|",
    ]
    for label, before, after in [
        ("Species rows", old["species"]["rows"], new["species"]["rows"]),
        ("Mapped species", old["mapped_species"]["species"], new["mapped_species"]["species"]),
        (
            "Coarse relationships",
            old["coarse_cells"]["relationships"],
            new["coarse_cells"]["relationships"],
        ),
    ]:
        lines.append(f"| {label} | {before} | {after} |")
    lines += [
        "",
        "See comparison.json for actual transition matrices, identity and optional coverage audit.",
        "Reports may contain restricted identifiers and derived counts; keep them private.",
    ]
    (args.output / "analysis.md").write_text("\n".join(lines) + "\n")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
