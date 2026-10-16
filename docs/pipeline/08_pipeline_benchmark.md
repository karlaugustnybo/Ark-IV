# Pipeline benchmark after acquisition

Run the entire post-acquisition pipeline on the same stratified polygon fixture
used for the pair-generation runtime studies:

```bash
just data-benchmark                       # shared PIPELINE_WORKERS/auto setting
just data-benchmark --workers 4           # one budget for the compute stages
just data-benchmark --max-per-bin 10      # shorter, deterministic stratified subset
just data-benchmark --dry-run             # show stages and paths without building
just data-benchmark --ui rich             # force the dashboard, including redirected output
just data-benchmark --fresh               # new calibration instead of resuming an interruption
```

The default is the existing 1,000-polygon fixture at
`data/spatial-test/benchmark-samples/iucn-polygons-stratified-1000.parquet`.
Its source hashes must match the acquired archives. Current row exclusions are
applied before processing; the report records the selected and excluded counts.
Because it was selected under v2, estimates carry a warning about newly eligible
polygons that are absent from that sample. `--rebuild-sample` creates a new
1,000-polygon fixture using the original deterministic size-band sampler and
the current row policy. A missing fixture is also built automatically. The
original fixture is preserved. `--sample /path/to/sample.parquet` selects another
fixture with its companion `.json` metadata. `--max-per-bin N` retains at most N
ordinary hash-selected rows per band plus the forced smallest/largest fixtures;
it does not simply take the first N polygons.

Each fresh run uses a **new directory** under
`GLOBAL_DATA_ROOT/benchmarks/pipeline/<timestamp-id>/`. Override it with
`--output-root /path/to/a/new/directory`. Repeating the same command resumes a
compatible interrupted benchmark automatically; an explicit output directory can
also resume that run. Completed benchmarks are never overwritten. Sources are
read in place. All generated pairs, lists, crosswalks, metadata, databases,
boundary catalogues, scratch files and tiles stay inside that directory.
The command does not download sources, load PostgreSQL, or select benchmark
outputs for the live application.

This is an end-to-end serving benchmark for a sampled **polygon pair input**,
not a full benchmark of every spatial source route. It does not stream the
point archives, normalize the HydroBASINS relationship archives, cover the
referenced v1c basins, or execute the basin/species join. Those operations run
in `just data-build` with resumable receipts and measured dashboard progress,
but they currently have no full-build ETA prior. Do not add the polygon estimate
to a release schedule as if it included those phases.

## Live dashboard and interruption

The Rich dashboard is automatic in a terminal. `--ui rich` forces it and
`--ui plain` selects line output. It refreshes four times per second, showing
stage timings, benchmark priors, live remaining time, expected finish, and the
projected full-build total. System CPU/RAM and process-tree memory/CPU appear
alongside the effective worker counts. Unavailable system measurements are
labelled rather than reported as zero.

The `Done` column shows a measured-work percentage for the active stage, such as
a percentage alongside the completed and selected polygon counts. It is not a percentage of elapsed or
remaining time. Unknown progress shows a dash; a successful stage shows a check.
Work counts stay on one line, while per-worker operations remain in Live Activity.
Worker counts are configured limits; `Active range jobs` reports how many
polygon tasks are still running. Large polygons borrow idle slots for native
tile threads, under the same spatial worker budget. Validation, simplification
and final deduplication are still serial within each polygon. With `--workers 1`,
tile processing also remains serial.

A process-tree reading near one core can be expected during checksum/source I/O,
single-query setup, or the tail of a parallel stage after seven workers have
finished and one large polygon remains. It does not mean the configured worker
limit was ignored. The dashboard now exposes active polygon jobs and borrowed
tile slots so those cases can be distinguished. Parallel tile work is bounded by
the shared spatial budget and cannot oversubscribe eight polygon workers into
eight additional threads each.

Run remaining and Expected finish are calculated only when every unfinished
stage has an estimate. Otherwise the dashboard explicitly counts the unestimated
stages, rather than presenting a partial subtotal as a run forecast. Pair ETAs
use each active polygon's elapsed time and grid progress, with a lower bound
from the longest active job; eight configured workers cannot divide the time
needed by one remaining polygon. Metric ETAs also consider recent throughput
and the longest active partition. These remain planning estimates: grid tiles
and partitions can have uneven costs, and unmeasured finalization can extend a
stage.

Tile export reports the number of features streamed across all base cells,
separately from subsequent compiler zoom/index passes. A Live left value ending
in `+ ?` estimates the streaming phase while later compilation remains
unestimated. It does not become a full-run ETA. Streaming progress resets only
when entering a new explicit phase, not when compiler or worker messages arrive.
A small polygon sample can still cover many fine cells, so row count alone does
not bound the time or disk space for aggregation and static tile compilation.
Stage logs do not separate feature generation from compiler backpressure.

