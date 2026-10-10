"""Fetch exact-ID IUCN evidence for a reconciliation queue; never merge identities.

Raw taxon and assessment responses are saved with their release and endpoint.
A 404 remains an unresolved record; authorization and service errors abort.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ark_pipeline.cli.sources_download_iucn import (
    DEFAULT_BASE_URL,
    TOKEN_ENV,
    IucnApi,
    atomic_json,
    iso_now,
    read_iucn_token,
)


def positive_id(value: object) -> int:
    """Reject lossy coercion at the queue/API identity boundary."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("IUCN IDs must be positive integers")
    if isinstance(value, str) and not value.isdecimal():
        raise ValueError("IUCN IDs must be positive integers")
    identity = int(value)
    if identity <= 0:
        raise ValueError("IUCN IDs must be positive integers")
    return identity


def fetch_details(
    api: IucnApi,
    queue: dict,
    output: Path,
    *,
    include_latest_global: bool = False,
    workers: int = 1,
) -> dict:
    version, _ = api.get("information/red_list_version")
    release = version.get("red_list_version")
    if not release:
        raise ValueError("IUCN response has no release identity")
    output.mkdir(parents=True, exist_ok=True)
    records = []
    report = {"status": "fetching", "release": release, "started_at": iso_now(), "records": records}
    atomic_json(output / "report.json", report)

    def fetch_one(identity, prefix):
        endpoint = f"{prefix}/{identity}"
        target = output / prefix / f"{identity}.json"
        if target.is_file():
            saved = json.loads(target.read_text())
            if saved.get("release") != release or saved.get("endpoint") != endpoint:
                raise ValueError(f"Cached evidence does not match this release: {target}")
        else:
            try:
                body, _ = api.get(endpoint)
                saved = {
                    "release": release,
                    "endpoint": endpoint,
                    "retrieved_at": iso_now(),
                    "status": "retrieved",
                    "body": body,
                }
            except urllib.error.HTTPError as error:
                if error.code != 404:
                    raise
                saved = {
                    "release": release,
                    "endpoint": endpoint,
                    "retrieved_at": iso_now(),
                    "status": "not_found",
                }
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(target, saved)
        return identity, endpoint, target, saved

    try:
        extra_assessments = set()
        for field, prefix in [
            ("assessment_ids", "assessment"),
            ("sis_ids", "taxa/sis"),
            ("latest_global_ids", "assessment"),
        ]:
            if field == "latest_global_ids":
                queue = {**queue, field: sorted(extra_assessments)}
            ids = sorted({positive_id(value) for value in queue.get(field, [])})
            executor = ThreadPoolExecutor(max_workers=workers)
            try:
                for identity, endpoint, target, saved in executor.map(
                    lambda value: fetch_one(value, prefix), ids
                ):
                    records.append(
                        {"endpoint": endpoint, "status": saved["status"], "path": str(target)}
                    )
                    if (
                        include_latest_global
                        and field == "sis_ids"
                        and saved["status"] == "retrieved"
                    ):
                        body = saved["body"]
                        if int(body.get("taxon", {}).get("sis_id", -1)) == identity:
                            for assessment in body.get("assessments", []):
                                if (
                                    assessment.get("latest") is True
                                    and int(assessment.get("sis_taxon_id", -1)) == identity
                                    and any(
                                        str(scope.get("code")) == "1"
                                        for scope in assessment.get("scopes", [])
                                    )
                                ):
                                    extra_assessments.add(positive_id(assessment["assessment_id"]))
                    if len(records) % 25 == 0:
                        atomic_json(output / "report.json", report)
                        print(f"Retrieved or cached {len(records)} exact-ID records", flush=True)
            finally:
                executor.shutdown(wait=True, cancel_futures=True)
        final_version, _ = api.get("information/red_list_version")
        if final_version.get("red_list_version") != release:
            raise ValueError(
                "IUCN release changed during retrieval; do not combine these responses"
            )
        # Retrieval is complete, not reconciliation. A name match is never proof
        # that old and new SIS IDs represent the same taxonomic concept.
        report.update(status="retrieved", completed_at=iso_now())
    except BaseException:
        report.update(status="incomplete")
        atomic_json(output / "report.json", report)
        raise
    atomic_json(output / "report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--token-env", default=TOKEN_ENV)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--include-latest-global", action="store_true")
    parser.add_argument("--workers", type=int, choices=range(1, 5), default=1)
    args = parser.parse_args()
    from dotenv import dotenv_values

    token = (
        read_iucn_token()
        if args.token_env == TOKEN_ENV
        else os.environ.get(args.token_env) or dotenv_values(".env").get(args.token_env)
    )
    if not token:
        parser.error(f"No IUCN token in environment variable {args.token_env}")
    if args.delay < 0:
        parser.error("--delay must be nonnegative")
    report = fetch_details(
        IucnApi(token, DEFAULT_BASE_URL, args.delay),
        json.loads(args.queue.read_text()),
        args.output_dir,
        include_latest_global=args.include_latest_global,
        workers=args.workers,
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "release": report["release"],
                "records": len(report["records"]),
            }
        )
    )


if __name__ == "__main__":
    main()
