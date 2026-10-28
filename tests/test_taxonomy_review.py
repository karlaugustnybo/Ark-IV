from __future__ import annotations

import contextlib
import csv
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from ark_pipeline.cli import taxonomy_review as review
from ark_pipeline.cli.sources_acquire import (
    Catalogue,
    Source,
    atomic_json,
    file_record,
    load_catalogue,
    validate_input_files,
)
from ark_pipeline.cli.sources_sync import check_upstream_releases


def archive(path: Path, rows: list[tuple], *, col: bool):
    columns = [
        "taxonID",
        "acceptedNameUsageID",
        "taxonomicStatus",
        "taxonRank",
        "genericName",
        "specificEpithet",
        "kingdom",
        "family",
    ]
    out = io.StringIO()
    writer = csv.writer(out, delimiter="\t", quoting=csv.QUOTE_NONE, lineterminator="\n")
    writer.writerow(["dwc:" + name for name in columns] if col else columns)
    writer.writerows(rows)
    with zipfile.ZipFile(path, "w") as handle:
        handle.writestr("Taxon.tsv", out.getvalue())
        handle.writestr("meta.xml", "<archive />")


def taxon(
    identifier, genus, epithet, accepted="", status="accepted", kingdom="Animalia", family="Felidae"
):
    return (identifier, accepted, status, "species", genus, epithet, kingdom, family)


def pair(sis="fixture-1", left="Felis leo", right="Panthera leo"):
    return {
        "iucn_sis_id": sis,
        "ncbi_taxid": "fixture-ncbi",
        "iucn_scientific_name": left,
        "goat_scientific_name": right,
        "iucn_kingdom": "Animalia",
        "iucn_family": "Felidae",
    }


