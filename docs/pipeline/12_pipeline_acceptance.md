# Pipeline acceptance and the full-run gate

This is the execution companion to [the plan](10_phylogenetic_scoring_plan.md)
and [implementation guide](11_scoring_implementation.md). It separates completed
code/fixture work from unperformed full-data execution. Passing fixtures does
not establish global coverage, accepted spatial error or a new default score.

**Expanded readiness, 20 October:** all-acquired-clade preparation and the
identified calibration/robustness additions passed cloud verification. The
final suite passed 124 checks; a committed private artifact was freshly
downloaded, verified, restored and reused through the normal global CLI. All
three DNA rules passed the scenario API checks against the restored copy. This supersedes the
narrower mammal record below. It establishes pre-full implementation readiness,
not 100% species coverage, scientific adoption or execution authorization.

**Earlier 20 October readiness:** Steps 1–9's narrower scope was checked for the
retained-source FAST candidate. The final cloud suite passed 117 checks in
28.318 seconds. Normal metadata preparation/reconciliation completed, the
registered mammal generation matched the saved reference, and an immutable
private pack was downloaded, checksum-verified, extracted and reused through
the normal DNA preparation entry point. Full-data execution and scientific
adoption are unperformed; correct-path coverage requires explicit authorization
and remains the final execution phase.

## Implemented pipeline scope

- Durable verified samples and an independent content-checked census cache;
  optional archive scan concurrency stays within the shared worker budget.
- FAST calibration reports, private repair audits and a guard against accidentally
  launching correct-path calibration.
- Retained polygon/point/basin OR support at distinct fine/coarse memberships;
  global mapped-cell counts/areas, explicit omissions, stewardship and rarity.
- Exact assessment attributes and a tree-independent shared DNA-rule evaluator.
- Checked complete mammal crown, ED/EDGE components and risk uncertainty,
  rule-specific gaps/withholding and paginated analytical APIs.
- Separate optional coarse/fine opportunity generations and common-cohort
  species/hotspot comparison reports. Base metric projections remain unchanged.
- Receipt validation, interruption/reuse and atomic publication for these stages.

The expanded candidate uses all acquired clades in the complete released tree,
the three documented DNA rules, global mapped-cell count denominators and the
existing partial-basin policy. Unavailable identities stay null. The original
mammal crown remains the historical reference. No new default priority is adopted.
Correct-mode optimization is withheld because profiling that mode has not been
authorized; it is not presented as a completed performance improvement.

## Reproduce the isolated acceptance check

Run this in the authorized cloud sandbox with the frozen project dependencies
and Tippecanoe installed; no user datasets or live database are required:

```sh
uv run --group edge python -m unittest tests.test_scoring_acceptance -q
```

The fixture uses the production FAST polygon, point and basin routes, registered
taxonomy matching, metadata, categorical fine metrics, source/coarse builders,
Arrow/PMTiles publication, optional tree/DNA enrichment, separate aggregates,
stewardship and fixture APIs. It checks a non-website qualifying reviewed tree
bridge, generation reuse, interrupted aggregate publication, profile mismatch,
unreceipted partitions and unchanged base-artifact hashes. All evidence is synthetic.

The separate saved-pilot check reproduced 7,965 mammal gaps and 4,225 previously
eligible priorities exactly. Its 5,945 assessed species and 12,546 baseline
records belong to the original snapshot; they are not a refreshed population.

Normal source preparation was also exercised on those retained inputs: the
registered tree/OTT mirrors, guarded historical baseline review, complete
mammal generation, tree-independent DNA generation and registered ADM2 stage
all passed. Metadata preparation produced 173,498 records; this is not a count
of scored species. DNA targets number 171,604, with 1,795,920 evidence records.
Independent multipass scenario parity covered all targets under website,
assembly and quality thresholds 90/95.

The real FAST calibration used eight deterministic rows from the verified
1,000-row current-v3 sample. Unsimplified/simplified FAST coverage yielded
63,947/63,798 cells, with 63,778 common and 169/20 excluded. Common-cohort top
1/5/10 percent overlap was 1.0; correlation is null for constant richness.
This bounded result establishes neither global spatial error nor correct-mode
agreement. Those scientific limits stay explicit in the candidate.

