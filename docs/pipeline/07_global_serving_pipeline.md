# Global serving pipeline

This guide separates the workflow on main from the optional, unmerged managed
serving/profile work. It describes code contracts, not a completed or publicly
releasable global dataset. Source access and passing validation do not grant data
redistribution rights; follow [DATA_POLICY](../../DATA_POLICY.md).

## Workflow available on main

The integration boundary is global IUCN/GoaT species metadata plus H3 lists.
Structural H3 validation can run before the final taxonomy crosswalk exists;
lossless species joins require that crosswalk and matching registered source
versions. The [pipeline guide](01_data_pipeline.md) describes the sequence.

`just spatial` or `just data-aggregate` produces compatible lists. Select the
reported `serving/current` generation with `GLOBAL_H3_ROOT`, then prepare metadata,
coarse products and fine aggregates through `just global-prepare`. `just data-prepare`
coordinates aggregation and this handoff after pair generation. Its `--dry-run`
prints paths/commands **without validating sources**. A real preparation may run
boundary acquisition; it is not a data-free verification command.

On main, `--tiles`/`just data-tiles` continues through the managed **static** PMTiles
builder, which publishes archive/metadata under `GLOBAL_PREVIEW_ROOT/tiles/current`.
It does not supply the future managed `serving.json` launcher. Main's
`just global-db-load` reads `data/global/exports`; `just global-start` uses the
configured PostgreSQL database, `data/global/tiles` coarse products,
`GLOBAL_H3_ROOT/res7_merged_parts` and `GLOBAL_PREVIEW_ROOT/res7_aggregates`.
Keep those paths from one compatible preparation and load its matching exports.
No database load, global build or server startup was run to write this guide.

The existing browser can use the coarse Arrow snapshot and dynamic fine endpoint
`/api/tiles/res7/{z}/{x}/{y}`. Static archive generation and dynamic browser delivery
are separate paths. A static fine archive is not required to prove dynamic
throughput, and its historical timings cannot seed a dynamic ETA.

## Optional managed map-serving contract