Progress events report actual downloaded/hashed bytes, archive batches, polygon
grid batches, individual polygon completions, pair writes, list batches, metric
partitions and compiler passes. A percentage for a polygon, SQL query or compiler
pass is explicitly local to that operation. Some native operations cannot report
an intermediate percentage; the dashboard continues updating elapsed time and
resource use, with their log messages in the activity panel.

Initial estimates come from the latest completed benchmark with matching source
and spatial-profile fingerprints. Use `--benchmark-report /path/to/benchmark-report.json`
to select one explicitly. Pair ETAs update independently within size bands,
keeping forced extremes separate from ordinary observations. Later stages use
actual cell/relationship counts and measured throughput. Unknown stages or
unrepresented size bands remain unestimated until enough evidence exists.
Worker-count scaling is an initial assumption, not a measured speedup.

`dashboard-state.json` is saved atomically every second and on Ctrl+C. It retains
completed stages, active time, observations, estimates and event-reader position;
`progress.jsonl` retains the event history. Repeating the command restores that
state without counting time spent paused. A lock prevents two processes from
resuming the same run simultaneously. Changes to sources, profile, code, sample,
boundary inputs or resource settings prevent incompatible state reuse.
Benchmark compatibility also includes Python/dependency versions, platform,
CPU model/count and physical memory, compiler executable path/content, dependency
lockfiles and source-affecting configuration. This is a conservative environment
fingerprint, not a guarantee that load, thermal state or OS caches are identical.
A changed environment starts a new automatically discovered run; an explicit
existing directory is rejected. Legacy benchmark identities lacking environment
provenance are preserved but cannot be migrated safely: choose fresh output.
Schema-1 dashboard checkpoints with an exactly matching identity remain supported.
On cancellation, the runner sends SIGTERM to its owned stage process group,
allows a bounded grace period and kills any remaining group members even if the
leader has exited. The direct process is reaped before interruption is reported.

On resume, the original run runtime stays attached to its measurements. Each
invocation adds an attempt ID and runtime record; new stages name that attempt.
Completed benchmark stages retain their original measurements and
their output inventory is checked before continuing. Downstream stages retain
their normal receipt/checksum validation. The interrupted stage retries; an
unfinished polygon archive or sample pair stage may need recalculation, while
validated metric partitions and tiles can be reused. Earlier observations remain
available to the ETA model, but unpublished polygons are not counted as saved
output. Stage timings include earlier attempts and exclude paused time. Resumed
reports flag that partial-stage cache reuse can affect throughput; use `--fresh`
with a new output directory for a clean calibration.

## Measured stages

1. Verify/read all registered spatial archives and recalculate the size-band
   population. This measures full source I/O on the current machine without
   polyfilling the full dataset. The census reads native ring envelopes and
   streams attributes joined by source FID, avoiding full WKB export and Shapely
   allocation for ordinary polygons. Ring-to-hole classification is disabled
   only during envelope reading and restored immediately afterward; coverage
   generation retains its normal geometry handling. Checksums, schema/CRS checks,
   row exclusions and per-layer row reconciliation remain required. Null, empty
   and degenerate envelopes use the original geometry path to preserve exclusion
   and failure behavior. Memory holds envelope/FID arrays for one layer plus an
   attribute batch, rather than a batch of full polygon geometries. This still
   reads every archive and can take minutes even for a small sample.
2. Build the crosswalk from the complete acquired IUCN, GoaT and NCBI references.
3. Generate sample pairs with the production kernel and bounded worker queue.
4. Feed **those pairs** into fine/coarse species-list aggregation.
5. Prepare the acquired global ADM2 boundaries in the benchmark directory.
6. Build complete species metadata, then coarse serving data and fine metrics
   against the sample's lists, followed by input reconciliation and real PMTiles.

Complete reference tables preserve lineage/DNA context; they are not spatially
sampled. No previous global pair/list/metric output is substituted. All five
boundary inputs are required; explicit `--admin0`, `--admin1`, `--municipality`,
`--eez` and `--conservation-framework` files can be supplied. An explicit existing
municipality file excludes ADM2 preparation from the measured/estimated scope.

## Reports and interpretation

`benchmark-report.md` shows measured wall time and an estimated full-build time
for each stage, plus both totals. `benchmark-report.json` adds resource settings,
source/profile/code fingerprints, workload counts, models and size-band means.
Each stage has a log under `logs/`; `polygon-timings.jsonl` retains individual
kernel timings and output counts. Peak process-tree RSS is sampled when process
inspection is available; an unavailable measurement is null. Reports are saved
after each stage, including failure; a failed run does not present a full total.

Pair estimates weight per-band means by the current full polygon population,
retain observed worker utilization, and separately scale pair writing. Forced
extreme fixtures exercise every stage but are excluded from sample means unless
the whole band was sampled. A populated band with no representative observation
prevents a full total estimate. The full source scan is added once as an I/O
calibration; its extra bounds decoding makes this a planning approximation.
Crosswalk, boundary preparation and species metadata run at full reference size,
so their measured costs are added once without multiplication. Fixture creation
is reported as setup, outside the projected build total.

