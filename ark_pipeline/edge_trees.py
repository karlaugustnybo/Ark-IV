"""Extract small, reproducible mammal pilots from the official global-tree archives."""

from __future__ import annotations

import hashlib
import json
import tarfile
import urllib.request
from pathlib import Path

from ark_pipeline.edge import extract_clade
from ark_pipeline.runtime.provenance import atomic_json, iso_now, sha256


class CountingReader:
    def __init__(self, handle):
        self.handle = handle
        self.bytes_read = 0
        self.digest = hashlib.sha256()

    def read(self, size=-1):
        data = self.handle.read(size)
        self.bytes_read += len(data)
        self.digest.update(data)
        return data


def extract_archive(handle, source: dict, directory: Path, clade: str, count: int) -> dict:
    if count < 2 or count > source["total_trees"]:
        raise ValueError("Tree pilot size must be between 2 and the archive tree count")
    directory.mkdir(parents=True, exist_ok=True)
    reader = CountingReader(handle)
    records = []
    with tarfile.open(fileobj=reader, mode="r|gz") as archive:
        for member in archive:
            if (
                not member.isfile()
                or Path(member.name).name.startswith("._")
                or "__MACOSX" in Path(member.name).parts
                or not member.name.endswith((".tre", ".tree", ".nwk"))
            ):
                continue
            content = archive.extractfile(member).read()
            crown = extract_clade(content.decode("utf-8"), clade)
            path = directory / f"{len(records):03d}.tre"
            temporary = path.with_suffix(".tre.part")
            temporary.write_text(crown)
            temporary.replace(path)
            records.append(
                {
                    "path": str(path.resolve()),
                    "sha256": sha256(path),
                    "archive_member": member.name,
                    "full_tree_bytes": len(content),
                    "full_tree_sha256": hashlib.sha256(content).hexdigest(),
                }
            )
            print(
                f"{source['id']}: extracted {len(records)}/{count} crowns ({reader.bytes_read / 1e6:.0f} MB received)",
                flush=True,
            )
            if len(records) == count:
                break
    if len(records) != count:
        raise ValueError(f"Archive contained only {len(records)} eligible trees")
    receipt = {
        "schema_version": 1,
        "source": source,
        "clade": clade,
        "created_at": iso_now(),
        "selection": "first eligible members in archive order; convenience pilot, not a random sample",
        "archive_checksum_verified": False,
        "compressed_prefix_bytes": reader.bytes_read,
        "compressed_prefix_sha256": reader.digest.hexdigest(),
        "trees": records,
    }
    atomic_json(directory / "manifest.json", receipt)
    return receipt


def acquire_archive(source: dict, directory: Path, clade: str, count: int) -> dict:
    manifest_path = directory / "manifest.json"
    if manifest_path.is_file():
        receipt = json.loads(manifest_path.read_text())
        if (
            receipt["source"] == source
            and receipt["clade"] == clade
            and len(receipt["trees"]) == count
        ):
            if not all(
                Path(item["path"]).is_file() and sha256(Path(item["path"])) == item["sha256"]
                for item in receipt["trees"]
            ):
                raise ValueError(
                    f"Cached {source['id']} crowns have changed; remove the cache and reacquire"
                )
            return receipt
    with urllib.request.urlopen(source["url"], timeout=90) as response:
        return extract_archive(response, source, directory, clade, count)
