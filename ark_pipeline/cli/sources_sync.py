#!/usr/bin/env python3
"""Coordinate the complete Ark-IV source acquisition workflow.

Public sources are refreshed automatically. Restricted IUCN sources are
validated and registered when the account holder has placed authorized files
in the generated staging directories and explicitly recorded that authorization.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

from ark_pipeline.cli.sources_acquire import (
    DEFAULT_CONFIG,
    Catalogue,
    Source,
    atomic_json,
    files_from_inventory_directory,
    inventory_release,
    is_due,
    load_catalogue,
    load_manifest,
    register_manual,
    registered_errors,
    required_file_names,
    source_inventory,
    update_public,
    validate_input_files,
)
from ark_pipeline.runtime.progress import tracked_stage

TRUTHY = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class SourceStatus:
    source: str
    status: str
    release: str | None = None
    detail: str | None = None


class DownloadLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.links.update(value for name, value in attrs if name == "href" and value)


def read_release_page(url: str, *, token: str | None = None) -> str:
    headers = {"User-Agent": "Ark-IV-release-check/1.0", "Accept": "*/*"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(
        urllib.request.Request(url, headers=headers), timeout=30
    ) as response:
        return response.read().decode("utf-8")


def check_upstream_releases(catalogue: Catalogue) -> list[SourceStatus]:
    """Inspect provider metadata, never downloading archives or changing inventories."""
    results = []
    for source in catalogue.sources.values():
        url = source.get("release_check_url")
        if not url:
            continue
        check_id = f"{source.id}-release"
        try:
            inventory = source_inventory(source)
            body = read_release_page(url)
            release = inventory_release(inventory)
            if source.id in {"col-xr", "ott-taxonomy"}:
                page = DownloadLinks()
                page.feed(body)
                if source.id == "col-xr":
                    rows = json.loads(body).get("result")
                    if not isinstance(rows, list) or len(rows) != 1:
                        raise ValueError("expected one latest COL XR release")
                    row = rows[0]
                    issued = row.get("issued", "")
                    if (
                        row.get("origin") != "xrelease"
                        or row.get("sourceKey") != 3
                        or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", issued)
                        or row.get("version") != f"{issued} XR"
                    ):
                        raise ValueError("invalid COL XR release identity")
                    release = f"{issued}-XR"
                else:
                    versions = [
                        tuple(map(int, match.group(1).split(".")))
                        for link in page.links
                        if (match := re.fullmatch(r"ott(\d+\.\d+(?:\.\d+)?)/", link))
                    ]
                    release = "OTT-" + ".".join(map(str, max(versions))) if versions else None
                if release is None:
                    raise ValueError("no versioned release links found")
                changed = (
                    []
                    if release == source.get("release")
                    else [f"configured {source.get('release')}; latest published {release}"]
                )
            elif inventory is None:
                raise ValueError("release check requires an inventory")
            elif source.id == "geoboundaries-adm2":
                rows = json.loads(body)
                if not isinstance(rows, list) or not rows:
                    raise ValueError("expected a non-empty ADM2 catalogue")
                upstream = {row["boundaryISO"]: row["boundaryID"] for row in rows}
                if len(upstream) != len(rows):
                    raise ValueError("duplicate countries in ADM2 catalogue")
                configured = {row["iso"]: row["boundary_id"] for row in inventory["countries"]}
                changed = sorted(
                    key
                    for key in upstream.keys() | configured.keys()
                    if upstream.get(key) != configured.get(key)
                )
            elif source.id == "hydrobasins":
                page = DownloadLinks()
                page.feed(body)
                upstream = {
                    urljoin(url, link)
                    for link in page.links
                    if any(
                        f"/hydrobasins/{kind}/" in link
                        for kind in ("standard", "customized_with_lakes")
                    )
                    and "_lev01-12_" in link
                    and link.endswith(".zip")
                }
                if not upstream:
                    raise ValueError("no HydroBASINS archive links found")
                configured = {row["download_url"] for row in inventory["files"]}
                changed = sorted(upstream ^ configured)
            else:
                raise ValueError(f"unsupported release checker: {source.id}")
            results.append(
                SourceStatus(
                    check_id,
                    (
                        "update-available"
                        if source.get("update_policy") == "pinned"
                        else "action-required"
                    )
                    if changed
                    else "current",
                    release,
                    f"provider inventory differs: {', '.join(changed)}"
                    if changed
                    else "provider metadata matches the configured inventory",
                )
            )
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            results.append(SourceStatus(check_id, "check-failed", detail=str(error)))

    if "iucn-red-list-tabular" in catalogue.sources:
        from ark_pipeline.cli.sources_download_iucn import DEFAULT_BASE_URL, read_iucn_token

        token = read_iucn_token()
        if not token:
            results.append(
                SourceStatus(
                    "iucn-release",
                    "unchecked",
                    detail="No API token; compare the published Red List release with the configured inventories.",
                )
            )
        else:
            try:
                url = os.environ.get("IUCN_API_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
                body = json.loads(
                    read_release_page(f"{url}/information/red_list_version", token=token)
                )
                release = body.get("red_list_version")
                if not isinstance(release, str) or not release:
                    raise ValueError("missing Red List version")
                configured = {
                    configured_release(source)
                    for source in catalogue.sources.values()
                    if source.id in {"iucn-red-list-tabular", "iucn-spatial", "iucn-spatial-tables"}
                }
                changed = configured != {release}
                results.append(
                    SourceStatus(
                        "iucn-release",
                        "action-required" if changed else "current",
                        release,
                        "provider release differs from the checked inventories"
                        if changed
                        else "provider release matches all Red List inventories",
                    )
                )
            except (OSError, ValueError, TypeError, AttributeError) as error:
                results.append(SourceStatus("iucn-release", "check-failed", detail=str(error)))
    return results


def configured_release(source: Source) -> str | None:
    return inventory_release(source_inventory(source)) or source.get("release")


def incoming_directory(root: Path, source: Source) -> Path:
    release = configured_release(source)
    if release is None:
        raise ValueError(f"{source.id} has no configured release")
    return root.resolve() / "acquisition" / "incoming" / source.id / release


def staged_files(source: Source, directory: Path) -> dict[str, Path]:
    if source_inventory(source) is not None:
        return files_from_inventory_directory(source, directory)
    return {name: directory / name for name in required_file_names(source)}


def register_staged_source(root: Path, source: Source, directory: Path) -> None:
    release = configured_release(source)
    if release is None:
        raise ValueError(f"{source.id} has no configured release")
    inventory = source_inventory(source)
    args = argparse.Namespace(
        root=root,
        source=source.id,
        release=release,
        authorized=True,
        reference=True,
        inventory_dir=directory if inventory is not None else None,
        file=[]
        if inventory is not None
        else [f"{name}={path}" for name, path in staged_files(source, directory).items()],
    )
    with contextlib.redirect_stdout(io.StringIO()):
        register_manual(args, Catalogue(sources={source.id: source}, profiles={}))


def sync_restricted_source(
    root: Path,
    source: Source,
    *,
    authorized: bool,
) -> SourceStatus:
    release = configured_release(source)
    manifest = load_manifest(root)
    record = manifest.get("sources", {}).get(source.id)
    errors = registered_errors(root, source, record)
    if not errors and (release is None or record.get("release") == release):
        return SourceStatus(source.id, "current", record.get("release"))

    directory = incoming_directory(root, source)
    directory.mkdir(parents=True, exist_ok=True)
    files = staged_files(source, directory)
    staged_errors = validate_input_files(source, files)
    if staged_errors:
        missing = sum(not path.is_file() for path in files.values())
        detail = f"{missing} file(s) missing from {directory}"
        if not missing:
            detail = "; ".join(staged_errors)
        return SourceStatus(source.id, "action-required", release, detail)
    if not authorized:
        return SourceStatus(
            source.id,
            "authorization-required",
            release,
            "files are ready; set IUCN_DATA_AUTHORIZED=true after accepting provider terms",
        )
    register_staged_source(root, source, directory)
    return SourceStatus(source.id, "registered", release)


def run_public_updates(
    root: Path,
    catalogue: Catalogue,
    source_ids: list[str],
) -> list[SourceStatus]:
    if not source_ids:
        return []
    args = argparse.Namespace(root=root, source=source_ids, force=False, dry_run=False)
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        update_public(args, catalogue)
    payload = json.loads(output.getvalue())
    return [SourceStatus(item["source"], item["status"]) for item in payload.get("status", [])]


def synchronize(
    root: Path,
    catalogue: Catalogue,
    *,
    mode: str,
    authorized: bool,
) -> tuple[list[SourceStatus], bool]:
    required = catalogue.profiles["authorized"].get("required_sources", [])
    public_ids = [
        source_id
        for source_id in required
        if catalogue.sources[source_id].get("access") not in {"manual", "browser-assisted"}
    ]
    statuses = run_public_updates(root, catalogue, public_ids)
    for source_id in required:
        source = catalogue.sources[source_id]
        if source.get("access") in {"manual", "browser-assisted"}:
            statuses.append(sync_restricted_source(root, source, authorized=authorized))

    if mode == "update":
        statuses.extend(check_upstream_releases(catalogue))

    optional_checks = {
        f"{source_id}-release" for source_id in catalogue.sources if source_id not in required
    }
    complete = all(
        status.status
        in {"current", "updated", "registered", "not-modified", "unchecked", "update-available"}
        or (status.status == "check-failed" and status.source in optional_checks)
        for status in statuses
    )
    return statuses, complete


def write_action_plan(
    root: Path,
    catalogue: Catalogue,
    statuses: list[SourceStatus],
    complete: bool,
) -> Path:
    by_source = {item.source: item for item in statuses}
    actions = []
    for status in statuses:
        if status.source in catalogue.sources or status.status not in {
            "action-required",
            "check-failed",
            "unchecked",
            "update-available",
        }:
            continue
        source_id = (
            "iucn-red-list-tabular"
            if status.source == "iucn-release"
            else status.source.removesuffix("-release")
        )
        source = catalogue.sources.get(source_id)
        actions.append(
            {
                "source": status.source,
                "official_url": source.get("official_url") if source else None,
                "release": status.release,
                "status": status.status,
                "detail": status.detail,
                "action": "Review the provider release and update the checked-in inventory before acquisition.",
            }
        )
    for source in catalogue.sources.values():
        status = by_source.get(source.id)
        if status is None or status.status not in {"action-required", "authorization-required"}:
            continue
        inventory = source_inventory(source)
        files = []
        for item in (inventory or {}).get("files", []):
            entry = {"filename": item.get("provider_filename", item["logical_name"])}
            if item.get("download_url"):
                entry["url"] = item["download_url"]
            elif item.get("file_id"):
                entry["url"] = "https://www.iucnredlist.org/resources/files/" + item["file_id"]
            files.append(entry)
        if not files:
            files = [{"filename": name} for name in sorted(required_file_names(source))]
        actions.append(
            {
                "source": source.id,
                "release": configured_release(source),
                "official_url": source.get("official_url"),
                "destination": str(incoming_directory(root, source)),
                "status": status.status,
                "detail": status.detail,
                "files": files,
            }
        )
    path = root.resolve() / "acquisition" / "action-required.json"
    atomic_json(path, {"complete": complete, "actions": actions})
    return path


def print_report(
    root: Path,
    mode: str,
    statuses: list[SourceStatus],
    complete: bool,
    action_plan: Path,
) -> None:
    print(f"Ark-IV data {mode}")
    print(f"Data root: {root.resolve()}")
    print()
    for item in statuses:
        release = f" ({item.release})" if item.release else ""
        detail = f" — {item.detail}" if item.detail else ""
        print(f"  {item.status:22} {item.source}{release}{detail}")
    print()
    if complete:
        print("All required source data is present and valid against configured contracts.")
        if any(status.status in {"unchecked", "check-failed"} for status in statuses):
            print("Some upstream releases could not be verified; see unchecked statuses above.")
    else:
        print(f"Source acquisition or upstream release checks need attention: {action_plan}")
        print("Review release changes before updating inventories. For missing restricted files,")
        print("use the plan's official URLs and destinations, then rerun this command.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("check", "download", "update"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--profile", default="authorized", help="local source profile for read-only check mode"
    )
    return parser


@tracked_stage("acquisition")
def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    catalogue = load_catalogue(args.config)
    if args.mode == "check":
        if args.profile not in catalogue.profiles:
            raise ValueError(f"Unknown source profile: {args.profile}")
        manifest = load_manifest(args.root)
        statuses = []
        for source_id in catalogue.profiles[args.profile]["required_sources"]:
            source = catalogue.sources[source_id]
            record = manifest.get("sources", {}).get(source_id)
            errors = registered_errors(args.root, source, record)
            release = configured_release(source)
            due = (
                is_due(source, record, False)
                if source.get("access") not in {"manual", "browser-assisted"}
                else release != (record or {}).get("release")
            )
            statuses.append(
                SourceStatus(
                    source_id,
                    "invalid" if errors else "due" if due else "current",
                    (record or {}).get("release"),
                    "; ".join(errors) or None,
                )
            )
        statuses.extend(check_upstream_releases(catalogue))
        print(json.dumps({"sources": [vars(status) for status in statuses]}, indent=2))
        return 0 if all(status.status in {"current", "unchecked"} for status in statuses) else 2
    if args.profile != "authorized":
        raise ValueError("--profile only applies to read-only check mode")
    authorized = os.environ.get("IUCN_DATA_AUTHORIZED", "").lower() in TRUTHY
    statuses, complete = synchronize(
        args.root,
        catalogue,
        mode=args.mode,
        authorized=authorized,
    )
    action_plan = write_action_plan(args.root, catalogue, statuses, complete)
    print_report(args.root, args.mode, statuses, complete, action_plan)
    return 0 if complete else 2


if __name__ == "__main__":
    raise SystemExit(main())
