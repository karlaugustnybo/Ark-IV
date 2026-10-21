# Optional headless scoring and spatial evidence

The implementation preserves the current categorical score and base map schema.
Mammal or complete-tree enrichment is explicit; new default priorities and exact spatial policy
acceptance remain scientific decisions. Check the [progress log](10_implementation_progress.md)
for measurements and the [plan](10_phylogenetic_scoring_plan.md) for remaining gates.

## Retained membership support

Direct archive-to-list aggregation retains source route from each verified
archive receipt before deduplication. It writes separate keyed Parquet under
`serving/generations/ID/source_support/`: polygon = 1, point = 2, HydroBASINS = 4,
unknown = 8. Duplicate fine and coarse species pairs combine support with OR.
The existing `h3_cell, species_ids` list schema is unchanged. Legacy finalized
relations cannot reconstruct discarded route information; they remain usable
without a support sidecar. Reaggregate from verified archive pairs to retain it.

Completed partition receipts cover lists and support together. Publication moves
`current` only after the complete generation reconciles. The new reduction
identity invalidates older reduction receipts; it never relaxes upstream checks
or edits source receipts. The spatial code fingerprint no longer includes the
unrelated complete acquisition catalogue, so future tree-source pin changes do
not invalidate geometry work. Existing legacy code fingerprints still undergo
normal invalidation; there is no synthetic migration of their receipts.

## Range/stewardship outputs

```sh
uv run python -m ark_pipeline.cli.spatial_analytics \
  --h3-root /private/spatial/serving/current \
  --output-root /private/analysis/spatial --profile SELECTED_PROFILE \
  --coverage-status partial --scratch-dir /private/scratch/analysis
```

This verifies and pins the membership generation and publishes global distinct
res7 counts and summed H3-cell areas per mapped species. Supply `--jurisdictions`
with res7 `h3_index, framework, code` membership to produce stewardship fractions.
Alternatively repeat `--boundary-catalogue admin0=/private/country-scope.geojson`
and `--boundary-catalogue eez=/private/eez.geojson`. The producer streams unique
mapped cells without expanding species lists and reuses the tile boundary and
antimeridian/polar policy. Its input boundary hashes and generated memberships
are included in the analytical receipt. Choose one input mode; duplicate
framework names are rejected.
Fractions describe touched mapped cells, may overlap between jurisdictions and
are not exact range/boundary intersection or occupancy estimates. Completeness
is explicit; absence of a range row is not a measured zero distribution.
Supply `--omissions` with the retained `missing_basin_relationships.parquet`
audit to add per-species missing-basin counts/flags. A separate omitted-species
artifact also retains affected species with no mapped cells. Without that audit,
the flags stay unknown. Jurisdiction inputs must contain nonnull res7 cells.

An optional `--weights` Parquet (`iucn_sis_id, weight`) creates separate rarity
artifacts. `sum(weight/N)` conserves one global weight budget per species.
Its coarse result sums fine contributions. Continuous DNA-gap aggregation instead
counts each distinct coarse species once. Both retain null/unscored coverage;
neither changes the base integer metrics.

## Mammal enrichment

The optional `phylogenetic` acquisition profile registers the same pinned dated
tree and OTT inputs as the existing investigation. Registration/download is an
explicit source operation; enrichment does not fetch missing inputs.

```sh
uv run --group edge python -m ark_pipeline.cli.phylogenetic_prepare \
  --root /private/registered-data --crosswalk /private/crosswalk/current/iucn_goat_crosswalk.parquet \
  --input-dir /private/pinned-tree-inputs --links-dir /private/checked-tree-links \
  --tree-tips /private/checked-global-tip-index.parquet \
  --baseline-review /private/review/report.json \
  --output-root /private/analysis/phylogenetic
```

Alternatively omit `--input-dir` to resolve registered tree/OTT sources and
supply `--ott-index` with its checked companion receipt. Overrides relocate
inputs while preserving their original content hashes. No receipt is rewritten.
Baseline review is explicit in this headless entry point; it does not silently
discover a workstation's cached review.

The complete mammalian crown remains in compact arrays for marginal rooted PD,
including unassessed relatives; the stem is excluded and lengths are in Myr.
ED/EDGE uses the existing extinction policy and risk-only uncertainty engine
on a tree copy. `ed_myr` and EDGE components share its extant-crown definition;
gap calculations retain the complete accepted gap crown. Unavailable identities
remain explicit. Tree-only identity does not grant genomic transfer permission.

