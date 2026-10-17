"""Pack only explicitly reviewed, hash-pinned files; never discover upload inputs."""

from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
from pathlib import Path, PurePosixPath

SECRET = re.compile(
    rb"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----|gh[pousr]_[A-Za-z0-9]{36,}|AKIA[A-Z0-9]{16}|sk-[A-Za-z0-9_-]{32,}"
)
PRIVATE_PARTS = {".git", ".venv", "__pycache__", "results", "receipts", "archive", "wandb"}
CODE_SUFFIXES = {".py", ".toml", ".lock", ".sh", ".sbatch"}


def relative_path(value, *, authorized_input=False):
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value or str(path) != value:
        raise ValueError("Expected a normalized relative POSIX path")
    if any(
        p.startswith(".env")
        or p in ({".git", ".venv", "__pycache__", "wandb"} if authorized_input else PRIVATE_PARTS)
        for p in path.parts
    ):
        raise ValueError("Credential, receipt, cache or historical path is not a payload")
    return path


def regular_file(root, relative, *, authorized_input=False):
    relative = relative_path(relative, authorized_input=authorized_input)
    root = Path(root).resolve(strict=True)
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("Payload symlinks are forbidden")
    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise ValueError("Payload must be a regular file inside its declared root")
    return path


def build_payload(manifest, *, allow_authorized_data=False):
    """Manifest entries pin source root, source path, destination, role and SHA256.

    Non-code entries additionally require provenance and transfer_authorization.
    Neither a private provider project nor a source licence implies authorization.
    Returns deterministic bytes and a local identity report; performs no transfer.
    """
    manifest = Path(manifest)
    entries = json.loads(manifest.read_text())["files"]
    if not entries:
        raise ValueError("Empty payload manifest")
    seen = set()
    report = []
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for entry in entries:
            authorized = entry["role"] == "authorized-input"
            destination = str(relative_path(entry["destination"], authorized_input=authorized))
            if destination in seen:
                raise ValueError("Duplicate payload destination")
            seen.add(destination)
            root = Path(entry["root"]).expanduser()
            if not root.is_absolute():
                root = manifest.resolve().parent / root
            source = regular_file(root, entry["path"], authorized_input=authorized)
            data = source.read_bytes()
            sha = hashlib.sha256(data).hexdigest()
            if sha != entry["sha256"]:
                raise ValueError("Payload changed since manual review")
            if SECRET.search(data):
                raise ValueError("Possible credential in payload")
            if entry["role"] == "code":
                parts = PurePosixPath(entry["path"]).parts
                if source.suffix not in CODE_SUFFIXES and source.name != "justfile":
                    raise ValueError("Non-code payload needs explicit data authorization")
                if any(p in {"data", "acquisition", "artifacts"} for p in parts):
                    raise ValueError("Data roots cannot be classified as code")
            elif entry["role"] == "authorized-input":
                if (
                    not allow_authorized_data
                    or not entry.get("provenance")
                    or not entry.get("transfer_authorization")
                ):
                    raise ValueError(
                        "Explicit per-input provenance and transfer authorization required"
                    )
            else:
                raise ValueError("Unknown payload role")
            item = tarfile.TarInfo(destination)
            item.size = len(data)
            item.mode = 0o644
            archive.addfile(item, io.BytesIO(data))
            report.append(
                {"path": destination, "bytes": len(data), "sha256": sha, "role": entry["role"]}
            )
    payload = buffer.getvalue()
    return payload, {"sha256": hashlib.sha256(payload).hexdigest(), "files": report}
