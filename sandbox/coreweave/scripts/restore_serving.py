"""Relocate only an inventory-verified serving configuration; preserve receipts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cloud_storage import validate_inventory, verify_inventory


def restore(root: Path, *, publish_current=False, validator=None):
    root = root.resolve(strict=True)
    inventory = json.loads((root / "_cloud_storage/inventory.json").read_text())
    verify_inventory(root, inventory)
    files = {item["path"]: item for item in validate_inventory(root, inventory)}
    selection = json.loads((root / "serving-selection.json").read_text())
    generation = root / selection["generation"]
    if not generation.resolve().is_relative_to(root) or generation.is_symlink():
        raise ValueError("Serving generation is outside verified download")
    source = generation / "serving.json"
    if source.relative_to(root).as_posix() not in files:
        raise ValueError("Serving config is not in the verified inventory")
    if "serving-selection.json" not in files:
        raise ValueError("Serving selection is not in the verified inventory")
    old = selection["original_root"]
    if not isinstance(old, str) or not old.startswith("/"):
        raise ValueError("Invalid original root")

    def relocate(value):
        if isinstance(value, dict):
            return {relocate(k): relocate(v) for k, v in value.items()}
        if isinstance(value, list):
            return [relocate(v) for v in value]
        if isinstance(value, str) and value.startswith(old + "/"):
            return str(root / value[len(old) + 1 :])
        return value

    config = relocate(json.loads(source.read_text()))
    if not config.get("pinned_files"):
        raise ValueError("Serving config has no pinned files")
    for name, pinned in config["pinned_files"].items():
        path = Path(name)
        if not path.resolve().is_relative_to(root):
            raise ValueError("Pinned file escapes download")
        record = files.get(path.relative_to(root).as_posix())
        if record is None or record["bytes"] != pinned["bytes"]:
            raise ValueError("Pinned file not covered by verified inventory")
        pinned["modified_ns"] = path.stat().st_mtime_ns
    target = generation / "serving-restored.json"
    if target.exists():
        raise ValueError("Refusing to overwrite existing relocated configuration")
    if publish_current and validator is None:
        raise ValueError("Publishing current requires a compatible serving validator")
    target.write_text(json.dumps(config, indent=2) + "\n")
    if validator is not None:
        validator(target)
    if publish_current:
        current = root / "tiles/current"
        if current.exists() or current.is_symlink():
            raise ValueError("Refusing to replace an existing current selection")
        current.parent.mkdir(parents=True, exist_ok=True)
        current.symlink_to(generation.relative_to(current.parent), target_is_directory=True)
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--publish-current", action="store_true")
    args = parser.parse_args()
    validator = None
    if args.publish_current:
        # This integration is available after the independent serving PR lands.
        from ark_pipeline.cli.map_serving import validate_config

        validator = validate_config
    print(restore(args.root, publish_current=args.publish_current, validator=validator))
