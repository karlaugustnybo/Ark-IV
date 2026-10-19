# Ark-IV roadmap — mini version

A mentor-facing agenda; full proposals are in the [roadmap](roadmap.md) and
[assumption audit](reference/roadmap_assumption_audit.md). Proposed priorities are
not implementation requirements, scientific approval or permission to publish data.

## The project in one line

Ark-IV asks *where does sequencing new genomes deserve priority?* It maps
co-occurrence of threatened species with recorded genomic-representation gaps on
H3 cells, using threat × DNA weights, a web map and a species table. Range contact
is not confirmed occupancy; recorded sample/project evidence is not necessarily
a completed reference genome, and missing evidence is not evidence of absence.

## Starting point and limits

Main contains the core pipeline/map/search and jurisdiction workflows. Metadata,
managed profile serving, interface refinements and dated-tree investigations have
separate unmerged branches identified in the full roadmap. Prototype calculations
and tests do not establish a scientifically validated global dataset. No historical
cohort counts, provider correspondence, benchmark results or hosting-size estimate
are public evidence in this agenda.

## Work in priority order: three tracks

| Track | Proposed work | Decision or acceptance gate |
|---|---|---|
| Scientific validation — P0 | Simplified versus unsimplified H3 calibration; any-touch versus centroid/10%/25%-overlap sensitivity | Per-species omissions/additions, agreed error limits and hotspot/rank stability |
| Scientific validation — P0 | Top-1/5/10% robustness across weights, taxonomy, evidence sources and rasterization | Jaccard/rank diagnostics; publish fragile patterns as well as stable ones |
| Scientific validation — P0 | Taxonomy precision, DNA evidence stages, family coverage, resampling and weight objectives | Preserve current inputs; distinguish coverage from correctness and sampling from assemblies |
| Scientific validation — P0 | Release-specific row/column lineage and biology-domain review | Data-Deficient/missingness, trends and new scores await independent scientific review |
| Reproducibility/release | Synthetic bring-your-own-data profile; authorized real-data profile | Pinned inputs, checksums, source/derived rights, reproducible validation and separate data release |
| Reproducibility/release | Integrate dated-tree/OTT identities, EDGE descriptors and phylogenetic gaps | Preserve identity/transfer gates, all-relative DNA baselines, null reasons and separate score fields |
| Reproducibility/release | Municipality coverage and authorized full build | Gaps, overlaps, disputed boundaries, represented years and coherent serving generations |
| New scientific surfaces — after validation | Range-rarity weighting and stewardship fractions | A separate range-normalized score and mapped distribution shares; compare rank changes |
| New scientific surfaces | Exact-species genome status versus genus/family/order representation | Explicit descriptive fields before score changes |
| New scientific surfaces | Spatial provenance and knowledge coverage | Retain polygon/point/basin evidence and uncertainty without rewarding ignorance |
| New scientific surfaces | Dated-tree EDGE-style and marginal DNA-gap investigations | Optional prototypes; whole-clade arithmetic, baseline/source-tree gaps and uncertainty still need acceptance |
| New scientific surfaces | GGBN material feasibility | Known holdings do not establish availability, transferability, quality or existing genomes |

Then P2: cell caveats/components, colour-distribution validation, protection/KBA
context, threat attributes, complementary portfolios and interface polish.
Longer investigations include climate refugia, costs and intraspecific variation;
keep them descriptive and respect their native information resolution. The
[GBIF concordance proposal](GBIF%E2%80%93IUCN%20Concordance%20Extension%20Idea.md)
asks whether credible occurrences outside accepted mapped ranges warrant review;
it neither replaces ranges nor treats record absence as absence.

## Asks for mentors

1. Biology/conservation review of weights, Data-Deficient handling, range-versus-
   occupancy, evidence stages and validation design.
2. Provider/licensing advice for IUCN, ZSL, Protected Planet, GGBN and proposed
   occurrence/derived surfaces; approval of code does not approve data distribution.
3. Related-work review of the proposed Miraldo → Theodoridis → Schultz → Ark-IV
   data-acquisition positioning, with bibliographic versions and claim limits checked.
4. Hosting/funding direction after measuring an authorized serving workload;
   synthetic data is the proposed public fallback while data-release rights remain unresolved.