The three rules are exact website compatibility, any recorded assembly and a
labelled taxon-level assembly/BUSCO proxy. The proxy cannot certify one assembly
or EBP compliance. Resampling evidence does not erase assemblies. Missing
evidence has a separate state. Baseline warnings and genus-level withholding
are recomputed for every rule using all retained relative evidence. Candidate
filters and threat weights do not shrink the represented baseline.

Generation receipts cover evidence, identities, compact topology, scores,
default gaps and a flat SQL detail export. Inputs are rechecked before atomic
publication. Changed sources/code build another generation; existing categorical
builds require none of these optional inputs. Serving rejects incompatible
calculation code and corrupt outputs rather than substituting a stale default.

## Detail/API configuration

### Complete released-tree scope

`phylogenetic_prepare --scope global` extends the checked input path to all
acquired clades. The main preparation entry point forwards
`--phylogenetic-scope global`. The default remains `mammals` for compatibility;
the expanded full-run candidate selects `global` explicitly.

The complete release is parsed directly into preorder arrays. Its root is
`cellular_organisms_ott93302`, incoming stem excluded, branch units Myr. This is
one globally rooted calculation, rather than a sum of separate stem-free class
crowns. Mammal crown results remain a separate comparison.

Every released leaf occurrence is retained. Repeated source labels get stable
occurrence suffixes for internal array identity; every repeated label remains
excluded from automatic taxon linking. This changes neither topology nor branch
lengths. Unlinked assessments remain in the output with null scores and reasons.
Checked tree identity alone cannot transfer DNA evidence.

The global path uses the normal DNA generation, direct current species-level
NCBI/OTT identities, checked IUCN bridges and the original guarded mammal review.
Conflicting or competing witnesses remain unresolved. Qualifying unassessed
relatives participate. Safe evidence identifiers must be unique; duplicate
unsafe records remain visible in baseline warnings.
Assessment ID and normalized scientific name must reconcile for every SIS ID
across the independently reviewed tree-link and genomic crosswalk generations.
Compatible genomic-review updates do not grant permission to change an
assessment identity or treat a SIS-only match as sufficient.

`compact_phylogeny.py` evaluates the pinned EDGE risk model using NumPy depth
groups. It retains relatives in branch sharing/risk products, uses the original
stable tip-name draw order and stores risk draws only for assessed output tips.
Only verified IUCN EX removes a tip from ED/EDGE; the gap tree keeps its complete
scope. Component parity is checked against the pinned object engine on small
trees, including EX, EW, unknown risks, polytomies and unary branches. This
one-tree risk uncertainty does not establish official EDGE-list membership or
date suitability across every clade.

Tree-independent DNA enrichment is an optional separate stage:

```sh
uv run python -m ark_pipeline.cli.dna_prepare \
  --root /private/registered-data --crosswalk /private/checked/iucn_goat_crosswalk.parquet \
  --output-root /private/analysis/dna
```

It validates the registered snapshots against the crosswalk review, resolves
current NCBI parent/rank IDs and retains all species-level relatives, including
unassessed records. Ambiguous canonical IDs cannot seed lineage representation.
Unsafe focal matches expose unknown evidence and unavailable lineage status.
No tree, EDGE dependency or spatial processing is required. It publishes checked
evidence/target tables and a default result, using the same evaluator as tree
scenarios. The API's completed cache is bounded to eight scenarios.

`serving_prepare --dna-evidence --spatial-analytics` schedules these optional
stages after metadata preparation. Pass an explicit `--spatial-coverage-status`
and `--spatial-omissions` when the omission audit is available. The default
coverage status is unknown; preparation never infers completeness from success.
The production missing-basin audit is `SPATIAL_OUTPUT/hydrobasins/missing_relationships.parquet`.
Use `--spatial-jurisdictions` or repeated `--spatial-boundary-catalogue framework=path`,
and `--spatial-weights` for stewardship and optional
range-rarity outputs. Their memory/thread limits use the shared run budget.

Registered tree inputs can also be scheduled by the main preparation command:

