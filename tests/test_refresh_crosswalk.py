from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb

from ark_pipeline.cli.crosswalk_refresh import INPUTS, refresh
from ark_pipeline.runtime.provenance import atomic_json, sha256


def fixture(root: Path) -> None:
    sources = root / "sources"
    sources.mkdir()
    taxonomy_fields = ["internalTaxonId", "scientificName", "kingdomName", "phylumName", "className",
                       "orderName", "familyName", "genusName", "speciesName", "authority"]
    taxonomy_rows = [
        (101, "Panthera leo"), (102, "Felis tigris"),
        (103, "Panthera ambigua"), (104, "Panthera fakeus"),
        (105, "Panthera sherperdii"), (106, "Panthera freshwaterus"),
    ]
    with (sources / "taxonomy.csv").open("w") as handle:
        writer = csv.writer(handle)
        writer.writerow(taxonomy_fields)
        for sis, name in taxonomy_rows:
            writer.writerow([sis, name, "Animalia", "Chordata", "Mammalia", "Carnivora", "Felidae",
                             "Panthera", name.split()[1], "Fixture"])
    with (sources / "assessments.csv").open("w") as handle:
        writer = csv.writer(handle)
        writer.writerow(["internalTaxonId", "assessmentId", "redlistCategory", "yearPublished",
                         "assessmentDate", "scopes", "systems"])
        for sis in range(101, 107):
            systems = "Terrestrial|Freshwater" if sis >= 105 else "Terrestrial"
            writer.writerow([sis, sis + 1000, "VU", 2026, "2026-01-01", "Global", systems])

    def provenance_json(slug: str, value: str, aggregation_source: str = "direct") -> str:
        source = {
            "date": "2026-09-10", "name": "iucn red list",
            "stub": "https://www.iucnredlist.org/species/",
            "description": "Summary of IUCN Red List of Threatened Species, includes only species rank.",
            "slug": slug,
        }
        raw_value = {
            "metadata": {"source": source},
            "source_url_stub": "https://www.iucnredlist.org/species/",
            "value": value, "source_slug": slug, "source_date": source["date"],
            "source_description": source["description"], "source": "IUCN Red List",
        }
        return json.dumps({"redlist_category": {
            "aggregation_source": aggregation_source,
            "aggregation_method": "list",
            "count": 1, "length": 1, "sp_count": 0,
            "rawValues": [raw_value],
        }})

    goat_fields = ["taxon_id", "scientific_name", "taxon_rank", "assembly_level", "bioproject",
                   "busco_completeness", "ebp_standard_criteria", "in_progress", "insdc_submitted",
                   "published", "resampling_required", "sample_acquired", "sample_available",
                   "sample_collected", "sequencing_status", "sequencing_status_ebp", "other_priority",
                   "family_representative", "redlist_category", "redlist_population_trend",
                   "redlist_scopes", "field_provenance_json"]
    redlist_index = goat_fields.index("redlist_category")
    trend_index = goat_fields.index("redlist_population_trend")
    scopes_index = goat_fields.index("redlist_scopes")
    provenance_index = goat_fields.index("field_provenance_json")
    goat_rows = [
        (10, "Panthera leo", "species"), (11, "Panthera tigris", "species"),
        (12, "Panthera ambigua", "species"), (13, "Panthera ambigua", "species"),
        (20, "Panthera fakeus", "subspecies"),
        (14, "Panthera sherperdi", "species"),
        (15, "Panthera deepwaterus", "species"),
        (16, "Panthera stonewallus", "species"),
    ]
    goat_redlist = {
        # Near-name candidate that no name rule can accept: the IUCN spelling
        # difference plus missing GBIF evidence previously kept it unresolved.
        14: ("VU", "Decreasing", "Global", provenance_json("105/1105", "VU")),
        # No name link at all: only the recorded SIS provenance can bridge it.
        15: ("EN", "Unknown", "Global", provenance_json("106/1106", "EN")),
        # Identity bridge from an ancestrally estimated row must be ignored.
        16: ("VU", "Unknown", "Global", provenance_json("106/1106", "VU", "estimate")),
    }
    with (sources / "tol_species_all_ranks.tsv").open("w") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(goat_fields)
        for taxid, name, rank in goat_rows:
            row = [taxid, name, rank] + [""] * (len(goat_fields) - 3)
            if taxid in goat_redlist:
                category, trend, scopes, provenance = goat_redlist[taxid]
                row[redlist_index] = category
                row[trend_index] = trend
                row[scopes_index] = scopes
                row[provenance_index] = provenance
            writer.writerow(row)
    nodes = [(1, 1, "no rank", "root"), (2, 1, "kingdom", "Animalia"), (3, 2, "phylum", "Chordata"),
             (4, 3, "class", "Mammalia"), (5, 4, "order", "Carnivora"), (6, 5, "family", "Felidae"),
             (7, 6, "genus", "Panthera"), (10, 7, "species", "Panthera leo"),
             (11, 7, "species", "Panthera tigris"), (12, 7, "species", "Panthera ambigua"),
             (13, 7, "species", "Panthera ambigua"), (20, 10, "subspecies", "Panthera fakeus"),
             (14, 7, "species", "Panthera sherperdi"), (15, 7, "species", "Panthera deepwaterus"),
             (16, 7, "species", "Panthera stonewallus")]
    (sources / "nodes.dmp").write_text("".join(
        "\t|\t".join([str(taxid), str(parent), rank] + [""] * 10) + "\t|\n"
        for taxid, parent, rank, _ in nodes
    ))
    (sources / "names.dmp").write_text("".join(
        f"{taxid}\t|\t{name}\t|\t\t|\tscientific name\t|\n" for taxid, _, _, name in nodes
    ) + "11\t|\tFelis tigris\t|\t\t|\tsynonym\t|\n")
    (sources / "merged.dmp").write_text("99\t|\t10\t|\n")
    (sources / "delnodes.dmp").write_text("98\t|\n")
    register(root)


