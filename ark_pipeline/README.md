# Pipeline code map

Use the stable `just` commands from the repository root. Direct Python commands
use `uv run python -m ark_pipeline.cli.<module>` or
`uv run python -m ark_pipeline.builders.<module>`.

Modules use descriptive names, not execution-order numbers: stages can be
resumed independently and some have multiple callers. The sequence below is
the normal full-build order; implementation files are grouped by responsibility.

## 01 — Acquire sources

In `cli/`, `sources_sync` coordinates download/update and authorized manual
registration; `sources_acquire` manages manifests, validation and snapshots.
`sources_download_adm2`, `sources_download_iucn`, and
`sources_download_goat.mjs` are provider-specific download helpers.

`sources_fetch_iucn_details` retrieves queued exact-ID evidence into private
outputs; retrieval never merges taxonomic identities.

Commands: `just download`, `just update`, `just data-status`.

## 02 — Resolve taxonomy

`cli/crosswalk_refresh.py` selects validated snapshots and publishes a reusable
crosswalk. `cli/crosswalk_match.py` implements the IUCN–GoaT matching rules.

These run automatically during `just data-build` and `just data-update`.

## 03 — Cover spatial sources and aggregate species

| Module | Responsibility |
| --- | --- |
| `cli/spatial_pairs.py` | Stream ranges/points/basin tables, reuse referenced basin coverage, and generate common H3 pairs |
| `cli/spatial_aggregate.py` | Resume direct pair-to-species-list aggregation |
| `cli/spatial_audit.py` | Audit the selected spatial row policy |
| `cli/spatial_validate.py` | Validate H3 input contracts |
| `spatial/coverage.py` | Production range/HydroBASINS polygon-to-H3 coverage kernel and profiles |
| `aggregation/pairs.py` | Streaming sorted-pair deduplication and reduction |
| `aggregation/species_lists.py` | Validated, resumable species-list publication |

Commands: `just spatial`, `just data-aggregate`, `just validate-h3`.

## 04 — Build serving artifacts

`cli/serving_prepare.py` orchestrates preparation; `cli/serving_metadata.py`
resolves registered metadata inputs. `cli/boundaries_prepare.py` installs ADM2
catalogues. The builders are named after their outputs:

| Builder | Output |
| --- | --- |
| `species_metadata.py` | Normalized species and ecosystem metadata |
| `spatial_metadata.py` | Exact-ID labels and affirmative systems from registered spatial rows |
| `iucn_metadata.py` | Release-pinned assessment enrichment and private reviewed ecosystem evidence |
| `source_database.py` | Normalized DuckDB source database |
| `coarse_cache.py` | Coarse-map snapshot, exports and score domains |
| `fine_metrics.py` | Resolution-7 aggregate partitions and compatibility exports |
| `administrative_boundaries.py` | Natural Earth administrative catalogues |
| `boundary_frameworks.py` | Additional boundary-framework assets |

`aggregation/metrics.py` provides native metric reduction. `spatial/boundaries.py`
provides jurisdiction membership; `spatial/paths.py` resolves boundary assets.

`cli/iucn_reconcile_stage.py` shares automatic/cache-only/off intake and
reconciliation; `cli/reconcile_species_metadata.py` repairs a separate species
dimension without rebuilding spatial membership or serving outputs.

Commands: `just data-prepare`, `just data-boundaries`, `just global-prepare`.

Optional headless enrichment uses `cli/spatial_analytics.py` for versioned range
counts/areas and requested stewardship/rarity outputs. Repeated
`--boundary-catalogue framework=GeoJSON` produces jurisdiction memberships from
mapped cells using the existing tile boundary policy; the main preparation
command forwards `--spatial-boundary-catalogue`.
`cli/phylogenetic_prepare.py` publishes receipt-checked mammal evidence, ED/EDGE and
default DNA gaps. `cli/analytical_prepare.py` publishes separate coarse/fine
opportunity presets from retained memberships. `serving_prepare` exposes these
through `--spatial-analytics`, checked `--phylogenetic` inputs and the optional
`--phylogenetic-aggregates` stage; legacy pinned input directories also work.
`cli/dna_prepare.py` and `--dna-evidence` add independently checked species and
lineage evidence without a tree dependency.
Their absence preserves the existing categorical build. No enrichment stage
fills polygons or adds base map fields. See [implementation and commands](../docs/pipeline/11_scoring_implementation.md).

`aggregation/analytical_scores.py` reduces separate continuous scenarios from
distinct fine/coarse species memberships. `dna_evidence.py` shares versioned
evidence rules and rank-verified lineage representation independently of trees;
`phylogenetic_scenarios.py` holds compact tree passes and a bounded scenario LRU.
`robustness.py` compares common-cohort ranks and top-1/5/10% overlaps with
deterministic ties; `cli/scoring_compare.py` writes checksum-pinned offline
reports for species ranks or cell hotspots. These analytical quantities remain
scientifically separate.

## 05 — Export tiles

`cli/serving_tiles.py` records prepared inputs and publishes validated tile
generations. `tiles.py` encodes bounded dense/sparse feature batches,
`tile_spatial.py` shares exact boundary proofs, and `tile_stream_worker.py` with
`tile_worker_state.py` isolates ordered binary producers before native compilation.
See [static tile export](../docs/performance/tile_export_performance.md) for encoding,
compiler grouping and recovery contracts.

