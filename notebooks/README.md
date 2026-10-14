# Serving export investigations

`data_overview.py` inspects record completeness, identifier/class joins, range
sizes, richness and overlapping ecosystem systems. `range_dna_investigation.py`
explores coarse mapped range, threat and species-level representation, fits
family-clustered conditional logistic models, and reports cohort and dark-set
cuts. `score_surfaces_investigation.py` compares the unchanged default ladder
with exploratory rarity, weight, date, class and boundary specifications, checks
range conservation and spatial dependence, and explores stewardship and score
components. These investigations do not alter serving artifacts or production
scores. Numerical outputs describe only the consumed snapshot and selected
cohorts, not all described life.

From the repository root:

```sh
just nb-graph-check
just nb-fixture /tmp/ark-invented-inputs
export ARK_NOTEBOOK_EXPORTS=/tmp/ark-invented-inputs
export ARK_NOTEBOOK_CROSSWALK=/tmp/ark-invented-inputs/crosswalk.parquet
just nb-check
just nb-export-smoke /tmp/ark-invented-html
```

The fixture output directory must be new or empty. Its organisms, identifiers,
cohorts and boundary codes are invented. It has enough variation for actual
logistic fitting; it is not a calibrated ecological simulation. Missing files,
missing required columns and empty input tables fail with an explicit input
message. Empty model selections or absent reference levels show an unavailable
model instead of inventing a result. Some small/singular cohorts cannot support
the configured fit; fitting errors remain visible.

Without environment overrides, the notebooks retain the original local defaults:
`data/global/exports` and
`data/exports/iucn_goat_global/iucn_goat_crosswalk.parquet`. No recipe acquires,
builds or moves these inputs. The displayed SHA-256 identity covers the consumed
files including crosswalk and boundaries, not provider dating or compatible
lineage. Review receipts separately before using real snapshots.
The age-decay comparison uses `ARK_NOTEBOOK_AS_OF_YEAR` (default 2026); future
assessment dates receive no age penalty. The 2015 date cutoff is an explicitly
fixed exploratory specification, not a rolling “last decade” filter. SQL paths are
escaped; optional catalogue labels use country codes without a catalogue.

`uv run notebook.py` uses its PEP 723 header. HTML export uses marimo's UV sandbox
for the same dependencies. Overview and score use Altair 6, DuckDB 1.5, Arrow,
Polars and H3; range analysis also keeps pandas `>=2.3,<3` and Statsmodels in its
own environment, independent of the pipeline's pandas 3. The 30 MB marimo output
cap permits local chart serialization; plotted frames select only needed fields.
It is not permission to publish a large payload. HTML exports stay local and can
include derived records even when interactive tables are hidden.

Representation flags may include samples/projects under the website rule;
“species-level DNA” does not imply a completed high-quality reference genome.
Unknown evidence is excluded from negative-evidence denominators. Scope overlap
is not a migration/diadromy measurement. Resolution-3 any-touch ranges are neither
occupancy, area weights nor IUCN AOO. Conditional odds ratios are associations;
family clustering and the selected mapped universe do not remove selection bias.
Assessment cohorts do not timestamp genome availability.

Top sets use deterministic cell-key tie breaking and report their own selected
universe. Zero or constant surfaces have undefined correlations. A null reference
is an unblocked diagnostic, not a spatial correction. Parent aggregation sums
child-cell budgets and can repeat species. Country-contact shares overlap;
ecoregions are biogeographic partitions, not protection. With sampled DNA weight
zero, gap-weight share is one in every positive-score cell, so the bivariate gap
axis is structurally degenerate under this ladder.

Every real input and derived result follows [DATA_POLICY.md](../DATA_POLICY.md).
Hidden tables, aggregate charts and local HTML are not redistribution clearance.
Do not commit or publicly host real notebook outputs without the separate
provider-permission and derivative review. Synthetic verification establishes
execution and fixture arithmetic, not global scientific validity.
