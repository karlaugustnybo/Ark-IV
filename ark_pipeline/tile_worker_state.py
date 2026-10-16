"""Single-threaded fork-server preload; never import DuckDB or Arrow here."""

import gc
import json
import os
from pathlib import Path

import psutil
import shapely

from ark_pipeline.tile_spatial import BoundaryBatchIndex

INDEXES = {}
if os.environ.get("ARK_TILE_BOUNDARIES"):
    INDEXES = {key: BoundaryBatchIndex.from_path(Path(path))
               for key, path in json.loads(os.environ["ARK_TILE_BOUNDARIES"]).items()}
    for index in INDEXES.values():
        # Build lazy GEOS predicate indexes before forking, so their pages are
        # shared too. Both point-location and segment-intersection indexes matter.
        shapely.intersects(index.geometries, shapely.envelope(index.geometries))
        shapely.contains(index.geometries, shapely.point_on_surface(index.geometries))
    if psutil.Process().num_threads() != 1:
        raise RuntimeError("Tile fork-server preload unexpectedly started native threads")
    gc.collect()
    gc.freeze()
