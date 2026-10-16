# Where another large pipeline speedup could come from

These are proposed experiments, not measured global performance or an
implementation specification. Historical local replays, input counts, projections,
source IDs and receipts are not public benchmark evidence. No global build or
new performance experiment was run for this document. First establish a matched,
authorized baseline using the [benchmark workflow](../pipeline/08_pipeline_benchmark.md).

The dependency matters more than an old ETA: eliminating downstream work cannot
remove the coverage kernel's cost. Set a target only after calibrating the current
source population, selection policy and hardware. Separate acquisition, pairs,
lists, fine metrics, coarse cache, export and database-load costs.

## Transformative design: keep exact H3 interiors compact

The large software hypothesis is to avoid treating every polygon–resolution-7-cell
relationship as a separate intermediate row. A polygon could produce an **exact**
compact cover: ancestors representing complete subtrees, fine leaves around
boundaries. Partition by H3 base cell, map species IDs to compact ordinals and
aggregate a tree per base cell. Inherit species memberships and metric vectors
while descending, applying local boundary changes. Investigate whether coarse
and fine metrics can share a traversal without global pair sorting and rereads.

If serving stores inherited species sets plus sparse child changes, reconstruct
fine lists on demand. If current per-cell lists remain required, expansion still
costs time. Preserve the direct path for mostly boundary coverage, where compaction
may provide little benefit. Do not turn a representation experiment into a
scientific membership change.

Start by compacting already computed exact fine sets. Expand and compare the
complete per-species pair **multiset**, lists and every metric column. For a new
geometric shortcut, native closed-touch coverage remains the authority: H3
hierarchy alone does not prove geometric containment of child footprints. Include
edge/vertex, holes, antimeridian and sparse/dense cases. See the optional
[hierarchy pilot plan](https://github.com/karlaugustnybo/Ark-IV/pull/6); that isolated prototype is not
an adopted production representation or demonstrated global speedup.

## Transformative deployment: sustained compute capacity

More sustained CPU capacity, memory and fast scratch may reduce elapsed time
without changing output. Polygon jobs and later base-cell partitions offer
possible sharding boundaries. Benchmark one representative slice on the target
host before moving a build. Record physical cores versus logical CPUs, CPU
architecture, memory pressure, storage throughput, thermal behavior, worker and
native-thread settings. Ideal core scaling is a capacity scenario, not an ETA;
writing, synchronization and downstream stages do not disappear. Use an explicit
planning model such as `worker_time × old_physical_cores / new_physical_cores +
nonparallel_work`; this is an assumption to test, not a throughput claim. Keep
physical cores distinct from advertised vCPUs, and check whether scratch is local
or network/block storage when comparing host classes.

For repeated builds, investigate content-addressed caching of exact polygon covers
and completed base-cell outputs. Rebuild only changed sources and affected cells,
with source/profile/code/dependency receipts. This could help updates without
shortening the first full build. Cache identity, invalidation and source mutation
need tests; source access or a plausible cost model does not authorize a transfer
or provider job.

## Reduce native boundary-fill and simplification work

Profile representative large size bands and several fallback rows before changing
the kernel. Distinguish native fill calls, geometry preparation, simplification
attempts, full-tile cache misses, deduplication and writing. Cumulative profile
times can overlap and cannot be added as independent stage costs.

Batched WKB filling of existing subdivision leaves is a hypothesis worth testing
with controlled worker/native-thread combinations. A serial kernel gain can reverse
under process/thread competition. Compare paired size-band timings and exact pair
multisets in alternating baseline/candidate runs, then a complete stage replay.
Candidate tile widths and deeper subdivision also need band-wide evidence rather
than a single favorable polygon.

Rejected simplification attempts may be expensive. A replacement must preserve
accepted geometry, validity/component-count audit records, topology-preserving
fallback and closed-touch membership. Skipping an attempt changes that contract.
Component-wise simplification needs output evidence even when validity and component
counts agree; equal counts do not prove equal geometry or covered cells.

## Fuse list grouping with metric generation

Lists are grouped/sorted, written, then read again for fine metrics. Investigate
counting metrics in the sorted stream to avoid an extra relationship scan and
species-ID lookup. Counting and output writing remain. The saving is bounded by
the work actually removed, not the whole build.

This may require moving species metadata earlier. Establish that IDs discovered
from finalized pair inputs equal those discovered from finished coarse lists for
**every source type**, after exact reconciliation. Do not guess fallback metadata
or transfer genomic evidence. Preserve partition receipts, publication validation,
whole-cell chunk boundaries, ecosystem missingness and every canonical metric
predicate. A coarse pass could feed coarse metrics too, but profile its SQL first.
This fusion can retain current serving lists and all metric columns; it is distinct
from replacing the serving representation.

## Profile list sorting and coarse SQL before redesigning them

Separate base-cell partitioning from sorting/grouping. Exact duplicates may be
rare, but grouping still requires ordering or an equivalent index. Test sorted
per-polygon runs or finer partitioning for spill/hash reduction; record memory,
scratch, spill and output equality. Avoid extrapolating a small sparse fixture to
a dense global population.

Profile coarse list expansion, joins and filtered counts before replacing SQL
with a categorical reducer. Compare **all** systems/columns with the canonical
query, including nulls and bookkeeping categories. Fine/coarse work might overlap
after lists, but shared memory, disk and thermal pressure can erase wall-time gains.

Species-ID lookup may matter at higher density. Avoid direct arrays sized by the
largest sparse ID without measuring per-worker memory; investigate compact mappings
while preserving unknown-ID handling. Separate lookup, reduction and Parquet costs.

## Calibration and shortcuts that still need evidence

Rebuild stratified samples after source-policy changes. Include forced extremes,
representative size bands and higher relationship density; document population
weights, source versions, uncertainty and missing measurements. Alternate heavy
comparisons serially on a thermally constrained host and inspect worker utilization
before blaming scheduling. A scenario span is neither a confidence interval nor
a finite bound on global completion time.

Measure exact WKB duplication and partial-tile reuse before adding caches. Distinguish
unique geometry counts, saved fill calls and time-weighted benefit. A low-reuse
sample does not prove global absence of reuse; a cold-cache microbenchmark does
not establish concurrent hit rate, cache capacity or full-build benefit.