def register(root: Path) -> None:
    manifest = {"schema_version": 1, "sources": {}}
    for source_id, logical_name, _ in INPUTS:
        path = root / "sources" / logical_name
        source = manifest["sources"].setdefault(source_id, {"validation_status": "passed", "release": "fixture", "files": []})
        source["files"].append({"logical_name": logical_name, "path": str(path.relative_to(root)),
                                "bytes": path.stat().st_size, "sha256": sha256(path)})
    atomic_json(root / "acquisition/current.json", manifest)


class RefreshCrosswalkTests(unittest.TestCase):
    def test_managed_raw_provenance_receipt_and_stale_input_rejection(self):
        from ark_pipeline.cli.sources_fetch_goat_provenance import API, normalize_batch

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            raw = root / 'raw.json'
            supplement = {'schema_version': 1, 'api': API,
                          'goat_snapshot_sha256': sha256(root / 'sources/tol_species_all_ranks.tsv'),
                          'rows': normalize_batch({'status': {'success': True}, 'records': [{'record': {
                              'taxon_id': '10', 'scientific_name': 'Panthera leo', 'taxon_rank': 'species',
                              'attributes': [],
                          }}]}, ['10'])}
            raw.write_text(json.dumps(supplement))
            output = root / 'managed'
            refresh(root, output, memory_limit='256MB', threads=1, goat_provenance=raw)
            current = (output / 'current').resolve()
            receipt = json.loads((current / 'receipt.json').read_text())
            self.assertEqual(receipt['identity']['inputs']['goat_raw_provenance']['sha256'], sha256(raw))
            supplement['goat_snapshot_sha256'] = '0' * 64
            raw.write_text(json.dumps(supplement))
            with self.assertRaisesRegex(ValueError, 'different registered snapshot'):
                refresh(root, output, memory_limit='256MB', threads=1, goat_provenance=raw)
            self.assertEqual((output / 'current').resolve(), current)

    def test_deleted_competitor_does_not_hide_a_valid_current_candidate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            goat = root / 'sources/tol_species_all_ranks.tsv'
            with goat.open() as handle:
                reader = csv.DictReader(handle, delimiter='\t')
                fields, rows = reader.fieldnames, list(reader)
            duplicate = {**rows[0], 'taxon_id': '17'}
            with goat.open('a') as handle:
                csv.DictWriter(handle, fieldnames=fields, delimiter='\t').writerow(duplicate)
            with (root / 'sources/nodes.dmp').open('a') as handle:
                handle.write('\t|\t'.join(['17', '7', 'species'] + [''] * 10) + '\t|\n')
            with (root / 'sources/names.dmp').open('a') as handle:
                handle.write('17\t|\tPanthera leo\t|\t\t|\tscientific name\t|\n')
            with (root / 'sources/delnodes.dmp').open('a') as handle:
                handle.write('10\t|\n')
            crosswalk = self.run_matcher(root)
            with duckdb.connect() as con:
                row = con.execute('SELECT matched_ncbi_species_taxid,safe_for_automatic_species_trait_transfer FROM read_parquet(?) WHERE iucn_sis_id=101', [str(crosswalk)]).fetchone()
            self.assertEqual(row, ('17', True))

    def test_identity_contradictions_withhold_acceptance_and_transfer(self):
        for case, reason in (
            ('deleted', 'NCBI_DELETED_ID'),
            ('merged', 'NCBI_MERGED_ID_REQUIRES_RECONCILIATION'),
            ('rank', 'NCBI_SPECIES_RANK_CONFLICT'),
            ('kingdom', 'KINGDOM_CONFLICT'),
            ('sis', 'DIRECT_SIS_CONFLICT'),
            ('kingdom_alias', None),
        ):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                fixture(root)
                if case in {'kingdom', 'kingdom_alias'}:
                    p = root / 'sources/taxonomy.csv'
                    p.write_text(p.read_text().replace('Animalia', 'Plantae' if case == 'kingdom' else 'Metazoa'))
                if case == 'rank':
                    p = root / 'sources/nodes.dmp'
                    p.write_text(p.read_text().replace('10\t|\t7\t|\tspecies', '10\t|\t7\t|\tsubspecies'))
                if case in {'deleted', 'merged'}:
                    name = 'delnodes.dmp' if case == 'deleted' else 'merged.dmp'
                    with (root / 'sources' / name).open('a') as handle:
                        handle.write('10\t|\n' if case == 'deleted' else '10\t|\t11\t|\n')
                if case == 'sis':
                    p = root / 'sources/tol_species_all_ranks.tsv'
                    with p.open() as handle:
                        reader = csv.DictReader(handle, delimiter='\t')
                        fields, rows = reader.fieldnames, list(reader)
                    rows[0]['redlist_category'] = 'VU'
                    rows[0]['field_provenance_json'] = json.dumps({'redlist_category': {
                        'aggregation_source': 'direct', 'rawValues': [
                            {'metadata': {'source': {'slug': '999/1999'}}}
                        ]}})
                    with p.open('w') as handle:
                        writer = csv.DictWriter(handle, fieldnames=fields, delimiter='\t')
                        writer.writeheader()
                        writer.writerows(rows)
                crosswalk = self.run_matcher(root)
                with duckdb.connect() as con:
                    status, safe, block = con.execute(
                        'SELECT match_status,safe_for_automatic_species_trait_transfer,identity_block_reason '
                        'FROM read_parquet(?) WHERE iucn_sis_id=101', [str(crosswalk)]
                    ).fetchone()
                self.assertEqual(status, 'MATCHED' if reason is None else 'REVIEW_UNRESOLVED')
                self.assertEqual(safe, reason is None)
                self.assertEqual(block, reason)

    def run_matcher(self, root, bridge=None, validation=None, provenance=None):
        output = root / "matched"
        command = [sys.executable, "-m", "ark_pipeline.cli.crosswalk_match",
                   "--iucn-taxonomy", str(root / "sources/taxonomy.csv"),
                   "--iucn-assessments", str(root / "sources/assessments.csv"),
                   "--goat-species", str(root / "sources/tol_species_all_ranks.tsv"),
                   "--ncbi-taxdump-dir", str(root / "sources"),
                   "--output-dir", str(output), "--threads", "1", "--memory-limit", "256MB"]
        if bridge is not None:
            path = root / "bridge.json"
            path.write_text(json.dumps({"rows": bridge}))
            command += ["--gbif-bridge-json", str(path)]
        if validation is not None:
            path = root / "validation.json"
            path.write_text(json.dumps({"rows": validation}))
            command += ["--gbif-validation-json", str(path)]
        if provenance is not None:
            path = root / 'raw-provenance.json'
            path.write_text(json.dumps(provenance))
            command += ['--goat-provenance-json', str(path)]
        subprocess.run(command, check=True, capture_output=True, text=True)
        return output / "iucn_goat_crosswalk.parquet"

    def test_raw_provenance_requires_snapshot_summary_and_identity_agreement(self):
        from ark_pipeline.cli.sources_fetch_goat_provenance import API, normalize_batch

        for case in ('conflicting_sis', 'changed_name', 'changed_summary', 'reordered_summary', 'wrong_snapshot'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                fixture(root)
                # Make the frozen summary agree with the live record by default.
                goat = root / 'sources/tol_species_all_ranks.tsv'
                with goat.open() as handle:
                    reader = csv.DictReader(handle, delimiter='\t')
                    fields, rows = reader.fieldnames, list(reader)
                rows[0]['redlist_category'] = 'VU;EN' if case == 'reordered_summary' else 'VU'
                with goat.open('w') as handle:
                    writer = csv.DictWriter(handle, fieldnames=fields, delimiter='\t')
                    writer.writeheader()
                    writer.writerows(rows)
                body = {'status': {'success': True}, 'records': [{'record': {
                    'taxon_id': '10', 'scientific_name': 'Changed name' if case == 'changed_name' else 'Panthera leo',
                    'taxon_rank': 'species', 'attributes': [{
                        'key': 'redlist_category', 'aggregation_source': 'direct',
                        'keyword_value': ['EN', 'VU'] if case == 'reordered_summary' else ['EN' if case == 'changed_summary' else 'VU'],
                        'values': [{'metadata': {'source': {'slug': '999/1999'}}}],
                    }],
                }}]}
                provenance = {'schema_version': 1, 'api': API,
                              'goat_snapshot_sha256': '0' * 64 if case == 'wrong_snapshot' else sha256(goat),
                              'rows': normalize_batch(body, ['10'])}
                if case == 'wrong_snapshot':
                    with self.assertRaises(subprocess.CalledProcessError):
                        self.run_matcher(root, provenance=provenance)
                    continue
                path = self.run_matcher(root, provenance=provenance)
                with duckdb.connect() as con:
                    status, reason = con.execute('SELECT match_status,identity_block_reason FROM read_parquet(?) WHERE iucn_sis_id=101', [str(path)]).fetchone()
                conflict = case in {'conflicting_sis', 'reordered_summary'}
                self.assertEqual(status, 'REVIEW_UNRESOLVED' if conflict else 'MATCHED')
                self.assertEqual(reason, 'DIRECT_SIS_CONFLICT' if conflict else None)

    def test_independent_gbif_near_name_keeps_its_relation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            crosswalk = self.run_matcher(root, validation=[{"iucn_sis_id": 105, "ncbi_taxid": "14", "gbif_confirmed": True, "gbif_iucn_source_id_confirmed": True}])
            with duckdb.connect() as con:
                self.assertEqual(con.execute("SELECT taxonomic_concept_relation,gbif_confirmed FROM read_parquet(?) WHERE iucn_sis_id=105", [str(crosswalk)]).fetchone(), ("GBIF_CORROBORATED_NEAR_NAME", True))

    def test_all_native_sis_values_preserve_ambiguity_and_trait_boundaries(self):
        from ark_pipeline.builders.species_metadata import build_global_species

        for supplied, reverse, repeated, taxid in (
            (True, False, False, "15"), (False, False, False, "15"),
            (True, True, False, "15"), (False, True, False, "15"),
            (True, False, True, "15"), (False, True, True, "15"),
            (True, False, False, "14"), (False, True, False, "14"),
            (True, True, True, "14"),
        ):
            with self.subTest(supplied=supplied, reverse=reverse, repeated=repeated, taxid=taxid), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                fixture(root)
                if supplied:
                    with (root / "sources/taxonomy.csv").open("a") as handle:
                        csv.writer(handle).writerow([107,"Panthera elsewhereus","Animalia","Chordata","Mammalia","Carnivora","Felidae","Panthera","elsewhereus","Fixture"])
                    with (root / "sources/assessments.csv").open("a") as handle:
                        csv.writer(handle).writerow([107,1107,"VU",2026,"2026-01-01","Global","Terrestrial"])
                goat = root / "sources/tol_species_all_ranks.tsv"
                with goat.open() as handle:
                    reader = csv.DictReader(handle, delimiter="\t")
                    fields, rows = reader.fieldnames, list(reader)
                original_sid = 106 if taxid == "15" else 105
                for row in rows:
                    if row["taxon_id"] == taxid:
                        provenance = json.loads(row["field_provenance_json"])
                        attribute = provenance["redlist_category"]
                        second = json.loads(json.dumps(attribute["rawValues"][0]))
                        second["metadata"]["source"]["slug"] = f"{original_sid}/9999" if repeated else "107/1107"
                        attribute["rawValues"].append(second)
                        if reverse:
                            attribute["rawValues"].reverse()
                        attribute.update(count=2, length=2)
                        row.update(field_provenance_json=json.dumps(provenance), sample_acquired="fixture-voucher", ebp_standard_criteria="6.C", resampling_required="yes")
                with goat.open("w") as handle:
                    writer = csv.DictWriter(handle, fields, delimiter="\t")
                    writer.writeheader()
                    writer.writerows(rows)
                register(root)
                result = refresh(root, root / "crosswalk", memory_limit="256MB", threads=1)
                with duckdb.connect() as con:
                    status, confirmed, safe, identities, raw = con.execute("SELECT match_status,goat_iucn_sis_confirmed,safe_for_automatic_species_trait_transfer,goat_iucn_sis_ids,goat_iucn_raw_values FROM read_parquet(?) WHERE iucn_sis_id=?", [result["crosswalk"], original_sid]).fetchone()
                self.assertEqual(safe, repeated)
                self.assertEqual(confirmed, repeated)
                self.assertEqual(status, "MATCHED" if repeated else "REVIEW_UNRESOLVED")
                self.assertEqual(identities, [original_sid] if repeated else [original_sid, 107])
                self.assertEqual(len(json.loads(raw)), 2)
                output = root / "species"
                build_global_species(crosswalk_path=Path(result["crosswalk"]), assessments_path=root / "sources/assessments.csv", goat_species_path=goat, h3_paths=[], output_dir=output)
                with duckdb.connect() as con:
                    traits = con.execute("SELECT has_dna_species_level,has_ebp_criteria_evidence,goat_resampling_required FROM read_parquet(?) WHERE gbif_accepted_id=?", [str(output / "species.parquet"), str(original_sid)]).fetchone()
                self.assertEqual(traits, (True, True, "yes") if repeated else (False, False, None))
                if supplied and not repeated:
                    with duckdb.connect() as con:
                        other = con.execute("SELECT has_dna_species_level,has_ebp_criteria_evidence,goat_resampling_required FROM read_parquet(?) WHERE gbif_accepted_id='107'", [str(output / "species.parquet")]).fetchone()
                        other_link = con.execute("SELECT match_status,safe_for_automatic_species_trait_transfer,goat_iucn_sis_ids FROM read_parquet(?) WHERE iucn_sis_id=107", [result["crosswalk"]]).fetchone()
                    self.assertEqual(other, (False, False, None))
                    self.assertEqual(other_link, ("REVIEW_UNRESOLVED", False, [original_sid, 107]))

    def test_partial_names_stay_unresolved_and_cannot_transfer_traits(self):
        from ark_pipeline.builders.species_metadata import build_global_species

        for name_class in ("includes", "in-part", "synonym"):
            with self.subTest(name_class=name_class), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                fixture(root)
                path = root / "sources/taxonomy.csv"
                path.write_text(path.read_text().replace("Panthera leo", "Panthera restricted"))
                path = root / "sources/names.dmp"
                path.write_text(path.read_text() + f"10\t|\tPanthera restricted\t|\t\t|\t{name_class}\t|\n")
                path = root / "sources/tol_species_all_ranks.tsv"
                with path.open() as handle:
                    reader = csv.DictReader(handle, delimiter="\t")
                    fields, rows = list(reader.fieldnames), list(reader)
                for rank in ("genus", "family", "kingdom"):
                    fields.append(rank)
                for row in rows:
                    row.update(genus="Panthera", family="Felidae", kingdom="Animalia")
                    if row["taxon_id"] == "10":
                        row.update(sample_acquired="fixture-voucher", ebp_standard_criteria="6.C", resampling_required="yes")
                with path.open("w") as handle:
                    writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
                    writer.writeheader()
                    writer.writerows(rows)
                crosswalk = self.run_matcher(root)
                with duckdb.connect() as con:
                    accepted, review, safe = con.execute(
                        "SELECT matched_ncbi_species_taxid, review_candidate_ncbi_taxid, safe_for_automatic_species_trait_transfer FROM read_parquet(?) WHERE iucn_sis_id=101", [str(crosswalk)]
                    ).fetchone()
                equivalent = name_class == "synonym"
                self.assertEqual(accepted, "10" if equivalent else None)
                self.assertEqual(review, None if equivalent else "10")
                self.assertEqual(safe, equivalent)
                build_global_species(crosswalk_path=crosswalk,
                    assessments_path=root / "sources/assessments.csv",
                    goat_species_path=path, h3_paths=[], output_dir=root / "species")
                with duckdb.connect() as con:
                    dna, ebp, resampling = con.execute(
                        "SELECT has_dna_species_level, has_ebp_criteria_evidence, goat_resampling_required FROM read_parquet(?) WHERE iucn_sis_id='101'", [str(root / "species/species.parquet")]
                    ).fetchone()
                self.assertEqual(dna, equivalent)
                self.assertEqual(ebp, equivalent)
                self.assertEqual(resampling, "yes" if equivalent else None)

    def test_bridge_evidence_counts_distinct_taxids_and_keeps_true_ambiguity(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixture(root)
            bridge = [{"iucn_sis_id": 101, "exact_species_concept": True,
                       "iucn_source_id_confirmed": True, "accepted_concept_key": "fixture-101",
                       "accepted_canonical_name": "Panthera leo"}] * 2
            bridge += [{"iucn_sis_id": 103, "exact_species_concept": True,
                        "iucn_source_id_confirmed": True, "accepted_concept_key": "fixture-103",
                        "accepted_canonical_name": "Panthera ambigua"}]
            crosswalk = self.run_matcher(root, bridge)
            with duckdb.connect() as con:
                row = con.execute("SELECT matched_ncbi_species_taxid, candidate_count, evidence_sources, gbif_accepted_concept_key, safe_for_automatic_species_trait_transfer FROM read_parquet(?) WHERE iucn_sis_id=101", [str(crosswalk)]).fetchone()
                ambiguous = con.execute("SELECT match_status, review_candidate_ncbi_taxid FROM read_parquet(?) WHERE iucn_sis_id=103", [str(crosswalk)]).fetchone()
            self.assertEqual(row[0:2], ("10", 1))
            self.assertIn("GBIF_IUCN_SOURCE_ID", row[2])
            self.assertIn("GOAT_CURRENT_NAME_EXACT", row[2])
            self.assertEqual(row[3:], ("fixture-101", True))
            self.assertEqual(ambiguous, ("REVIEW_UNRESOLVED", "12"))
            with duckdb.connect(str(crosswalk.parent / "matching_work.duckdb"), read_only=True) as con:
                counts = con.execute("SELECT DISTINCT candidate_count FROM decisions WHERE iucn_sis_id=103").fetchall()
            self.assertEqual(counts, [(2,)])

    def test_export_lineage_recovers_taxon_absent_from_pinned_dump(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture(root)
            path = root / "sources/tol_species_all_ranks.tsv"
            with path.open() as handle:
                reader = csv.DictReader(handle, delimiter="\t")
                fields = list(reader.fieldnames) + ["genus", "family", "order", "class", "phylum", "kingdom"]
                rows = list(reader)
            for row in rows:
                row.update(genus="Panthera", family="Felidae", order="Carnivora",
                           **{"class": "Mammalia", "phylum": "Chordata", "kingdom": "Animalia"})
            with path.open("w") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
                writer.writeheader()
                writer.writerows(rows)
            for name in ("names.dmp", "nodes.dmp"):
                path = root / "sources" / name
                path.write_text("".join(line for line in path.read_text().splitlines(keepends=True)
                                        if not line.startswith("10\t|")))
            register(root)
            result = refresh(root, root / "crosswalk", memory_limit="256MB", threads=1)
            with duckdb.connect() as con:
                self.assertEqual(con.execute("SELECT matched_ncbi_species_taxid FROM read_parquet(?) WHERE iucn_sis_id=101", [result["crosswalk"]]).fetchone(), ("10",))
                # Duplicate exact candidates must stay unresolved despite lineage.
                self.assertEqual(con.execute("SELECT match_status FROM read_parquet(?) WHERE iucn_sis_id=103", [result["crosswalk"]]).fetchone(), ("REVIEW_UNRESOLVED",))

    def test_class_phylum_compatibility_requires_actual_ancestry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture(root)
            path = root / "sources/taxonomy.csv"
            with path.open() as handle:
                reader = csv.DictReader(handle)
                fields, rows = reader.fieldnames, list(reader)
            for row in rows:
                row["className"], row["phylumName"] = "Mammalclade", "Chordateclade"
            # This label is not an ancestor and must remain a contradiction.
            rows[1]["className"], rows[1]["phylumName"] = "OtherClass", "OtherPhylum"
            with path.open("w") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            path = root / "sources/nodes.dmp"
            path.write_text(path.read_text().replace("4\t|\t3\t|", "4\t|\t30\t|") +
                "30\t|\t31\t|\tclade" + "\t|" * 11 + "\n" +
                "31\t|\t3\t|\tclade" + "\t|" * 11 + "\n")
            path = root / "sources/names.dmp"
            path.write_text(path.read_text() +
                "30\t|\tMammalclade\t|\t\t|\tscientific name\t|\n" +
                "31\t|\tChordateclade\t|\t\t|\tscientific name\t|\n")
            register(root)
            result = refresh(root, root / "crosswalk", memory_limit="256MB", threads=1)
            with duckdb.connect() as con:
                rows = con.execute("SELECT iucn_sis_id, match_status FROM read_parquet(?) WHERE iucn_sis_id IN (101,102,103) ORDER BY 1", [result["crosswalk"]]).fetchall()
                self.assertEqual(rows, [(101,"MATCHED"), (102,"REVIEW_UNRESOLVED"), (103,"REVIEW_UNRESOLVED")])

    def test_real_matcher_preserves_identities_unresolved_cases_and_reuses_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture(root)
            output = root / "crosswalk"
            first = refresh(root, output, memory_limit="256MB", threads=1)
            self.assertEqual(first["status"], "built")
            self.assertEqual(first["counts"], {"iucn_taxa": 6, "MATCHED": 4,
                                              "REVIEW_UNRESOLVED": 1, "NO_GOAT_NCBI_CANDIDATE": 1})
            with duckdb.connect() as con:
                rows = con.execute("SELECT iucn_sis_id, matched_ncbi_species_taxid, match_status FROM read_parquet(?) ORDER BY 1",
                                   [first["crosswalk"]]).fetchall()
            self.assertEqual(rows, [(101, "10", "MATCHED"), (102, "11", "MATCHED"),
                                    (103, None, "REVIEW_UNRESOLVED"), (104, None, "NO_GOAT_NCBI_CANDIDATE"),
                                    (105, "14", "MATCHED"), (106, "15", "MATCHED")])
            with duckdb.connect() as con:
                bridge = con.execute(
                    f"""
                    SELECT match_method, match_status, taxonomic_concept_relation,
                           confidence_tier, reviewer, goat_redlist_category,
                           goat_redlist_status_source, goat_iucn_sis_id,
                           goat_redlist_sis_id_agrees, goat_redlist_category_agrees
                    FROM read_parquet('{first["crosswalk"]}')
                    WHERE iucn_sis_id = 106
                    """
                ).fetchone()
                confirmed = con.execute(
                    f"""
                    SELECT match_method, reviewer, goat_iucn_sis_confirmed,
                           evidence_sources, goat_redlist_category,
                           goat_redlist_category_agrees, iucn_systems, taxonomic_concept_relation, gbif_confirmed
                    FROM read_parquet('{first["crosswalk"]}')
                    WHERE iucn_sis_id = 105
                    """
                ).fetchone()
                unresolved = con.execute(
                    f"""
                    SELECT iucn_systems FROM read_parquet('{first["crosswalk"]}')
                    WHERE iucn_sis_id = 103
                    """
                ).fetchone()
            self.assertEqual(bridge[0], "GOAT_REDLIST_SIS_PROVENANCE")
            self.assertEqual(bridge[1], "MATCHED")
            self.assertEqual(bridge[2], "GOAT_REDLIST_SIS_PROVENANCE")
            self.assertEqual(bridge[3], "B")
            self.assertEqual(bridge[4], "deterministic")
            self.assertEqual(bridge[5], "EN")
            self.assertEqual(bridge[6], "direct")
            self.assertEqual(bridge[7], 106)
            self.assertTrue(bridge[8])
            self.assertFalse(bridge[9])
            self.assertEqual(confirmed[0], "AI_NEAR_NAME_REVIEW")
            self.assertEqual(confirmed[1], "deterministic-taxonomy-v2")
            self.assertTrue(confirmed[2])
            self.assertIn("GOAT_REDLIST_SIS_SLUG", confirmed[3])
            self.assertEqual(confirmed[4], "VU")
            self.assertTrue(confirmed[5])
            self.assertEqual(confirmed[6], "Terrestrial|Freshwater")
            self.assertEqual(confirmed[7], "GOAT_SIS_CORROBORATED_NEAR_NAME")
            self.assertFalse(confirmed[8])
            self.assertEqual(unresolved[0], "Terrestrial")
            summary = json.loads(Path(first["crosswalk"]).with_name("match_summary.json").read_text())
            self.assertEqual(summary["counts"]["goat_redlist_sis_bridge_rows"], 1)
            self.assertEqual(summary["counts"]["goat_redlist_sis_confirmed_rows"], 1)
            self.assertEqual(summary["counts"]["rows_with_goat_native_redlist_status"], 2)
            self.assertEqual(summary["counts"]["goat_native_redlist_agrees_with_iucn"], 1)
            with patch("ark_pipeline.cli.crosswalk_refresh.subprocess.run") as run:
                second = refresh(root, output, memory_limit="256MB", threads=1)
                run.assert_not_called()
            self.assertEqual(second["status"], "reused")
            self.assertEqual(first["crosswalk"], second["crosswalk"])
            summary = json.loads(Path(first["crosswalk"]).with_name("match_summary.json").read_text())
            self.assertTrue(summary["automatic_refresh"])
            self.assertTrue(all(Path(path).is_file() for path in summary["outputs"].values()))

            # A newly registered snapshot triggers a refresh; failure keeps the
            # complete previous generation selected and removes partial work.
            path = root / "sources/tol_species_all_ranks.tsv"
            path.write_text(path.read_text().replace("Panthera leo\t", "Panthera leo updated\t"))
            register(root)
            old_current = (output / "current").resolve()
            with patch("ark_pipeline.cli.crosswalk_refresh.subprocess.run", side_effect=subprocess.CalledProcessError(1, ["matcher"])):
                with self.assertRaises(subprocess.CalledProcessError):
                    refresh(root, output, memory_limit="256MB", threads=1)
            self.assertEqual((output / "current").resolve(), old_current)
            self.assertFalse(list((output / "generations").glob(".building-*")))
            third = refresh(root, output, memory_limit="256MB", threads=1)
            self.assertEqual(third["status"], "built")
            self.assertNotEqual(first["crosswalk"], third["crosswalk"])
            self.assertTrue(Path(first["crosswalk"]).is_file())

            # Missing or modified published files cannot be reused.
            Path(third["crosswalk"]).with_name("unresolved_candidates.parquet").unlink()
            with patch("ark_pipeline.cli.crosswalk_refresh.subprocess.run", side_effect=subprocess.CalledProcessError(1, ["matcher"])) as run:
                with self.assertRaises(subprocess.CalledProcessError):
                    refresh(root, output, memory_limit="256MB", threads=1)
                run.assert_called_once()

    def test_duplicate_source_identities_fail_before_publication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture(root)
            taxonomy = root / "sources/taxonomy.csv"
            taxonomy.write_text(taxonomy.read_text() + taxonomy.read_text().splitlines()[1] + "\n")
            register(root)
            with self.assertRaisesRegex(ValueError, "reconciliation"):
                refresh(root, root / "crosswalk", memory_limit="256MB", threads=1)
            self.assertFalse((root / "crosswalk/current").exists())


if __name__ == "__main__":
    unittest.main()
