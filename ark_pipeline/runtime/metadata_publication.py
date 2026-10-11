"""Publish a complete metadata dimension, restoring selected files on failure."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from ark_pipeline.runtime.provenance import sha256

METADATA_FILES = ("species.parquet", "species_systems.parquet", "build-report.json")


def source_registration_guard(root: Path | None) -> Callable[[], None]:
    """Pin the external acquisition selection, including legacy absence.

    Keep this guard separate from dimension hashes: publication intentionally
    replaces those files, while the selected source registration must stay fixed.
    """
    path = root / "acquisition/current.json" if root is not None else None

    def identity():
        return sha256(path) if path is not None and path.exists() else None

    pinned = identity()

    def validate():
        if identity() != pinned:
            raise ValueError("Spatial source registration changed before metadata publication")

    return validate


def publish_metadata(staged: Path, target: Path, *, validate: Callable[[], None] = lambda: None) -> None:
    """Select all three completed files with rollback on errors or changed evidence.

    This protects failed refreshes, including absent targets; concurrent readers
    should still coordinate with the writer because three renames are not atomic.
    """
    validate()
    for name in METADATA_FILES:
        if not (staged / name).is_file():
            raise ValueError(f"Incomplete metadata publication: {name}")
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".metadata-rollback-", dir=target) as temporary:
        backup = Path(temporary)
        existing = {name for name in METADATA_FILES if (target / name).exists()}
        for name in existing:
            shutil.copyfile(target / name, backup / name)
        try:
            for name in METADATA_FILES:
                validate()
                (staged / name).replace(target / name)
                validate()
        except BaseException:
            for name in METADATA_FILES:
                if name in existing:
                    shutil.copyfile(backup / name, target / name)
                elif (target / name).exists():
                    (target / name).unlink()
            raise
