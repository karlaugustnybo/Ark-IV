# Exact H3 cover and tree pilot

This isolated research tool tests whether a mixed-resolution H3 cover can
preserve ARK's current fine lists, coarse lists and categorical metrics. It does
not replace production pairs, change scoring or select a serving generation.
No global throughput improvement is established by this code release.

## Exactness and identity

The producer first runs the existing resolution-7 closed-boundary coverage
kernel under the supplied spatial profile. It compacts only those accepted
leaves; a parent's geometry alone is never a proof of child coverage. Expanding
each cover must recover the exact reference set. Complete ordinary and pentagon
children, incomplete children, boundary touches, antimeridian and polar cases
are covered by synthetic tests.

Each record carries `kind` (`subtree` or `leaf`), numeric `h3_index`, resolution,
a stable species ordinal, a source-row provenance key and the proof label
`exact-leaf-compaction`. The species dictionary and its source fingerprint
travel with the isolated generation. The proof label is a producer assertion,
not independent evidence: use the validated producer and checksum receipts.
Duplicate components and source records union by species before counting.

The tree traverses one H3 base-cell partition from ancestors to resolution 7,
inheriting species sets and pruning empty branches. It writes numeric fine
lists, resolution-3 unions and all fields in the existing metric schema. Coarse
membership is a species union, not a sum of fine counts. Rows and species IDs
are canonical, and the existing SQL predicates supply metric flags.

## Entry points

Use only synthetic or separately authorized fixtures and keep generated
products and receipts private. A fixture Parquet has `sample_id`,
`logical_name`, `source_sha256`, `source_layer`, `source_row`, `iucn_sis_id`,
`presence`, `origin`, `seasonal`, and `geometry_wkb`; the single-row pilot also
expects `size_bin`. Synthetic IDs such as 101 and 202 are labels, not real species.

```bash
# Exact cover and reference pairs for one sample/base; fresh output required.
uv run python -m ark_pipeline.cli.h3_cover_pilot \
  --fixture /path/to/authorized-fixture.parquet --sample-id 1 \
  --output /path/to/private/single-cover

# Chunked producer for a bounded fixture; matching receipts can be reused.
uv run python -m ark_pipeline.cli.h3_fixture_partitions \
  --fixture /path/to/authorized-fixture.parquet \
  --output /path/to/private/cover-inputs --chunk-size 2

# Compare every fine/coarse list row and every metric field against pair reducers.
uv run python -m ark_pipeline.cli.h3_fixture_validate \
  --source /path/to/private/cover-inputs \
  --species /path/to/authorized-species.parquet \
  --species-systems /path/to/authorized-species-systems.parquet \
  --output /path/to/private/cover-validation
```

Supply `--profile` to each producer when testing a non-default profile. Metadata
must cover the canonical species-ID universe. `h3_tree_pilot` combines separate
single-cover outputs for one base, supports a forced duplicate provenance
record, and accepts `--first baseline` or `--first candidate` for run-order
comparisons. Use fresh outputs for each single-base comparison.

The fixture validator checks source/dictionary hashes, chunk totals and output
hashes before receipt reuse. An incomplete chunk/base requires manual review;
stale or damaged receipts are refused. A selected-base run is partial. Only a
complete fixture comparison writes `pilot_generation.json`; this marker never
changes serving `current`. A failed fresh attempt does not write a marker; existing validated outputs are
not overwritten when an input mismatch is refused.

## Resource and measurement limits

This is a bounded-fixture pilot, not an out-of-core global producer. Fixture
rows and one geometry's accepted cells are materialized. The tree rejects cover
partitions above `max_cover_records` (default one million), buffers metrics by
`batch_cells` (default 16,384), and flushes list writes by
`write_relationships` (default one million). The latter is a flush target: one
batch or exceptionally rich cell can exceed it. Species dictionaries, inherited
sets, coarse unions and caches also consume memory. DuckDB's limit does not cap
Arrow, NumPy or Python allocations. Single-cover and combined-cover commands
materialize their inputs. Do not interpret the settings as a total RSS cap.

Reports retain input/output checksums, code and metadata identity, runtime,
worker count, route order, elapsed timings and sampled RSS/scratch use. Compare
all outputs before interpreting timings. A performance experiment additionally
needs machine/CPU details, resolutions, fixture scope, cold/warm state, sustained
load and both run orders. Producer timings include writing both representations;
they are not a paired end-to-end comparison with one path disabled. No historical
restricted-input counts, timings or receipts are included here.

Run the small reproducible checks with:

```bash
uv run python -m unittest discover -s tests -p 'test_h3*.py'
```

The [research plan](h3_hierarchy_build_plan.md) records future acceptance gates.
Production migration, adaptive routing, compact serving/API integration and
full global validation remain separate decisions.
