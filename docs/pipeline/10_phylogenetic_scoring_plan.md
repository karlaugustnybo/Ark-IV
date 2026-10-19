# Next pipeline milestones: scoring, evidence and compact map outputs

**Planning date:** 19 October 2026. **Status:** proposed implementation plan.

Implementation observations describe the inspected local working checkout and
its saved audits. Some research/serving paths remain optional or unmerged on
`main`; this plan does not claim they are already merged or deployed.

The next substantial pipeline work should develop dated-tree scores and
configurable DNA evidence alongside range-rarity/stewardship calculations and
spatial evidence provenance. Preserve useful evidence during the next run,
then expose each analytical capability through the smallest appropriate output.
Deliver one validated clade through the dated-tree path first, then expand to
supported clades.

**Payload constraint:** richer species tables and cell details do not justify
larger PMTiles. Keep the existing base map payload unchanged by default. New
map modes may require compact cell aggregates delivered separately on demand;
species records, evidence histories and audits stay outside map tiles.

**Deferred:** GGBN holdings, protected-area/KBA acquisition, bird distributions,
and other substantial new source integrations. Work with already registered
inputs for this milestone; do not expand acquisition to complete every optional
quality rule or assessment attribute.

This plan combines the scientific integration work with the checks needed before
another full build and investigations of scan/coverage performance and sandbox
resource sizing. It records proposed behavior, not completed integration or
new scientific acceptance. The [methodology](../../methodology.md),
[prototype guide](09_edge_prototype.md), and
[roadmap integration item](../roadmap.md#integrate-dated-tree-identities-edge-and-dna-gaps-into-the-data-pipeline)
remain the sources for current behavior and evidence.

**Implementation order:** follow Section 7, not the order of the topic sections.
Establish reusable measurements, settle spatial assumptions, optimize measured
bottlenecks, preserve evidence, integrate species scoring, then tune the final
workload and run it. Table UI and optional map modes follow the validated build;
they must not force another polygon-coverage run.

## 1. Starting point and intended outcome

The existing work provides a substantial foundation:

- The documented tree-link investigation linked **150,270 of 171,604** assessed
  records. These are identity links, not globally calculated or accepted scores.
- The mammal EDGE investigation calculated scores for **4,695 mammals across
  18 pilot trees**. Arithmetic checks and tree sensitivity are documented;
  global coverage and acceptance of the tree dataset remain separate questions.
- The 5 October DNA-gap pilot retains a **7,965-tip** mammal crown. Of **5,945**
  assessed mammals, **4,705** have usable checked focal GoaT/tree identities.
  Its baseline has **724** represented tips; **59** DNA-positive GoaT records
  lack verified tree identities. It withholds **480** otherwise eligible
  priorities and reports **4,225** priorities. These snapshot-specific counts
  must be recalculated for new evidence or a different DNA definition.
- These investigations are outside the required full-build and serving workflow.
  The existing map uses categorical threat/DNA counts rather than these
  continuous phylogenetic scores.

The first useful dated-tree outcome is a mammal data generation that the normal
pipeline can reproduce, with separate species score fields, explicit
unavailable-score reasons, optional map aggregates, and an on-demand DNA-rule
calculation path. The first spatial outcome is retained evidence provenance
and global mapped-range denominators, without expanding base tile properties.
The existing genus/family score remains reproducible. New default priorities
require a separate scientific decision.

## 2. Keep the scientific quantities separate

Evolutionary distinctiveness describes a species' allocation of evolutionary
history under a specified tree and metric. Original EDGE and EDGE2 combine
distinctiveness and extinction-risk assumptions through their respective
definitions. Compute and preserve their components and method versions; do not
add another threat multiplier to an already risk-weighted EDGE output.

ARK's phylogenetic DNA gap asks a different question. For represented tips `S`
and candidate species `s`:

```text
gap(s, S) = rooted_PD(S union {s}) - rooted_PD(S)
sequencing_priority(s, S) = threat_weight(s) * gap(s, S)
```

The gap has units of branch length, currently million years. It is neither DNA
sequence divergence nor a measure of within-species genetic diversity. The
nearest represented relative and its tree-path distance are separate descriptors;
nearest-tip distance must not replace marginal branch coverage.

Record the tree, root, stem treatment, calculation scope, and date units. The
pilot excludes the mammalian stem. Separate stem-free clade calculations must
not be presented as a single globally rooted calculation without reviewing the
omitted connecting branches and cross-clade comparability. Keep the complete
accepted clade tree, including unassessed tips, during calculation.

## 3. Precompute stable evidence; calculate DNA scenarios on demand

### Prepare during the pipeline

Register and pin the dated tree and OTT taxonomy through the existing source
and provenance machinery. Build reusable artifacts for:

- Tree topology, branch lengths, traversal orders and stable tip indexes. Use
  compact arrays and bounded clade processing rather than millions of Python
  node objects in each request.
- Reviewed SIS/assessment, current NCBI, OTT and tree-tip identity mappings,
  with source hashes, match reasons and independent genomic-transfer permissions.
- DNA evidence for **all qualifying relatives in the supported tree scope**,
  including GoaT species without IUCN assessments. Preserve unresolved identities
  and evidence provenance for every supported rule, not just today's baseline.
- ED/EDGE components, calculation configuration, uncertainty summaries and
  eligibility reasons for the accepted scope.
- A default DNA-gap result and only the preset aggregates needed by agreed map
  modes. Prepare these separately from the base map; load them when that mode
  is selected. Reuse existing species-list artifacts for dynamic spatial
  aggregation before introducing another membership representation.

Initially expose bounded, versioned DNA rules: the exact existing website rule,
any recorded assembly, and an assembly-level/BUSCO threshold rule where supported.
Keep sample, project, criteria-text, assembly and resampling evidence distinct.
An assembly-level/BUSCO combination is a limited proxy, not EBP certification.
Define missing-value behavior explicitly; unrecorded evidence is not proof of
absence. Preserve the existing rule's exact behavior for compatibility, including
its treatment of nonempty strings, rather than silently cleaning it differently.

Taxon-level aggregates can contain measurements from different assemblies.
Before offering a rule that claims a particular assembly meets several quality
criteria, require evidence tying those criteria to the same assembly. If that
would need new acquisition, defer the rule; label the supported aggregate proxy
accurately or leave that rule unavailable.
Resampling-required evidence must not erase a known existing assembly.

### Recompute when the user changes the DNA rule

Apply the rule to the evidence, construct represented set `S`, and reevaluate
baseline-completeness flags. Then mark covered branches in a postorder pass
and accumulate uncovered branch lengths in a preorder pass. A represented tip
covers its root path. Each candidate's marginal gap is the sum of its still
uncovered path lengths.

The gap-only calculation can be **O(V + E)** in tree nodes and edges, plus the
evidence scan and output mapping. It needs no species-by-species distance
matrix. The current [prototype](../../ark_pipeline/phylogenetic_gap.py) already
uses tree passes, but its Python objects, Pandas output and nearest-tip companion
calculation are not a measured global interactive implementation. Optimize the
gap kernel separately if those diagnostics dominate request cost.

Start with a server-side implementation using the existing Python backend.
Benchmark the compact kernel before deciding whether a browser worker or WASM
adds value. Do not require a global tree or restricted evidence download merely
to change a filter. No interactive latency is established by this plan.

Changing threat weights leaves unweighted gaps unchanged. Changing the DNA rule
can change gaps across the tree, not just for species whose evidence flag changed.
Candidate filters and jurisdiction filters should restrict what is ranked or
displayed while retaining the full represented baseline, unless a separately
named scientific mode explicitly changes that baseline.

## 4. Treat map aggregation as a separate performance problem

For a cell `c`, the proposed additive map priority is:

```text
cell_priority(c, S) = sum over distinct eligible species s in c of
                      threat_weight(s) * gap(s, S)
```

This sums species' individual marginal opportunities. Shared uncovered branches
can appear in multiple species' contributions: it is not the phylogenetic
diversity gained by sequencing every species in the cell. A joint-benefit map
would need a separately specified branch-union calculation.

The existing **164-column categorical metric schema** cannot reconstruct an
arbitrary continuous gap score. A new DNA baseline requires new weighted
aggregation from memberships or an exactly equivalent representation. It does
not require repeating polygon coverage or taxonomy acquisition.

Implement a bounded path first:

1. Precompute the default coarse/fine score aggregates for selected modes in
   separate artifacts; do not append every preset to base PMTiles or Arrow.
2. Calculate custom species scores once per scenario, then aggregate requested
   coarse or visible fine cells from distinct species memberships.
3. Cache completed results and measure dense uncached views before expanding
   scope. If global custom aggregation is too slow, make preset map modes and
   custom species rankings the initial supported scope, with an explicit limit.

For a fixed DNA rule, storing gap sums **by threat category** per cell can make
later threat-weight changes cheap. It does not make a new DNA definition free.
Do not enumerate all continuous quality thresholds or Boolean rule combinations.

Derive coarse values from distinct coarse species membership, never by summing
fine-cell priorities. Retain ecosystem and resident/passage profile semantics.
Report scored, unscored and baseline-incomplete coverage alongside totals;
unsupported clades must not silently contribute zero to an apparently global
score. Define stable colour domains per scenario rather than renormalizing
every viewport independently.

Give every result a scenario identity covering tree/root/scope, evidence and
identity generations, rule version and parameters, and baseline-review policy.
Weighted spatial results additionally depend on threat weights, membership
generation, ecosystem/profile and aggregation version. Return that identity
with results. Cancel obsolete requests, reject stale responses, and update map
colours and displayed scenario together. An existing default layer must not
appear to be the completed custom result while calculation is pending.

## 5. Other pipeline work using existing inputs

### Preserve spatial evidence before it is discarded

Carry polygon, point and HydroBASINS support through pair deduplication as a
compact source-type bitmask. Combine duplicate support with bitwise OR; a pair
supported by two routes is still one species membership. Preserve the result
in a versioned membership artifact or accompanying keyed relation, not in
every tile feature. Keep archive/row lineage in private audit artifacts rather
than repeating source paths or histories per cell.

This should be designed before the next full pair reduction. Once source type
has been discarded, a later metadata-only pass cannot reconstruct it from the
deduplicated species lists. Measure the extra intermediate/output bytes and
resume compatibility on a small mixed-source fixture before selecting storage.

Cell-detail queries can then return counts/fractions supported by each source
type and multiple types. Source-type fractions can overlap; they are not
mutually exclusive shares. Coarse evidence must first union support for each
distinct coarse cell/species pair. This is source support, not a calibrated
biological confidence score. Default delivery is selected-cell details, with
no new base PMTiles or fine map fields. A provenance colour layer is a later,
separately selected map mode.

### Calculate range-rarity denominators and stewardship

Compute each species' global distinct resolution-7 membership count for the
selected spatial profile, with partial-coverage status. Store the count in the
species dimension or a related analytical table. Calculate jurisdiction-level
mapped-distribution fractions using deduplicated memberships and documented
boundary semantics; retrieve them when a species or jurisdiction is requested.
No species range counts or lists need to be repeated in PMTiles.

A separate optional range-rarity map mode can use `sum(w_s / N_s)`, where `N_s`
is the global fine-cell count. Retain sufficient category sums only if editable
weights are required. Decide whether the scientific denominator is cell count
or mapped area; H3 cells do not all have equal area. Treat any-touch ranges as
mapped distributions, not measured occupancy. Boundary-touch filtering is not
an exact range/administrative-polygon intersection.

Specify coarse aggregation separately: summing fine contributions and counting
each coarse species once answer different questions for this mode. Verify the
chosen global-budget interpretation and colour domains rather than borrowing
the existing distinct-species coarse score formula silently. Viewport and
jurisdiction filters must not redefine the global denominator.

### Structure DNA evidence independently of the tree

Use the shared evaluator from Section 3 to expose exact-species sample/project/
assembly states and scenario-dependent genus/family representation. This is
useful without EDGE or a dated tree. Store evidence and rule-specific results
in species/scenario data, keeping the distinction between missing evidence,
unresolved taxonomy and a known qualifying record. Recalculate lineage status
from all qualifying relatives, not only the assessed table.

Tables and species details can use this capability before custom map scoring
is ready. The current joint-count map supports the current categorical rule;
it cannot reproduce arbitrary changed DNA rules from the same counts alone.
Reaggregate memberships only if a custom map mode is actually requested.

### Retain useful assessment attributes without enlarging tiles

Audit registered assessment inputs for population trend, assessment date/scope
and threat classifications. Carry available fields through species exports,
the PostgreSQL loader/schema and API. A field existing in an acquired file or
GoaT export does not establish that it is authoritative or already served;
record source and assessment identity and prefer the appropriate IUCN evidence.
Do not acquire a new feed for unavailable fields in this milestone.

Expose these first in species tables/details. Selected-cell composition or
assessment-age summaries can be calculated or cached behind detail endpoints.
Keep them descriptive until score changes receive scientific review. None
requires new base map properties just to appear in a table or cell panel.

### Add reproducible robustness analysis

Build an isolated analysis workflow using the same versioned inputs and rule
evaluator. Compare weights, DNA definitions, source-route subsets, range-rarity
and calibrated spatial alternatives on a representative fixture. Report common
cohort denominators, rank correlations and top-1/5/10% hotspot overlap with
deterministic tie handling. Store reports and scenario outputs outside the
serving payload. A robustness map is optional future work, not a reason to put
all scenario results in every tile.

## 6. Output contracts: PMTiles, map requests, tables and audits

### Current delivery paths and the payload rule

In the inspected checkout, the
[managed serving contract](07_global_serving_pipeline.md) compiles **resolution 3 only** into `priorities.pmtiles` (zooms 0–6). It
retains the coarse Arrow snapshot that the frontend normally uses. Fine
resolution-7 metrics arrive through `/api/tiles/res7/{z}/{x}/{y}` as compact
arrays backed by partitioned Parquet. Full static fine PMTiles are an explicit
legacy option (`--static-fine`), not the default path. Rich species and cell
records are served separately through the API and PostgreSQL/Parquet data.

Consequently, PMTiles file size is only one budget. Coarse Arrow affects startup
download and memory; fine API bytes affect panning, decoding and rendering.
Adding a field to Arrow or every fine response instead of PMTiles still has a
cost. Optional analytical modes should have optional payloads.

**No new field is required in the base PMTiles merely to support the proposed
tables or details.** Existing geometry, identity, boundary memberships and
categorical metrics remain the base contract. Optional map colours need cell
values and domain/availability information, not the species evidence used to
calculate them. Prefer a mode-specific coarse artifact keyed by H3 and a
mode/scenario-specific fine response over duplicating geometry or expanding
the base archive. This is proposed delivery work, not an existing endpoint.

### Destination of each planned output

| Capability | Species tables / selected details | Needed to colour a map | New base PMTiles properties |
| --- | --- | --- | --- |
| ED / EDGE descriptors | Per-species components, status, uncertainty and method | Only an explicitly selected aggregate; define the scientific cell summary first | None planned |
| Configurable phylogenetic gap | Species gaps, priorities, nearest relative and availability reasons | Cell sum or supported category sums for the selected scenario; compact availability information | None planned; separate mode data |
| Sample/project/assembly and lineage states | Evidence fields and rule-specific flags | Scenario-specific counts only if custom map scoring is selected | None for table support |
| Range-rarity | Global range count/area, denominator/profile and completeness | Weighted cell aggregate or category sums for the selected mode | None planned; separate mode data |
| Stewardship fractions | Requested species/jurisdiction fractions and coverage caveats | None for the table/detail feature | None |
| Spatial source provenance | Cell/species bitmask and selected-cell support summaries | None initially; a later provenance colour mode needs only its aggregate | None |
| Trends, dates, scope and threat types | Per-species attributes and selected-cell composition | None initially | None |
| Robustness diagnostics | Reports and requested scenario comparisons | None initially | None |
| Row lineage, tree links and validation receipts | Private build/audit data; API may expose concise permitted status | Generation/rule identifiers belong in metadata, not repeated histories | None |

"Tables/details" includes cell panels and on-demand API queries; it does not
mean all fields must be shipped in the initial table response. Project only the
requested columns, paginate species, and fetch lengthy provenance when needed.
Tree arrays and baseline evidence stay server-side. If a selected map needs
coverage counts, include the smallest sufficient aggregate with that mode;
do not require per-species payloads for colouring.

### Prevent accidental schema expansion

Current tile metrics are derived from shared `METRICS` definitions in
`builders/coarse_cache.py` and `tiles.py`; the native reducer, coarse Arrow
export and compact frontend arrays also depend on those definitions. Do not
append every new analytical field to that list. Adding a Parquet column alone
also does not make it available in PostgreSQL or the table API: update explicit
export/loader/API projections only for the fields that surface needs.

Keep existing map projections explicit and versioned. Separate species/detail
and analysis schemas from base map metrics and optional mode payloads. Reuse
existing machinery; avoid introducing a generic schema framework. Current
sparse-zero tile encoding is for known integer counts: never truncate continuous
ED/gap values with integer casts or interpret an omitted unavailable score as
zero. New score channels need explicit numeric precision and null/availability
semantics, with error checks before any proposed quantization.

### Payload acceptance gates

- Start with no additional base PMTiles properties or geometry and no eager
  loading of optional score artifacts. Retain the existing coarse/fine switch
  and static-fine opt-in behavior.
- Compare decoded tile properties and base metric projections before/after
  detail-only schema additions. Distinguish expected changed scientific values
  from accidental new fields. Prefer reuse when dependencies are unchanged;
  broad existing fingerprints may need narrowing before detail-only edits can
  avoid a rebuild. Do not promise byte-identical recompilation without checking.
- Measure compressed archive size, representative compressed/uncompressed tile
  bytes and decode time, Arrow bytes/startup memory, and fine response bytes and
  latency. Use matched dense/sparse fixtures and cold/warm views; archive size
  alone does not measure range-request or rendering cost.
- For each optional map mode, report incremental bytes, request count, cached
  memory, calculation time and stale-response behavior. Set numeric budgets
  against the measured current baseline before accepting that mode. Do not
  load one channel for every preset, clade and ecosystem automatically.
- A table/detail-only addition should preserve base map schemas and request
  payloads. Larger private build artifacts can be justified for provenance,
  but must be measured separately from browser-delivered data.

## 7. Implementation sequence and acceptance gates

This is the recommended default order for one implementation stream. Each step
should leave a reviewable result; the topic sections describe its detailed
requirements. Performance investigations may conclude that an optimization is
not worthwhile. Do not make a speculative speedup, every possible UI feature,
or a new default scoring policy a prerequisite for the next full run.

### 1. Freeze scope, output contracts and a measurable baseline

Record the current code, registered sources, scientific profile and known
omissions. Check for newer releases of the already registered inputs early,
before investing in fixtures tied to superseded snapshots. Choose mammals for
the initial dated-tree scope and bounded DNA
presets. Define the separate species/detail and optional map projections from
Section 6, with no new base PMTiles properties or eager map downloads.

Capture effective CPU/RAM/worker/thread/DB limits and per-stage telemetry before
optimizing. Establish current archive/Arrow/fine-response payload measurements.
Request biology-domain review early for unsettled assumptions; implementation
can preserve current descriptive semantics while that review proceeds.

**Exit:** a pinned comparison baseline and explicit build/output scope. This
prevents both performance comparisons against changing inputs and accidental
tile-schema growth as species fields are added.

### 2. Make representative fixtures reusable

Implement or repair verified-sample discovery/reuse and independent census
reuse from Section 10B. Refresh selection under the current row policy rather
than treating the historical v2 sample as representative. Add a small mixed
polygon/point/basin fixture, difficult geometries and a dense list/metric
partition. Preserve the original baseline and receipt identities.

**Exit:** repeated experiments reuse unchanged verified inputs, changed
selection dependencies invalidate them, and preparation cost is reported
separately. This comes before repeated optimization trials to avoid paying
the same sample-preparation cost each time.

### 3. Calibrate spatial semantics and settle coverage limitations

Run same-policy simplified/unsimplified calibration and investigate alternative
rasterization rules on bounded inputs. Review geometry repair separately.
Record accepted assumptions and error limits; do not change any-touch semantics
merely for performance. If selection rules change, refresh Step 2's affected
fixtures before continuing comparisons.

Resolve existing missing basin references with authoritative evidence where
available, or explicitly retain the audited partial-coverage profile. Verify
the already-integrated lineage and IUCN reconciliation repairs and their
cache/API prerequisites. Identify ADM2 coverage/year/overlap limitations relevant
to stewardship before interpreting jurisdiction fractions.

**Exit:** a reproducible scientific reference and explicit unresolved coverage
status. A provisional run must carry those limits rather than claim global
scientific completeness. Scientific acceptance is separate from code completion.

### 4. Optimize measured scan and correct-mode bottlenecks

Investigate archive-level scan concurrency, serial boundary-check work and
DuckDB spills as described in Sections 10A, 10C and 10D. Improve algorithms
before spending on larger machines, and keep all nested work within one shared
resource budget. Retain only changes that preserve exact selected-policy output
and improve representative wall time or resource use.

**Exit:** documented wins or documented no-go results, exact output parity and
bounded memory. Do an initial worker sanity check now; defer final sandbox
allocation selection to Step 9, after new retained evidence and reductions
have changed the workload.

### 5. Preserve spatial evidence and structure species metadata

Implement source-type OR/deduplication through fine and coarse membership
reduction, with measured storage overhead. Add global mapped-range count/area
builders and completeness flags. These denominators are computed from completed
memberships; their implementation must be ready before the full build, but
they do not precede pair generation at runtime.

Audit and retain available assessment dates/scope, trends and threat types.
Implement the shared configurable DNA-rule evaluator and taxonomy-based
genus/family representation, independently of the tree. Keep all new data in
appropriate analytical/species/detail artifacts, outside base map metrics.

**Exit:** the mixed-source fixture preserves provenance and known/unknown
metadata, global denominators reconcile, and the exact existing DNA rule is
reproducible. This is the key step before discarding upstream evidence in a
full reduction; UI work can wait.

### 6. Integrate the mammal tree and EDGE foundation

Register pinned tree/OTT inputs, audited identity mappings and full-relative
baseline evidence using existing receipts/checkpoints and atomic generations.
Replace workstation-specific pilot paths. Integrate ED/EDGE components, method
configuration, uncertainty summaries and eligibility reasons, preserving IUCN
extinction authority and independent genomic-transfer gates.

Make phylogenetic enrichment explicit: its absence must not disable an
existing-score build, while an explicitly requested phylogenetic build must
not silently reuse unavailable/stale results. Use dependency-specific
invalidation so tree/evidence changes do not require polygon filling.

**Exit:** same-input mammal reference results and arithmetic checks are
reproducible; unsafe/unavailable identities remain explicit; stale inputs and
interruption/resume are handled. Passing this gate does not adopt EDGE as a
default priority or validate every source-tree date.

### 7. Add configurable DNA gaps and the table/detail serving contract

Build the compact gap kernel on Step 5's shared evaluator and Step 6's checked
tree/evidence. Recompute withholding and coverage warnings per rule; never
freeze the pilot's 480 withheld species. Include unassessed represented
relatives, keep unsafe joins null, and distinguish valid zero gaps.

Add a bounded scenario cache, parameter validation and the species/detail
export, loader/schema and API projections. Update `openapi.json`, generated
frontend types and clients together. Publish scores, nearest relatives and
availability reasons separately. Validate fixture endpoints in isolation; no
active database load or interface redesign is needed to prove this contract.

**Exit:** independent PD checks agree, the existing rule reproduces the same
input pilot, custom scenarios have measured cold/warm performance, and new
table data does not enter base PMTiles/Arrow/fine arrays.

### 8. Build range/stewardship outputs and the robustness workflow

Finish species/jurisdiction mapped-distribution outputs from Step 5's
denominators, with reviewed boundary and coarse aggregation semantics. Add
fixture-scale range-rarity aggregates in separate artifacts. Implement the
offline robustness comparisons against existing categorical scoring, candidate
DNA rules and calibrated spatial alternatives. Keep ED/EDGE, threat-weighted
DNA gap and range-rarity separate analytical quantities.

**Exit:** fixture fractions and score calculations reconcile; reports give
common-cohort denominators, deterministic ties and hotspot/rank changes.
Builders are ready for full data, without requiring a range-rarity map UI or
large collections of preset channels in every map payload.

### 9. Tune sandbox resources on the final candidate and rehearse publication

Run Section 10E's bounded CPU/worker/RAM/DB-limit experiments only within an
agreed sandbox budget. Include the new provenance, metadata and aggregate work.
Choose simple measured stage settings and report elapsed-time/resource-cost
tradeoffs; more RAM is not a substitute for Step 4's serial-loop work.

Then run the miniature complete pipeline, exercising all source routes,
metadata reconciliation, optional mammal enrichment, lists, existing base map
outputs, separate analytical artifacts and fixture APIs. Verify interruption,
stale-input invalidation, atomic publication and payload constraints.

**Exit:** selected settings with observed headroom, a passed isolated acceptance
generation and a comparison-report template. Use measured fallback settings
if further optimization has diminishing returns; avoid an endless tuning gate.

### 10. Run the authorized full build and inspect its results

The recommended next full build happens here, after Steps 1–9's scoped
acceptance gates and a final source-freshness preflight:

1. Check official release/version metadata for registered IUCN assessments and
   spatial inputs, GoaT, NCBI taxonomy, tree/OTT inputs and boundary sources.
   Record the check time, available release, selected release and any deliberate
   retention of an older compatible snapshot. This refreshes existing sources;
   it does not reopen the deferred new-source integrations.
2. Acquire selected updates through the existing authorized workflow, respecting
   manual/provider access steps. Verify checksums, schemas, identifiers, release
   compatibility and source terms. Do not assume newer assessment, spatial and
   taxonomy files automatically form a compatible set.
3. Invalidate only affected fixtures, crosswalks, identity/baseline audits,
   coverage outputs and aggregates through their normal dependency receipts.
   Repeat the relevant Steps 2–9 checks on changed inputs before expensive
   execution. Reassess resource sizing if workload size/distribution changed
   materially; preserve old benchmark results as old-generation measurements.
4. Pin the validated source manifest and resolved generations for the entire
   run. Report any unavailable update explicitly. Do not mix releases or advance
   sources partway through the build; later releases belong to the next update.

Then generate retained source provenance, memberships, range
denominators/stewardship, available assessment metadata, mammal ED/EDGE and
default DNA-gap species outputs, existing base map products, and only the
separate optional aggregates explicitly included in the build scope.

Review species/score coverage, DNA witnesses and warnings, spatial omissions,
ecosystem gaps, robustness summaries, resources and payload sizes against the
pinned baseline. Do not infer biological trends from changed processing.
Changed input generations require relevant revalidation, not edited receipts.
Building, loading a database and activating serving remain distinct actions.

**Exit:** a validated full generation and an explained comparison report, or
specific failed gates with affected outputs left unpublished. Global execution
and any subsequent load/activation require their existing authorization.

### 11. Expose tables/details, then optional map modes

Use validated species and cell data to prepare the required A/B/C mocks and
wait for selection before changing real UI or interface copy. Implement table
columns, scenario controls and details first; these can ship without increasing
PMTiles size. Check search/filter/selection, reset, errors, unavailable states,
and relevant keyboard/mobile paths.

Next, add one optional map mode at a time through Section 6's separate
coarse/fine data path. Benchmark payloads and calculation latency before
expanding presets. Preserve stable domains, matching scenario identities and
stale-response handling. Verify the actual coarse/fine path, not just tables.

**Exit:** consistent selected scenarios across the surfaces that support them,
with a measured incremental payload cost. Neither UI work nor a new map score
requires rerunning polygon coverage: rebuild only affected downstream products.
Mocks may be prepared earlier if useful, but UI selection must not block the
headless pipeline build or require exposing restricted data.

### 12. Expand scope only after the first capability is validated

Expand to additional already-acquired clades after reviewing identity coverage,
dates/root semantics, baseline completeness, tree sensitivity and performance.
Adopting new default scores remains a separate scientific decision. A later
budgeted sequencing/locations portfolio must recompute shared marginal gains;
the current threat-weighted greedy pilot is a heuristic, not a proven optimum.

Keep the acquisition-dependent extensions and H3-tree redesign deferred under
Section 11. Improve synthetic/bring-your-own-data packaging as a separate release
task when sharing is scoped; it need not block an authorized private build.

### What can be postponed without wasting the full spatial run?

Do not postpone **source-provenance retention, accepted spatial policy, or
compatible membership publication** if their omission would require revisiting
raw geometry. Have the range/metadata/score builders ready to make this run
useful. In contrast, table rendering, optional score endpoints/channels, broader
clade scoring, portfolios and release packaging can evolve from retained
validated artifacts. They need only their own downstream recalculation.

Section 8 is the pre-run checklist for this sequence, Section 9 defines checks,
and Section 10 supplies the investigation detail. Their document position is
not an instruction to do validation or performance work last.

## 8. What to settle before the next full run

The full run should generate a clear new capability, not serve as the first
integration test. Before launching it:

- **Choose scope:** supported clades, accepted tree/root policy, DNA presets,
  provenance storage and range denominators. Explicitly list table/detail
  fields, optional map aggregates and base map properties; the default is no
  new base properties. Keep descriptive output acceptance separate from
  adoption of default priorities. Defer new acquisition-dependent extensions.
- **Check for updated registered data:** perform Step 10's freshness preflight,
  acquire/validate selected releases through authorized paths, and rerun affected
  fixture and identity checks before pinning the full run's manifest. Record
  checked/selected versions and reasons for retaining older snapshots.
- **Calibrate spatial approximation:** compare current-v3 simplified coverage
  with a same-policy unsimplified reference, including fragmented islands,
  holes, large ranges and antimeridian cases. Report per-species added/omitted
  cells and hotspot changes; agree acceptable error. Review geometry-repair
  effects separately from simplification effects.
- **Refresh representative validation:** the saved timing fixture predates v3
  sampling, and point/HydroBASINS work is absent from its projected benchmark.
  Exercise all three source routes through metadata, memberships, metrics and
  serving artifacts using small isolated inputs. Verify changed evidence
  invalidates dependent outputs and completed work resumes correctly.
- **Resolve or explicitly retain partial basin coverage:** the documented audit
  records 4,535 missing geometry IDs affecting 7,523 basin–species relationships.
  Seek provider-matched geometry or authoritative mappings. If using the
  existing partial-basins policy, retain omissions and affected-species status;
  those counts do not quantify omitted area or lost species.
- **Verify existing repairs:** the 4 October integration audit records GoaT
  lineage/ancestor compatibility and automatic IUCN metadata reconciliation in
  both local and sandbox workflows. Exercise the current shared stages and
  cache/API prerequisites rather than repeating the old repairs manually.
- **Prepare comparison reporting:** record assessed/spatial-only/unscored
  populations, ecosystem gaps, DNA evidence/baseline changes, score coverage,
  membership changes and top-rank/hotspot overlap on common cohorts. Include
  denominators, source versions, tie handling and explanations of exclusions.
- **Investigate runtime and resources:** follow Section 10 to separate reusable
  benchmark preparation, serial coverage work, storage bottlenecks and worker/
  memory sizing. Record a measured sandbox configuration rather than assuming
  that more RAM or workers will shorten every stage.

Use focused fixtures and targeted checks first. A build passing structural
validation does not establish scientific completeness. Source acquisition,
global builds, cloud jobs, loads and serving activation require authorization
for those operations; this document does not authorize them.

## 9. Verification that matters

Extend the existing [gap tests](../../tests/test_phylogenetic_gap.py) and
pipeline fixtures rather than creating a parallel validation system:

- Compare marginal gaps with independent branch-union PD for every baseline
  on small trees. Check empty/full baselines, zero-length branches, polytomies,
  deterministic ties and the rule that adding representation cannot increase
  a gap on the same tree.
- Test evidence thresholds, unknown values, conflicting/unsafe identities,
  unassessed represented relatives and rule-dependent withholding. Never let
  a tree-only name link grant genomic-transfer permission.
- Validate ED/EDGE outputs against the existing independent arithmetic checks;
  record tree/risk sensitivity separately from calculator agreement.
- Compare cached/default and dynamic results field by field on identical inputs.
  For maps, verify coarse distinctness, cross-source deduplication, null-coverage
  counts, profile isolation, and rejection of stale scenario responses.
- Check provenance OR/deduplication, range denominators and jurisdiction fractions
  on a mixed-source fixture. Verify table/detail fields survive explicit serving
  projections without appearing in base tiles, Arrow or fine metric arrays.
- Apply the payload gates in Section 6 to every proposed map channel. A new
  table column is not a reason to expand the metric schema.
- Benchmark evidence evaluation, tree passes, result serialization and spatial
  aggregation separately. Record nodes/tips, memberships, workers, hardware,
  cold/warm state, memory and concurrent-request behavior. Set an explicit
  interaction budget from measured results before committing to global support.

## 10. Performance investigations and sandbox resource sizing

### Starting observations and what they do not establish

The run observations supplied for this plan are approximately **4 minutes of
source scanning**, **15 minutes of repeat benchmark preparation potentially
avoidable by sample reuse**, and correct-mode coverage spending most of its
time using roughly **one CPU core**. No evidence of running out of memory was
reported. These are investigation inputs, not timings independently reproduced
for this document, guaranteed savings, or global-build estimates.

Locate the corresponding run report, code/profile identity, timing boundaries
and effective resource limits before comparing improvements. In particular,
identify what that run called "correct mode" and its exact coverage contract;
do not equate it with a faster approximate profile. Distinguish CPU time, wall
time, active jobs and the single-job tail.

### A. Verify and scan independent archives concurrently

The current [benchmark source scan](../../ark_pipeline/cli/benchmark_pipeline.py)
iterates archives serially, deeply verifies each archive and performs its census.
Investigate bounded archive-level concurrency under a shared CPU, memory and
storage budget, rather than giving each archive an independent full-size pool.
Each task should return its counts, exclusions and validation result; reduce
them deterministically and publish a complete census only after all succeed.

Measure hashing, archive validation/decompression and census separately. Check
whether concurrency helps on the actual sandbox/local storage or merely causes
competing reads. Include a large-archive tail and preserve per-archive progress,
row reconciliation, checksum checks and failure behavior. Reuse compatible
verification/census receipts where safe, while retaining an explicit cold-scan
benchmark; cached work is not evidence of faster cold source scanning.

**Acceptance:** identical archive identities, size-bin totals and exclusions,
bounded memory, and a measured end-to-end scan improvement over the same inputs.

### B. Reuse the verified sample across repeated benchmark runs

The existing harness already accepts `--sample` with companion metadata, checks
source hashes and distinguishes `--rebuild-sample`. Investigate why the reported
run repeated preparation: sample creation, input staging/transfer, verification
and full source census are different costs. A fixture generated inside an
isolated run must be discoverable or explicitly reusable by the next run.

Define a durable verified-sample identity from source content, row-selection
rules, stratification/size-bin definitions, seed, sampler version and output
checksums. Keep derived subset selection parameters identifiable too. A change
to worker count, DuckDB memory or coverage-kernel implementation alone should
not force resampling if the selected population and stored geometry are still
valid. Conversely, do not reuse the historical v2-selected sample as if it
represented newly eligible v3 rows.

Cache the population census separately when compatible so a saved sample does
not inadvertently trigger the same expensive population work on every replay.
Pin immutable sample artifacts across sandbox jobs; never rewrite receipts or
point benchmarks at production outputs to obtain reuse. Record which steps were
reused and keep fresh timing results in separate benchmark generations.

**Acceptance:** identical selected row IDs, geometry and coverage results for
unchanged selection inputs; changed sources/rules invalidate reuse; reported
preparation time saved is measured separately from kernel execution time. Do
not claim all 15 minutes disappear until their actual components are accounted for.

### C. Improve correct-mode boundary checks and parallelism

Profile the exact-mode coverage path before changing it: candidate generation,
boundary intersection checks, geometry preparation/repair, simplification,
deduplication, serialization and coordination. Determine whether one-core usage
comes from serial Python work, a serial native call, insufficient task splitting,
I/O, or one final large geometry after other workers finish.

First reduce avoidable boundary-check work using spatial rejection/containment
proofs, prepared geometry or native batching where exact equivalence is proven.
Then partition independent remaining work into bounded jobs. Use the existing
shared spatial budget for both polygon workers and nested tile work; avoid
workers multiplied by unrestricted native threads. Measure scheduling and data
transfer overhead rather than assuming finer tasks are faster.

Preserve the selected closed-boundary any-touch semantics, CRS handling,
antimeridian/holes, polar/pentagon cases, distinct species membership and repair
policy. Do not substitute centroid tests, simplification or H3-parent geometry
as an unreviewed shortcut. Validate exact per-species cell sets and resulting
lists/metrics against the unchanged correct-mode baseline before comparing time.

**Acceptance:** zero unexplained membership differences, bounded memory and a
measured wall-time improvement on both representative and difficult geometries.
Higher CPU utilization alone is not success.

### D. Check DuckDB spills before increasing memory limits

The supplied run calls out a **4-GB DuckDB limit**. Confirm its effective value
and scope in that run: it is not a universal setting. For example, the current
[full sandbox job](../../sandbox/coreweave/scripts/full_pipeline_job.py) passes
`2GB` through the benchmark plan. Capture environment, CLI and connection-level
overrides rather than inferring limits from requested sandbox RAM.

Record temporary-disk high-water bytes, read/write activity and available query
profiling/spill indicators for sort/group/join stages. Empty scratch after
completion does not prove there was no spill. Compare larger limits only on
the same workload and after observing pressure or a plausible affected operator.
Record process-tree and cgroup peak memory: DuckDB's limit does not cap Python,
Arrow, geometry libraries, compiler processes, filesystem cache or the entire
sandbox, and several worker connections can each have their own limit.

Extra RAM can permit more workers, larger bounded batches or less spill. It
does not accelerate a serial loop by itself. Retain headroom for result
persistence and do not interpret a large reclaimable file cache as proof of
application memory exhaustion.

### E. Find useful sandbox CPU, worker and RAM configurations

Reuse the existing
[profiling launcher](../../sandbox/coreweave/scripts/profile_benchmark.py) and
[job telemetry](../../sandbox/coreweave/scripts/profile_benchmark_job.py).
Some current launcher/job settings request 8 CPUs/24 GiB and use fixed worker
values; treat them as starting configurations to investigate, not an optimum.
Check automatic sizing against effective cgroup CPU quotas, cpusets and memory
limits; host-wide `os.cpu_count()` or available RAM may overstate sandbox capacity.

Run a small staged experiment rather than a full Cartesian sweep:

1. Freeze a verified current-policy sample, code, correct-mode profile and
   stage outputs. Include difficult polygons and a dense aggregation partition.
   Establish cold/warm loading and scratch-device conditions.
2. Within one allocation, test a few worker counts up to the effective CPU
   budget, initially with one native/DB thread per concurrent worker. Measure
   scan, coverage, lists and metrics separately; their best settings can differ.
3. Hold CPU and workers fixed while testing a few RAM/DB-limit/batch choices.
   Change one variable at a time first; test selected combinations only where
   memory actually limits concurrency or causes spill.
4. Compare a few available sandbox CPU/RAM allocations using the best plausible
   stage settings. Keep total nested threads and per-worker memory within the
   allocation. Compare summed stage wall time and a representative combined
   replay, not just one favorable kernel.
5. Repeat the leading configurations in alternating order. Select a simple
   default and, if useful, one faster configuration with an explicit resource
   tradeoff; avoid a new adaptive scheduler unless measurements justify it.

Record requested/effective CPUs and RAM, stage worker/thread counts, DB limits,
batches, wall time, CPU seconds, throttling/pressure, peak cgroup/process memory,
scratch high-water bytes/I/O, output parity, and persistence overhead. Compare
elapsed time and billed compute/resource cost where available; do not choose
the largest allocation merely because one stage is faster. Publish the chosen
settings, observed headroom, diminishing-return point and remaining serial work.

All performance work stays in isolated outputs and preserves Section 6's map
payload constraints. This section authorizes no cloud provisioning or benchmark
execution by itself; scope and resource spending must be agreed before running
the experiment. This document update launches no jobs and changes no defaults.

## 11. Deferred work and boundaries

GGBN holdings, new bird distributions, protected-area/KBA inputs, climate/cost
layers and other substantial new acquisition integrations are deferred. They
are not prerequisites for the existing-input work in this plan. Do not add
their adapters, dependencies, speculative tile properties or placeholder
payloads in this milestone. Likewise, defer a new assembly-quality feed;
unsupported custom rules remain unavailable until separately scoped.

Keep H3-tree replacement out of this milestone. The
[optimized compatible pilot](../performance/h3_tree.md)
measured only a 1.9% reducer-path gain before cover-production costs, smaller
than observed baseline run variation. It does not justify coupling a spatial
representation redesign to score integration.

Preserve scientific review of weights, Data Deficient handling, evidence-stage
meaning and tree uncertainty as explicit decisions. Do not multiply EDGE and
DNA-gap scores together without a separately justified objective. Reuse the
current stack and generation machinery; defer generic formula builders, a new
distributed execution system and browser-side global trees until measurements
show a need.

All assessment-derived scores, evidence, tree-linked serving products and
reports remain subject to [the data policy](../../DATA_POLICY.md). Local
availability does not authorize a public endpoint or downloadable data pack.
Use synthetic fixtures for publicly shareable implementation examples.

This plan was checked against current code, documentation and saved audits.
It introduces no score calculation, timing measurement, scientific sign-off,
pipeline run or application change.
