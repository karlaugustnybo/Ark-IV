# Pipeline optimization: evaluation method and experiment limits

This guide retains reusable questions from earlier local optimization work.
Historical run IDs, source-derived counts, timings, private reports and projected
global outcomes remain private. No historical target is represented as achieved,
and no benchmark was rerun for this guide. These are proposals and acceptance
methods; consult current builders and the [benchmark guide](../pipeline/08_pipeline_benchmark.md)
for runnable entry points. Historical probes are not required application tools.

## Compare scientific outputs before timing

- Compare per-species pair multisets, **including multiplicity**, before reduction;
  then compare exact deduplicated lists and all coarse/fine metric columns across
  every output partition. Aggregate totals alone can conceal substitutions.
- Compare canonical SQL predicates against any vectorized/categorical reducer.
  Include all ecosystem systems, threat/DNA joint categories, null/missing states,
  bookkeeping categories, unknown IDs, batch/chunk boundaries and whole-cell behavior.
- Retain source/profile/code/dependency identity, output receipts and validation.
  A successful test suite or count match does not establish source completeness,
  scientific calibration or permission to publish the outputs.
- Keep temporary output separate from the last valid generation. Exercise failure,
  interruption, resume, stale receipts and changed metadata; a candidate must not
  replace a valid output before its validation succeeds.

## Matched performance comparisons

Record hardware, physical/logical CPUs, workers/native threads, memory/scratch,
source and fixture policy, resolution, sampling weights, cold/warm cache and stage
boundaries. Alternate baseline/candidate order (for example ABBA); inspect per-band
results and repeat promising full-stage comparisons. Sustained thermal throttling,
storage and concurrent workloads prevent unmatched run times from isolating a
code change. Fewer workers can be tested, rather than presumed faster or slower.

Keep kernel, stage replay and end-to-end claims separate. A serial WKB/native-fill
improvement may lose under multiprocessing/native-thread contention. Reusing
unchanged stage timings produces a planning model, not an end-to-end measurement.
Exclude acquisition/database loading only when that scope is explicit. Scenario
ranges are sensitivity analyses, not confidence intervals or guarantees.

## Coverage kernel experiments

Adaptive subdivision, hole-boundary indexing, compact full-tile coverage, validation
placement and sorted-H3 transport/encoding are distinct mechanisms. Profile each
without changing the spatial policy. Continuous bounded scheduling and descending
size-band order need utilization and full-stage evidence, not assumed speedups.

Test larger/smaller candidate tiles and deeper subdivision across size bands.
For example, a controlled depth-four/depth-six comparison can retain the same
coordinate threshold and minimum span, while a tile-width comparison changes
only that width. These are experimental controls, not recommended new defaults;
record their actual values with the baseline.
Include
including median and upper-quartile difficult rows and forced extremes. Shared
preparation and cleared caches must be recorded. Small-band percentage differences
can correspond to negligible absolute work. Ordinary fixtures alone cannot prove
closed-touch equivalence: tiny rectangles anchored at exact H3 vertices/edges,
holes, coastlines and antimeridian cases must agree with the native authority.
An approximate hybrid outline must not be adopted merely because ordinary rows agree.

Compact native output immediately expanded to fine cells may save no work. Test it
separately from carrying a mixed-resolution representation downstream. Direct WKB
and batched WKB filling must preserve the same subdivision and predicate, and then
be checked under actual worker/thread settings before integration.

## Simplification experiments

Ring-interaction grouping can be evaluated against exact accepted geometry, followed
by pair multiset equality and stage timing. Restricting a candidate by bounding-box
area or ring count requires representative band coverage (including either side
of the proposed cutoffs); overhead may dominate
smaller geometries. Retain invalid-candidate rejection and original-geometry fallback.
A successful simplification-only experiment is not a production speedup.

Profile all attempts before fallback, including rejected non-topological attempts.
Cumulative preparation, decision, native-fill and cache timings overlap; do not sum
them. Component-wise simplification may preserve validity/component counts while
changing coordinates. A geometry mismatch does not alone prove different H3 cells,
but absent speed evidence there is no reason to weaken output checks.

## Metric and sort experiments

Evaluate larger cell batches independently of relationship-chunk limits. Compare
small boundary fixtures and every output metric, then record writing, ID lookup
and category reduction costs separately. Packed Boolean reduction may improve a
synthetic kernel while increasing lookup memory and losing at full-stage scale;
keep that hypothesis separate from cell batching and categorical counting.

Profile list partitioning and sorting, duplicates, spill and grouping before adding
an alternative sorter. Coarse SQL and fine metric work have different density and
join behavior. Exact categorical arithmetic in one path does not prove another
path's join/null/filter semantics. See [remaining performance hypotheses](next_large_gains.md).

## Cache experiments

Test exact WKB reuse and normalized partial-leaf reuse with both frequency and
saved work. Retain source identity and memory limits; sample reuse rates are not
population estimates without a defensible sample design.

Separate process-local LRU, multiprocessing shared dictionary, cold generation and
warm replay. Compare arrays exactly. Clearing only one cache does not make a run
cold. A shared-cache microbenchmark does not measure concurrent misses, capacity,
eviction, full-build hit rate or the cost of workers racing to populate it.

## Decision record for future experiments

For each candidate retain the hypothesis, exact baseline/candidate code and inputs,
output equality checks, timing scope, failures, uncertainty, decision and reason.
Do not resurrect retired probes merely because their idea remains useful. Rejected
or inconclusive historical experiments motivate these checks; they do not establish
that a new candidate is wrong, that the old targets are met, or that global throughput
has been measured. Require current evidence before adopting a change.
