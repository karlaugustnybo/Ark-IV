# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow>=21", "h3>=4.4,<5"]
# ///
"""Deterministic invented inputs with mapping, evidence, class and scope variation.

This exercises charts and actual logistic fitting. It is not a simulation of
biology, calibrated model, source sample, or performance benchmark.
"""

import argparse
import random
from pathlib import Path

import h3
import pyarrow as pa
import pyarrow.parquet as pq

parser = argparse.ArgumentParser(
    description="Create invented inputs for the three serving investigations; never reads source data."
)
parser.add_argument("output", type=Path, help="New or empty directory for invented Parquet inputs")
root = parser.parse_args().output
if root.exists() and any(root.iterdir()):
    raise SystemExit("Use a new or empty output directory; existing files are never overwritten")
root.mkdir(parents=True, exist_ok=True)
rng = random.Random(147)
cells = sorted(h3.grid_disk(h3.latlng_to_cell(5, 110, 3), 20))[:1100]
ids = [h3.str_to_int(c) for c in cells]
classes = [
    "Magnoliopsida",
    "Actinopteri",
    "Lepidosauria",
    "Amphibia",
    "Mammalia",
    "Insecta",
    "Gastropoda",
    "Aves",
]
categories = [
    "Least Concern",
    "Near Threatened",
    "Vulnerable",
    "Endangered",
    "Critically Endangered",
    "Data Deficient",
    "Not Assessed",
    "Lower Risk/conservation dependent",
]
species = []
systems = []
ranges = []
cw = []
lists = {c: [] for c in ids}
for i in range(1800):
    k = i + 1
    cls = classes[i % 8]
    category = categories[rng.randrange(8)]
    n = rng.choice([1, 5, 12, 30, 70, 150, 300, 600, 1000])
    mapped = rng.random() < 0.75
    dna = rng.random() < 0.4
    genus = dna or rng.random() < 0.5
    family = genus or rng.random() < 0.6
    unknown = rng.random() < 0.08
    species.append(
        dict(
            gbif_accepted_id=k,
            iucn_sis_id=str(k),
            species_name=f"Invented species {k}",
            family=f"Invented family {i % 50}",
            redlist_category=category,
            has_dna_species_level=dna,
            genus_has_dna=genus,
            family_has_dna=family,
            goat_data_deficient=unknown,
            has_ebp_criteria_evidence=rng.random() < 0.2,
            edge_group_name="invented cohort" if rng.random() < 0.1 else None,
            iucn_assessment_id=k,
            gbif_taxon_id=k,
            goat_taxon_id=k,
        )
    )
    scope = rng.choice(
        [
            ["Terrestrial"],
            ["Marine"],
            ["Freshwater"],
            ["Terrestrial", "Marine"],
            ["Terrestrial", "Freshwater"],
            ["Marine", "Freshwater"],
        ]
    )
    systems.extend(dict(gbif_accepted_id=k, system=s) for s in scope)
    if mapped:
        used = rng.sample(ids, n)
        ranges.append(dict(gbif_accepted_id=k, h3_indexes=used))
        for c in used:
            lists[c].append(k)
    cw.append(
        dict(
            iucn_sis_id=k,
            ncbi_class=cls,
            iucn_class=cls.upper(),
            iucn_assessment_date=f"{1997 + i % 30}-01-01",
            iucn_year_published=1997 + i % 30,
            taxonomic_concept_relation=rng.choice(["CURRENT_NAME_EXACT", "NCBI_SYNONYM", None]),
            safe_for_automatic_species_trait_transfer=True,
        )
    )
bounds = [
    dict(
        h3_index=c,
        admin0=["XXA", "XXB"] if i % 3 == 0 else ["XXA"],
        admin1=["invented-adm1"],
        municipality=["invented-adm2"] if i % 7 == 0 else [],
        conservation_framework=["invented-ecoregion"] if i % 4 == 0 else [],
    )
    for i, c in enumerate(ids)
]
for name, rows in [
    ("species", species),
    ("species_systems", systems),
    ("species_cells_res3", ranges),
    ("cell_species_lists", [dict(h3_index=c, species_ids=v) for c, v in lists.items()]),
    ("cell_boundaries", bounds),
    ("crosswalk", cw),
]:
    pq.write_table(pa.Table.from_pylist(rows), root / f"{name}.parquet")
print(
    {
        "invented_registered": len(species),
        "invented_mapped": len(ranges),
        "cells": len(lists),
        "pairs": sum(map(len, lists.values())),
    }
)