```sh
uv run --group edge python -m ark_pipeline.cli.serving_prepare \
  --root /private/registered-data --h3-root /private/spatial/serving/current \
  --crosswalk /private/crosswalk/current/iucn_goat_crosswalk.parquet \
  --preview-root /private/new-build \
  --profile config/spatial_semantics_iucn_richness_v3_fast.toml \
  --dna-evidence --spatial-analytics --spatial-coverage-status partial \
  --phylogenetic --phylogenetic-links-dir /private/checked-tree-links \
  --phylogenetic-ott-index /private/checked-ott-index.parquet \
  --phylogenetic-tree-tips /private/checked-tree-tips.parquet \
  --phylogenetic-baseline-review /private/review/report.json \
  --phylogenetic-aggregates --dry-run
```

This prints the concrete plan without reading datasets or building anything.
Removing `--dry-run` executes the existing metadata/base-output stages and the
explicit optional stages; it still does not load PostgreSQL or activate serving.
Legacy pinned tree directories remain supported through `--phylogenetic-input-dir`.
Identity witnesses do not depend on today's DNA rule, so a reviewed relative
with only a Contig assembly can participate in an assembly scenario. Acquisition
check timestamps do not invalidate unchanged enrichment generations.

`--phylogenetic-aggregates` creates a separate private opportunity generation.
It contains species priorities, distinct-coarse and distinct-fine cell sums,
coverage counts and pinned scenario/profile identities. It enables no map mode.
Only the default website rule is scheduled automatically; a different explicit
preset can reuse the same tree and memberships:

```sh
uv run python -m ark_pipeline.cli.analytical_prepare \
  --phylogenetic-generation /private/analysis/phylogenetic/current \
  --h3-root /private/spatial/serving/current --profile SELECTED_PROFILE \
  --output-root /private/analysis/opportunities --scratch-dir /private/scratch \
  --rule any-assembly --memory-limit 256MB --threads 1
```

Published membership profiles must agree with the requested profile when their
receipts identify one. Unreceipted fine partitions are rejected. Interrupted
analytical work leaves the previous `current` selected and can be resumed;
already published corrupt generations are not overwritten. Sparse evidence
records preserve all fields when written to Parquet, and reloaded scenarios
must agree with their saved default.

Configure only the validated optional paths needed by a private serving profile:

- `PHYLOGENETIC_GENERATION_PATH`: optional scoring `current` directory.
- `DNA_GENERATION_PATH`: optional tree-independent DNA `current` directory.
- `SPATIAL_ANALYSIS_GENERATION_PATH`: optional spatial analytics `current`.
- `MEMBERSHIP_GENERATION_PATH`: serving-list `current` with source-support outputs.

With a coverage-profile registry, set spatial analytical/support paths explicitly
in each profile's serving configuration environment. They do not inherit a
default resident generation into the passage profile. DNA/tree generations do
not depend on the seasonal distribution selection.

The API resolves a complete generation per request. Optional endpoints are:

- `/api/analysis/dna`: paginated species evidence and rule-specific checked
  family/genus representation; works without a dated-tree generation.
- `/api/analysis/phylogenetic`: paginated rule-specific rankings, components,
  evidence states, nearest relatives when requested and availability reasons.
  Threat weights are finite/nonnegative. `expected_scenario` rejects stale
  requests with 409. A completed LRU holds at most eight scenarios per engine;
  no tree or full evidence table is sent to the browser.
  `unresolved_without_genus` exposes a global baseline warning when a qualifying
  unresolved relative cannot be assigned to a reviewed genus.
- `/api/analysis/ranges/{species_id}`: global range denominator and paginated
  jurisdiction fractions when prepared.
- `/api/analysis/cells/{h3_index}/support`: overlapping source counts/fractions
  for one coarse/fine cell. Only the requested support partition is read.
- `/api/species/{id}/assessment`: exact assessment/SIS-linked descriptive
  trend, date, scope and available threat text plus source hash. Missing fields
  stay null; GoaT does not replace IUCN authority for these fields.
  Conflicting values for the same SIS/assessment pair stop metadata publication;
  unrelated SIS/assessment rows cannot supply attributes. A legacy SQL schema
  returns 503 for these details until the separate authorized schema/load step.

The PostgreSQL schema/loader explicitly carries assessment fields and optionally
loads `species_phylogenetic.parquet` from the configured checked generation.
Loading remains a separate operator action. These endpoints and generated
frontend client types add no UI controls, eager requests or base map fields.
Provider publication restrictions apply to all of them.

