"""Shared local/cloud metadata stage: discover exact IDs, acquire and reconcile."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

from ark_pipeline.builders.iucn_metadata import evidence_identity, id_matches, validate_evidence
from ark_pipeline.cli.reconcile_species_metadata import reconcile
from ark_pipeline.cli.sources_download_iucn import DEFAULT_BASE_URL, IucnApi, read_iucn_token
from ark_pipeline.cli.sources_fetch_iucn_details import fetch_details, positive_id
from ark_pipeline.runtime.metadata_publication import publish_metadata, source_registration_guard
from ark_pipeline.runtime.provenance import atomic_json, sha256
from ark_pipeline.runtime.resources import configure_duckdb


def reconciliation_queue(species_dir: Path) -> dict:
    with duckdb.connect() as db:
        configure_duckdb(db)
        db.execute(
            "CREATE TABLE species AS SELECT * FROM read_parquet(?)",
            [str(species_dir / "species.parquet")],
        )
        db.execute(
            "CREATE TABLE systems AS SELECT * FROM read_parquet(?)",
            [str(species_dir / "species_systems.parquet")],
        )
        # Include both sides of names shared with spatial-only IDs. Names select
        # investigation candidates only; they never authorize identity transfer.
        rows = db.execute("""
            SELECT DISTINCT s.gbif_accepted_id,s.iucn_assessment_id FROM species s
            WHERE s.iucn_assessment_id IS NULL
               OR NOT EXISTS (SELECT 1 FROM systems e WHERE e.gbif_accepted_id=s.gbif_accepted_id)
               OR s.species_name IN (SELECT species_name FROM species WHERE iucn_assessment_id IS NULL)
            ORDER BY s.gbif_accepted_id
        """).fetchall()
    return {
        "sis_ids": [positive_id(sid) for sid, _ in rows],
        "assessment_ids": sorted({positive_id(aid) for _, aid in rows if aid is not None}),
    }


def spatial_release(manifest: dict) -> str | None:
    releases = {
        str(manifest["sources"][key]["release"])
        for key in ("iucn-spatial", "iucn-spatial-tables")
        if manifest.get("sources", {}).get(key, {}).get("release")
    }
    if len(releases) > 1:
        raise ValueError("Registered spatial sources have conflicting IUCN releases")
    return next(iter(releases), None)


def include_synonym_candidates(queue: dict, species_dir: Path, folder: Path, release: str | None = None) -> dict:
    names = set()
    for sid in queue["sis_ids"]:
        path = folder / "taxa/sis" / f"{sid}.json"
        if not path.is_file():
            continue
        saved = json.loads(path.read_text())
        if saved.get("endpoint") != f"taxa/sis/{sid}" or (release and saved.get("release") != release):
            raise ValueError("IUCN synonym evidence identity mismatch")
        if saved.get("status") != "retrieved":
            continue
        body = saved.get("body", {})
        if not id_matches(body.get("taxon", {}).get("sis_id"), positive_id(sid)):
            continue
        for synonym in body.get("taxon", {}).get("synonyms", []):
            if synonym.get("genus_name") and synonym.get("species_name"):
                names.add(
                    " ".join(
                        str(synonym[key])
                        for key in ("genus_name", "species_name", "infra_name")
                        if synonym.get(key)
                    )
                )
    with duckdb.connect() as db:
        configure_duckdb(db)
        rows = db.execute(
            """SELECT gbif_accepted_id,iucn_assessment_id FROM read_parquet(?)
            WHERE species_name IN (SELECT unnest(?::VARCHAR[]))""",
            [str(species_dir / "species.parquet"), sorted(names)],
        ).fetchall()
    return {
        "sis_ids": sorted(set(queue["sis_ids"]) | {positive_id(sid) for sid, _ in rows}),
        "assessment_ids": sorted(
            set(queue["assessment_ids"]) | {positive_id(aid) for _, aid in rows if aid is not None}
        ),
    }


def cache_covers(folder: Path, queue: dict, release: str | None) -> bool:
    report_path = folder / "report.json"
    if not report_path.is_file():
        return False
    report = json.loads(report_path.read_text())
    if not report.get("release") or (release and report.get("release") != release):
        raise ValueError("IUCN detail release differs from the spatial source release")
    if report.get("status") != "retrieved":
        return False

    def cached(endpoint):
        path = folder / (endpoint + ".json")
        if not path.is_file():
            return None
        saved = json.loads(path.read_text())
        if saved.get("endpoint") != endpoint or saved.get("release") != report["release"]:
            raise ValueError("IUCN detail cached evidence identity mismatch")
        if saved.get("status") not in ("retrieved", "not_found"):
            return None
        return saved

    assessments = {positive_id(aid) for aid in queue["assessment_ids"]}
    for value in queue["sis_ids"]:
        sid = positive_id(value)
        saved = cached(f"taxa/sis/{sid}")
        if saved is None:
            return False
        if saved["status"] == "retrieved":
            body = saved["body"]
            if not id_matches(body.get("taxon", {}).get("sis_id"), sid):
                continue  # Retained mismatched response remains unresolved.
            for assessment in body.get("assessments", []):
                if (assessment.get("latest") is True
                    and id_matches(assessment.get("sis_taxon_id"), sid)
                    and any(str(scope.get("code")) == "1" for scope in assessment.get("scopes", []))):
                    assessments.add(positive_id(assessment.get("assessment_id")))
    return all(cached(f"assessment/{aid}") is not None for aid in assessments)


def reconcile_stage(
    root: Path,
    species_dir: Path,
    manifest: dict,
    *,
    details_dir: Path | None = None,
    mode: str = "auto",
    reviewed_evidence_path: Path | None = None,
) -> dict:
    if mode not in ("auto", "cache-only", "off"):
        raise ValueError("Unknown IUCN metadata mode")
    if mode == "off":
        return {"status": "disabled", "reason": "explicit pipeline option"}
    validate_registration = source_registration_guard(root)
    pinned_evidence = evidence_identity(reviewed_evidence_path)
    dimension_inputs = {species_dir / name: sha256(species_dir / name)
                        for name in ("species.parquet", "species_systems.parquet", "build-report.json")}
    manifest_path = root / "acquisition/current.json"
    if manifest_path.is_file():
        if json.loads(manifest_path.read_text()) != manifest:
            raise ValueError("Spatial source registration changed before metadata reconciliation")
    validate_registration()
    release = spatial_release(manifest)
    queue = reconciliation_queue(species_dir)
    atomic_json(species_dir / "iucn-reconciliation-queue.json", queue)
    if not queue["sis_ids"]:
        return {"status": "not_needed"}
    folder = details_dir or root / "derived/iucn-details" / (release or "unregistered-release")
    queue = include_synonym_candidates(queue, species_dir, folder, release)
    if not cache_covers(folder, queue, release):
        if mode == "cache-only":
            raise ValueError("IUCN detail cache does not cover the generated reconciliation queue")
        token = read_iucn_token()
        if not token:
            raise ValueError(
                "Metadata reconciliation needs IUCN_REDLIST_API_KEY or a completed matching detail cache; use --iucn-metadata-mode off only to explicitly accept unresolved metadata"
            )
        fetch_details(
            IucnApi(token, DEFAULT_BASE_URL, 0.5),
            queue,
            folder,
            include_latest_global=True,
            workers=4,
        )
        expanded = include_synonym_candidates(queue, species_dir, folder, release)
        if expanded != queue:
            queue = expanded
            fetch_details(
                IucnApi(token, DEFAULT_BASE_URL, 0.5),
                queue,
                folder,
                include_latest_global=True,
                workers=4,
            )
    atomic_json(species_dir / "iucn-reconciliation-queue.json", queue)
    output = species_dir / "api-reconciled"
    report = reconcile(species_dir, folder, output, expected_release=release,
                       reviewed_evidence_path=reviewed_evidence_path)
    baseline = json.loads((species_dir / "build-report.json").read_text())
    baseline.update(
        iucn_api_enrichment=report,
        species_without_ecosystem_metadata=report["ecosystem_gaps_after"],
        ecosystem_metadata_gaps=report["ecosystem_gaps_remaining"],
    )
    baseline["inputs"]["iucn_details_dir"] = str(folder)
    baseline["iucn_reconciliation_mode"] = mode
    if "h3_species_missing_iucn_ids" in baseline:
        with duckdb.connect() as db:
            configure_duckdb(db)
            unassessed = {
                row[0]
                for row in db.execute(
                    "SELECT gbif_accepted_id FROM read_parquet(?) WHERE iucn_assessment_id IS NULL",
                    [str(output / "species.parquet")],
                ).fetchall()
            }
        baseline["h3_species_missing_iucn_ids"] = [
            sid for sid in baseline["h3_species_missing_iucn_ids"] if str(sid) in unassessed
        ]
        baseline["h3_species_missing_iucn_metadata"] = len(baseline["h3_species_missing_iucn_ids"])
    if any(sha256(path) != digest for path, digest in dimension_inputs.items()):
        raise ValueError("Metadata inputs changed before publication")
    if evidence_identity(reviewed_evidence_path) != pinned_evidence:
        raise ValueError("Reviewed ecosystem evidence changed before metadata publication")
    def validate():
        validate_registration()
        validate_evidence(report)
        if evidence_identity(reviewed_evidence_path) != pinned_evidence:
            raise ValueError("Reviewed ecosystem evidence changed before metadata publication")
    validate()
    atomic_json(output / "build-report.json", baseline)
    publish_metadata(output, species_dir, validate=validate)
    return report