Command: `just data-tiles` (also included in `just data-build`).

## 06 — Benchmark and monitor

`cli/benchmark_sample.py` builds the stratified fixture;
`cli/benchmark_pipeline.py` runs it through the production stages.

`runtime/` contains `resources.py`, `progress.py`, `dashboard.py`,
`benchmark_estimates.py`, `forecasts.py`, `provenance.py`, and `checkpoints.py`.
It owns execution support, not data transformations.

Command: `just data-benchmark`.

For source requirements and configuration, see the
[ordered pipeline guides](../docs/README.md). Historical tools live in ignored
`archive/` and are not imported by this package. Renames change code fingerprints;
let the normal receipt checks decide which existing outputs need rebuilding.

`cli/map_serving.py` publishes and validates coherent coarse/dynamic-fine bundles,
selects startup inputs, and loads their recorded SQL exports.
`cli/species_passage.py` builds the optional coarse species-range supplement.
See [coverage profiles](../docs/pipeline/coverage_profiles.md) for request routing,
operator responsibilities and validation limits.

## Optional dated-tree investigation

`edge.py` wraps the pinned reference metric engine with reviewed identifier
joins, crown extraction, risk sampling and mammal-reference comparison.
`cli/edge_prototype.py` validates public inputs and authorized assessments,
then writes local descriptive scores and reports. It is independent of the
required update and serving pipeline.

`edge_matching.py` audits conservative exact-name/lineage bridges;
`edge_trees.py` streams reproducible crown pilots from official archives;
`edge_validation.py` and `cli/edge_validate.py` compare reference disagreements
and rank sensitivity using common risk draws. Outputs retain prototype status.
`edge_benchmark.py` independently enumerates shared branches and checks
matched-input calculator agreement. `cli/edge_benchmark.py` runs the unmodified
authors' R calculator and compares 100 Upham mammal trees with Duke medians
using an identical species cohort and threat vector. Original-author source
and results remain in ignored local data.
`edge_links.py` and `cli/edge_link.py` index the unchanged global release and
link every reviewed assessment through NCBI/OTT identity or exact-name/current
lineage checks. Ambiguities remain unresolved; linkage does not compute scores
or change the tree. The optional `phylogenetic_prepare --scope global` path uses
these checked identities for complete-tree descriptive scoring;
`--phylogenetic-scope global` forwards it from the main preparation entry point.
`compact_phylogeny.py` parses the released tree into arrays and computes
descriptive ED/EDGE components using the pinned risk model. The original
mammal path stays available for comparison. See the
[headless implementation guide](../docs/pipeline/11_scoring_implementation.md).
`edge_link_extension.py` and `cli/edge_link_expand.py` add independent exact
IUCN/OTT identities using current IUCN taxonomy and compatible lineage/clade
membership. Original links and genomic-transfer flags remain unchanged.
Additional tree identity does not imply a verified NCBI/GoaT genomic join.
`edge_synonyms.py` and `cli/edge_link_synonyms.py` check documented binomial
name changes using the released OTT alias table and registered NCBI names.
Unchanged epithets, compatible higher lineage, unique species identity and
preservation of previous links are required; species-concept disagreements
remain in a separate review queue. Names missing from the dated tree are
checked against the full OTT taxonomy to distinguish different source gaps.
`phylogenetic_gap.py` and `cli/phylogenetic_gap.py` compare rooted marginal
branch coverage with the existing genus/family score. The mammal prototype
uses the exact website DNA rule, a fresh reviewed GoaT crosswalk, and all
uniquely linked DNA-positive mammal records as its baseline, including those
without IUCN assessments. Missing baseline identities are audited; same-genus
gaps with missing positive records have their priorities withheld. Batch
selection recomputes uncovered branches after every choice. This custom Ark
priority is separate from EDGE/EDGE2 and remains outside production serving.
`edge_baseline_review.py` and `cli/edge_baseline_review.py` audit missing
DNA-positive mammal identities using current NCBI names/lineage and full OTT
names/aliases. Unique documented species leaves can be recovered; merged IDs
with multiple leaves, missing taxa, nonspecies ranks, hybrids and conflicting
identities remain audited. The gap pilot applies a cached review only when
source, code and output receipts match its freshly checked evidence.
`extinction_status.py` centralizes IUCN authority over conflicting OTT extinction
flags for verified assessed species. EDGE scoring, baseline review and the gap
pilot record effective classification, source and overrides while preserving
raw flags; missing dated branches remain unscored coverage gaps.

Commands: `just edge-download`, `just edge-prototype`,
`just edge-tree-download`, `just edge-validate`, `just edge-benchmark-setup`,
`just edge-benchmark`, `just edge-link`, `just edge-link-expand`,
`just edge-link-synonyms`, `just phylogenetic-gap <validation-receipt>`, `just edge-baseline-review`.
After reviewing, rerun `just phylogenetic-gap <validation-receipt>` to update gaps and priorities. See the
[prototype guide](../docs/pipeline/09_edge_prototype.md).