## Source preflight observations, 19 October 2026

The registered local manifest was read as metadata only. It was last updated
1 October. Public metadata was checked in the cloud at approximately 16:05 UTC;
checksums and responses are retained in the ignored task results. That metadata-only check advanced no source. The subsequent prerequisite pass
registers the exact reviewed tree/OTT bytes through the normal acquisition
workflow using private offline mirrors; it does not claim a fresh provider
download or advance the agreed assessment/GoaT/NCBI snapshot.

| Source | Existing selected snapshot | Observation and remaining action |
| --- | --- | --- |
| IUCN assessments | 2026-1 | Official API documentation cites 2026-1; independently confirm the licensed assessment and bulk spatial releases before acquiring/pinning a full run |
| IUCN polygons/tables | 2026-1 | Existing 30 polygon and 31 table files remain pinned; bulk-page access was unavailable during the public check, so current provider files are unverified |
| GoaT | 30 August 2026 | A successful live count-only Eukaryota request returned no evidence records; Deliberately retain this exact reviewed snapshot with its compatible NCBI/crosswalk/baseline inputs for the candidate build |
| NCBI taxonomy | 29 August 2026 | Provider checksum metadata was updated 19 October; Deliberately retain the complete reviewed names/nodes/merged/deleted set; a later refresh must rebuild dependent identity and baseline reviews |
| Dated tree | Zenodo 19049120 | Registered optional pin matches the prototype configuration; the record exists, but the check did not establish the latest available version |
| OTT | 3.7.3 pin | Official release documentation describes 3.7.3; the live taxonomy API reports `ott3.7draft3`. Preserve the reviewed tree/taxonomy pairing and verify archive checksums rather than equating labels |
| HydroBASINS | v1c | Provider still describes version 1; retain audited missing-geometry relationships explicitly unless provider-matched evidence resolves them |
| IUCN island basins | August 2023 | Current provider revision was not independently verified; no missing basin was repaired or inferred |
| geoBoundaries ADM2 | gbOpen-ADM2-16adf4a45756 | All 180 selected country boundary IDs match current API metadata; represented years span 2005–2023. ID agreement is not a geometry checksum or universal ADM2 coverage |
| GBIF backbone | 28 August 2023 | Provider metadata gives publication 28 August 2023 and modification 17 November 2023; retain the configured legacy backbone/identifier policy |