## Reproduction and measurements

```sh
# In the authorized cloud sandbox, using synthetic data only:
uv run --group edge python -m ark_pipeline.cli.scoring_benchmark --output /private/new-probe

# Same-policy simplified/unsimplified fast-path comparison:
uv run python -m ark_pipeline.cli.spatial_calibrate \
  --sample /private/current-policy-sample.parquet --output /private/new-calibration

# Compare retained pilot outputs and their original pinned tree:
uv run --group edge python -m ark_pipeline.cli.scoring_validate_pilot \
  --input-dir /private/checked-pilot --output /private/pilot-parity.json
```

`spatial_calibrate` refuses a correct profile without `--allow-correct`. That
flag is for the final separately authorized run; it does not itself supply
authorization. Fast results do not establish exact-contact correctness.

The benchmark distinguishes evidence evaluation, gap passes, optional nearest
diagnostics, cold/warm ranking, concurrent requests, serialization and dense
membership reduction. It is bounded and synthetic. No global interaction
budget, cold archive speedup or optimal cloud allocation follows from it.

Benchmark sample selection identities include source hashes, row rules, sampler
code/dependencies and fixed stratification. Default stale samples are preserved
while a current sample is built in a durable content-addressed directory.
Explicit stale samples require `--rebuild-sample`. `--census-cache` independently
reuses verified archive censuses; omit it for cold I/O measurements. Cached
sources are rehashed. `--scan-workers` is opt-in and shares the configured worker
budget; archive results reduce deterministically only after every task succeeds.

Offline comparison uses explicit same-identifier finite cohorts and deterministic
ties. It records source checksums, scenario identities, excluded populations,
rank correlation and top 1/5/10 percent overlap. Supply an existing ecosystem or
status column as `--stratum` for separate cohorts; missing strata are labelled.

```sh
uv run python -m ark_pipeline.cli.scoring_compare \
  --reference /private/old/species.parquet --alternative /private/new/species.parquet \
  --identifier iucn_sis_id --score priority --reference-generation OLD_ID \
  --alternative-generation NEW_ID --output /private/new-comparison.json
```

Use `--identifier h3_index` for cell hotspot comparisons. The default input limit
is one million rows per side, explicitly adjustable up to five million; it is an
offline tool, not a request-time global operation. Processing differences do not
establish biological trends.

## Bounded execution and restoration

The synthetic robustness matrix is executable in the cloud with:

```sh
uv run --frozen python -m ark_pipeline.cli.scoring_rehearsal --output /private/new-robustness
```

Nine scenarios cover categorical scoring, website/assembly/quality gaps,
changed threat weights, range-rarity and individual source-route subsets.
Independent oracles check production fine sums, distinct coarse sums, OR masks
and global weight conservation over mapped species. Unmapped assessed species
are counted separately. Reports include common-cohort rank/hotspot comparisons
and ecosystem/status strata; synthetic changes are not biological findings.

`spatial_calibrate.repair_effects` separates repair sensitivity from
simplification. With simplification disabled it compares the configured repair
against a labelled `buffer(0)` alternative. Production repair rejections remain
rejected. Invalid original geometry has no authoritative unrepaired cell set;
the alternative is not automatically adopted.

`--preview-root` isolates build databases, metrics, ADM2 installation, static
assets and analytical generations. Registered ADM2 preparation consumes the
frozen source; it does not automatically update acquisition. `data-boundaries`
remains the explicit source-update command. A configured missing municipality
root fails instead of silently substituting another generation.

Keep the reviewed compatible GoaT/NCBI/crosswalk/tree pairing together. To
relocate the historical review, `edge_baseline_review --source-paths FILE`
accepts a JSON mapping of its source keys to physical paths and verifies every
original content hash. Rebuild the review through that command when its code
changes; never edit its receipt. Exact already-obtained tree/OTT bytes can be
registered from private offline mirrors through `sources_acquire`, with that
transport recorded explicitly rather than labelled a new provider download.

The DNA preparation stage projects only taxon ID and the six raw evidence fields
before Python materialization. Scenario classification scans each relative once
and retains focal states only for uniquely identified safe targets. All relatives
still contribute to rule-specific coverage counts and checked lineage status.
See [the resource evidence](13_resource_evidence.md) for measured scope and limits.
