"""Exact-ID assessment enrichment from a completed, release-pinned API intake.

No scientific-name join, ID aliasing, or sequencing trait transfer takes place.
Suitable terrestrial habitat evidence is distinguished from explicit systems.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ark_pipeline.cli.sources_fetch_iucn_details import positive_id
from ark_pipeline.runtime.provenance import sha256

DEFAULT_EVIDENCE_PATH = Path(__file__).resolve().parents[2] / "config/iucn_reviewed_ecosystem_evidence.json"


def evidence_identity(path: Path | None = None) -> dict:
    selected = (path or DEFAULT_EVIDENCE_PATH).expanduser().resolve()
    return {"path": str(selected), "sha256": sha256(selected) if selected.is_file() else None}


def reviewed_rules(path: Path | None = None) -> list[dict]:
    selected = (path or DEFAULT_EVIDENCE_PATH).expanduser().resolve()
    return json.loads(selected.read_text())["entries"] if selected.is_file() else []


def id_matches(value: object, expected: int) -> bool:
    try:
        return positive_id(value) == expected
    except ValueError:
        return False


TERRESTRIAL_HABITATS = {"1", "2", "3", "4", "6", "8", "14"}
SYSTEM_NAMES = {
    "Terrestrial": "Terrestrial",
    "Marine": "Marine",
    "Freshwater (=Inland waters)": "Freshwater",
    "Freshwater": "Freshwater",
}
CATEGORIES = {
    "Extinct",
    "Extinct in the Wild",
    "Critically Endangered",
    "Endangered",
    "Vulnerable",
    "Near Threatened",
    "Least Concern",
    "Data Deficient",
    "Not Evaluated",
    "Lower Risk/conservation dependent",
    "Lower Risk/least concern",
    "Lower Risk/near threatened",
}


def ecosystem_evidence(body: dict, *, reviewed_evidence_path: Path | None = None) -> tuple[list[str], str]:
    systems = sorted(
        {
            SYSTEM_NAMES[item.get("description", {}).get("en")]
            for item in body.get("systems", [])
            if item.get("description", {}).get("en") in SYSTEM_NAMES
        }
    )
    if systems:
        return systems, "explicit_assessment_systems"
    # Positive habitat evidence establishes terrestrial occurrence; it does not
    # establish that terrestrial is the species' only realm. Unknown, unsuitable,
    # subterranean and mixed aquatic habitat classes cannot supply a realm.
    if any(
        str(item.get("code", "")).replace(".", "_").split("_")[0] in TERRESTRIAL_HABITATS
        and item.get("suitability") == "Suitable"
        for item in body.get("habitats", [])
    ):
        return ["Terrestrial"], "suitable_terrestrial_habitat"
    rules = reviewed_rules(reviewed_evidence_path)
    for rule in rules:
        if (
            str(body.get("sis_taxon_id")) == rule["sis_id"]
            and str(body.get("assessment_id")) == rule["assessment_id"]
        ):
            if rule.get("external_url"):
                taxon = body.get("taxon") or {}
                if (
                    taxon.get("scientific_name") == rule["taxon_name"]
                    and rule["external_url"].startswith("https://")
                    and len(rule.get("external_snapshot_sha256", "")) == 64
                    and all(char in "0123456789abcdef" for char in rule["external_snapshot_sha256"])
                    and bool(rule.get("retrieved_at"))
                    and taxon.get("authority") == rule["taxon_authority"]
                    and rule["external_iucn_assessment_url"]
                    == f"https://www.iucnredlist.org/species/{rule['sis_id']}/{rule['assessment_id']}"
                ):
                    return rule["systems"], "reviewed_external_taxon_ecology"
            elif (body.get("documentation") or {}).get("habitats") == rule["habitat_text"]:
                return rule["systems"], "reviewed_assessment_habitat_account"
    return [], "no_affirmative_evidence"


def collect_iucn_metadata(
    folder: Path, species: list[dict], *, expected_release: str | None = None,
    reviewed_evidence_path: Path | None = None
) -> tuple[list[dict], dict]:
    initial_evidence = evidence_identity(reviewed_evidence_path)
    input_hashes = {}
    report_data = (folder / "report.json").read_bytes()
    input_hashes[folder / "report.json"] = hashlib.sha256(report_data).hexdigest()
    report = json.loads(report_data)
    if report.get("status") != "retrieved" or not report.get("release"):
        raise ValueError("IUCN detail intake is incomplete or has no release identity")
    release = report["release"]
    if expected_release is not None and release != expected_release:
        raise ValueError("IUCN detail release differs from the spatial source release")
    updates, rejected = [], []
    # A full dimension has hundreds of thousands of assessed IDs. Index the
    # small intake once instead of issuing a filesystem lookup for every row.
    available = {
        str(p.relative_to(folder)).removesuffix(".json")
        for prefix in ("assessment", "taxa/sis")
        for p in (folder / prefix).glob("*.json")
        if not p.name.startswith(".")
    }

    def read(endpoint):
        if endpoint not in available:
            return None, None
        path = folder / (endpoint + ".json")
        if not path.is_file():
            return None, None
        data = path.read_bytes()
        input_hashes[path] = hashlib.sha256(data).hexdigest()
        record = json.loads(data)
        if record.get("release") != release or record.get("endpoint") != endpoint:
            raise ValueError(f"IUCN evidence identity mismatch: {path}")
        if record.get("status") != "retrieved":
            return None, None
        return record["body"], {
            "path": str(path),
            "sha256": hashlib.sha256(data).hexdigest(),
            "endpoint": endpoint,
            "release": release,
        }

    for item in species:
        identity = positive_id(item["gbif_accepted_id"])
        sid = str(identity)
        aid = item.get("iucn_assessment_id")
        if aid is not None:
            aid = str(positive_id(aid))
        sources = []
        if aid is None:
            taxon, source = read(f"taxa/sis/{sid}")
            if not taxon:
                continue
            if not id_matches(taxon.get("taxon", {}).get("sis_id"), identity):
                rejected.append({"sis_id": sid, "reason": "returned_taxon_id_differs"})
                continue
            candidates = [
                a
                for a in taxon.get("assessments", [])
                if a.get("latest") is True
                and id_matches(a.get("sis_taxon_id"), identity)
                and any(str(s.get("code")) == "1" for s in a.get("scopes", []))
            ]
            if len(candidates) != 1:
                rejected.append({"sis_id": sid, "reason": "no_unique_latest_global_assessment"})
                continue
            aid = str(positive_id(candidates[0]["assessment_id"]))
            sources.append(source)
        body, source = read(f"assessment/{aid}")
        if not body:
            continue
        if item.get("iucn_assessment_id") is None and body.get("latest") is not True:
            rejected.append({"sis_id": sid, "reason": "latest_assessment_detail_mismatch"})
            continue
        if (
            not id_matches(body.get("sis_taxon_id"), identity)
            or not id_matches(body.get("assessment_id"), positive_id(aid))
            or not id_matches(body.get("taxon", {}).get("sis_id"), identity)
            or not any(str(s.get("code")) == "1" for s in body.get("scopes", []))
        ):
            rejected.append({"sis_id": sid, "reason": "assessment_identity_or_scope_mismatch"})
            continue
        category = body.get("red_list_category", {}).get("description", {}).get("en")
        # Match the dimension's existing normalization. Conservation-dependent
        # is intentionally left unchanged for the separately deferred policy.
        category = {
            "Lower Risk/least concern": "Least Concern",
            "Lower Risk/near threatened": "Near Threatened",
        }.get(category, category)
        if category not in CATEGORIES:
            rejected.append({"sis_id": sid, "reason": "unsupported_category", "category": category})
            continue
        if item.get("iucn_assessment_id") is not None and category != item["redlist_category"]:
            rejected.append({"sis_id": sid, "reason": "pinned_category_mismatch"})
            continue
        name = body["taxon"].get("scientific_name")
        family = body["taxon"].get("family_name")
        if not name or not family:
            rejected.append({"sis_id": sid, "reason": "incomplete_authoritative_taxonomy"})
            continue
        sources.append(source)
        systems, method = ecosystem_evidence(body, reviewed_evidence_path=reviewed_evidence_path)
        if method == "reviewed_external_taxon_ecology":
            rules = reviewed_rules(reviewed_evidence_path)
            rule = next(
                r for r in rules if r["sis_id"] == sid and r["assessment_id"] == str(aid)
            )
            sources.append(
                {
                    "url": rule["external_url"],
                    "sha256": rule["external_snapshot_sha256"],
                    "retrieved_at": rule["retrieved_at"],
                    "evidence": rule["habitat_text"],
                    "assessment_link": rule["external_iucn_assessment_url"],
                }
            )
        if method.startswith("reviewed_"):
            sources.append(initial_evidence)
        updates.append(
            {
                "gbif_accepted_id": sid,
                "iucn_assessment_id": str(aid),
                "species_name": name,
                "family": family,
                "redlist_category": category,
                "systems": systems,
                "ecosystem_evidence": method,
                "provenance": json.dumps(sources, sort_keys=True),
                "assessment_latest": body.get("latest"),
                "previous_name": item["species_name"],
            }
        )
    if evidence_identity(reviewed_evidence_path) != initial_evidence:
        raise ValueError("Reviewed ecosystem evidence changed during metadata reconciliation")
    if any(sha256(path) != digest for path, digest in input_hashes.items()):
        raise ValueError("IUCN evidence changed during metadata reconciliation")
    return updates, {
        "reviewed_evidence": initial_evidence,
        "evidence_inputs": {str(path): digest for path, digest in input_hashes.items()},
        "release": release,
        "records_enriched": len(updates),
        "rejected": rejected,
        "systems_evidence": sum(
            u["ecosystem_evidence"] == "explicit_assessment_systems" for u in updates
        ),
        "habitat_evidence": sum(
            u["ecosystem_evidence"] == "suitable_terrestrial_habitat" for u in updates
        ),
        "reviewed_habitat_accounts": sum(
            u["ecosystem_evidence"] == "reviewed_assessment_habitat_account" for u in updates
        ),
        "reviewed_external_ecology": sum(
            u["ecosystem_evidence"] == "reviewed_external_taxon_ecology" for u in updates
        ),
        "historical_pinned_assessments": sum(u["assessment_latest"] is False for u in updates),
    }


def apply_iucn_metadata(connection, folder: Path, *, expected_release: str | None = None,
                        reviewed_evidence_path: Path | None = None) -> dict:
    columns = ["gbif_accepted_id", "species_name", "iucn_assessment_id", "redlist_category"]
    species = [
        dict(zip(columns, row, strict=True))
        for row in connection.execute(
            "SELECT gbif_accepted_id,species_name,iucn_assessment_id,redlist_category FROM species"
        ).fetchall()
    ]
    updates, report = collect_iucn_metadata(folder, species, expected_release=expected_release,
                                            reviewed_evidence_path=reviewed_evidence_path)
    connection.execute(
        "ALTER TABLE species ADD COLUMN IF NOT EXISTS iucn_metadata_provenance VARCHAR"
    )
    connection.execute(
        "ALTER TABLE species ADD COLUMN IF NOT EXISTS iucn_ecosystem_evidence VARCHAR"
    )
    connection.execute(
        "ALTER TABLE species ADD COLUMN IF NOT EXISTS iucn_assessment_latest BOOLEAN"
    )
    connection.execute("""CREATE OR REPLACE TEMP TABLE iucn_api_metadata (
        gbif_accepted_id VARCHAR, iucn_assessment_id VARCHAR, species_name VARCHAR,
        family VARCHAR, redlist_category VARCHAR, systems VARCHAR[],
        provenance VARCHAR, ecosystem_evidence VARCHAR, assessment_latest BOOLEAN)""")
    if updates:
        connection.executemany(
            "INSERT INTO iucn_api_metadata VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (
                    u["gbif_accepted_id"],
                    u["iucn_assessment_id"],
                    u["species_name"],
                    u["family"],
                    u["redlist_category"],
                    u["systems"],
                    u["provenance"],
                    u["ecosystem_evidence"],
                    u["assessment_latest"],
                )
                for u in updates
            ],
        )
        report["spatial_only_assessments_recovered"] = connection.execute("""SELECT count(*)
            FROM species s JOIN iucn_api_metadata m USING (gbif_accepted_id)
            WHERE s.iucn_assessment_id IS NULL""").fetchone()[0]
        connection.execute("""UPDATE species s SET
            iucn_assessment_id=m.iucn_assessment_id,
            species_name=CASE WHEN s.iucn_assessment_id IS NULL THEN m.species_name ELSE s.species_name END,
            family=CASE WHEN s.iucn_assessment_id IS NULL THEN m.family ELSE s.family END,
            redlist_category=m.redlist_category, metadata_status='assessed',
            iucn_metadata_provenance=m.provenance, iucn_ecosystem_evidence=m.ecosystem_evidence,
            iucn_assessment_latest=m.assessment_latest
            FROM iucn_api_metadata m WHERE s.gbif_accepted_id=m.gbif_accepted_id""")
        connection.execute("""INSERT INTO species_systems
            SELECT DISTINCT m.gbif_accepted_id, u.system
            FROM iucn_api_metadata m, unnest(m.systems) AS u(system)
            WHERE NOT EXISTS (SELECT 1 FROM species_systems e
                WHERE e.gbif_accepted_id=m.gbif_accepted_id AND e.system=u.system)""")
    report.setdefault("spatial_only_assessments_recovered", 0)
    return report