The [unmerged serving/profile PR](https://github.com/karlaugustnybo/Ark-IV/pull/11)
introduces the following managed contract. These commands/fields are **not available
on main** and are not an instruction to copy prerequisite code into a documentation
branch. Validate their assembled producers/consumers before integration.

- “Prepare map serving” retains `tiles` as its CLI compatibility name. Default
  compilation is coarse resolution 3, zooms 0–6, sparse integer metric properties,
  plus the retained Arrow descriptor. It does not compile/join fine archives.
  Earlier fine-cell appearance remains a separate interface investigation.
- Metadata retains score/normalization/boundary domains, weights and
  `coarse_snapshot`. Dynamic metadata declares `res7_delivery: dynamic-h3-v1`,
  exact base-cell coverage, and available/complete/detail resolutions. Complete
  means parity with the selected generation, not worldwide source completeness.
- Fine aggregates expose `h3_index` and canonical metrics for `all`, `terrestrial`,
  `freshwater` and `marine` as `<metric>__<system>`. The fine endpoint returns
  compact arrays in the canonical metric order. Separate numeric-H3/species-list
  partitions support cell details; rendering aggregates do not replace those lists.
- Country scope, admin1, municipality, EEZ and conservation inputs are pinned to
  the same compilation generation. Fine requests load jurisdiction indexes for
  active filters; adding a second fine index is not implied by this guide.
- The bundle links immutable files where supported; non-linkable filesystems need
  safe copies or retained immutable/content-addressed generations. Never edit
  published inodes in place or remove referenced generations. Boundaries remain
  verified references requiring retention. Missing/changed files must reject.
- Publication validates inputs, receipts and outputs before advancing `tiles/current`.
  A failed build leaves the accepted pointer unchanged. Runtime config resolves
  one generation for a server lifetime. `GLOBAL_SERVING_CONFIG` selects another
  validated bundle; `GLOBAL_DATABASE_URL` still selects the database. Load matching
  exports before serving it. Benchmark bundles are not production startup inputs.
- `just data-tiles --static-fine` is an explicit legacy alternative on that branch,
  not the managed dynamic launcher's output. `global-db-load`/`global-start` consume
  the selected managed configuration rather than assuming old loose directories.

The optional preparation gates also reject **used nonidentity mappings** between
source IDs and serving IDs, because existing fine consumers use canonical SIS-valued
aliases. Standalone coarse-source building still supports explicit one-to-one
nonidentity mappings. Do not infer a general fine remapping capability from the
coarse adapter. Preserve separate source/fine/coarse membership, scored-metric,
Arrow content and consumed-metadata identity gates when integrating other branches.
Those checks do not establish global performance or publication permission.

## Input and identity contracts

The global list schema is:

| Column | Type | Meaning |
|---|---|---|
| `h3_cell` | `UBIGINT` | Numeric H3 index |
| `species_ids` | `BIGINT[]` | Source IUCN SIS / `internalTaxonId` identities |

The standalone source adapter also accepts prototype
`(h3_index VARCHAR, gbif_accepted_ids VARCHAR[])` lists. That legacy name does not
establish that the values are external GBIF concept IDs.

For global numeric lists, supply `H3_ID_CROSSWALK_PATH`:

| Column | Meaning |
|---|---|
| `source_species_id` or `iucn_sis_id` | Source identifier, castable to text |
| `app_species_id` | Explicit serving target where supplied |
| `gbif_accepted_id` | Legacy target fallback; an application alias |
| `match_method`, `match_confidence` | Recommended identity evidence |
| `goat_taxon_id` | Recommended GoaT/NCBI link |
| `gbif_accepted_concept_key` | Exact GBIF identity only when independently available |

The crosswalk generated by `cli/crosswalk_match.py` can use `iucn_sis_id` as the
application target, with `matched_ncbi_species_taxid` as the GoaT link. Source
and target identities must be non-null and one-to-one; many-to-one targets would
collapse distinct species within cells. Preserve source versions and unresolved
identity evidence. A tree-only or name-only match does not grant genomic transfer.

## Structural validation before metadata

Example paths below are placeholders, not local execution receipts:

```bash
uv run python -m ark_pipeline.cli.spatial_validate \
  --res3 /path/to/h3_res3_species_global_merged.parquet \
  --res7 /path/to/h3_res7_species_global_merged.parquet \
  --output data/validation/global-h3.json
```

Check cell/relationship counts, null H3/list values and encoded resolution.
`--deep` additionally checks duplicate cell rows and duplicate IDs within lists;
its fine-scale cost is separate from the structural pass. A structural pass alone
is not a full duplicate audit, metadata join or source-completeness claim.

## Standalone source and serving builders

With authorized matching metadata and lists, the standalone builders can be
configured explicitly:

```bash
H3_RES3_PARQUET=/path/to/h3_res3_species_global_merged.parquet \
H3_RES7_PARQUET=/path/to/h3_res7_species_global_merged.parquet \
H3_ID_CROSSWALK_PATH=/path/to/iucn_goat_crosswalk.parquet \
uv run python ark_pipeline/builders/source_database.py --overwrite

uv run python ark_pipeline/builders/coarse_cache.py \
  --rebuild-aggregates --skip-expanded-cell-species
```

These are build commands, not smoke checks. Source validation writes its report
before replacing the source database; lossless mode rejects unmatched relationships,
duplicate mappings, missing species metadata and dropped cells. Coarse validation
compares normalized source and all-system totals after the species join. Main's
preview recipes explicitly defer the expensive lossless audit; do not describe a
preview as having passed the complete standalone audit.

Compact `cell_species_lists.parquet` avoids expanding a huge relation into the
PostgreSQL `cell_species` table. Without the skip flag the builder refuses expanded
exports above its configured safety threshold (currently 100 million relationships).
The existing API can read selected fine-cell lists from base-cell partitions;
absence of list data and a genuinely empty cell require separate handling. The
optional serving branch adds explicit availability contracts and generation checks.

## Scaling, reuse and scientific meaning

There is no need for a global centroid table or a country-border loop to normalize
every relationship. Species systems supply habitat categories; canonical IUCN IDs
can remain application keys. Fine aggregation works by base-cell partition and
bounded batches, evaluating species flags and reducing canonical metric counts.
Keep every system and threat/DNA predicate, null behavior and whole-cell chunking
when changing reducers. The [metric method](../performance/serving_metrics_performance.md)
and [optimization guide](../performance/pipeline_optimization.md) explain comparisons;
no historical measured speedup or global ETA is asserted here.

Reuse needs source-partition, species/system metadata, code and dependency identity,
not just matching row counts. Changed DNA or threat evidence must invalidate scores.
Check source/output cell and relationship totals, H3 resolution/base-cell placement,
duplicate rows and metric consistency before replacement; revalidate resumable
current-schema partitions. A reducer change may require metric receipts to rebuild
without changing geometry/pair/list identity. Preserve the optional metric-owner's
consumed-metadata pinning correction during integration.

Tile rows stream from DuckDB in batches into Tippecanoe; bounded Python memory does
not bound all compiler/native memory, scratch, CPU or I/O. Incremental C-backed H3
boundary construction still costs work. Benchmark authorized geographic/base-cell
slices and equivalent outputs before planning a full static fine export.

## Validation record to retain privately

Record source versions, exact IDs, placeholder/missing metadata reasons, structural
versus deep scope, source/list/scored totals, dependency identity and whether the
served generation was actually rebuilt. An upstream taxonomy-version mismatch
must not become a guessed GoaT join: retain a placeholder and null source-specific
metadata until authoritative evidence resolves it. Preserve compatibility-build
losslessness checks as well as global ones. This guide publishes no historical
species IDs, census tables, private timings or outcome claims.