Downstream estimates extrapolate the observed duplicate rate, cell counts and
relationship counts, with physical H3 cell-count caps. The report compares cell
and relationship throughput for metrics, and linear versus N log N scaling for
aggregation/tiles. Those scenario ranges **are not confidence intervals**.
Global overlap, disk spilling, compiler behavior and worker utilization may
differ from the sample. The benchmark creates fresh outputs but does not clear
OS filesystem caches. The fixture includes very large polygons, so the full
1,000-row run can still require substantial time and disk space.

Worker configuration follows [the pipeline guide](01_data_pipeline.md). `--workers`
sets spatial processes, fine-metric processes, single-process DuckDB threads and
Tippecanoe threads. Metric threads per worker and the tile query helper default
to one; explicit stage overrides remain available. `--memory-limit` controls
DuckDB memory per process, not total memory or native geometry allocations.

The default `dynamic-h3-v1` tile projection uses resolution-3 feature counts,
including the global H3 coarse-cell cap; fine-cell counts do not scale coarse
compilation time. Legacy static archive estimates use coarse plus fine features.
Saved estimates record their serving contract. Full-build forecasts omit tile
priors without a compatible estimate contract, including older dynamic-tagged
reports that used the static workload model. These remain planning calculations,
not measured global runtimes.

## Census implementation and verification

The implementation uses [Pyogrio's native bounds reader](https://pyogrio.readthedocs.io/en/latest/api.html#pyogrio.read_bounds)
with GDAL's documented [ring-organization setting](https://gdal.org/en/stable/user/configoptions.html#config-OGR_ORGANIZE_POLYGONS).
Skipping classification preserves the envelope of all ring coordinates, but is
not suitable for computing polygon area or cell coverage. The setting is scoped
to the dedicated census process and must not be used concurrently with other
GDAL work in threads. No additional Rust extension or build toolchain is required.

## Pair scheduling and exact transport

Pair work streams across Arrow batches through a bounded queue, with larger WKB
payloads submitted first within each batch. `--reverse-bands` reverses the saved
sample's row-group/row order before that local scheduling; for the standard
size-band-sorted fixture this visits large bands first. It changes order only,
not sample selection or coverage policy. With a custom unsorted sample it does
not sort all rows into size bands.

Prepared tile predicates classify empty, complete and boundary-crossing tiles in
the producer. A ring-section index avoids repeated scans of polygons with many
holes. Complex overlaps are subdivided along closed windows before native fill,
then deduplicated. Complete tiles use a bounded build-local cache keyed by grid
position, tile size and resolution; worker initialization clears local cache
state and optionally shares compact tile entries through the build's manager.
The cache stores only H3 IDs and does not persist between builds.

Large resolution-7 results use lossless H3 index compaction for process transport.
The parent expands and sorts the fine cells before pair writing. Parquet uses
delta encoding for H3 IDs and dictionary encoding for species IDs; the read
schema and relationship values stay unchanged. Neither mechanism is polygon
simplification. Quoted CSV fields may contain newlines without splitting a
relationship into multiple records.

Synthetic checks compare subdivision/classification against an unsplit native
fill, verify holes/seams/polar tiles, and round-trip compact transport and delta
pair encoding. These establish output equivalence for those fixtures, not global
performance or geometric correctness for every provider range. A performance
comparison must first compare sorted `(h3_index, iucn_sis_id)` outputs, then record
hardware, dependency versions, resolution/profile, worker settings, fixture,
input/output storage, and cold/warm cache conditions. Fresh output directories
do not flush OS caches. Historical source-derived timings and reports remain local.

## Replay one stage

```bash
just data-benchmark --only-stage pairs --from-run /path/to/previous/run --workers 4
just data-benchmark --only-stage lists --from-run /path/to/previous/run --dry-run
```

Both options are required. A replay requires completed prerequisite stages and
inherits the prior source, profile, sample selection and boundary paths. Worker
and memory limits are those of the new invocation. Outputs and scratch go to a
new, separate directory under `ROOT/benchmarks/stages/`, or an explicit
`--output-root`; the old run is read in place. Consumed saved artifacts are checked
against their recorded inventory before and after execution; production readers
retain their receipt/checksum checks. Consumed directories must retain every
recorded member and contain no added files; unrelated outputs are ignored.
The profile and pair sample/metadata identities are rechecked before success is
reported. A persistent input change during execution leaves a failed replay
report and isolated outputs, preserving the previous run.
External boundary hashes must match the
original run. Changed or unrecorded inputs are rejected before output creation.
Tile replay has a 20-minute cutoff, using the same owned-group cleanup as cancellation.

The report is marked `scope: stage-replay`, records the old stage time and
resource/runtime context alongside the new measurement, and does not extrapolate
a full build or qualify as an ETA prior. Different worker settings, environments,
input cache state or kernel changes prevent interpreting the two times as a
controlled speedup. Replay itself does not establish equivalent output; compare
the generated artifacts before making a performance claim.