Sources: [IUCN API](https://api.iucnredlist.org/),
[GoaT API](https://goat.genomehubs.org/api/v3/search),
[NCBI checksum](https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdump.tar.gz.md5),
[tree record](https://zenodo.org/records/19049120),
[OTT release](https://tree.opentreeoflife.org/about/taxonomy-version/ott3.7.3),
[HydroBASINS](https://www.hydrosheds.org/products/hydrobasins),
[ADM2 metadata](https://www.geoboundaries.org/api/current/gbOpen/ALL/ADM2/),
[GBIF metadata](https://api.gbif.org/v1/dataset/d7dddbf4-2cf0-4f39-9b2a-bb099caae36c).

The candidate freeze deliberately retains the compatible historical assessment,
spatial, GoaT and NCBI snapshots. This follows the plan's option to retain an
older compatible release with a reason: advancing GoaT/NCBI would invalidate the
reviewed crosswalk and identity/baseline comparisons. Newer release metadata is
recorded, not silently substituted. It is a reproducible retained-source build,
not a claim to contain every provider's latest release. Restricted source access
and publication rules continue to apply.

The registered tree/OTT generation and rebuilt historical baseline review must
pass their original checksum and compatibility guards. Additional nonqualifying
bridges are applied after historical review and cannot overwrite reviewed tips.
The selected FAST partial-basin profile retains 4,535 missing basin IDs / 7,523
relationships / 26 affected species explicitly. Strict FAST still fails on these
omissions. No missing basin is inferred or silently treated as zero range.

## Full-generation acceptance record

For the authorized full phase, record the following with the immutable source
manifest and output receipts. Do not substitute historical pilot counts.

| Check | Required evidence |
| --- | --- |
| Source freeze | Check time, provider/selected versions, checksums, terms and reasons for deliberately retained older compatible releases; registered tree/OTT pins and checked indexes/links/review |
| Populations | Assessed, spatial-only and unscored species; unique memberships by ecosystem/profile; unsupported clades and unavailable-score reasons |
| DNA baseline | Rule identity, qualifying/unknown/unresolved records, represented tips, unsafe joins, genus withholding and unresolved-without-genus warnings |
| Spatial limits | Missing-basin relationships/affected species, ADM2 coverage/year/overlap, same-policy simplification/repair diagnostics and explicit scientific acceptance status |
| Scores | ED/EDGE components and uncertainty, unweighted gaps and threat weights separately; global range denominators independent of viewport/jurisdiction filters |
| Comparisons | Common-cohort denominators, exclusions, deterministic ties, rank correlation and top 1/5/10 percent overlap, ecosystem/status strata and membership/hotspot changes |
| Resources | Requested/effective CPU/RAM, shared workers/threads/DB limits, stage wall/CPU/RSS, scratch/I/O, artifact bytes and measured cloud cost where available |
| Payloads | Unchanged base metric/property projections; archive/tile/Arrow/fine response bytes and latency; optional artifacts kept out of startup requests |
| Publication | Complete receipts, stale-input rejection, interruption/resume and atomic generation selection; independent validation before any load/activation |

Use `scoring_compare` on explicitly prepared same-identifier species/cell tables.
Preserve source versions and scenario identities with each report; changed
processing or sources do not establish biological trends. Use null correlation
for constant/single-member cohorts instead of inventing a statistic.

## Selected execution settings

See [the real-input resource evidence](13_resource_evidence.md). Requested
8 CPU / 32 GiB is the cost-oriented default, four archive census workers and
at most eight metric workers, with one native/DB thread per metric worker.
Explicit 16-worker metrics are available on a 16-CPU allocation, with a measured
modest within-host benefit. The observations do not justify 64 GiB or a global
runtime prediction. Use an 8-GB main-process DuckDB limit for serial reductions and a separate
2-GB limit per metric worker. The measured 8-GB reduction used about 8.9 GB
process RSS and was about 10% faster than 4 GB; 32 GiB retains headroom. The
corrected canonical range calculation no longer needs a large pair hash set.

All build, ADM2, static asset and analytical output roots are isolated under the
candidate preview root. Preparation consumes registered ADM2 without updating
sources. Source updates, database loading and activation are distinct actions.

The two/three-hour workers used for prerequisites are not a full-run lifetime
plan. With the historical five-hour observation, propose an eight-hour full-run
window including staging, validation and durable retention, at $13.6192 maximum
compute for 8 CPU/32 GiB at the checked rates. That is a separate authorization
scenario, not an ETA or invoiced cost. Verify provider limits/capacity and scratch
headroom for that allocation, reserve time for retention, and commit intermediate
phases; a hard lifetime can destroy unretained scratch. Do not launch the full
candidate with the short-lived prerequisite helper's limits.

## Durable prerequisite retention

The authorized private `ark-pipeline-storage` project contains immutable
catalogues `scoring-prefull-foundation-inputs:v0` and `scoring-prefull-final:v0`
alongside the original `pipeline-inputs:v0`, `pipeline-results:v4` and
`pipeline-aggregation-checkpoint:v0`. Exact inventories, candidate source/code
hashes and transfer receipts stay in ignored task results; no datasets are
part of this code release. The final pack is 1,009,520,640 bytes, SHA256
`1ca2f8b58e8da2ffbe225f88054ab1e1ab2ce43df8820bb9fc4b5033db894804`.

The normal storage command verified the committed catalogue and file checksum
on a fresh cloud download. Extraction preserved relative `current` links;
the 7,965-tip mammal generation passed load validation and the normal
source-pinned DNA CLI returned `reused` against the restored generation.
Private `scoring-prefull-readiness:v0` retains the final verification record.
The earlier expired, incomplete handoff is superseded and must not be used.

The expanded candidate overlays that base with private
`scoring-all-clades-final:v1` and `scoring-all-clades-readiness:v0`. The expanded
pack is 238,817,280 bytes, SHA256
`77d470ac68c2086d3c82ede07fc79f409581dd1f9fc8d8ccde6452ae0ceec6b1`.
It contains checked global/DNA generations, calibration/robustness artifacts,
current code snapshot and diagnostic records. The readiness pack retains the
124-check receipt, candidate plan/freeze, unchanged-value proof, fresh download/
restoration/reuse proof and restored API results. The earlier expanded `v0`
predates the final assessment/name guard; use the explicit `v1` candidate pin.
Restoring it at the same isolated paths and running the normal global CLI
returned `reused`; relative generation links and source receipts stayed intact.
The supplementary measured API memory report is retained privately as
`scoring-all-clades-api-resources:v0`; it covers the restored generation with
all three rules and cached/concurrent requests loaded in one process.

Restore original immutable inputs first and overlay the retained acquisition
manifest/generations in the same isolated `/probe` layout. The compact pack
excludes bulk inputs already in the original catalogues. Preserve source paths,
inventories and receipts; verify the exact code snapshot before reuse. Do not
extract an older embedded checkout over newer reviewed code. A new source or
code selection requires the relevant checks again.

Conservative pre-full compute reservations total **USD 45.7984 / 75** across
all attempts and full allocation lifetimes. This is an upper reservation at
the published compute rates, not an invoice or a full-run budget.
All 14 identified verification sandboxes are stopped and the report watcher
has exited. No local development server was started.

## Explicitly unperformed actions

The following remain execution/scientific gates, not silently completed work:

1. Stage the verified private immutable candidate freeze in the full-run
   sandbox and recheck its source/code identities. The prerequisite download/
   restore/reuse proof has passed. If selecting different releases or changing code, repeat affected
   checks rather than editing receipts or reusing incompatible generations.
2. Obtain full-run scope/budget authorization, including private cloud staging,
   durable output retention and a resource cap suitable for the actual inputs.
3. Run and review the resulting full generation and its comparison record.
4. Run correct-path geometry coverage only as the final execution action after
   Karl August's explicit authorization. No such run has occurred. Review
   scientific error limits before adopting approximate outputs or new defaults.

Database loading and serving activation are separate actions. UI selection is
not a gate on this pipeline work: the requested later direction is more filters
and options in the existing table. The rejected redesign mocks are removed.
Optional map UI, broader clades and acquisition-dependent extensions stay deferred.

## Candidate command to review before authorization

Restore the original private input catalogue plus the retained prerequisite pack
into the same isolated `/probe` layout, verify their inventories, and sync the
exact reviewed checkout. The source manifest and registered tree/OTT snapshots
are immutable for that execution. Do not run an acquisition update or refresh
the reviewed crosswalk midway through this retained-source build.

The following is a **read-only command plan**. It does not authorize execution:

```sh
PIPELINE_WORKERS=8 DUCKDB_MEMORY_LIMIT=8GB RES7_MEMORY_LIMIT=2GB \
uv run --frozen --group edge python -m ark_pipeline.cli.serving_prepare \
  --root /probe/real-inputs \
  --profile config/spatial_semantics_iucn_richness_v3_fast_partial_basins.toml \
  --crosswalk /probe/foundation-inputs/crosswalk/iucn_goat_crosswalk.parquet \
  --crosswalk-mode require-current --build-pairs \
  --preview-root /probe/full-candidate \
  --iucn-details-dir /probe/iucn-cache --iucn-metadata-mode cache-only \
  --workers 8 --spatial-workers 8 --metric-workers 8 \
  --duckdb-threads 4 --metric-threads 1 --tile-threads 4 --tile-duckdb-threads 4 \
  --dna-evidence --spatial-analytics --spatial-coverage-status partial \
  --spatial-omissions /probe/real-inputs/derived/iucn-richness-fast-v3-partial-basins/hydrobasins/missing_relationships.parquet \
  --spatial-boundary-catalogue admin0=/probe/data/boundaries/country-scope.geojson \
  --spatial-boundary-catalogue eez=/probe/data/boundaries/eez.geojson \
  --phylogenetic --phylogenetic-scope global \
  --phylogenetic-links-dir /probe/foundation-inputs/tree-links \
  --phylogenetic-ott-index /probe/foundation-inputs/indexes/ott_index.parquet \
  --phylogenetic-tree-tips /probe/foundation-inputs/indexes/global_tree_tips.parquet \
  --phylogenetic-baseline-review /probe/tuning/baseline-review-current/output/report.json \
  --phylogenetic-aggregates --tiles --dry-run
```

Verify the profile-derived omission path against the printed spatial root before
executing. The scoped stewardship outputs use country-scope and EEZ frameworks;
ADM2 is prepared through the registered boundary stage for existing base serving.
Per-ADM2 analytical fractions can use the same explicit boundary-catalogue flag
when included in a later scope. Range-rarity weights remain an explicit optional
scientific input; the existing categorical score is unchanged.

Removing `--dry-run` is a full-run action and needs the agreed execution scope,
resource/lifetime budget and durable retention plan. Correct-path coverage is
still reserved for the absolute final execution phase and requires a separate
explicit authorization. No load, activation or data publication is implied.

## Expanded coverage and measured limits

The final expanded cloud suite passed **124 checks in 28.25 seconds**, FAST
geometry only. The new assessment/name reconciliation guard was also exercised
on the real inputs: all 171,604 identities matched and tree, evidence, species,
score and default-gap Parquet hashes were unchanged. The guard adds rejection
of incompatible inputs rather than changing any calculated scientific value.

The complete source tree retains 2,294,776 tips under
`cellular_organisms_ott93302`, incoming stem excluded, in Myr. All 171,604
assessed records across 74 classes remain in the output. ED/EDGE components are
available for 150,270 records (87.57%) in 68 classes. Default DNA gaps have
95,702 available rows, 4,134 withheld for incomplete baselines,
50,434 with unsafe genomic identity and 21,334 without checked tree identity.
These are different denominators and permissions; a checked tree link does not
grant a DNA-transfer permission. Missing values are not zero scores.

All assessment IDs and normalized scientific names reconcile between the
current genomic crosswalk and independently reviewed tree links. Their reviewed
crosswalk generations differ; the source hashes are preserved and no receipt
was rewritten. Identity drift stops the global preparation path before evidence
bridges can be applied.

The representative real FAST diagnostic covers full fragmented, hole-rich,
antimeridian and large geometries, including a bbox over 30,000 square degrees.
Its six species have Jaccard overlaps 0.979308–1.0 against FAST unsimplified
coverage. The common cell cohort is 1,467,283, with 10,907/4,947 reference/
alternative exclusions; top 1/5/10 percent overlap is
0.981258/0.994875/0.997029. Repair sensitivity is separate and retains rejected
zero-area inputs. The nine-scenario synthetic robustness matrix reconciles
fine/coarse sums, OR masks, range budgets, source subsets and stratified ranks.
See the [resource evidence](13_resource_evidence.md) for populations and timings.

In-process global API page-100 probes measured 24.58 seconds for the first
website request including load, 7.24/7.65 seconds for cold assembly/quality rules,
and 0.30–0.31 seconds for cached requests. Four concurrent warm requests took
1.20 seconds. JSON responses were about 68.5–68.8 KB. These are server-side
measurements, not browser/network latency. Initial loading is not instantaneous;
later table controls need explicit loading/error states and bounded requests.
Planning budgets for that later UI are 30 seconds initial load, 10 seconds for
a new rule, 0.5 seconds for a warm page and two seconds for four warm pages on
the measured sandbox. They are profile-specific verification targets, not an
internet service guarantee. No global continuous map channel is enabled.
The completed full API resource probe peaked at 5.076 GB process RSS, including
loaded DNA/tree evidence and scenario caches, with sampled scratch zero. This
is per API process; serving worker counts need their own memory allocation at
activation. It does not require raising the selected full-build RAM above 32 GiB.

All acquired clades are eligible for checked descriptive output. This does not
resolve absent tree tips, unsafe taxonomy, missing basin geometry or scientific
date acceptance. Those limits remain explicit in a provisional FAST build.
