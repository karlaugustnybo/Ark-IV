"""Fail when the prospective Git commit contains private or unsafe artifacts."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import BinaryIO

ROOT = Path(__file__).resolve().parents[1]
MAX_GITHUB_FILE_BYTES = 95 * 1024 * 1024
REQUIRED_FILES = (
    "DATA_POLICY.md",
    "LICENSE",
    "NOTICE.md",
    "ai_declaration.md",
    "docs/publication_checklist.md",
)
ALLOWED_DATA_FILES = {
    "data/exports/.gitkeep",
    "data/tiles/.gitkeep",
}
BLOCKED_EXACT_PATHS = {
    "data/Ark-IV.duckdb",
    "data/precomputed_cache.duckdb",
}
BLOCKED_SUFFIXES = {
    ".arrow",
    ".csv",
    ".db",
    ".duckdb",
    ".feather",
    ".ipc",
    ".mov",
    ".parquet",
    ".pmtiles",
    ".sqlite",
    ".sqlite3",
    ".tsv",
}
BLOCKED_DIRECTORIES = {
    "archive", "acquisition", "denmark_prototype", ".tmp", ".venv",
    "node_modules", "__pycache__", ".ruff_cache", ".pytest_cache", ".svelte-kit",
    ".firecrawl", ".kilo", ".codex", "__marimo__", "results", "receipts", "artifacts", "_cloud_storage", "wandb",
}
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(rb"gh[oprs]_[A-Za-z0-9]{36,}"),
    re.compile(rb"sk-[A-Za-z0-9_-]{32,}"),
)


def git_output(*args: str) -> bytes:
    return subprocess.check_output(("git", *args), cwd=ROOT)


SCAN_CHUNK_BYTES = 1024 * 1024
# Detection needs only the shortest accepted prefix, not the full token. Keeping
# 128 bytes covers every minimum pattern even when a token spans two chunks.
SCAN_OVERLAP_BYTES = 128
ATTRIBUTION_MANIFEST = "app/static/data/boundary-frameworks.json"
PRIVATE_EVIDENCE_PATH = "config/iucn_reviewed_ecosystem_evidence.json"


def contains_secret(stream: BinaryIO) -> bool:
    tail = b""
    while chunk := stream.read(SCAN_CHUNK_BYTES):
        content = tail + chunk
        if any(pattern.search(content) for pattern in SECRET_PATTERNS):
            return True
        tail = content[-SCAN_OVERLAP_BYTES:]
    return False


def path_errors(relative: str) -> list[str]:
    path = Path(relative)
    errors: list[str] = []
    if any(part in BLOCKED_DIRECTORIES for part in path.parts[:-1]):
        errors.append(f"local-only artifact must remain outside Git: {relative}")
    if (path.name == ".env" or path.name.startswith(".env.")) and path.name != ".env.example":
        errors.append(f"environment secrets must remain outside Git: {relative}")
    if relative == PRIVATE_EVIDENCE_PATH:
        errors.append(f"restricted reviewed evidence must remain outside Git: {relative}")
    if relative in BLOCKED_EXACT_PATHS:
        errors.append(f"restricted database must not be published: {relative}")
    if relative.startswith("data/") and relative not in ALLOWED_DATA_FILES:
        errors.append(f"generated or source data must remain outside Git: {relative}")
    if path.suffix.lower() in BLOCKED_SUFFIXES:
        errors.append(f"blocked data/binary format in prospective commit: {relative}")
    return errors


def attribution_errors(content: bytes) -> list[str]:
    errors: list[str] = []
    try:
        frameworks = json.loads(content)["frameworks"]
        for framework in frameworks:
            if framework.get("status") == "ready":
                for key in ("license", "source_url"):
                    if not framework.get(key):
                        errors.append(
                            f"ready boundary framework lacks {key}: {framework.get('id', '?')}"
                        )
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        errors.append(f"invalid boundary attribution manifest: {error}")
    return errors


def content_errors(relative: str, size: int, stream: BinaryIO) -> list[str]:
    errors: list[str] = []
    if size >= MAX_GITHUB_FILE_BYTES:
        errors.append(f"file is too large for a normal GitHub release: {relative}")
    if relative == ATTRIBUTION_MANIFEST:
        # Attribution is small metadata, unlike allowed media assets. Bound the
        # JSON parser too, and fail closed rather than omit its review.
        content = stream.read(SCAN_CHUNK_BYTES + 1)
        if len(content) > SCAN_CHUNK_BYTES:
            errors.append("boundary attribution manifest exceeds review size limit")
        else:
            errors.extend(attribution_errors(content))
        stream.seek(0)
    if contains_secret(stream):
        errors.append(f"possible credential or private key detected: {relative}")
    return errors


def candidate_files() -> list[Path]:
    """Non-ignored untracked paths for the separate pre-staging assessment."""
    return sorted(
        ROOT / raw.decode("utf-8", errors="surrogateescape")
        for raw in git_output("ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
        if raw
    )


def worktree_errors(paths: list[Path]) -> list[str]:
    """Conservatively assess filesystem candidates; never stand in for the index."""
    errors: list[str] = []
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        errors.extend(path_errors(relative))
        if path.is_symlink():
            errors.append(f"file symlinks require explicit release review: {relative}")
        elif not path.is_file():
            errors.append(f"untracked candidate is not a regular file: {relative}")
        else:
            with path.open("rb") as stream:
                errors.extend(content_errors(relative, path.stat().st_size, stream))
    return sorted(set(errors))


def index_errors() -> tuple[list[str], int, int]:
    """Inspect exactly the indexed tree, including missing worktree files."""
    errors: list[str] = []
    relative_paths: set[str] = set()
    count = total_bytes = 0
    for raw in git_output("ls-files", "--stage", "-z").split(b"\0"):
        if not raw:
            continue
        metadata, raw_path = raw.split(b"\t", 1)
        mode, oid, stage = metadata.decode("ascii").split()
        relative = raw_path.decode("utf-8", errors="surrogateescape")
        errors.extend(path_errors(relative))
        if stage != "0":
            errors.append(f"unresolved index entry requires release review: {relative}")
            continue
        count += 1
        if mode not in {"100644", "100755"}:
            errors.append(f"indexed non-regular file requires explicit release review: {relative} ({mode})")
            continue
        relative_paths.add(relative)
        size = int(git_output("cat-file", "-s", oid))
        total_bytes += size
        # SpooledTemporaryFile keeps memory bounded and permits JSON metadata to
        # rewind without reading an arbitrary asset into memory.
        with tempfile.SpooledTemporaryFile(max_size=SCAN_CHUNK_BYTES) as stream:
            subprocess.run(("git", "cat-file", "blob", oid), cwd=ROOT, stdout=stream, check=True)
            stream.seek(0)
            errors.extend(content_errors(relative, size, stream))
    for required in REQUIRED_FILES:
        if required not in relative_paths:
            errors.append(f"missing required indexed release document: {required}")
    return sorted(set(errors)), count, total_bytes


def history_errors() -> list[str]:
    errors: list[str] = []
    for path in sorted(BLOCKED_EXACT_PATHS):
        commits = git_output("log", "--all", "--format=%H", "--", path).splitlines()
        if commits:
            errors.append(
                f"restricted artifact remains in Git history ({len(commits)} commit(s)): {path}"
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--history",
        action="store_true",
        help="also fail if known restricted databases remain anywhere in local Git history",
    )
    args = parser.parse_args()

    paths = candidate_files()
    errors, indexed_count, indexed_bytes = index_errors()
    errors.extend(worktree_errors(paths))
    if args.history:
        errors.extend(history_errors())

    if errors:
        print("Release check failed:")
        for error in errors:
            print(f"  - {error}")
        return 1

    scope = "prospective commit and history" if args.history else "prospective commit"
    total_mebibytes = indexed_bytes / (1024 * 1024)
    print(
        f"Release check passed for {scope} "
        f"({indexed_count} indexed files, {total_mebibytes:.1f} MiB inspected; "
        f"{len(paths)} untracked candidates assessed separately)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
