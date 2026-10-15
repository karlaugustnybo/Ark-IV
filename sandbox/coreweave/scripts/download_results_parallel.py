"""Restore a results inventory with bounded parallelism and external-only staging."""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from cloud_storage import credentials, download_file, validate_inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--authorize-transfer", action="store_true")
    args = parser.parse_args()
    if not args.authorize_transfer:
        parser.error("Explicit transfer authorization required")
    root = args.root.resolve(strict=True)
    state = root / "_transfer"
    if state.is_symlink():
        raise ValueError("Symlink in transfer state")
    for name in ("cache", "tmp"):
        (state / name).mkdir(parents=True, exist_ok=True)
    os.environ.update(
        WANDB_CACHE_DIR=str(state / "cache"), WANDB_DIR=str(state), TMPDIR=str(state / "tmp")
    )
    credentials()
    import wandb

    local = threading.local()
    inventory = json.loads((root / "_cloud_storage/inventory.json").read_text())
    files = validate_inventory(root, inventory)
    # Fetch tabular evidence first, so comparisons can run during the transfer.
    files.sort(
        key=lambda item: (not item["path"].startswith(("species/", "exports/")), item["path"])
    )

    def fetch(item):
        if not hasattr(local, "api"):
            local.api = wandb.Api(timeout=60)
        for attempt in range(3):
            try:
                return item["path"], download_file(local.api, root, item)
            except Exception:
                if attempt == 2:
                    raise RuntimeError("Transfer failed: " + item["path"]) from None
                time.sleep(2**attempt)

    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 16))) as executor:
        futures = [executor.submit(fetch, item) for item in files]
        for index, future in enumerate(as_completed(futures), 1):
            path, action = future.result()
            print(f"[{index}/{len(files)}] {action}: {path}", flush=True)
    print("All inventory files SHA-256 verified.", flush=True)


if __name__ == "__main__":
    main()
