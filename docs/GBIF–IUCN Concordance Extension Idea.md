# GBIF–IUCN Concordance Extension for Ark-IV

## TL;DR

> **Research proposal, not implemented behavior.** This is a future investigation,
> not an implementation specification or an authorization to acquire or publish data.
> SQL, schemas, commands, thresholds, UI sketches and example-species values below
> are conceptual and unverified as executable interfaces. Numerical examples are
> invented illustrations, not measured Ark-IV results. Existing source identity,
> genomic-transfer, seasonality and publication rules remain in force.
>
> On main, IUCN membership and the taxonomy crosswalk provide starting contracts;
> a GBIF occurrence-concordance relation, scores, build profiles and UI do not exist.
> Reuse of aggregation/serving infrastructure would require validation rather than
> imply drop-in compatibility. Keep occurrence evidence separate from range membership.
>
> GBIF documents [predicate downloads](https://techdocs.gbif.org/en/data-use/api-downloads)
> and an [experimental SQL download API](https://techdocs.gbif.org/en/data-use/api-sql-downloads).
> The latter supports selected columns and summaries; syntax and limits must be checked
> at implementation time. [GBIF terms](https://www.gbif.org/terms) distinguish CC0,
> CC BY and CC BY-NC datasets: GBIF-mediated does not mean unrestricted reuse.
> An aggregate, identifier list or drill-down also needs a source-specific disclosure
> review under [DATA_POLICY](../DATA_POLICY.md). No download or provider request was
> made for this proposal. A future Ebbe Nielsen Challenge entry is a possible framing;
> eligibility and rules for any future call have not been verified.


Ark-IV could gain a substantial GBIF-focused extension without attempting to ingest the full global GBIF occurrence corpus.

The core idea is to use Ark-IV's existing prioritization to identify a manageable subset of species, retrieve GBIF occurrence evidence only for those taxa, aggregate the records to the existing `(IUCN species, H3 res-7 cell)` model, and compare them against IUCN mapped ranges.

This would enable a new scientific question:

> **Which high-priority species have credible GBIF-mediated occurrence evidence outside their currently mapped IUCN ranges?**

The most useful architecture is probably hybrid:

- precompute GBIF evidence for priority species using GBIF bulk/SQL downloads;
- collapse raw occurrences immediately into species × H3 evidence summaries;
- compare these cells against IUCN polygon membership;
- serve only aggregated concordance/disagreement layers;
- use the ordinary GBIF API only as an interactive drill-down for individual species/cells and underlying records.

This avoids downloading the entire GBIF occurrence corpus while making GBIF a scientifically central part of Ark-IV.

---

# 1. Motivation

During the initial design of Ark-IV, GBIF occurrences were considered as an alternative to IUCN spatial data.

The concern was scale. The size of a complete extraction has not been established here; a global mirror is unnecessary for this scientific question.

However, Ark-IV does not actually need a local copy of GBIF.

Ark-IV already identifies species that may be particularly interesting from a conservation-genomics perspective. This creates a natural filter that can be applied *before* requesting GBIF occurrence data.

Instead of:

```text
all GBIF occurrences
        ↓
unbounded scope for this investigation
        ↓
filter to interesting species
```

the architecture can become:

```text
Ark-IV species universe
        ↓
conservation/genomic prioritization
        ↓
subset of interesting species
        ↓
GBIF taxon reconciliation
        ↓
filtered GBIF retrieval
        ↓
H3 aggregation
```

This changes the problem from "mirror GBIF" to "retrieve evidence relevant to a defined scientific question."

---

# 2. Core scientific idea

For each species `s`, Ark-IV already has a set of mapped IUCN cells:

```text
I(s) = set of H3 res-7 cells intersecting the accepted IUCN range
```

From GBIF, Ark-IV could construct:

```text
G(s) = set of H3 res-7 cells containing sufficiently credible GBIF occurrences
```

The elementary comparison then becomes:

```text
I ∩ G
```

IUCN-mapped cells with GBIF-mediated occurrence evidence.

```text
I - G
```

IUCN-mapped cells where Ark-IV found no qualifying GBIF-mediated occurrence evidence.

```text
G - I
```

GBIF occurrence evidence outside the accepted IUCN mapped range.

The third category is likely the most scientifically interesting.

It creates a way to identify species for which public observational data appear to disagree with, extend beyond, or otherwise complicate the current mapped range.

---

# 3. Important epistemic distinction

`G - I` should **not** automatically be described as "IUCN is wrong."

An occurrence outside an IUCN polygon could reflect many different processes:

- genuine range expansion;
- incomplete or outdated IUCN range mapping;
- an introduced population;
- a vagrant individual;
- migration or seasonal use;
- a taxonomic mismatch;
- a georeferencing error;
- an occurrence from a captive/cultivated context;
- large coordinate uncertainty;
- a historical record inconsistent with the temporal interpretation of the current range;
- differing spatial concepts between the GBIF record and the IUCN assessment.

Similarly:

```text
I - G
```

must **not** be interpreted as absence.

A lack of GBIF records in an IUCN range cell may simply indicate:

- low sampling effort;
- poor digitization;
- restricted data;
- observer bias;
- taxonomic detectability differences;
- inaccessible geography;
- differences among countries or institutions in data publication.

A better label would be something like:

> **No qualifying GBIF-mediated occurrence evidence detected**

rather than:

> Unsampled

and certainly not:

> Unoccupied

This should be explicit in `methodology.md` and the assumption audit.

---

# 4. Why this fits Ark-IV

Ark-IV's scientific unit is already:

```text
(H3 res-7 cell, IUCN species)
```

That is almost ideal for this extension.

A GBIF occurrence can be transformed directly into the same contract:

```text
(decimalLatitude, decimalLongitude)
        ↓
H3 res-7 cell
        ↓
(IUCN species, H3 cell)
```

The rest of the pipeline can therefore reuse existing aggregation, partitioning, scoring, serving, and visualization infrastructure.

This is preferable to introducing a second spatial grid system.

---

# 5. Proposed architecture

## 5.1 Species selection

Do not retrieve GBIF data for every species by default.

Start from a subset derived from Ark-IV.

Possible initial filters include:

- threatened species only;
- CR / EN / VU;
- species with high Ark-IV priority;
- threatened species lacking genomic representation;
- species above a configurable priority threshold;
- species selected interactively for investigation;
- all species in a particular clade or region.

For example:

```text
registered IUCN species universe
        ↓
taxonomy/genomics joins
        ↓
priority filter
        ↓
candidate species (size to be measured)
        ↓
GBIF retrieval
```

The exact size should be determined empirically.

---

# 6. First investigation: estimate the real GBIF size

Before building a bulk ingestion system, estimate how many records actually exist for Ark-IV's candidate species.

Use lightweight GBIF occurrence-count queries to collect something like:

```text
iucn_id
gbif_species_key
gbif_occurrence_count
```

This would answer:

- how many candidate species have GBIF records;
- the total number of records for the candidate set;
- the distribution of occurrence counts;
- which species dominate the total volume;
- whether a few extremely common taxa require special treatment;
- whether the total extraction is millions, tens of millions, or billions of records.

This investigation should happen before choosing the final ingestion architecture.

A useful output would be a histogram or quantiles such as:

```text
species with:
0 records
1–10
11–100
101–1,000
1,001–10,000
10,001–100,000
>100,000
```

and cumulative record counts by species rank.

It may turn out that the relevant subset is dramatically smaller than the global corpus.

---

# 7. Retrieval strategy

## Preferred approach: GBIF bulk/SQL download

The main analysis should probably use GBIF's download infrastructure rather than repeatedly calling the occurrence search API.

The idea is to request only:

- relevant species;
- georeferenced occurrences;
- records marked present;
- fields required for spatial/evidence analysis;
- optionally additional quality predicates.

GBIF can perform a significant amount of filtering before the data reaches Ark-IV.

Conceptually:

```sql
SELECT
    speciesKey,
    gbifID,
    decimalLatitude,
    decimalLongitude,
    coordinateUncertaintyInMeters,
    year,
    eventDate,
    datasetKey,
    basisOfRecord,
    establishmentMeans,
    issue
FROM occurrences
WHERE
    speciesKey IN (...)
    AND occurrenceStatus = 'PRESENT'
    AND hasCoordinate = TRUE
```

Exact syntax should be checked against the current GBIF SQL/download API when implementation begins.

---

# 8. Basic spatial quality filtering

An initial spatial filter could reject clearly problematic coordinates, for example records associated with issues equivalent to:

```text
ZERO_COORDINATE
COORDINATE_OUT_OF_RANGE
COORDINATE_INVALID
COUNTRY_COORDINATE_MISMATCH
```

However, filtering policy should be conservative.

Some fields are better retained as evidence metadata rather than used as hard exclusion criteria.

Useful fields to preserve include:

```text
coordinateUncertaintyInMeters
year
eventDate
basisOfRecord
establishmentMeans
datasetKey
gbifID
```

This allows the evidence model to distinguish strong from weak apparent range disagreements later.

---

# 9. Convert GBIF occurrences to H3 locally

Ark-IV should remain H3-native.

For every qualifying occurrence:

```python
cell = h3.latlng_to_cell(latitude, longitude, 7)
```

There is little reason to introduce another global grid merely because a server-side service happens to support it.

The pipeline should ideally stream the filtered GBIF data through DuckDB/Arrow/Polars rather than materializing large intermediate files unnecessarily.

---

# 10. Collapse raw occurrences immediately

Ark-IV does not need to preserve every GBIF occurrence in its serving layer.

Raw records can be collapsed into:

```text
(
    iucn_id,
    h3_cell,
    n_occurrences,
    n_datasets,
    first_year,
    last_year,
    min_coordinate_uncertainty,
    max_coordinate_uncertainty?,
    basis_of_record_summary?,
    establishment_summary?,
    quality_flags
)
```

Possible additional fields:

```text
n_years
n_recent_occurrences
latest_year
n_inaturalist_records
n_preserved_specimen_records
n_human_observation_records
n_machine_observation_records
n_datasets_recent
```

These should only be added if they prove scientifically useful.

The key principle is:

> Keep evidence summaries, not a second copy of GBIF.

A species with hundreds of thousands of occurrence records may collapse to only thousands of H3 cells.

That reduction is likely to make the extension tractable.

---

# 11. Concordance categories

Each `(species, cell)` pair can be classified into a small number of interpretable states.

For example:

```text
IUCN + GBIF
```

The cell is inside the accepted IUCN range and contains qualifying GBIF evidence.

```text
IUCN only
```

The cell is inside the accepted IUCN range but no qualifying GBIF evidence was detected.

```text
GBIF only
```

The cell is outside the accepted IUCN range but contains qualifying GBIF evidence.

Potentially:

```text
GBIF near boundary
```

A special category for records only slightly outside the IUCN polygon.

This is useful because an observation 1 km outside a rasterized range boundary is very different from one 2,000 km outside it.

---

# 12. Distance from mapped range

For `G - I` cells, compute distance to the nearest accepted IUCN range polygon or range cell.

Possible field:

```text
distance_to_iucn_range_km
```

This can help distinguish:

```text
minor geometric/raster boundary disagreement
```

from:

```text
substantial spatial disagreement
```

Example rough categories:

```text
0–5 km
5–25 km
25–100 km
100–500 km
>500 km
```

These thresholds should not be treated as scientifically validated defaults without investigation.

They are mainly useful as exploratory bins.

---

# 13. Evidence strength for outside-range records

A single GBIF record outside an IUCN range should probably carry little weight.

Ark-IV could instead summarize evidence strength.

A conceptual hierarchy:

```text
Level 0
single occurrence
```

```text
Level 1
qualifying occurrence with reasonable coordinates
```

```text
Level 2
multiple occurrences in the same cell/area
```

```text
Level 3
evidence from multiple independent datasets
```

```text
Level 4
persistent evidence across multiple years
```

The exact thresholds need investigation.

For example, a preliminary definition might consider:

```text
n_occurrences
n_datasets
n_years
coordinate_uncertainty
recency
basis_of_record
distance_from_iucn_range
```

A more continuous disagreement/evidence score could eventually be defined as:

\[
D(s,c)
=
f(
n_{\text{occurrences}},
n_{\text{datasets}},
n_{\text{years}},
u_{\text{coord}},
d_{\text{range}},
r_{\text{recency}}
)
\]

where:

```text
s = species
c = H3 cell
u_coord = coordinate uncertainty
d_range = distance to nearest accepted IUCN range
r_recency = some measure of record recency
```

This should not initially be presented as a validated probability.

As with Ark-IV's existing priority score, the underlying components should remain visible and preferably independently filterable.

---

# 14. Independence matters

Ten observations from one citizen-science dataset on the same day are not equivalent to evidence from:

- several datasets;
- multiple institutions;
- multiple years;
- multiple observation methods.

For apparent range extensions, independent evidence may therefore be more informative than raw occurrence count.

Potential metrics:

```text
n_occurrences
n_unique_datasets
n_unique_years
n_bases_of_record
date_span_years
```

Potentially also:

```text
n_unique_publishers
```

if publisher identity is available and useful.

This could become an important part of the evidence-strength model.

---

# 15. Temporal interpretation

Range disagreement should probably include time.

A GBIF occurrence from 1890 may have a very different interpretation from one observed in 2025.

Possible categories:

```text
historical
recent
current/recent repeated evidence
```

The exact definitions need scientific justification.

Useful fields include:

```text
first_year
last_year
n_years
n_records_last_5_years
n_records_last_10_years
```

Possible questions:

- Is the species now observed beyond a historically mapped range?
- Is an apparent disagreement based entirely on old museum records?
- Is there recent repeated evidence of expansion?
- Has evidence disappeared from parts of an IUCN range?

The last question should be handled particularly carefully because absence of GBIF records is not reliable absence evidence.

---

# 16. Establishment status and introduced populations

Occurrences outside IUCN native ranges may reflect introduced populations.

If GBIF provides useful establishment information, retain it.

Possible categories:

```text
native
introduced
invasive
uncertain
unknown
```

Do not assume this field is complete or consistently populated.

However, where available it may explain a significant fraction of apparent `G - I` disagreements.

A future interface could distinguish:

```text
outside range — possibly introduced
outside range — establishment unknown
outside range — potentially native range discrepancy
```

---

# 17. Taxonomic reconciliation is critical

This extension depends heavily on the IUCN ↔ GBIF taxonomic bridge.

Ark-IV has a crosswalk pipeline. Investigate which reviewed identity evidence it can safely reuse for GBIF taxon reconciliation rather than introducing an independent matcher. A GoaT/NCBI bridge or a legacy application alias is not automatically an exact GBIF taxon key.

Each GBIF-derived result should ultimately map back to the stable Ark-IV identity:

```text
IUCN SIS ID
```

Potential taxonomic disagreement classes may need to be retained:

```text
exact accepted-name bridge
synonym bridge
GBIF taxon-key bridge
ambiguous
unresolved
```

Apparent spatial disagreement should not be surfaced as strong evidence if the taxonomic bridge is itself uncertain.

---

# 18. Priority-species focus

The most interesting extension is probably **not** a general-purpose IUCN-vs-GBIF map for every species.

Instead, focus attention where Ark-IV already identifies conservation/genomic importance.

For example:

```text
high threat
+
poor genomic representation
+
strong GBIF/IUCN spatial disagreement
```

This creates a new upper-level research question:

> Which species are simultaneously conservation priorities, genomically underrepresented, and spatially uncertain or potentially under-mapped?

This can produce a useful 2D conceptual space:

```text
                        Conservation/genomic priority
                          low ───────────────► high

GBIF–IUCN      high      interesting          highest-review
disagreement             discrepancy          priority

               low       low priority          sequencing /
                                               conservation
                                               priority
```

The upper-right quadrant may be particularly useful for follow-up investigation.

---

# 19. Potential interpretation of the upper-right quadrant

Species in the high-priority / high-disagreement quadrant could merit different kinds of follow-up:

- range-map review;
- taxonomic review;
- targeted field surveys;
- sequencing effort;
- collection of voucher specimens;
- validation of suspicious records;
- assessment of introduced populations;
- investigation of climate-driven range shifts;
- investigation of poorly represented peripheral populations.

Ark-IV should not itself claim which intervention is appropriate.

It can instead identify **where evidence warrants attention**.

---

# 20. UI concept

A species page or map mode could provide toggles such as:

```text
[ IUCN range ]
[ GBIF evidence ]
[ concordance ]
[ outside-range evidence ]
```

Cells could be rendered using categories such as:

```text
IUCN + GBIF
IUCN only
GBIF only
high-confidence GBIF-only
```

For a selected species:

```text
Species: Example species

Ark-IV conservation/genomic priority: 8.4

IUCN mapped cells:                 12,482
Cells with GBIF evidence:           4,901
IUCN cells with GBIF evidence:       4,612
GBIF cells outside IUCN range:         289
High-evidence outside cells:             21
```

The wording must emphasize that the "IUCN only" count does not measure absence.

---

# 21. Cell inspection

Clicking an outside-range cell could show something like:

```text
GBIF evidence outside accepted IUCN range

Occurrences:                  14
Independent datasets:          3
Unique years:                  7
First record:               2009
Most recent record:         2025
Best coordinate uncertainty: 12 m
Distance from IUCN range:     87 km

Evidence interpretation:
Repeated observations from multiple datasets over several years.
```

This is more useful than plotting raw occurrence dots alone.

---

# 22. Live GBIF drill-down

A live GBIF API lookup still makes sense, but primarily as a **drill-down tool**, not as the main data layer.

Architecture:

```text
precomputed Ark-IV concordance layer
             ↓
user clicks suspicious species/cell
             ↓
species + cell bounds are known
             ↓
live GBIF API request
             ↓
show underlying records
```

This allows users to inspect:

- individual records;
- dataset provenance;
- dates;
- basis of record;
- coordinates;
- institution/source;
- GBIF links.

This avoids storing the full raw occurrence dataset.

It also keeps the map responsive because global analytical layers remain precomputed.

---

# 23. Why not use only the live occurrence API?

A pure live-query architecture is attractive because it requires little storage.

However, it has several drawbacks:

- difficult to perform reproducible global comparisons;
- dependent on API latency;
- repeated requests for the same taxa;
- harder to compute aggregate disagreement metrics;
- harder to create global ranking or filtering;
- possible query/page limits;
- results may change between sessions as GBIF updates;
- harder to pin analysis to a specific GBIF snapshot/DOI;
- not suitable for large-scale batch retrieval.

Therefore:

> Use GBIF's bulk/download infrastructure for the analytical layer, and the search API for inspection.

---

# 24. Reproducibility and provenance

The GBIF extension should be snapshot-based.

For each build, record:

```text
GBIF download DOI
download creation date
GBIF query/predicate
GBIF taxon-key set
filter profile version
Ark-IV crosswalk version
H3 resolution
aggregation version
```

This fits Ark-IV's existing provenance architecture.

A future build manifest might contain:

```toml
[gbif]
enabled = true
download_doi = "..."
created_at = "..."
taxon_filter_sha256 = "..."
quality_profile = "gbif_concordance_v1"
crosswalk_version = "..."
```

This is important because GBIF is continuously updated.

A map saying that a species had 14 outside-range records must be reproducible against a particular data snapshot.

---

# 25. Potential storage contract

A compact Parquet schema might be:

```text
iucn_id                    uint32/int64
h3_cell                    uint64

n_occurrences              uint32
n_datasets                 uint16
n_years                    uint16

first_year                 int16
last_year                  int16

min_uncertainty_m          float32
max_uncertainty_m          float32

inside_iucn                bool
distance_to_iucn_km        float32

evidence_class             uint8
quality_flags              uint16/uint32
```

Potential optional fields:

```text
n_recent_occurrences
n_human_observations
n_machine_observations
n_preserved_specimens
n_introduced
n_native
```

Keep the schema minimal until exploratory analysis shows which fields actually matter.

---

# 26. Integration with existing Ark-IV aggregation

This could become another evidence stream producing the familiar contract:

```text
(h3_cell, iucn_id)
```

plus evidence metadata.

Existing pipeline:

```text
IUCN polygons
IUCN points
HydroBASINS
      ↓
(h3, species)
```

Extended pipeline:

```text
IUCN polygons
IUCN points
HydroBASINS
GBIF occurrences
      ↓
(h3, species, evidence source/metadata)
```

However, GBIF should probably remain a distinct evidence relation rather than being blindly unioned into the core range-membership relation.

The distinction between:

```text
mapped range evidence
```

and:

```text
observational occurrence evidence
```

is scientifically important.

---

# 27. Do not merge GBIF into "occupancy"

GBIF records should not transform Ark-IV into an occupancy model.

A GBIF point means:

> A record was published with coordinates placing the occurrence in this cell.

It does not necessarily mean:

- stable population;
- current range;
- habitat suitability;
- native occupancy;
- abundance;
- breeding population.

Ark-IV should preserve the distinction between:

```text
potential mapped occurrence
```

and:

```text
GBIF-mediated observational evidence
```

---

# 28. Possible new metrics

At the species level:

```text
gbif_occurrence_count
gbif_cell_count
gbif_dataset_count
gbif_year_count

iucn_cell_count
concordant_cell_count
outside_range_cell_count

outside_range_fraction_of_gbif
gbif_supported_fraction_of_iucn

high_evidence_outside_cell_count
max_distance_outside_iucn_km
median_distance_outside_iucn_km
```

Use caution with:

```text
gbif_supported_fraction_of_iucn
```

because it will be heavily affected by sampling bias.

It should not be called "range coverage."

---

# 29. Cell-level metrics

Potential metrics:

```text
gbif_n_occurrences
gbif_n_species
gbif_n_datasets
gbif_n_recent_occurrences

gbif_iucn_concordant_species
gbif_outside_iucn_species
gbif_high_evidence_outside_species
```

These could potentially become additional Ark-IV global map layers.

For example:

> Cells containing many priority species with credible observations outside accepted IUCN ranges.

That could produce geographic clusters of possible biodiversity knowledge gaps.

---

# 30. Species-level ranking

A new investigation table could rank species by:

```text
Ark-IV priority
×
GBIF/IUCN discrepancy evidence
```

Possible columns:

```text
species
IUCN category
DNA representation class
Ark-IV priority

GBIF occurrences
GBIF datasets
outside-range cells
high-evidence outside cells
max outside distance
latest outside record
```

This may be one of the most useful outputs for researchers.

---

# 31. Geographic ranking

Another view could rank countries, regions, or H3 areas by:

```text
number of priority species
with strong GBIF evidence
outside accepted IUCN ranges
```

This might identify:

- rapidly changing distributions;
- regions with outdated mapping;
- taxonomic problem areas;
- strong citizen-science coverage producing new evidence;
- cross-border mapping inconsistencies.

Again, these interpretations should be treated as hypotheses rather than conclusions.

---

# 32. Potential false-positive investigation

Before exposing strong "range disagreement" claims, manually investigate a benchmark sample.

For example:

```text
100–500 species
```

stratified by:

```text
threat status
taxonomic group
continent
occurrence count
distance outside range
evidence strength
```

For each sampled disagreement, classify likely explanation:

```text
credible possible range extension
introduced population
vagrant
taxonomic mismatch
coordinate problem
IUCN geometry interpretation issue
historical occurrence
unknown
```

This would provide empirical evidence for designing the evidence thresholds.

It might also become a publishable methodological result.

---

# 33. Benchmark dataset

Propose a checked-in benchmark only after confirming each selected record and derived label is redistributable. Synthetic fixtures are the default public starting point.

Possible contents:

```text
species ID
GBIF occurrence IDs
H3 cells
distance from IUCN range
evidence summary
manual discrepancy classification
```

Be careful not to redistribute restricted IUCN geometry.

Possible derived classifications may need licensing review before release.

The benchmark could instead use synthetic or independently open range data for public reproducibility.

---

# 34. Open/restricted build distinction

This extension may also help Ark-IV's reproducibility story.

Potential build profiles:

```text
ark build --profile=full
```

Uses licensed IUCN data plus GBIF and GoaT.

```text
ark build --profile=open
```

Uses fully open sources and demonstrates the concordance pipeline on taxa for which open range information is available.

This could be particularly useful for an Ebbe Nielsen Challenge submission because judges could reproduce the open pathway without obtaining IUCN archives.

---

# 35. Potential Ebbe Nielsen Challenge framing

This extension could become the substantial new work associated with a future Challenge entry.

Possible framing:

# Ark-IV × GBIF: Conservation Knowledge Gaps

Ark-IV existed as a conservation-genomics prioritization platform.

For the Challenge, the new work would introduce:

- large-scale GBIF occurrence integration;
- taxon-filtered GBIF extraction;
- H3 occurrence aggregation;
- IUCN–GBIF range concordance;
- evidence-strength modelling;
- outside-range discrepancy detection;
- live GBIF record drill-down;
- GBIF snapshot provenance;
- global ranking of priority species with spatial evidence disagreements.

This would make GBIF central to the scientific contribution rather than merely an optional taxonomic bridge.

---

# 36. Possible project pitch

A concise scientific pitch could be:

> Conservation status, genomic representation, expert range maps, and observational biodiversity evidence currently live in separate infrastructures. Ark-IV combines them spatially to identify species and places where conservation importance, genomic underrepresentation, and uncertainty in our mapped understanding of biodiversity overlap.

A narrower version:

> Which threatened and genomically underrepresented species have strong, repeated GBIF occurrence evidence outside their currently mapped IUCN ranges?

This may be a stronger competition/research question than simply "visualize GBIF occurrences."

---

# 37. Strongest novelty

The main innovation should probably not be:

> We plotted GBIF points over IUCN polygons.

That is straightforward.

The interesting part is the combination of:

```text
conservation priority
+
genomic representation
+
expert range knowledge
+
open occurrence evidence
+
spatial evidence quality
```

with:

```text
global aggregation
+
interactive drill-down
+
explicit epistemic uncertainty
```

That is much more distinctive.

---

# 38. Avoid collapsing everything into one scalar

As with the existing Ark-IV priority metric, disagreement evidence probably should not be hidden behind one score.

Prefer preserving joint components.

For example, users should be able to inspect or filter by:

```text
n_occurrences
n_datasets
n_years
last_year
coordinate uncertainty
distance from range
establishment status
```

A configurable composite evidence score can exist for ranking, but the raw dimensions should remain available.

This fits Ark-IV's existing philosophy of exact reweighting and explicit assumptions.

---

# 39. Potential evidence matrix

A useful conceptual representation:

| Evidence dimension | Weak | Medium | Strong |
|---|---|---|---|
| Occurrences | 1 | several | many |
| Independent datasets | 1 | 2 | 3+ |
| Temporal persistence | one date | multiple dates | multiple years |
| Coordinate uncertainty | high | moderate | low |
| Distance beyond range | near edge | moderate | large |
| Recency | historical | mixed | recent |
| Taxonomic confidence | ambiguous | probable | strong |

This is not yet a validated scoring scheme.

It is an investigation framework.

---

# 40. Important edge cases

The design should explicitly investigate:

## Marine species

GBIF coordinates and IUCN marine polygons may create edge cases around:

- coastlines;
- land/sea coordinate errors;
- EEZ boundaries;
- migratory species;
- pelagic distributions.

## Migratory species

IUCN seasonality categories may matter when comparing point occurrences with accepted polygons.

Ark-IV's current range policy already filters seasonality.

The GBIF extension should preserve enough temporal information to avoid interpreting seasonal observations incorrectly.

## Very large ranges

Species with near-global ranges may have huge occurrence counts.

These may need:

- record caps for exploratory work;
- direct aggregation;
- stratified retrieval;
- exclusion from early prototypes.

## Extremely common species

A small number of taxa may dominate data volume.

Investigate whether they are relevant to Ark-IV's priority set before optimizing for them.

## Sparse species

A single occurrence may be highly informative for a very poorly observed threatened species, but also highly error-prone.

Do not use a universal evidence threshold without testing taxonomic and geographic effects.

---

# 41. API lookup mode

A simpler first prototype could avoid all bulk ingestion.

For one selected species:

```text
user selects species
      ↓
IUCN → GBIF taxon key
      ↓
GBIF API query
      ↓
download/sample occurrence records
      ↓
convert to H3
      ↓
compare against IUCN polygon
      ↓
show disagreement
```

This could be useful as an exploratory tool and UX prototype.

It should probably not become the final global analytical architecture.

Benefits:

- low implementation cost;
- immediate scientific exploration;
- no large storage requirement;
- useful for manually inspecting priority species.

Potential first milestone:

> Add "Compare with GBIF" to the species view.

That button could retrieve recent/all qualifying records and overlay them on the IUCN range.

---

# 42. Suggested staged implementation

## Phase 0 — feasibility

Investigate:

- number of Ark-IV priority species;
- GBIF crosswalk success rate;
- GBIF occurrence count distribution;
- estimated filtered download size;
- suitable GBIF quality predicates;
- relevant API/download limits.

No permanent pipeline changes yet.

---

## Phase 1 — single-species prototype

Implement:

```text
species
→ GBIF lookup
→ coordinate filtering
→ H3 conversion
→ IUCN comparison
→ map overlay
```

Manually inspect ~20–50 species.

Goal:

Determine whether the disagreement signal is scientifically interesting.

---

## Phase 2 — evidence model

Add:

```text
n_occurrences
n_datasets
n_years
recency
coordinate uncertainty
distance to range
```

Manually classify false positives.

Develop a defensible evidence-strength scheme.

---

## Phase 3 — bulk priority-species ingestion

Build reproducible GBIF download generation.

Pipeline:

```text
Ark-IV candidate species
→ GBIF taxon keys
→ bulk download
→ streaming H3 aggregation
→ compact Parquet
→ concordance join
```

---

## Phase 4 — global concordance UI

Add:

```text
IUCN range
GBIF evidence
concordance
outside-range evidence
```

plus species-level summary statistics.

---

## Phase 5 — ranking and discovery

Add:

```text
priority × disagreement
```

species ranking and potentially geographic hotspot views.

---

## Phase 6 — live provenance drill-down

From any suspicious cell:

```text
open underlying GBIF records
```

using live GBIF queries or direct record links.

---

# 43. Questions to investigate

Before implementation, answer:

1. How many Ark-IV species fall into a plausible "GBIF investigation set"?

2. How many of those have reliable GBIF taxon mappings?

3. What is the total GBIF occurrence count for that set?

4. How concentrated is the data volume among common species?

5. What subset of GBIF fields is actually needed?

6. Which GBIF quality issues should be hard exclusions?

7. Which should remain soft evidence?

8. How should coordinate uncertainty interact with H3 res-7?

9. How should records close to the IUCN polygon boundary be handled?

10. Should distance be measured to the polygon itself or the rasterized H3 range?

11. How should introduced populations be represented?

12. How should migratory/seasonal species be handled?

13. What constitutes independent evidence?

14. Does evidence from multiple datasets materially reduce false positives?

15. How strongly does record recency matter?

16. How many high-priority species have substantial `G - I` evidence?

17. What fraction of apparent disagreements survive manual inspection?

18. Are disagreements concentrated in particular taxa or regions?

19. Can a fully open reproducible demo be created without redistributing IUCN data?

20. Is the result useful enough to justify inclusion as a first-class Ark-IV feature?

---

# 44. Initial success criterion

Do not judge the extension initially by how many GBIF records can be ingested.

A better feasibility criterion is:

> Among a manually reviewed sample of high-priority Ark-IV species, does aggregated GBIF evidence reliably surface scientifically interesting spatial discrepancies that would otherwise be difficult to notice?

If yes, scale it.

If not, keep GBIF as an on-demand supporting lookup rather than a full analytical layer.

---

# 45. Possible final feature

The eventual user workflow could be:

```text
1. Open Ark-IV.

2. Find a high-priority threatened species.

3. Inspect its conservation/genomic priority.

4. Enable "GBIF evidence."

5. See which IUCN range areas have GBIF-mediated observations.

6. Highlight occurrence evidence outside the accepted IUCN range.

7. Filter to repeated / recent / multi-dataset evidence.

8. Click a discrepancy.

9. Inspect the evidence summary.

10. Open the underlying GBIF records.

11. Decide whether the discrepancy warrants scientific follow-up.
```

This would make Ark-IV useful not only for conservation-genomics prioritization but also for identifying biodiversity knowledge discrepancies.

---

# 46. Summary

The GBIF extension should **not** attempt to replace IUCN ranges with raw GBIF occurrences and should not attempt to ingest the complete global GBIF corpus.

Instead:

```text
Ark-IV identifies the species worth investigating.
GBIF mediates observational evidence under dataset-specific terms.
IUCN provides expert mapped ranges.
Ark-IV reconciles the two spatially.
```

The most promising signal is:

```text
credible GBIF evidence outside accepted IUCN ranges
```

especially when it occurs for species that are already:

```text
threatened
+
genomically underrepresented
```

The architecture should use:

```text
filtered GBIF bulk retrieval
→ H3 res-7 aggregation
→ species × cell evidence summaries
→ IUCN concordance classification
→ compact serving layer
→ live GBIF drill-down
```

This preserves Ark-IV's existing scientific unit, avoids the global GBIF scale problem, keeps the analysis reproducible, and potentially creates a substantial new scientific contribution suitable for future development and an Ebbe Nielsen Challenge entry.
