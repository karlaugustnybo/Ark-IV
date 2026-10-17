"""Download the official alternative median and extract topology/date tree pilots."""

import argparse
import json
import tomllib
from pathlib import Path

from ark_pipeline.edge import acquire_inputs
from ark_pipeline.edge_trees import acquire_archive
from ark_pipeline.runtime.provenance import atomic_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/edge_prototype.toml"))
    parser.add_argument("--input-dir", type=Path, default=Path("data/edge/inputs"))
    parser.add_argument("--trees-per-archive", type=int, default=8)
    args = parser.parse_args(argv)
    config = tomllib.loads(args.config.read_text())
    sources = acquire_inputs({"sources": config["validation_sources"]}, args.input_dir, True)
    atomic_json(args.input_dir / "validation_sources.json", sources)
    for source in config["tree_archives"]:
        acquire_archive(
            source, args.input_dir / source["id"], config["clade"], args.trees_per_archive
        )
    print(json.dumps({"status": "downloaded", "trees_per_archive": args.trees_per_archive}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
