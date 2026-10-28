# Spatial analytics optimization — 27 October 2026

The benchmark now executes the production spatial-analytics path: mapped-cell
jurisdiction intersections, cell areas, species range denominators and
stewardship aggregation. Iterations use `--only-stage spatial_analytics` against
saved inputs, with a separate output and scratch directory for every trial.
No pair generation, list aggregation, metadata, fine metrics or tiles are rerun.

## Matched CoreWeave evidence

Measurements use one isolated requested 8-CPU/32-GiB sandbox, an 8-GB DuckDB
limit, four SQL threads and the same pinned membership inputs and admin0/EEZ
catalogues. The candidate uses up to eight spatial-intersection workers;
intersection workers finish before SQL reduction. Python is 3.13.18, DuckDB
1.5.3, PyArrow 24.0.0, H3 4.4.2, h3ronpy 0.22.0 and Shapely 2.1.2.
Trials run sequentially; OS caches are not cleared. These are stage wall times,
not complete pipeline runtimes.

The pilot contains 131,072 original cells and 89,170,532 memberships. Its original
38.12-second stage spent 29.96 seconds in stewardship aggregation. This located
the reduction bottleneck before changes to execution.

The larger fixture retains four complete original base-cell parts: 3,246,473
cells and 1,624,355,551 memberships. Their recorded checksums agree with the
latest FAST generation. The measured comparison is:

| Stage/phase | Original | Optimized |
| --- | ---: | ---: |
| Complete spatial-analytics stage | 182.50 s | 47.40 s |
| Jurisdiction intersections | 107.04 s | 9.22 s |
| Canonical uniqueness proof | 7.39 s | 7.34 s |
| Cell areas | 6.86 s | 6.82 s |
| Global species range denominators | 7.00 s | 4.24 s |
| Stewardship aggregation | 53.55 s | 19.10 s |
| Sampled peak process-tree RSS | 1.69 GB | 2.78 GB |
| Sampled peak stage scratch | 0 | 0 |

This is **3.85×** for the measured stage, with unchanged resource allocation.
The higher candidate RSS includes its bounded geometry worker processes; process
RSS and container memory are different measurements. Five-second scratch samples
cannot rule out shorter-lived spills. The earlier dense pilot did spill: its
original sampled scratch high-water mark was 3.96 GB. A serial candidate reduced
that pilot to 6.02 seconds with no sampled spill, showing that density and query
plan matter in addition to total memberships.

## Execution changes

- Attach areas and jurisdiction tags at cell granularity before expanding
  canonical species lists. Reduce species/jurisdiction memberships before
  joining global species range metadata.
- Keep narrow cell dimensions on the hash-build side. The first rewritten
  query passed the pilot but exhausted its 8-GB DuckDB limit on the larger
  fixture. Scoped join-order/build-side controls resolved that failure without
  raising the memory limit. The caller's previous settings are restored on
  success and failure. These controls follow the
  [DuckDB 1.5 join-order interface](https://duckdb.org/docs/1.5/guides/performance/join_operations).
- Construct ordinary cell polygons in native geometry batches using the same
  H3 C boundary coordinates, avoiding the GeoJSON conversion round trip. Cells
  crossing the dateline or winding around a pole keep the existing shared
  clipping path.
- Use a bounded process pool for independent intersection batches. Each worker
  owns its boundary indexes; the coordinator writes batches in input order.
  The production launcher forwards the existing spatial worker budget.

Canonical membership validation remains in place. Arbitrary/overlapping inputs
still deduplicate. No range definition, geometry approximation, missing-evidence
rule, jurisdiction overlap policy or denominator is changed.

## Output checks

On the larger fixture, all 4,125,538 jurisdiction memberships, 20,771 species-range
records and 82,287 stewardship records match in schema and all non-floating
columns, including multiplicity, identifiers, counts and completeness metadata.
Cell fractions match exactly. Maximum relative differences in area calculations
are below 1.5e-14, within the 1e-12 comparison tolerance for changed summation order.
These checks compare full outputs, not a sample of rows.

## Full retained-input validation

The final candidate also completed a spatial-only replay over the original
96,631,588 cells and 30,954,332,879 memberships. The original membership receipt,
boundary hashes and basin-omission audit were verified before the run. Inputs
were restored from the immutable retained packs; source acquisition, coverage,
list building, scoring and tiles were not rerun.

The full spatial stage took **1,009.64 seconds (16 min 50 s)**:

| Phase | Wall seconds |
| --- | ---: |
| Verify membership publication | 4.94 |
| Jurisdiction intersections | 111.85 |
| Canonical uniqueness proof | 141.69 |
| Cell areas | 184.03 |
| Global species range denominators | 81.73 |
| Stewardship aggregation and connection cleanup | 484.77 |
| Validate and publish | 0.40 |

Peak sampled stage scratch was **108.06 GB** and peak process-tree RSS was
**10.45 GB**. Spilling remains at full scale; the smaller fixture's zero sampled
spill must not be extrapolated globally. The historical spatial-stage window
was approximately 11,074.8 seconds, making this about 11× shorter. That historical
comparison spans different allocations and reconstructed timing boundaries;
the 3.85× larger-fixture result above is the controlled same-worker comparison.

The complete historical and new outputs match in schema and all non-floating
columns: 92,966,356 jurisdiction memberships, 115,408 range records, 1,047,624
stewardship records and 26 omission records. Cell fractions match exactly;
floating area sums/fractions pass the 1e-12 relative comparison tolerance.
Input completeness remains **partial** because the retained basin omissions
remain unchanged. This validates computational equivalence over the frozen FAST
memberships, not correct-mode coverage or a new scientific adoption.

The retained guide keeps its historical timings intact and records the new
spatial result separately as `spatial_calibration`. Both the benchmark full-run
ETA and cloud monitor prefer it only for matching coverage mode and producer
code. The monitor also uses the original-work pair/list/fine priors rather than
the continuation's receipt-reuse times. A precise-mode fallback does not adopt
these FAST-only calibrations. Hardware, resource, cache and input changes can
still invalidate the timing prior.

Focused cloud tests cover exact ordinary/dateline/polar/pentagon cell geometry,
parallel versus serial boundary results, overlapping source inputs and boundary
catalogues, omission flags, optional scores, receipt reuse/corruption, optimizer
setting restoration, stage replay without polygon discovery, and phase timing
after failure. Two fixture tile-export tests require Tippecanoe and one recipe
rendering test requires Just; these three checks are skipped in this isolated
worker. Targeted cloud tests and Ruff checks passed.

The owned CoreWeave worker was stopped after results were recovered and verified.
Its four-hour maximum compute reservation was $6.81, within the approved $20
cap. The private local recovery archive preserves output Parquet files, timing
records, comparisons, source snapshots and input provenance; active serving data
was not changed.

## Limits

The historical observation of more than 2 TB described filesystem usage alongside
large DuckDB temporary files. It is not an isolated stage spill measurement.
New trials record their own scratch directories separately from filesystem free
space and cgroup/process memory. Smaller fixtures do not establish global spill
behavior or an optimal hardware configuration. Correct-mode coverage and a fresh
end-to-end pipeline build are outside these stage-only trials.
