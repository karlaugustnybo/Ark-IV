"""Execute the notebook ladder against production predicates, including NULLs."""

from __future__ import annotations

import itertools
import sys
import unittest
from pathlib import Path

import duckdb

from ark_pipeline.builders.coarse_cache import (
    DEFAULT_WEIGHTS,
    DNA_SCORE_PREDICATES,
    THREAT_SCORE_PREDICATES,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "notebooks"))
import _weight_ladder as wl


class WeightLadderTests(unittest.TestCase):
    def test_all_dna_states_execute_like_production(self):
        # Includes correlated missingness and contradictory evidence: predicate
        # precedence is observable even when lower-level flags disagree.
        rows = list(itertools.product((True, False, None), repeat=4))
        with duckdb.connect() as con:
            con.execute(
                "CREATE TABLE flags(goat_data_deficient BOOLEAN, family_has_dna BOOLEAN, genus_has_dna BOOLEAN, has_dna_species_level BOOLEAN)"
            )
            con.executemany("INSERT INTO flags VALUES (?, ?, ?, ?)", rows)
            production = " + ".join(
                f"CASE WHEN {predicate} THEN {DEFAULT_WEIGHTS[bucket]} ELSE 0 END"
                for bucket, predicate in DNA_SCORE_PREDICATES.items()
            )
            outputs = con.sql(f"SELECT {wl.dna_weight_sql()}, {production} FROM flags").fetchall()
        self.assertEqual(len(outputs), 81)
        for flags, (actual, expected) in zip(rows, outputs, strict=True):
            with self.subTest(flags=flags):
                self.assertEqual(actual, expected)
        # Unknown GoaT state must never become confirmed family absence.
        self.assertEqual(outputs[rows.index((None, False, False, False))][0], 0)
        self.assertEqual(outputs[rows.index((True, False, False, False))][0], 4)
        self.assertEqual(outputs[rows.index((False, False, True, True))][0], 4)
        self.assertEqual(outputs[rows.index((False, None, False, True))][0], 3)

    def test_threat_states_execute_like_production(self):
        categories = [
            *wl.CATEGORY_KEYS,
            "Lower Risk/conservation dependent",
            "Not Assessed",
            "Extinct",
            "unrecognized",
            None,
        ]
        production = " + ".join(
            f"CASE WHEN {predicate} THEN {DEFAULT_WEIGHTS[bucket]} ELSE 0 END"
            for bucket, predicate in THREAT_SCORE_PREDICATES.items()
        )
        with duckdb.connect() as con:
            con.execute("CREATE TABLE categories(redlist_category VARCHAR)")
            con.executemany("INSERT INTO categories VALUES (?)", [(c,) for c in categories])
            outputs = con.sql(
                f"SELECT {wl.threat_weight_sql()}, {production} FROM categories"
            ).fetchall()
        for category, (actual, expected) in zip(categories, outputs, strict=True):
            with self.subTest(category=category):
                self.assertEqual(actual, expected)
        self.assertEqual([row[0] for row in outputs[-5:]], [0] * 5)

    def test_custom_column_names_and_product(self):
        with duckdb.connect() as con:
            result = con.sql(f"""
                SELECT {wl.threat_weight_sql("category")} *
                       {wl.dna_weight_sql("unknown", "family", "genus", "species")}
                FROM (VALUES ('Endangered', false, true, false, true))
                  flags(category, unknown, family, genus, species)
            """).fetchone()[0]
        self.assertEqual(result, DEFAULT_WEIGHTS["en"] * DEFAULT_WEIGHTS["gen"])


if __name__ == "__main__":
    unittest.main()