class TaxonomyReviewTests(unittest.TestCase):
    def test_explicit_false_hybrid_flag_does_not_discard_species(self):
        row = {
            "taxonRank": "species",
            "genericName": "Panthera",
            "specificEpithet": "leo",
            "notho": "false",
        }
        self.assertEqual(review.canonical_name(row), "panthera leo")
        self.assertEqual(review.canonical_name({**row, "notho": "true"}), "")

    def test_synonyms_share_accepted_concepts_without_claiming_identifier_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            col, legacy = root / "col.zip", root / "gbif.zip"
            for path, key, is_col in [(col, "ABC1", True), (legacy, "101", False)]:
                archive(
                    path,
                    [
                        taxon(key, "Panthera", "leo"),
                        taxon("syn-" + key, "Felis", "leo", key, "synonym"),
                    ],
                    col=is_col,
                )
            result = review.review([pair()], col, legacy)
            row = result["rows"][0]
            self.assertEqual(row["col_xr"]["accepted_taxon_id"], "ABC1")
            self.assertEqual(row["legacy_gbif"]["accepted_taxon_id"], "101")
            self.assertEqual(row["col_xr"]["status"], "shared_name_concept")
            self.assertFalse(row["identifier_identity_confirmed"])
            self.assertFalse(row["eligible_for_trait_transfer"])

    def test_ambiguity_missing_targets_and_lineage_conflicts_remain_unresolved(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "col.zip"
            archive(
                path,
                [
                    taxon("A", "Panthera", "ambigua"),
                    taxon("B", "Panthera", "ambigua"),
                    taxon("C", "Felis", "orphan", "missing", "synonym"),
                    taxon("D", "Panthera", "planta", kingdom="Plantae"),
                    taxon("E", "Panthera", "unknown", family=""),
                    taxon("F", "Panthera", "leo"),
                    taxon("G", "Panthera", "tigris"),
                ],
                col=True,
            )
            rows = [
                pair(str(index), name, name)
                for index, name in enumerate(
                    ["Panthera ambigua", "Felis orphan", "Panthera planta", "Panthera unknown"], 1
                )
            ]
            rows.append(pair("5", "Panthera leo", "Panthera tigris"))
            result = review.review(rows, path, path)
            self.assertEqual(
                [r["col_xr"]["status"] for r in result["rows"]],
                ["ambiguous", "unresolved", "unresolved", "unresolved", "concept_conflict"],
            )
            self.assertTrue(all(not r["eligible_for_trait_transfer"] for r in result["rows"]))

    def test_cli_records_pins_and_limits_and_preserves_output_on_bad_queue(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = {"schema_version": 1, "sources": {}}
            for source, filename in [
                ("col-xr", "col-xr-dwca.zip"),
                ("gbif-backbone", "backbone.zip"),
            ]:
                path = root / filename
                archive(path, [taxon("A", "Panthera", "leo")], col=source == "col-xr")
                manifest["sources"][source] = {
                    "release": "fixture-pin",
                    "validation_status": "passed",
                    "files": [file_record(root, filename, path)],
                }
            atomic_json(root / "acquisition/current.json", manifest)
            queue, output = root / "queue.json", root / "review.json"
            queue.write_text(json.dumps([pair("1", "Panthera leo"), pair("2", "Panthera leo")]))
            args = [
                "--root",
                str(root),
                "--queue",
                str(queue),
                "--output",
                str(output),
                "--limit",
                "1",
            ]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(review.main(args), 0)
            report = json.loads(output.read_text())
            self.assertEqual(
                (report["input_rows"], report["reviewed_rows"], report["limited"]), (2, 1, True)
            )
            self.assertEqual(report["inputs"]["col-xr"]["release"], "fixture-pin")
            selected = output.read_bytes()
            queue.write_text('[{"iucn_sis_id":"bad"}]')
            with self.assertRaisesRegex(ValueError, "nonempty"):
                review.main(args)
            self.assertEqual(output.read_bytes(), selected)

    def test_col_acquisition_checks_archive_columns(self):
        configured = load_catalogue(Path("config/data_sources.toml")).sources["col-xr"]
        source = Source({**configured.values, "file_sha256": {}})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "col-xr-dwca.zip"
            archive(path, [taxon("A", "Panthera", "leo")], col=True)
            self.assertEqual(validate_input_files(source, {path.name: path}), [])
            with zipfile.ZipFile(path, "w") as handle:
                handle.writestr("Taxon.tsv", "dwc:taxonID\nA\n")
                handle.writestr("meta.xml", "<archive />")
            self.assertTrue(
                any(
                    "missing column" in error
                    for error in validate_input_files(source, {path.name: path})
                )
            )

    def test_version_checks_use_published_releases_and_numeric_ott_order(self):
        catalogue = Catalogue(
            {
                name: Source(
                    {
                        "id": name,
                        "release": version,
                        "update_policy": "pinned",
                        "release_check_url": "https://fixture.invalid",
                    }
                )
                for name, version in [("col-xr", "2026-10-16-XR"), ("ott-taxonomy", "OTT-3.7.3")]
            },
            {},
        )
        col = json.dumps({"result": [{"origin": "xrelease", "sourceKey": 3,
                                      "issued": "2026-10-16", "version": "2026-10-16 XR"}]})
        ott = '<a href="ott3.7/">base</a><a href="ott3.7.3/">patch</a><a href="ott3.8draft1/">draft</a>'
        with patch("ark_pipeline.cli.sources_sync.read_release_page", side_effect=[col, ott]):
            self.assertEqual(
                [r.status for r in check_upstream_releases(catalogue)], ["current", "current"]
            )
        with patch(
            "ark_pipeline.cli.sources_sync.read_release_page",
            side_effect=[col, ott + '<a href="ott3.10/">new</a>'],
        ):
            result = check_upstream_releases(catalogue)
            self.assertEqual(result[1].status, "update-available")
            self.assertEqual(result[1].release, "OTT-3.10")
        with patch("ark_pipeline.cli.sources_sync.read_release_page", return_value="maintenance"):
            self.assertTrue(
                all(row.status == "check-failed" for row in check_upstream_releases(catalogue))
            )


if __name__ == "__main__":
    unittest.main()
