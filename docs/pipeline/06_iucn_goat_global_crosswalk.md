# Global IUCN–GoaT identifier crosswalk

## Screened crosswalk selection

Rebuilds prefer the registered private `screened-crosswalk` product. Its package
contains the screened Parquet, source summary, screening receipt and original
provenance. `crosswalk_refresh` validates its complete IUCN/GoaT/NCBI/COL XR/
legacy GBIF/OTT/tree bindings and selects a reusable managed generation at
`derived/iucn-goat-crosswalk/current`. A changed contributing source requires a
new screen; the build never silently falls back to the older matcher.

To register a completed investigation, use:

```sh
uv run python -m ark_pipeline.cli.screened_crosswalk \
  --root /private/data-root \
  --investigation /private/investigation \
  --release REVIEWED-RELEASE
```

Then `just data-build` or `just data-update` selects the product during preparation.
For crosswalk selection alone, run `uv run python -m ark_pipeline.cli.crosswalk_refresh
--root /private/data-root`. Neither registration nor crosswalk selection loads the
database or rebuilds tiles. The registered pack is included in normal private
source staging for cloud builds. Crosswalk coverage and screening eligibility do
not establish independently measured biological precision.

## Historical matching results

The enriched results below are historical, not the coverage expected from a
fresh deterministic refresh. The [September assumption audit](../reference/roadmap_assumption_audit.md)
checks current behavior and source evidence. The [methodology](../../methodology.md#4-identities-and-taxonomy-matching)
also records differences between the uniformly strict lineage policy described
here and the acceptance conditions implemented by individual matching branches.

## Result

The global build reviewed all **171,604** species in the supplied IUCN export
against **2,004,790** species records in the supplied GoaT export.

| Outcome | Species | Share |
|---|---:|---:|
| Accepted IUCN SIS → GoaT/NCBI link | 126,285 | 73.59% |
| No defensible GoaT/NCBI candidate | 43,342 | 25.26% |
| Candidate exists but remains unresolved | 1,977 | 1.15% |

The 73.59% figure is **coverage, not estimated accuracy**. No representative
manually curated gold-standard crosswalk was supplied, so a numerical precision
claim would be unsupported. The build instead maximizes precision using
conservative acceptance rules and retains unresolved evidence separately.

Of the accepted rows, **125,883** are marked safe for automatic species-level
trait transfer. The remaining 402 accepted rows represent 196 NCBI TaxIDs that
each correspond to multiple IUCN species concepts. They are useful identifier
cross-references but must not be treated as equivalent taxonomic concepts.

## Reviewer

Ambiguous and near-name decisions are documented as:

> **AI-review (GPT-5.6 Sol)**

The reviewer label identifies the policy author and expert-review stage. It
does not imply that a model-generated guess was accepted without source
evidence. Accepted AI-review rows require compatible authoritative lineage.
Near-name rows additionally require independent GBIF/Catalogue of Life evidence
or a unique direct GoaT-native SIS concept identity; the output distinguishes
`GBIF_CORROBORATED_NEAR_NAME` from `GOAT_SIS_CORROBORATED_NEAR_NAME`.

## Sources

- IUCN taxonomy and assessments:
  `/Volumes/KA T7/Karl August/Ark-IV_data/IUCN_Red_List/`
- GoaT global species export:
  `/Volumes/KA T7/Karl August/Ark-IV_data/TOL/tol_species.tsv`
- NCBI Taxonomy `names.dmp`, `nodes.dmp`, `merged.dmp`, and `delnodes.dmp`
  downloaded on 2026-07-23
- GoaT API taxonomy index `taxon--ncbi--goat--2026.07.22`
- GBIF v2 species matcher using the current Catalogue of Life extended index

Exact source paths and SHA-256 hashes are stored in
`data/exports/iucn_goat_global/match_summary.json`.

## Matching tiers

### Tier A — deterministic

Accepted only when there is a unique candidate and at least two compatible
lineage levels:

- exact current scientific name in GoaT;
- exact current scientific name in NCBI Taxonomy; or
- an exact authoritative NCBI synonym.

An exact spelling alone is insufficient. The lineage check rejected 12
cross-kingdom or deep-lineage homonyms, including:

- IUCN plant *Solenopsis bicolor* versus an ant GoaT TaxID;
- IUCN plant *Salacia pyriformis* versus a hydrozoan GoaT TaxID; and
- IUCN plant *Burttia prunoides* versus an orthopteran GoaT TaxID.

### Tier B — exact concept with expert resolution

Used when an exact name or synonym has more than one candidate, or when an
accepted GBIF concept provides the bridge. Acceptance requires:

- an exact GBIF species concept;
- the matching IUCN SIS ID in GBIF's IUCN source status where applicable; and
- compatible GoaT/NCBI lineage.

### Tier C — AI-reviewed near name

Near-name candidates are generated only within tightly blocked genus/epithet
groups and at edit distance no greater than two. They are accepted only when:

- GBIF resolves the IUCN name and candidate to the same exact accepted species
  concept;
- the IUCN source ID is confirmed or the exact GBIF species match is otherwise
  explicit;
- at least two lineage levels agree; and
- there is no more than one lineage conflict.

The initial near-name rules proposed 196 matches. Independent GBIF concept
validation rejected 136 of those superficially plausible candidates. After
supplemental GoaT lineage was added, the final accepted Tier C set contains 79
rows.

### SIS-provenance bridge (deterministic, automatic refresh)

GoaT's own `redlist_category` attribute carries field provenance that records
the IUCN SIS taxon ID and assessment ID GoaT itself matched when it ingested
the Red List summary (`source_slug`, e.g. `101/1101`). The matcher now
parses these identifiers from the GoaT export's per-field provenance JSON and
uses them as independent, identifier-level evidence:

- For an IUCN row with **no other candidate**, a GoaT taxon whose recorded SIS
  ID equals the row's `internalTaxonId` becomes a deterministic bridge
  candidate (`match_method = GOAT_REDLIST_SIS_PROVENANCE`, Tier B,
  reviewer `deterministic`).
- For a row with exactly one candidate that the tier rules alone rejected, the
  same recorded identifier corroborates that unique candidate and accepts it;
  ambiguous rows with more than one candidate are never accepted this way.
- Both paths still require the same lineage guard (at least two agreements,
  at most one conflict), and estimated (ancestry-derived) redlist values are
  ignored because they are not anchored to an IUCN concept.

The bridge relies on recorded SIS IDs rather than shared status codes. This
keeps identifier evidence separate from category agreement; it does not prove
biological equivalence independently of the upstream match. The exported columns
`goat_redlist_category`, `goat_redlist_population_trend`, `goat_redlist_scopes`,
`goat_redlist_status_source`, `goat_redlist_source_date`, `goat_iucn_*` and
`goat_redlist_category_agrees` keep the native values and code-level
agreement visible for auditing.

### Tier D — unresolved

The accepted TaxID remains null. The best candidate and its evidence are kept
in the `review_candidate_*` fields and in
`unresolved_candidates.parquet`.

No fuzzy or higher-rank result is forced into the accepted crosswalk.

## Why coverage cannot approach 100%

For **38,437** of the no-candidate rows, GBIF independently confirmed the exact
IUCN SIS species concept, but that accepted species name still had no
defensible record or synonym in the supplied GoaT species export. This is a
GoaT/NCBI coverage limitation, not an unresolved string-matching problem.

Inventing a TaxID for these rows would raise nominal coverage while lowering
accuracy. They remain `NO_GOAT_NCBI_CANDIDATE`.

Coverage differs substantially by kingdom:

| IUCN kingdom | Matched | Total | Coverage |
|---|---:|---:|---:|
| Animalia | 80,107 | 93,607 | 85.58% |
| Plantae | 45,259 | 76,677 | 59.03% |
| Fungi | 916 | 1,302 | 70.35% |
| Chromista | 3 | 18 | 16.67% |

## Output fields to use

- `matched_ncbi_species_taxid`: accepted GoaT/NCBI species TaxID; null when
  unresolved.
- `match_status`: `MATCHED`, `REVIEW_UNRESOLVED`, or
  `NO_GOAT_NCBI_CANDIDATE`.
- `confidence_tier`: A, B, or C for accepted matches.
- `match_method` and `reviewer`: how the accepted decision was made.
- `lineage_source`: `NCBI_TAXDUMP`, `GOAT_API`, or `GOAT_EXPORT`.
- `taxonomic_concept_relation`: distinguishes current-name, synonym,
  GBIF-corroborated, and IUCN-split/NCBI-lump relationships.
- `ncbi_taxid_iucn_taxon_count`: number of supplied IUCN species mapped to the
  accepted TaxID.
- `safe_for_automatic_species_trait_transfer`: recommended gate for automatic
  species-level GoaT metadata joins.
- `review_candidate_*`: evidence retained for unresolved rows.
- `goat_redlist_category`, `goat_redlist_population_trend`,
  `goat_redlist_scopes`, `goat_redlist_status_source`,
  `goat_redlist_source_date`: GoaT's own IUCN summary import for the accepted
  TaxID ("direct" means the GoaT taxon carries its own value; anything else is
  ancestry-derived and must not be treated as a species assessment).
- `goat_iucn_source_slug`, `goat_iucn_sis_id`, `goat_iucn_assessment_id`,
  `goat_iucn_sis_confirmed`, `goat_redlist_sis_id_agrees`: provenance
  identifiers recorded by GoaT and the identity checks used by the
  SIS-provenance bridge.
- `goat_redlist_category_agrees`: code-level agreement between the IUCN
  assessment category and GoaT's native status; null for legacy pre-2001
  categories that the modern code set cannot represent.
- `iucn_scopes` and `iucn_systems`: the assessment's recorded scopes and the
  pipe-joined realm systems (`Terrestrial`, `Freshwater`, `Marine`) copied from
  the registered assessments file, retained for downstream interpretation; the current species builder still
  reads the assessment file.

Never deduplicate IUCN assessments by NCBI or GBIF ID. Keep threat category and
assessment scope keyed by `iucn_sis_id` and `iucn_assessment_id`.

## Rebuilding

`just data-build` and `just data-update` now refresh matches automatically from
the registered IUCN taxonomy/assessments, GoaT export and NCBI taxdump. They use
the deterministic rules above, restrict GoaT candidates to species rank, and
retain ambiguous or unsupported links with null accepted IDs. They do not
reuse older cached GBIF or supplemental lineage evidence. This automatic
crosswalk can have lower coverage than the enriched July result documented above.

Each refresh reconciles every IUCN species ID, rejects duplicate identities and
invalid accepted links, then publishes a versioned generation under
`GLOBAL_DATA_ROOT/derived/iucn-goat-crosswalk/current/`. The receipt records all
seven source hashes, matcher code, DuckDB version and output checksums. Unchanged
generations are reused; failed builds preserve earlier results. The original
reviewed crosswalk files are not overwritten. Counts and unresolved candidates
are included in the generation for optional follow-up review.

For manual enrichment using independently checked API evidence, the local build is:

```sh
python3 ark_pipeline/cli/crosswalk_match.py \
  --iucn-taxonomy "/Volumes/KA T7/Karl August/Ark-IV_data/IUCN_Red_List/taxonomy.csv" \
  --iucn-assessments "/Volumes/KA T7/Karl August/Ark-IV_data/IUCN_Red_List/assessments.csv" \
  --goat-species "/Volumes/KA T7/Karl August/Ark-IV_data/TOL/tol_species.tsv" \
  --ncbi-taxdump-dir data/reference/ncbi_taxdump \
  --gbif-validation-json data/exports/iucn_goat_global/gbif_validation.json \
  --gbif-bridge-json data/exports/iucn_goat_global/gbif_bridge_results.json \
  --goat-lineages-json data/exports/iucn_goat_global/goat_lineages_complete.json \
  --output-dir data/exports/iucn_goat_global
```

The API evidence files are generated by:

```sh
node archive/crosswalk/gbif_validate_iucn_goat_candidates.mjs INPUT.json OUTPUT.json
node archive/crosswalk/gbif_bridge_iucn_names.mjs INPUT.json OUTPUT.json
node archive/crosswalk/fetch_goat_lineages.mjs TAXIDS.json OUTPUT.json
```

All API responses are cached as build inputs so the final crosswalk can be
reproduced without silently changing decisions during a later taxonomy update.

### Candidate identity and partial names

Candidate counts and runner-up comparisons use distinct TaxIDs after combining
all evidence routes. Exact names and a bridge to the same TaxID corroborate one
candidate; the bridge key and evidence sources remain in the output. Distinct
TaxIDs remain ambiguous under the existing acceptance guards.

NCBI `includes` and `in-part` name classes remain explicit review evidence, not
synonyms. Alone they yield a null accepted TaxID and an unsafe trait-transfer
flag, even when only one IUCN split member occurs in the supplied input.
Independent direct SIS provenance can corroborate a concept; partial names
alone cannot. Genuine synonym/current-name rules retain their lineage guards.

### Native SIS ambiguity

All native `rawValues` identities are inspected before accepting SIS evidence.
`goat_iucn_sis_ids` retains distinct positive SIS concepts and
`goat_iucn_raw_values` retains the complete raw provenance, including multiple
assessment identities. More than one native SIS concept cannot corroborate an
accepted equivalence or authorize automatic species traits, even when only one
concept is present in the supplied IUCN taxonomy. Such SIS bridges stay in the
review queue. Repeated assessments of one SIS concept remain eligible with the
same lineage guard. Ambiguous scalar SIS/assessment/slug fields are null.
