# Ark-IV roadmap

This is a research and product agenda, not an implementation specification.
Priorities express proposed sequencing, not authorization to acquire data, change
scientific inputs or deploy anything. Implemented behavior is not automatically
scientifically validated. Historical private measurements and execution records
are not public validation evidence.

**Status boundary (main at this revision).** The existing pipeline, range-based
map and species table provide the starting point. Optional cleanup branches for
[metadata reconciliation](https://github.com/karlaugustnybo/Ark-IV/pull/3),
[hierarchy experiments](https://github.com/karlaugustnybo/Ark-IV/pull/6),
[managed serving/profiles](https://github.com/karlaugustnybo/Ark-IV/pull/11),
[interface behavior](https://github.com/karlaugustnybo/Ark-IV/pull/12) and
[dated-tree research](https://github.com/karlaugustnybo/Ark-IV/pull/13) remain
unmerged. Their capabilities are not available by cloning main. The sections
below explicitly identify these optional prototypes; future scientific surfaces
remain proposals even where supporting calculations exist in a prototype.

The [methodology](../methodology.md) describes the actual scientific and
implementation decisions across the pipeline. The [assumption audit](reference/roadmap_assumption_audit.md)
records 20 findings with evidence and acceptance criteria. Implemented behavior
is not automatically a scientifically validated assumption.

## Priority and status guide

- **P0 — Foundation:** Required for trustworthy results or for other features to work correctly.
- **P1 — High value:** Important improvements to the main conservation-analysis workflow.
- **P2 — Enhancement:** Useful additions that can follow the core work.
- **P3 — Nice to have:** Polish, outreach, or longer-term ideas.
- **Investigation:** The requirements or underlying assumptions still need to be verified.
- **In progress:** Implementation or validation is partly complete, with remaining work identified.
- **Proposed:** The feature is understood at a high level but has not been implemented.
- **External:** Progress depends on feedback, funding, or another outside contribution.
- **Completed:** The feature is already present in the current application.
- **Retired:** The item belonged to the prototype and is no longer part of the supported architecture.

### Recommended sequence after the 8 September 2026 revision

Work follows three roughly parallel tracks. Scientific validation outranks new
acquisition and score surfaces.

1. **Scientific validation.** Calibrate simplification and rasterization, then run
   hotspot robustness across alternative rules and weights. Request independent
   biology review in parallel: weights, Data-Deficient treatment, population trends
   and new score adoption depend on it. Reviewer turnaround may be the longest lead time.
2. **Reproducibility.** Propose a bring-your-own-data release profile with a synthetic
   pack, validate municipality gaps/years/overlaps, and run a full IUCN build only
   when authorized. Integrate pinned dated-tree identities, descriptors and DNA gaps
   without treating pipeline wiring as acceptance of new default scores.
3. **New scientific surfaces.** Investigate range-rarity/stewardship, separate
   genome-status and lineage gaps, provenance/robustness, a shared dated-tree score,
   and GGBN feasibility. Protection/KBA context, threat attributes, complementarity
   and interface polish follow the core validation.

## 1. Data correctness and prioritisation

### Validate H3 aggregation assumptions

**Priority:** P0 · **Status:** Boundary-contact repair, taxonomy validation and scientific calibration pending

The intended resolution-7 policy is polygon any-touch membership, including
edge/vertex contact. Main still has the known SPA-002 boundary-contact omission;
the repair is available in optional [PR4](https://github.com/karlaugustnybo/Ark-IV/pull/4),
not merged main. Resolution 3 uses distinct parents of included fine cells.
Neither is a point-observation or whole-cell occupancy
claim. Large v3 ranges may be simplified before intersection; 2.5-degree
computational tiles are separate from that approximation. The antimeridian
hole-placement bug is fixed.

Remaining P0 work includes the boundary-contact repair and scientific calibration
after the geometry fixes: measure per-species
omitted/added cells and local effects against an unsimplified reference with the
same v3 row policy, agree acceptance limits, and rebuild the timing fixture to
include v3-only eligible rows. Review planar repair-area thresholds and the
interpretation of coarse boundary filtering. See audit A01–A05 and A15.

### Verify complete and lossless data use

**Priority:** P0 · **Status:** Investigation

Selected pair/list relationships are reconciled, but complete source-field
preservation is not established. The methodology now records the principal
field transformations and omissions. Seasonal and spatial-evidence distinctions
disappear in species unions; several assessment/genomic fields remain unused.
Point and HydroBASINS memberships now enter the common pair pipeline, with
source-level receipts and decision summaries, but their coordinates/basin IDs
do not survive into serving lists. Finish a release-specific column/row lineage
audit and distinguish acquired, integrated, and served fields. See A06 and A18.

### Refine missing-DNA priority by family coverage

**Priority:** P0 · **Status:** Implemented rule; release-specific validation and evidence-stage scoring pending

The rule searches all qualifying GoaT species, validates family/genus names
against unique rank identities in the registered NCBI taxdump, qualifies lineage
names by family and normalized kingdom, and never treats extinction as genomic
evidence. Release-specific acceptance and ambiguous-name outcomes require their own private validation report.
Separate sample, project, assembly, quality and resampling score states remain an
open biological/product choice.
See A09–A11.

### Validate taxonomy transfer, DNA evidence, and priority weights

**Priority:** P0 · **Status:** Investigation

All automatic crosswalk branches now share the minimum-agreement and
maximum-conflict guard, and the downloader preserves generic field provenance.
Measure match precision independently of coverage. Review the broad
sample/project/assembly evidence predicate and the `has_ebp_criteria_evidence`
criteria-text check against a versioned assembly
standard. Keep IUCN Data Deficient separate from missing GoaT information.
Validate score objectives and ranking sensitivity before treating configurable
weights as conservation recommendations. Migrate the SIS-valued
`gbif_accepted_id` compatibility alias. See A07–A13.

The optional metadata work preserves resampling markers alongside evidence stage.
A dedicated resampling score/filter remains to be designed: a resampling request
must not erase evidence of an existing qualifying assembly. Keep stage-specific
missingness visible and measure ranking sensitivity before score adoption.

### Add population trends

**Priority:** P1 · **Status:** Proposed

Population trends are present in the registered assessment table but are not
currently used by the scoring model. Display increasing, stable, decreasing or
unknown with assessment date/scope, and review any score contribution before
adding it. See A18.

### Reintroduce EDGE enrichment as an optional source

**Priority:** P3 · **Status:** Proposed

Keep EDGE out of the required update pipeline while the core IUCN spatial and
assessment workflow is rebuilt. Reintroduce it later as optional enrichment for
the permitted source cohort, with a reviewed identifier join, source
version, and redistribution permission; its absence must never block a global
build. Computed distinctiveness scores that do not use the ZSL lists are a
separate track, proposed in
[Compute Ark-IV EDGE-style scores from a dated global tree](#compute-ark-iv-edge-style-scores-from-a-dated-global-tree).

### Add IUCN point data

**Priority:** P1 · **Status:** Integrated; evidence-type display and full-run validation pending

The IUCN point archives, HydroBASINS relationship tables, and HydroBASINS base
geometry are acquired, versioned, schema-checked, and included in the derived
H3 membership pipeline. Eligible points map vectorially to their containing
resolution-7 cell. Basin relationships are deduplicated out of core; each
referenced v1c basin is covered once and reused across species tables. Their
pairs are globally deduplicated with polygon pairs and therefore flow through
the existing metrics, API, tiles, map, status view, and run dashboard.

The integrated serving relation deliberately does not preserve evidence type.
A future provenance-aware map layer is still needed if users must distinguish
recorded point locations, basin distributions, and broader range polygons. Run
and review an authorized full global build before treating integration as a
validated release dataset.

### Add bird data

**Priority:** P2 · **Status:** External

Request access to the bird dataset linked from the IUCN website but distributed by a separate provider. Confirm its licensing and handling requirements before downloading it, then document how it complements the current sources and expose relevant bird-specific attributes in the map and table.

### Create a bring-your-own-data release profile

**Priority:** P0 · **Status:** Proposed

Keep the complete global map, species highlighting, cell details, and table
features in the public CC0-1.0 codebase without bundling restricted inputs or
generated serving data. A normal clone should run immediately with a small,
clearly labelled synthetic data pack, while authorized users can construct the
real global profile from files they obtained directly from the providers.

Build on the validated `just download`, `just update`, and `just start`
workflow. The remaining release profile must:

- identify every required input, version, and official download location
  without automating acceptance of provider terms;
- validate paths, schemas, identifiers, checksums, source versions, and licence
  acknowledgements before starting an expensive build;
- produce a versioned data-pack manifest containing source/build versions,
  checksums, attribution, schema compatibility, and validation results;
- keep all source files and generated Arrow, PMTiles, Parquet, database, and
  report artifacts ignored by Git;
- explain unavailable restricted features clearly and direct authorized users
  to the data-pack setup rather than silently degrading results; and
- support privately distributed prebuilt packs for authorized collaborators
  without using Git LFS or a public download URL.

Treat permission for a full public deployment as a parallel external track.
Send IUCN and ZSL a concrete disclosure of the non-commercial conservation use,
aggregated H3 fields, Arrow/PMTiles delivery, species/cell endpoints, absence of
bulk exports, attribution, and proposed downstream terms. Preserve the complete
feature set while awaiting their answer; introduce a reduced public API only
if the providers require it. The fallback public deployment should use the
synthetic pack, while the authorized local/private profile retains all features.

The real data pack must retain the current optimized serving formats and pass
the established map-performance trace: no uncovered viewport, no resolution
flash, no visible fine-tile loading gap, and no regression against same-session
idle cadence. Live per-user calls to the IUCN API are not a replacement for the
precomputed spatial pipeline.

### Complete global municipality coverage

**Priority:** P1 · **Status:** In progress

Global ADM2 acquisition/installation and country-scoped catalogues now support
the configured country snapshots. The Denmark,
Germany and Sweden preview remains a code-only fallback. Remaining work is
coverage-gap, represented-year, overlap and disputed-boundary validation, with
consistent rebuilt memberships, filters and serving generations. ADM2 does not
mean municipality in every country. See A16.

## 2. Map analysis and interaction

### Filter cells by jurisdiction

**Priority:** P1 · **Status:** Core filtering exists; protection context proposed

The core jurisdiction filtering works: users can search and select up to 30
boundaries per framework, every H3 cell whose polygon intersects a selection
is included, non-matching cells are removed from static and dynamic map layers
without drawing a separate boundary overlay, the colour scale uses the
selected resolution-3 subsets, and the same spatial scope filters the species
table. Municipalities and terrestrial ecoregions are selectable, and selecting
a country automatically includes the matching EEZ ocean scope, while the
internal EEZ catalogue only feeds membership and selected-cell context.

The missing part is the protected-area framework. No Protected Planet adapter
code exists yet — the boundary manifest only lists the protected-area source
as `source-required`, which renders a disabled filter card. Completing it
requires a WDPA/OECM licence review with acquisition kept separate from
download automation, plus a builder that joins protected-area memberships
into the boundary-pair pipeline so cells, colour domains, and the species
table treat protected areas like every other framework. That clearance is also
the prerequisite for the conservation context in
[Add protected-area and KBA context](#add-protected-area-and-kba-context).

### Search for H3 cells on the map

**Priority:** P2 · **Status:** Proposed

Species search and map highlighting are complete. The remaining work is to let
users enter a specific H3 index, validate it, navigate to its centre, select the
cell, and show a clear error for invalid or unavailable indexes. Demoted from
P1 on 8 September 2026 as a navigation aid rather than an analysis capability.

### Switch to resolution 7 at a lower zoom level

**Priority:** P2 · **Status:** Proposed

Evaluate switching from resolution-3 to resolution-7 cells sooner while zooming in, so users can see and compare more resolution-7 cells at once. Choose the transition threshold based on readability, rendering performance, and consistency with species highlighting and cell selection. Demoted from P1 on 8 September 2026 alongside the cell search: a readability and rendering refinement, not a change to what the map can answer.

### Add natural-breaks and logarithmic colour scales

**Priority:** P2 · **Status:** Optional unmerged interface prototype; fine-distribution investigation

The optional interface branch explores linear, logarithmic and Jenks modes in the
paint-bucket menu. Its proposed defaults are log with offset 1 (0.01–1000),
contrast gamma 1 (0.1–5), Jenks 5 classes (2–9, bounded by distinct values), and
80% transparency (0% opaque to 100% invisible). Increment/decrement arrows,
hover/focus help and compact inline parameters should remain usable. Explanatory text,
parameter expansion and the bottom-left legend were intentionally removed in
that prototype; this roadmap does not request their return.

Keep log independent of Jenks, preserve the prototype's lowest-colour treatment
of non-positive scores, and apply
modes consistently to coarse/static/dynamic layers, ecosystems, weights,
normalization and jurisdiction filters. Gamma above 1 should emphasize higher
scores under the documented transform `log(1 + score / offset)`, normalized to
the score domain and raised to gamma; validate endpoints and defaults. These are interface choices, not a new scientific score.

The prototype computes exact breaks from matching coarse snapshot cells with
weighted duplicates in a worker; changes in weights, ecosystem, normalization,
filters or class count trigger updates, panning does not. The coarse reference
also colours fine cells with endpoint clamping; empty/unavailable coarse data
falls back to linear. This is not an estimate of the fine distribution.

Investigate a compact resolution-7 sidecar of sampled metric vectors and off-main-
thread weighted classification. Per-metric histograms lose correlations needed
for arbitrary weights; fixed presets could instead have exact precomputed breaks.
Compare candidate sample sizes/strategies with exact or larger reference populations
across ecosystems, weights, normalized/raw scores and representative filters.
Measure break error, class agreement, visual/rank stability, bytes, latency and
reproducibility before choosing a sample size. Breaks must stay stable while panning.

### Show occupancy and knowledge limits in the map

**Priority:** P2 · **Status:** Proposed

This proposed explanation builds on the optional interface caveats. The resolution-7 grid invites a stronger impression of spatial certainty than
the underlying evidence carries: polygon contact means potential mapped
occurrence, not confirmed occupancy, and fine hexagons do not gain biological
resolution that IUCN range products, point records, and the taxonomic
crosswalk do not have — map resolution and information resolution are
different quantities. State that distinction in cell
tooltips and detail panels instead of leaving it in the
[methodology](../methodology.md), and show each cell's knowledge limits from
[Expose spatial evidence provenance in cells](#expose-spatial-evidence-provenance-in-cells)
next to its conservation score, so a poorly linked cell reads as sparse
knowledge rather than as a confident priority. Keep caveats compact enough to
respect the established tooltip size and latency targets, apply the same
wording across coarse, static, and dynamic layers and jurisdiction-filtered
states, and track the occupancy-interpretation language agreed under
[Validate H3 aggregation assumptions](#validate-h3-aggregation-assumptions);
see A01.

### Show the components of the priority score

**Priority:** P2 · **Status:** Proposed

The priority score multiplies per-species threat and DNA weights before the
cell sum, so two cells with the same score can differ in what drives them:
many threatened species with adequate genomic resources, few species
weighed heavily on both dimensions, or sheer species richness adding many
low-weight contributions. A research question motivated by the related-work survey is how to keep
components independently interpretable. Users should be able to ask whether a cell is driven by
threat, by representation gaps, by richness, or genuinely by both. In cell
details and tooltips, present the joint threat × DNA distribution already
retained in the 30-bin serving counts as readable composition — per-cell
species counts and score shares by bin, plus headline counts of species
high on threat or on DNA gap irrespective of the other dimension — across
static and dynamic layers and jurisdiction
filters. This is a presentation change over existing serving data, reviewed
alongside the colour-scale normalization under
[Add natural-breaks and logarithmic colour scales](#add-natural-breaks-and-logarithmic-colour-scales);
respect the established tooltip latency targets.

### Add a high-resolution map image download

**Priority:** P2 · **Status:** Proposed

Add a download-image button to the map that exports the current map view as a retina-resolution image. The export must contain the complete rendered map, including the background map and active data overlays, but exclude all buttons, tooltips, panels, and other interface controls. Keep the button disabled while map tiles or layers are still loading, and only capture the image after the background map and overlays for the current view have finished rendering at the export resolution.

## 3. Interface and navigation

### Unify shadows, animations, menus, and component styling

**Priority:** P2 · **Status:** Proposed

Apply a consistent visual and interaction system across the application. Standardize elevation and shadow levels, animation timing and easing, menu opening and closing behaviour, hover and focus states, spacing, borders, and control styling. Menus should support keyboard navigation, close with Escape or an outside click, behave consistently across screen sizes, and respect reduced-motion preferences. Reuse shared design tokens and component patterns so new and existing interfaces feel cohesive without obscuring content or slowing interaction.

### Explain unavailable detailed species lists when raw data is absent

**Priority:** P1 · **Status:** Proposed

Add a small error/explanation screen for features that require the generated resolution-7 cell/species partitions when those files are unavailable, as they will be after a normal Git clone because generated datasets are intentionally kept outside the repository. Show the screen only when a user requests a dependency-backed feature, such as the full species-detail table for a selected resolution-7 cell. The global map, tooltips, aggregate conservation metrics, and any other features that do not use the raw partitions should continue to work normally and should not display the warning.

### Optimize cell-table loading after map selection

**Priority:** P1 · **Status:** Proposed

Investigate cell-details latency from map selection; no public baseline or latency regression is established here. Profile the full path from cell selection through data lookup, API response, and table rendering, then optimize the bottlenecks so the table becomes usable promptly. Measure cold and repeated selections for cells with small and large species lists, and record before/after loading times while preserving complete, correct selected-cell results. Show immediate loading feedback while the table is being fetched.

### Improve the species table

**Priority:** P2 · **Status:** Proposed

Review the main species table and the cell-details table on the map page for layout, sorting, filtering, pagination, responsive behaviour, and clear presentation of threat, DNA, and priority values. Give both tables consistent styling and add the relevant shared features while retaining controls specific to each context.

## 4. Documentation and communication

### Add map images to the README

**Priority:** P2 · **Status:** Proposed

Include current screenshots that demonstrate the map, controls, cell details, and overall visual result without requiring readers to run the application first.

## 5. Research, review, and sustainability

### Decide how to serve detailed resolution-7 species lists

**Priority:** P2 · **Status:** Investigation

The compact resolution-7 aggregate partitions are sufficient for rendering the global map, tooltips, and conservation metrics. The separate raw cell/species partitions are only required when a user selects a resolution-7 cell and requests its full species-detail table. The current metadata completeness check also compares the raw and aggregate partition sets, but that dependency could be replaced by a small build manifest.

Measure list-data size, density, compressed bytes per relationship and list
repetition on authorized representative partitions. Do not assume integer-width
changes will dominate compression, or publish source-derived census results
without the separate release review.

Before production hosting, decide whether full per-cell species lists are essential. If they are optional, omit the raw partitions and retain the complete map and aggregate-statistics experience, reducing deployed list storage. If they are required, benchmark representative low-, medium-, and high-diversity partitions before choosing among these exact-data serving approaches:

- Recompress the Parquet partitions with Zstandard for a relatively simple, compatible reduction.
- Store unique species lists once and map cells to list identifiers; measure repetition across regions and diversity classes.
- Investigate spatial or hierarchical delta encoding, storing only species additions and removals between related cells.
- Keep the partitions in object storage and retrieve the relevant base-cell partition on demand, reducing local server storage without reducing total stored data.

Any replacement must preserve exact selected-cell results and be measured for lookup latency, compressed size, memory use, build time, and operational complexity. Keep these generated datasets outside Git rather than committing them to the application repository.

Demoted from P1 on 8 September 2026: the decision gates only a funded
production deployment, which is itself an external P2 item, so the release
profile and the P0 validation work precede it.

### Identify new data insights

**Priority:** P2 · **Status:** Investigation

Explore the combined datasets for additional conservation questions, useful
derived metrics, and patterns that could become new views or features. Define
each insight as a concrete research question before implementation.

### Survey related work and scientific lineage

**Priority:** P2 · **Status:** Investigation

Survey the scientific lineage without confusing species-level reference-genome
representation with measured within-species genetic diversity. The proposed
positioning is: observed diversity and knowledge gaps → prediction where direct
evidence is sparse → protection-gap prioritization → Ark-IV as a data-acquisition
layer asking where better genomic information deserves priority. That is an
authorial hypothesis to assess, not a claim that Ark-IV measures genetic diversity.

- [Miraldo et al. 2016](https://doi.org/10.1126/science.aaf4381) is an anchor for
  mapping genetic knowledge. Investigate its separate taxonomic-coverage/sequence-
  availability axes and Figure 2 as a precedent for the knowledge layer. Examine
  its heterogeneous-data/georeferencing workflow and regional bias before carrying
  that analogy to finer Ark cells.
- [Theodoridis et al. 2020](https://pmc.ncbi.nlm.nih.gov/articles/PMC7244709/)
  examines evolutionary history, climate and mammalian genetic diversity. Evaluate
  how phylogeny versus richness predicts diversity, how low-coverage-cell exclusion
  changes model strength, and the limits of mitochondrial markers. This motivates
  testing a phylogenetic gap rather than presuming it is biologically superior.
- Investigate Schultz et al.'s *Gaps in the global protection of terrestrial genetic
  diversity* (original proposal cited the 2024 bioRxiv manuscript,
  DOI `10.1101/2024.08.23.609342`). Its current bibliographic/version status and the
  detailed interpretation remain **unverified here**. Questions to check include
  separately interpretable diversity/protection components, geographic mismatch
  with richness, and explicit metric shortfalls. Do not use an unverified precedent
  to validate an Ark score.

Relate every finding to provenance/knowledge coverage, occupancy caveats, separate
reference-genome and lineage fields, population-genetic ontology, component display
and the optional diversity raster. Ask whether prioritized sequencing could supply
the genome-wide georeferenced evidence those approaches lack. Keep the narrower
conservation-genomics vocabulary: a general conservation optimizer would also need
protection, cost, complementarity and exposure evidence.

Choose a deliverable (methodology positioning, a README-linked related-work note,
or report citations), verify exact versions, and record whether each finding
changes a roadmap item or remains citation-only.

### Request biology-domain review

**Priority:** P0 · **Status:** External review required

Ask a biology/conservation reviewer to assess the explicit decisions in the
[methodology](../methodology.md) and [assumption audit](reference/roadmap_assumption_audit.md):
range-versus-occupancy interpretation, geometric error limits, taxonomic transfer,
DNA evidence stages, extinction handling, family representation, missingness,
priority weights and ranking sensitivity. No external review was performed by
the code/test audit, and no reviewer has approved those choices merely because
the implementation passes tests.

Raised from P1 to P0 on 8 September 2026: the ranking-sensitivity,
Data-Deficient, population-trend, and Section 6 scoring decisions are all
gated on this review, and reviewer turnaround is the longest lead time the
project faces.

Initial conversations are not a written scientific review and do not satisfy
this gate. No external approval is asserted by this roadmap.

### Secure server-hosting funding

**Priority:** P2 · **Status:** External

Estimate production hosting and data-storage costs, then identify funding or institutional infrastructure that can keep the public application reliably available.

## 6. Scientific extensions from external design review

These proposals sharpen the existing question — where threatened, genomically
underrepresented species co-occur — with rarity, uncertainty, feasibility and
protection. They are not an external scientific approval. Compare them with
systematic conservation-planning ideas (target-based workflows, complementarity,
irreplaceability, KBA context, biobanking and threat-abatement) before adoption.

Keep confidence, material availability and protection separate from the main
score. A possible longer-term niche is mapping preventable loss of unrepresented
evolutionary genomic information with explicit evidence and feasibility:
extinction risk × range irreplaceability × phylogenetic genomic gap. This is a
proposed framing, not a validated composite. Suggested sequence: rarity/stewardship;
separate exact-species and lineage evidence; provenance/robustness; sample feasibility;
protection and a complementary target portfolio.

### Add range-rarity weighting and stewardship fractions

**Priority:** P1 · **Status:** Proposed

Per-cell scoring currently gives every mapped cell of a species the same
contribution regardless of how widespread that species' mapped range is: a
critically endangered species spread across 50,000 cells adds roughly the same
per-cell value as a critically endangered micro-endemic found in ten cells,
and the contrast disappears from the map. Investigate whether explicitly representing this contrast improves sequencing-priority decisions. Add a second global score that
distributes each species' existing
threat × DNA weight over its mapped range instead of repeating it per cell:
`R_c = Σ over species s in cell c of w_s / N_s`, where `N_s` is the number of
mapped resolution-7 cells for species `s` and `w_s` is the existing threat ×
DNA weight. Every species then contributes a roughly conserved global budget,
and range-restricted species dominate locally rather than contributing the same
marginal unit everywhere.

All these values derive from the same validated membership pairs; no
additional acquisition is needed. Expose both
`Priority density = Σ threat × DNA` (the current model) and
`Irreplaceability-weighted priority = Σ threat × DNA / mapped range` as
selectable colour metrics with their own colour domains, consistently across
coarse, static, and dynamic layers and jurisdiction filters; review
display-domain and normalization implications under A17. Report rank changes
against the existing score for each new colour choice.

Also expose per-species distribution fractions: the share of each species'
total mapped distribution that a given cell, ADM2 area, country, or
ecoregion holds. Species range cells are already in the pipeline, so this is
mostly a builders-and-serving change. It answers stewardship-style questions
such as "this ADM2 area contains 47% of the mapped global distribution of these
genomically unrepresented threatened species". See A13 and A17.

### Separate exact-species genome status from lineage representation

**Priority:** P1 · **Status:** Proposed

The current DNA weight collapses several different questions into one bucket.
Split the genomic-gap concept into at least three explicit variables:
`species_reference_status` (does this exact species have an adequate reference
genome?), `nearest_sequenced_relative` (how phylogenetically or taxonomically
distant is the closest adequately represented species?), and `lineage_gap`
(is the genus, family, or order entirely unrepresented or thinly
represented?). Expose these separately in species metadata, the cell table,
and the weights interface rather than summing them back into one flag.

A cheap first version needs no complete phylogeny: derive a categorical
ladder from the existing taxonomy — species absent, genus represented, genus
absent but family represented, family absent, order absent — and compare its
rankings with the current binary lineage flag. The ladder distinguishes a
threatened species whose genus already contains many chromosome-level
assemblies from one that is the sole surviving member of an otherwise
unsequenced family, a distinction that matters enormously for biodiversity
genomics and that the current weight cannot express.

Propose the more precise `reference-genome representation` vocabulary where evidence supports it, while explicitly explaining the existing broader sample/project/assembly predicate. One reference genome cannot answer every question about inbreeding, gene flow or adaptive variation; do not silently tighten the agreed DNA rule or relabel sample evidence as a completed genome. Score consequences for each new
variable wait for independent validation; until then keep them descriptive
columns rather than additional multipliers. Later work feeds the
phylogenetic-gap item below. This item builds directly on the
P0 items [Refine missing-DNA priority by family coverage](#refine-missing-dna-priority-by-family-coverage)
and [Validate taxonomy transfer, DNA evidence, and priority weights](#validate-taxonomy-transfer-dna-evidence-and-priority-weights);
see A09–A11.

### Add a phylogenetic-gap score

**Priority:** P2 · **Status:** Optional unmerged mammal prototype; validation/integration pending

The optional research branch measures rooted marginal PD: branch length of the
represented set plus a focal species minus branch length of the represented set.
Apply threat once. This Ark sequencing-gap score is separate from original
EDGE/EDGE2. It preserves the agreed tree and exact website DNA predicate:
nonempty sample, project-progress or EBP-criteria evidence, or a chromosome/complete
assembly with BUSCO ≥90. Those broad flags do not certify a reference genome.

Include uniquely linked qualifying relatives without assessments in the baseline.
Check a fresh reviewed crosswalk against pinned GoaT/NCBI; tree-only identity never
authorizes genomic transfer. Audit missing positive baseline links, withhold
otherwise eligible same-genus priorities where that evidence is incomplete, and
flag family coverage. Keep null/reason separate from zero gap. Compare old/new
ranks and fixed-budget overlap with explicit tie-breaks; a chosen-species example
is a diagnostic, not biological validation.

For a batch portfolio, recompute branch coverage after every selection so shared
history is not counted repeatedly. Threat-weighted greedy selection is a heuristic,
not a proven optimum. Validate marginal gaps by independent branch unions, website-
rule agreement, source/identity receipts and actual notebook execution. Tree-date
sensitivity of gap rankings is separate from earlier EDGE sensitivity and does not
reopen the agreed source-tree choice.

Review unresolved baseline identities explicitly: merged NCBI IDs with competing
leaves, undocumented OTT names, nonspecies ranks, absent leaves, hybrids, missing
current NCBI species, unspecified species and conflicting identity evidence need
different reasons. Preserve verified IUCN assessment authority over conflicting
OTT extinction flags, raw flags and explicit overrides. A missing dated leaf is a
coverage gap, not an instruction to erase contemporary DNA evidence. This policy
exists in the optional research source; no production map consumes its output.

### Integrate dated-tree identities, EDGE and DNA gaps into the data pipeline

**Priority:** P1 · **Status:** Planned

Turn the investigations into reproducible pipeline stages: registered, pinned
dated-tree/OTT inputs → audited species/tree identities → checked current GoaT
joins → clade EDGE descriptors and DNA baselines → marginal-gap outputs → species
metadata, API, table and map aggregation. The optional identity links and mammal prototypes are starting artifacts, not a completed global score. Global coverage and scientific acceptance remain explicit gates.

- Carry IUCN SIS/assessment IDs, current NCBI TaxIDs, OTT IDs and unique tree-tip
  identities through the build, with source/code/output hashes and audit reasons.
  Preserve genomic-transfer gates independently of tree-only name links; reject
  stale snapshots, competing leaves and unsafe species concepts.
- Reuse the website DNA rule through shared code so the pilot and production
  flags agree. Rebuild the baseline whenever GoaT evidence changes for **any**
  represented species, including unassessed relatives. Reconcile the missing
  DNA-positive baseline identities; preserve incomplete-genus withholding and
  family-coverage flags until those gaps are resolved.
- Publish ED, original EDGE/EDGE2, marginal gap, nearest represented relative,
  baseline status and source provenance as separate fields. Keep unavailable
  scores null with reasons. Validate tree-date sensitivity, clade comparability
  and score units before adopting a new default priority; apply threat only once.
  Preserve IUCN authority for assessed-species extinction status, raw OTT flags
  and explicit overrides; absent branches remain coverage gaps.
- Add explicit score modes and continuous-gap aggregates to the serving metadata,
  API, species table and H3 caches. Verify the existing genus/family mode remains
  reproducible. Explain the DNA rule and conditional coverage in species details.
- Support an optional batch-selection stage that recomputes branch coverage after
  each target, rather than taking a static list that repeatedly counts shared
  branches. Treat its threat-weighted greedy strategy as a heuristic.
- Use memory-bounded tree processing, checkpointed clade jobs and atomic output
  generations. Retain licensed assessment-derived outputs in the authorized data
  profile. Add regression checks for identities, stale inputs, website DNA-rule
  agreement, independent PD calculations, baseline changes and score availability.

Preserve compatible source/code receipts and unresolved values. Identity links do not
authorize genomic transfers. Data publication requires the existing release policy.

Implementation and scientific acceptance are separate milestones. This roadmap
item proposes future pipeline work; the optional pilot remains outside production serving.

### Compute Ark-IV EDGE-style scores from a dated global tree

**Priority:** P1 · **Status:** Optional unmerged research workflow; dataset acceptance pending

The optional [research workflow](pipeline/09_edge_prototype.md) uses pinned Duke reference-engine/tree/OTT inputs,
complete mammal crowns, licensed assessments through reviewed IDs, seeded risk
sampling, conservative current-name/lineage matching, median/sample-tree comparisons
and an original-author R calculator benchmark. It records failed joins, distribution
summaries, reference residuals and source/code/output identity. Arithmetic agreement
on matched inputs is separate from reproducing published rankings, full-ensemble
uncertainty and scientific dataset acceptance. No map weight consumes these outputs.

Retain distinct link routes: direct NCBI/OTT, exact-name/current-lineage, independent
IUCN-name/OTT ancestor membership and documented binomial genus changes with unchanged
epithet. Require compatible higher lineage, unique identities and no separately
assessed-species conflict. Preserve prior links and genomic-transfer flags. Review
unsafe/missing NCBI identities, synonyms, species concepts and names present in OTT
but absent from dated leaves separately. Identifier coverage is not scored coverage.

Use the whole dated clade for fair-proportion ED before joining assessments; a
crosswalk subset changes branch sharing. The original proposal's published-method
reference points are [Isaac et al. 2007](https://doi.org/10.1371/journal.pone.0000296)
and [Gumbs et al. 2023](https://doi.org/10.1371/journal.pbio.3001991).
Preserve separate ED, threat and EDGE-style components. The original-method
comparison should check `EDGE = ln(1 + ED) + GE × ln(2)`, with the LC-to-CR
GE ladder 0–4. For EDGE2, check the terminal branch plus each ancestral branch
weighted by the product of the *other* descendants' extinction probabilities,
not a species' own threat applied twice. The original proposal contrasted the
illustrative probability curve CR 0.97, EN 0.485, VU 0.2425, NT 0.12125,
LC 0.060625 with the pinned engine's curve CR 0.9695, EN 0.4875, VU 0.2373,
NT 0.1269, LC 0.0563. These numbers are a protocol/version reconciliation
question, not a request to replace the agreed engine inputs. Verify DD/NE draw
distributions separately. [The EDGE2 protocol](https://doi.org/10.1371/journal.pbio.3001991)
defines the reference method; retain the exact engine revision alongside it.
A taxonomic rank table has no branch lengths. A fully dated tree can contain
imputed topology/interpolated ages; “dated” does not establish empirical certainty.

The original feasibility questions remain acceptance gates:

1. Verify release availability, exact hashes and separate tree, OTT, NCBI, engine,
   assessment and reference-data rights. Workflow-code licensing does not license
   tree inputs or derived scores. Record release-specific calibration provenance;
   a newer cache cannot establish an older release's imputation history. The
   proposed starting artifacts are the released equal-splits and birth-model
   medians, sampled trees and OTT taxonomy; keep tree, taxonomy and workflow
   licences separate. The reference workflow is an engine-reuse candidate, not
   evidence that every plant/invertebrate input or identifier route is adequate.
2. Measure realized tip coverage by taxonomic class and crosswalk status. Do not
   reuse an undocumented third-party OTT-only threat CSV or assume identifier
   equality implies safe concept transfer. Prefer licensed SIS/assessment-grade joins.
3. Compare ED2/GE2/EDGE2 medians, ranks and residuals with a permitted published
   mammal reference. Separate matched arithmetic from full-clade production and
   published-cohort reproduction. Test whether residuals concentrate in imputed
   dates/topology without presuming that explanation; record calibration provenance.
   Check terminal/promoted/placeholder tips and
   outgroup removal. Median trees may differ in topology as well as dates; small
   convenience samples cannot isolate interpolation or establish full uncertainty.
4. Resolve legacy categories, DD/NE uncertainty, EX/EW and possibly-extinct handling,
   global versus subpopulation scope and assessment precedence. Preserve settled
   optional-engine policies rather than silently changing inputs. A top-N by mean
   score is not the protocol's priority-list definition: review per-clade thresholds,
   sampled-tree support and distinct research/watch lists before claiming those lists.
   Specifically evaluate threatened species above the clade median in at least
   95% of sampled trees, the DD/NE Research List and the LC/NT Watch List. The
   earlier proposal requested near-certain EW risk and excluding already-extinct
   species from avertable-loss priorities; reconcile those aims with the agreed
   engine's actual EX/EW and possibly-extinct policies before any future change.
5. Use project-local naming rather than an official ZSL “EDGE Species” designation.
   Keep outputs descriptive with uncertainty until biological review, ranking
   sensitivity and hotspot robustness justify adoption. Global extension, including
   plants/invertebrates, is a proposal requiring coverage and scientific acceptance.

Share pinned dated-tree identities and data-plane work with the phylogenetic-gap
investigation; keep expected evolutionary loss and gap to represented relatives
conceptually separate. Preserve source-tree choice and uncertainty rather than
claiming an authoritative global dataset or improved conservation decisions.

### Expose spatial evidence provenance in cells

**Priority:** P1 · **Status:** Proposed

For every cell/species pair, retain an evidence class such as
`range_polygon`, `point_occurrence`, `hydrobasins`, or a marker for
confluence from two or more independent sources, plus per-pair spatial
confidence attributes where the source provides them. Cells can then report, alongside richness and priority: the
percentage of pairs supported by polygons, by point occurrences, and by two or
more evidence types; the median assessment age driving the cell score; and the
median spatial-data age. A documented uncertainty in
[methodology](../methodology.md) becomes a visible property of the map instead
of a methodological footnote.

Extend the same surface from spatial evidence to knowledge coverage, as a proposed two-axis knowledge/ignorance view to compare with Miraldo et al. in the related-work survey: each cell should also
report the fraction of its species linked to GoaT/NCBI at all, the fraction
with species-level genomic-status information versus none, the fraction whose
genomic gap is inferred only at lineage level (the categorical ladder from
[Separate exact-species genome status from lineage representation](#separate-exact-species-genome-status-from-lineage-representation)),
and the fraction Data Deficient or unevaluated. These derive from data the
crosswalk and assessment tables already hold. They stay display properties,
never score inputs — "we don't know" must not silently become "high
biological priority" — and any scoring use is gated on
[Request biology-domain review](#request-biology-domain-review); see A11–A13.

The integrated serving relation currently destroys evidence type when
deduplicating polygon, point, and basin pairs, so this requires carrying a
small provenance aggregate through the pair pipeline, serving relation, and
cell-detail endpoints. Cells, tiles, and tooltips must stay within the
established size and latency targets. This is the future provenance-aware map
layer deferred in [Add IUCN point data](#add-iucn-point-data); see A01 and A06.

### Test sensitivity of the polygon rasterization rule

**Priority:** P0 · **Status:** Investigation

The intended resolution-7 membership rule is any-touch polygon contact, including
edge and vertex touching. Main still omits some exact boundary contacts (SPA-002);
optional [PR4](https://github.com/karlaugustnybo/Ark-IV/pull/4) provides the repair.
The intended rule is defensible and documented, but it imposes an arbitrary
spatialization decision on small,
narrow, and coastal ranges, and produces a systematic edge effect.
Investigate the scientific sensitivity to alternative membership rules on a
representative benchmark subset:

- any intersection (intended current policy, with exact-contact repair required);
- cell centroid inside the range polygon;
- at least 10% of the cell polygon overlapping the range;
- at least 25% cell-polygon overlap.

Measure pairwise membership differences, Spearman rank correlation of cell
scores, and Jaccard stability of the top-1% hotspots across each rule, both
globally and for species within size classes (small, narrow, coastal versus
large ranges). Compare per-species cell additions and omissions. The purpose is
not to replace the any-touch rule silently; it is to measure how much of the
striking core pattern survives a more restrictive membership definition, and to
publish that stability check in the methodology. This belongs alongside the
simplification calibration already listed under
[Validate H3 aggregation assumptions](#validate-h3-aggregation-assumptions);
see A01–A05.

### Run a hotspot robustness diagnostic

**Priority:** P0 · **Status:** Proposed

Before acquiring additional data sources, quantify the stability of the
current top-1%, top-5%, and top-10% resolution cells under plausible
alternative specifications of the existing model:

- Data Deficient treated as weight 0, 1, 2, and 4;
- alternative DNA-deficiency weightings, including the family-coverage rule
  and exact-species versus lineage-gap weighting;
- range-rarity weighting on and off;
- exclusion of near-name crosswalk matches versus the full accepted set;
- matched taxonomy only versus all registered species;
- polygon evidence only versus all spatial evidence;
- each polygon rasterization rule tested above;
- no simplified geometries on the calibration subset;
- recent assessments only versus the full assessment record;
- each taxonomic class scored separately.

Report Jaccard overlap of top hotspots and Spearman rank correlation across
all cells for each specification, on the same benchmark subset the audits use.
A pattern that survives most reasonable specifications — for example, a
region that remains top-1% under 95% of them — is strong evidence. A region
that disappears whenever the Data-Deficient weight moves from 2 to 1 is an
equally important finding, and must be phrased that way rather than
suppressed.

Publish results as a robustness summary in the methodology and, optionally,
as a serving-side surface `robustness(c)`: the fraction of reasonable model
specifications under which a cell remains in the top 5%. The surface stays a
display property consistent with the honest-epistemics approach, instead of
being folded into the priority score. See A13, A17, and A19.

### Add GGBN sample availability as a feasibility dimension

**Priority:** P1 · **Status:** Investigation

[GGBN](https://www.ggbn.org/ggbn_portal/site/wf?p=What_is_GGBN) makes DNA and tissue collections discoverable. Investigate whether holdings metadata can help identify suitable material and the sample-quality constraints on reference-genome production. Joining holdings to the species metadata separates two completely
different project opportunities: `genome missing + no known material` versus
`genome missing + sequence-ready/vouchered tissue apparently exists`. This is
probably the single change that moves Ark-IV from a conservation-genomics
visualization toward an actionable sequencing-prioritization tool.

Requirements still need verification before implementation:

- licence and redistribution position through [DATA_POLICY](../DATA_POLICY.md),
  and the feasible exposure of GGBN-derived fields without implying bulk
  redistribution;
- record freshness, holding-type distinction (tissue versus genomic DNA),
  and counting units — records are not unique accessions;
- the conservative label: known holdings support at most "known biobanked
  material appears to exist", never availability, legal transferability, or
  suitability for long-read reference sequencing.

Introduce a deliberately simple feasibility indicator `F_s` from confirmed
holdings, and a separate sequencing-action score `A_s = T_s × G_s × F_s` shown
as its own view rather than replacing the conservation priority score. GGBN
presence must never be treated as genomic evidence in the DNA predicate; it is
sample-side information only.

### Add protected-area and KBA context

**Priority:** P2 · **Status:** Proposed

Add protected-area coverage, KBA overlap, and ecoregion identity as context —
not as factors inside the core priority score. For each species compute a
provisional
`protected_fraction = range cells intersecting protected areas / range cells`,
surface it first as a species attribute, and then offer a cell-level
"underprotected priority" view once range and protection cells are joined.
Treat these layers as contextual sanity checks rather than optimization
inputs: a polygon on a map is not an effectively managed reserve, and species
presence inside it does not establish persistence or security.

Investigate KBA threat/irreplaceability criteria as an external sanity check.
Agreement with Ark is a question, not validation by itself; disagreement may be
informative. Verify authoritative KBA and protected-area providers, current terms,
versions and permitted derived fields separately. Do not assume Protected Planet
WDPA/OECM contains or licenses KBA data. Acquisition and disclosure approval remain
separate from download automation and boundary-builder implementation.

### Add threat-type attributes and cell composition

**Priority:** P2 · **Status:** Proposed

The current score says "these species are threatened" but not why. Licensed
IUCN threat classifications (habitat loss/deterioration, invasive species,
harvest, pollution, climate, etc.) — retained internally where the licence
permits — would allow cells to report the threat composition of their scored
species. This avoids conflating "high genome-sequencing opportunity" with
"high-impact target for conventional habitat-threat abatement" priorities:
the areas are often similar but not identical, and the distinction matters for
conservation genomics.

Do not brand any derived view as STAR unless it implements the actual STAR
threat-abatement and restoration metric. Show threat types as descriptive
attributes; they are secondary evidence for interpretability, not a new score
weight, until biology review accepts one. This depends on the licensed
assessment feed retaining threat-class attributes through the pipeline (see
A06 and A18).

### Add a complementarity portfolio mode

**Priority:** P2 · **Status:** Proposed

A ranked cell list has a known failure mode for prioritization: the top N
cells by score are geographically clustered, and contain largely the same
species. Systematic conservation planning avoids this with complementarity —
the value of the next site is what it adds that is not yet represented. Add a
"next sequencing locations" mode implementing a simple greedy algorithm:

1. initialize the selected set `S` as empty;
2. pick the cell with greatest remaining marginal gain, where
   `gain(c | S) = Σ over species s in cell c not already in S of P_s`
   using the current priority score;
3. mark its species as represented;
4. recompute and repeat until the requested N (10, 50, 100).

This explores complementarity without claiming equivalence to Marxan or a full optimization backend.
It distinguishes "hotspot ranking" from "portfolio selection" — the
sequencing-planning form of the question. Later work could add
travel/accessibility constraints, representation targets for
ecoregion/lineage diversity, and sequencing-priority weights from the
phylogenetic-gap work above, but the greedy version is itself a meaningful
scientific feature. Cross-check portfolio results against the robustness and
provenance work: a selected set should carry its own evidence and feasibility
labels rather than silently repeating the headline score.

### Investigate climate vulnerability and refugia

**Priority:** P3 · **Status:** Investigation

Climate-velocity, exposure, and refugial surfaces could identify where the
same genomic-priority species face the highest and lowest projected change,
reframing sampling around adaptive potential rather than current
distribution alone. This would substantially expand the model's scientific
claims. Requirements: a defensible environmental-source library, accepted
vulnerability models, and peer-reviewable treatment of refugial potential —
probably the largest single increment of modelling complexity in this
section. Keep as an investigation probe until the rarity and genomic-gap
surfaces are validated.

### Add human-pressure and cost layers

**Priority:** P3 · **Status:** Proposed

Land-use-pressure, accessibility, and cost proxies have established sources
and standard treatment in conservation planning, but their primary purpose
here is intervention planning — accessibility and cost as metadata per
portfolio cell rather than additional weights inside the main score.
Prioritize only once the portfolio mode provides a decision framework users
act on.

### Track population-genetic status in the ontology

**Priority:** P3 · **Status:** Proposed

Within-species genetic diversity differs from having one reference genome.
[CBD monitoring guidance](https://www.cbd.int/doc/c/ea34/8414/8c5e6797d291af15f33d6e40/cop-16-inf-03-rev1-en.pdf)
describes effective-population-size monitoring; a reference genome alone does not
measure inbreeding, gene flow or adaptive variation. Once sources permit, distinguish:

- `reference genome exists`
- `population resequencing exists`
- `populations sampled` (count of populations with genomic data)
- `geographic coverage of genomic sampling`

No large global population-genetic dataset is currently incorporated, and
constructing one is a research project in itself; until then adopt the
narrower `reference-genome representation` wording for fields that actually
establish a genome, alongside the broader existing sample/project evidence
states, so later additions fit the schema without mislabelling those states. This is an ontology statement now, a
data-aggregation project later. A concrete feasibility decomposition of that
data-aggregation project is proposed in
[Add intraspecific variation and distinctiveness](#add-intraspecific-variation-and-distinctiveness).

### Add intraspecific variation and distinctiveness

**Priority:** P2 · **Status:** Investigation

"Intraspecific variation" spans several questions with different costs. Investigate whether the first two tranches can use existing licensed fields; availability and feasibility require verification, not an assumption that acquisition is trivial. The items below are ordered
cheapest-first; each stays a descriptive surface until the taxonomic-transfer
and priority-weight validations accept it.

**Tranche 1 — subpopulation threat structure (no new acquisition).** The
registered IUCN export carries `infraType`, `infraName`, and
`subpopulationName` columns, and many species have subpopulation-scoped
assessments with their own threat categories. The integrated serving
relation currently flattens these away. Expose per-species attributes:
number of assessed subpopulations, the severity spread across them, and
any locally extinct subpopulations. This is a schema and serving change;
it preserves the species-level canonical row and adds evidence, and must
not aggregate away the distinction the crosswalk rules already enforce
about never deduplicating assessments by identifier.

**Tranche 2 — CBD indicator proxies (no new acquisition).** The
[CBD guidance](https://www.cbd.int/doc/c/ea34/8414/8c5e6797d291af15f33d6e40/cop-16-inf-03-rev1-en.pdf)
discusses `Ne > 500` with a default effective-to-census ratio of 0.1. A census
threshold near 5,000 is a proxy assumption to validate, not a measured genomic
quantity or a universal conversion. Review current indicator definitions and
Mastretta-Yanes et al.'s guideline materials before selecting acceptance rules.
The
assessment table's population fields would feed a first-pass
`Ne > 500` indicator and a "Populations Maintained" analogue per
species, marked explicitly as Nc-proxy derived rather than genomic, and
with acceptance rules for species whose population fields do not carry
counts. The DNA-based monitoring indicator is a separate knowledge flag
(whether any monitoring exists), not a status estimate. This directly
populates the ontology committed in
[Track population-genetic status in the ontology](#track-population-genetic-status-in-the-ontology)
and can be co-reported with the subpopulation severities from
Tranche 1.

**Tranche 3 — subspecies-level distinctiveness as a sensitivity layer.**
ED values must stop at species tips, but each species' terminal branch
can be subdivided across its recognized subspecies recorded in the IUCN
taxonomy (equal-splits style), producing subspecies-granularity ED values
and a distinct-form richness attribute. This is a robustness/uncertainty
layer around
[Compute Ark-IV EDGE-style scores from a dated global tree](#compute-ark-iv-edge-style-scores-from-a-dated-global-tree),
not a headline metric: subdivided ED is proportional to taxonomic
attention rather than biology, and it redistributes sister species' ED
globally. Quantify species-concept sensitivity and ranking stability against
published EDGE/EDGE2 methods rather than declaring these subdivisions biologically
authoritative. The joins exist only where subspecies evidence is available.

**Tranche 4 — optional genetic-variation raster, deliberately limited.**
Two external sources could seed a within-species genetic-diversity view.
Investigate a Miraldo-style marker-diversity recipe with authorized BOLD data
at H3 resolution, recording dataset licences under [DATA_POLICY](../DATA_POLICY.md).
Assess COI/mitochondrial, animal and geographic sampling biases, source-coordinate
quality and actual effort before promising a small project or global coverage.
Separately, [Shaw et al. 2025](https://pmc.ncbi.nlm.nih.gov/articles/PMC11839457/)
provides a temporal genetic-diversity meta-analysis. Review its permitted data and
annotations as candidates for per-species “genetic monitoring exists” flags, trend
context and an external check on bird/mammal interpretations. Monitoring existence
is a knowledge flag, not a status estimate. Population-resolution geophylogenies
would require separate per-species research; no feasible global pipeline source is
established here.

Externally predicted macrogenetic surfaces need a resolution guardrail. Verify
native grain, interpolation uncertainty and genome-wide georeferenced evidence in
Theodoridis/Schultz before adopting a contextual layer. Rasterizing coarse predictions
onto resolution-7 cells creates no new information. Serve any such layer at native
grain or a justified coarse H3 resolution and show the mismatch explicitly; do not
present a prediction as a measured fine-cell diversity quantity. Test where richness
and genetic-diversity patterns coincide rather than assuming equivalence. This
connects to separate score-component display.

No tranche feeds the priority score on introduction. Tranche 1 is a
serving/provenance change consistent with
[Expose spatial evidence provenance in cells](#expose-spatial-evidence-provenance-in-cells);
Tranche 2 populates ontology fields committed in
[Track population-genetic status in the ontology](#track-population-genetic-status-in-the-ontology);
Tranche 3 rides entirely on the dated-tree item's validation work; and
Tranche 4 should wait until the GGBN
[feasibility item](#add-ggbn-sample-availability-as-a-feasibility-dimension)
and its licence verification establish how third-party sequence-derived
data can be displayed at all. See A06 (field preservation), A13, and
A18.
