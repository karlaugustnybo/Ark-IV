from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import h3.api.numpy_int as h3
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from ark_pipeline.aggregation.h3_tree import aggregate_cover_base
from ark_pipeline.aggregation.metrics import METRIC_NAMES
from ark_pipeline.cli.h3_cover_pilot import COVER_SCHEMA


class H3TreeTests(unittest.TestCase):
    def test_inherited_leaf_and_duplicate_species_are_unioned(self) -> None:
        parent = h3.latlng_to_cell(55.6761, 12.5683, 6)
        base = h3.get_base_cell_number(parent)
        children = np.sort(h3.cell_to_children(parent, 7))
        rows = [
            ("subtree", parent, 6, 0, "first", "exact-leaf-compaction"),
            ("subtree", parent, 6, 0, "duplicate", "exact-leaf-compaction"),
            ("leaf", int(children[0]), 7, 1, "second", "exact-leaf-compaction"),
        ]
        rows.sort(key=lambda row: (row[1], row[3], row[4]))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "cover.parquet"
            pq.write_table(
                pa.Table.from_pylist(
                    [dict(zip(COVER_SCHEMA.names, row)) for row in rows], schema=COVER_SCHEMA
                ),
                source,
            )
            flags = np.zeros((2, len(METRIC_NAMES)), dtype=np.uint8)
            flags[0, 0] = 1
            flags[1, 1] = 1
            with self.assertRaisesRegex(ValueError, "record cap"):
                aggregate_cover_base(
                    source, root / "capped-fine.parquet", root / "capped-coarse.parquet",
                    root / "capped-metrics.parquet", base_cell=base,
                    species_ids=[101, 202], metric_flags=flags, max_cover_records=2,
                )
            self.assertFalse((root / "capped-fine.parquet").exists())
            summary = aggregate_cover_base(
                source,
                root / "fine.parquet",
                root / "coarse.parquet",
                root / "metrics.parquet",
                base_cell=base,
                species_ids=[101, 202],
                metric_flags=flags,
                batch_cells=2,
            )
            self.assertEqual(summary["fine_cells"], len(children))
            self.assertEqual(summary["fine_relationships"], len(children) + 1)
            fine = pq.read_table(root / "fine.parquet").to_pylist()
            self.assertEqual([row["h3_cell"] for row in fine], list(map(int, children)))
            self.assertEqual(fine[0]["species_ids"], [101, 202])
            self.assertTrue(all(row["species_ids"] == [101] for row in fine[1:]))
            coarse = pq.read_table(root / "coarse.parquet").to_pylist()
            self.assertEqual(coarse[0]["species_ids"], [101, 202])
            metrics = pq.read_table(root / "metrics.parquet")
            self.assertEqual(metrics[METRIC_NAMES[0]].to_pylist(), [1] * len(children))
            self.assertEqual(metrics[METRIC_NAMES[1]].to_pylist(), [1] + [0] * (len(children) - 1))

    def test_pentagon_subtree_has_exact_descendants(self) -> None:
        parent = next(iter(h3.get_pentagons(6)))
        base = h3.get_base_cell_number(parent)
        children = np.sort(h3.cell_to_children(parent, 7))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "cover.parquet"
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {
                            "kind": "subtree",
                            "h3_index": int(parent),
                            "resolution": 6,
                            "species_ordinal": 0,
                            "provenance_key": "pentagon",
                            "proof": "exact-leaf-compaction",
                        }
                    ],
                    schema=COVER_SCHEMA,
                ),
                source,
            )
            aggregate_cover_base(
                source,
                root / "fine.parquet",
                root / "coarse.parquet",
                root / "metrics.parquet",
                base_cell=base,
                species_ids=[101],
                metric_flags=np.zeros((1, len(METRIC_NAMES)), dtype=np.uint8),
            )
            self.assertEqual(
                pq.read_table(root / "fine.parquet")["h3_cell"].to_pylist(),
                list(map(int, children)),
            )

    def test_unproven_subtree_is_rejected(self) -> None:
        parent = h3.latlng_to_cell(55.6761, 12.5683, 6)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "cover.parquet"
            pq.write_table(
                pa.Table.from_pylist(
                    [
                        {
                            "kind": "subtree",
                            "h3_index": int(parent),
                            "resolution": 6,
                            "species_ordinal": 0,
                            "provenance_key": "unproven",
                            "proof": "guess",
                        }
                    ],
                    schema=COVER_SCHEMA,
                ),
                source,
            )
            with self.assertRaisesRegex(ValueError, "invalid cover record"):
                aggregate_cover_base(
                    source,
                    root / "fine.parquet",
                    root / "coarse.parquet",
                    root / "metrics.parquet",
                    base_cell=h3.get_base_cell_number(parent),
                    species_ids=[101],
                    metric_flags=np.zeros((1, len(METRIC_NAMES)), dtype=np.uint8),
                )


if __name__ == "__main__":
    unittest.main()
