# Static tile export

`just data-tiles` reads the verified prepared-input report and builds a static
PMTiles generation. The default remains static export; database loading and
public data deployment are separate actions. Use a new isolated output directory
for experiments and follow [the data policy](../../DATA_POLICY.md) before releasing
any generated artifact.

## Encoding and compilation

Managed streams omit metric properties whose value is zero. The metadata declares
`tile_metric_encoding: sparse-zero-v1` and `tile_metric_default: 0`. Consumers must
interpret an absent declared metric as zero; this is a transport encoding of a
calculated count, not a replacement for missing scientific evidence. Null metric
inputs fail the export. The dense diagnostic stream keeps its prior JSON bytes.

Parquet columns are excluded from the query only when every row group's statistics
prove zero values with no nulls. Missing statistics retain the column. Projected
values map to compact properties by column name, preserving all 164 metric slots.
A query-scoped cache retains at most 4,096 encoded metric vectors. Geometry and
boundary membership remain specific to each cell; writes use bounded batches.

Boundary parts are indexed separately and deduplicated back to their catalogue
entries. Catalogue order and duplicate codes on distinct entries are preserved.
Outward-snapped quarter-degree rectangles cache exact covered/disjoint proofs;
border cases still use cell intersection predicates. Each framework retains at
most 8,192 proofs. H3 parent polygons are not used as containment substitutes.
The established H3 geometry and dateline lookup behavior are retained.

Coarse features compile at zooms 0–6 and fine features at zooms 8–12, followed by
`tile-join`. One compiler thread uses a GeoJSON pipe. With multiple tile threads,
a separate producer uses a bounded fork-server pool and ordered batches to write
partitioned FlatGeobuf inputs. Integer MVT attribute types are declared explicitly.
The producer exits before native compilation starts. Fine partitions are assigned
to deterministic feature-balanced compiler groups (`--fine-groups`, default 4,
capped by partition count). Each compiler may use `--tile-threads` threads, so
multiple groups can increase CPU and memory demand; start with one group when
resources are constrained. Temporary binary inputs are removed after compilation.

`--checkpoint-shards` retains the serial per-base-cell compilation mode. The
batch size, DuckDB threads and memory limit can be set separately. DuckDB's limit
does not cap Arrow, Shapely, producer workers or compiler memory. Budget disk space
for binary inputs, compiler scratch, cached archives and retained generations.
Automatic pruning is not implemented.

## Publication and recovery

Prepared inputs are checksummed and require complete coarse/fine coverage and
current metric receipts. All five boundary inputs are required. Compiler identity
includes exporter code, dependencies, boundaries, binaries and thread settings.
Each completed resolution group has a checksum receipt; a failed join reuses these
groups. Per-base-cell shard receipts support finer recovery when explicitly chosen.

The builder validates compiler success, emitted counts and PMTiles format, then
writes metadata and switches `current` atomically only after a complete generation.
Handled failures and interruptions retain the previous selected generation.
Failed compiler launches stop processes already started by that compilation.
Consumers needing archive and metadata across requests should pin the immutable
generation path. Interrupted groups restart; completed groups remain reusable.
Ordinary concurrent input changes are rejected before publication.

## Verification and performance claims

The synthetic tile tests compare dense and sparse GeoJSON, all metric slots,
serial and binary batches, native decoded integer MVT properties, coarse/fine
zooms, boundary memberships, seam/polar cells, pentagons, holes, overlaps, nulls,
unknown statistics, cache eviction, interruption and receipt reuse. Native tests
run only when Tippecanoe, tile-join and tippecanoe-decode are installed; compiler-free
encoding and orchestration tests still run without them:

```sh
uv run python -m unittest discover -s tests -p 'test_tile_export.py'
```

These checks establish fixture equivalence, not global throughput. No global
speedup, runtime, archive size or universal memory bound is claimed here. For a
benchmark, first compare decoded cell IDs, geometry, boundary membership and all
164 metric values with absent properties expanded to zero. Record hardware,
compiler/dependency versions, fixture construction, resolutions, worker/group
counts, storage and cold/warm cache conditions. Separate query, feature encoding,
pipe-write, binary byte counts and compiler/join time. Producer encoding totals
sum worker CPU durations and can exceed elapsed wall time. Repeat comparable
runs and keep source-derived reports and outputs local.
