# Dated-tree, EDGE and sequencing-gap research

These optional investigations produce descriptive local research artifacts.
They do not change production metadata, map weights, API contracts or serving
caches, and do not certify official EDGE lists. The selected dated-tree input,
registered IUCN assessments and existing website DNA rule are preserved.
Historical reports, source records and measured biological results remain private.

## Run and source identity

Install the optional Python engine with `uv sync --group edge`. Its git revision
is pinned in `pyproject.toml` and checked by scoring/benchmark entry points.
`config/edge_prototype.toml` pins the tree, OTT taxonomy, reference supplement,
alternative median and calibration cache; `config/edge_benchmark.toml` pins the
original-author calculator and mammal benchmark inputs separately.

With an authorized local source pack and reviewed crosswalk configured:

```sh
just edge-download
just edge-prototype 256 42
just edge-tree-download 8
just edge-validate 128 42
just edge-link
just edge-link-expand
just edge-link-synonyms
just phylogenetic-gap /path/to/website-validation-report.json 20
just edge-baseline-review
just phylogenetic-gap /path/to/website-validation-report.json 20
```

Download recipes acquire public inputs explicitly; they are not ordinary
application checks. Direct CLIs support input/output overrides. For example:

```sh
uv run --group edge python -m ark_pipeline.cli.edge_prototype \
  --root /path/to/authorized/source-pack \
  --crosswalk /path/to/reviewed/iucn_goat_crosswalk.parquet \
  --input-dir data/edge/inputs --output-dir data/edge/mammals \
  --samples 256 --seed 42
```

Crosswalks require an adjacent `match_summary.json` with the exact assessment
source hash. Registered assessments and NCBI files use the normal source-pack
checks. EDGE-only scoring does not consume GoaT attributes, so its historical
reviewed crosswalk need not follow a newer GoaT acquisition. The gap investigation
uses fresh crosswalk/source checks and an explicit `--legacy-validation` receipt
for the matching website species generation. That exception does not apply to
ordinary serving builds. No local generation identifier is a public default.

All acquired inputs, indexes, receipts, author source, trees, comparisons and
reports belong under ignored `data/edge/` or an authorized external root.
Local access and successful arithmetic checks establish no redistribution grant.
See [data policy](../../DATA_POLICY.md#optional-dated-tree-investigation) and
[attribution](../../NOTICE.md#optional-dated-tree-prototype).

## Crown, units and arithmetic

Extract the configured mammal crown and exclude its stem. Branch lengths are
assumed to be dated lengths in millions of years (Myr); arbitrary distance or
substitution trees do not satisfy this interpretation. Nonfinite/negative lengths
and duplicate tip labels are rejected by scoring. Retain the complete crown,
including unmatched relatives, rather than scoring only the assessed subset.
Placeholder OTT-zero tips remain in branch sharing without invented identifiers.
Promoted nonspecies tips and taxonomy flags remain scientific acceptance caveats.

Fair-proportion ED divides each ancestral branch among descendant tips. Original
EDGE is `ln(1 + ED) + GE ln(2)`, with GE 0–4 for LC–CR; DD, NE, EW and EX have no
ordinary GE value. ED2 sums the terminal branch and ancestral lengths multiplied
by the extinction probabilities of the *other* descendants. GE2 is the focal
extinction probability; EDGE2 is ED2 × GE2. ED, ED2 and EDGE2 have Myr units;
original EDGE is dimensionless. Sum ED must reconcile with complete-crown PD.

The engine supplies category-specific empirical risk distributions. DD, NE and
unjoined tips use pooled risks, independently sampled; the deterministic point
uses the pooled median. Safely joined EX tips are removed with retained branch
paths preserved. EW uses fixed probability 0.97 and has no candidate designation.
Possibly-extinct flags and assessment dates remain recorded without category
conversion. Seeded draws reproduce within the same input/environment. The
validation stage assigns common uniforms by sorted tip name across identical tip
sets, avoiding sibling-order changes in assigned draws.

Risk quantiles from one tree measure risk sampling only. A local candidate flag
requires strictly above-crown-median EDGE2 in at least 95% of draws: CR/EN/VU,
DD/NE and LC/NT map to threatened, research and watch candidates respectively.
These flags omit phylogenetic uncertainty and are not ZSL designations.

## Audited identity routes

Direct links require exact SIS/assessment identity, global species scope, the
existing safe transfer gate, current species-rank NCBI identity after merge
canonicalization, a species-rank OTT leaf and uniqueness in both directions.
Ambiguous mappings, repeated labels, missing assessments and unsafe crosswalks
remain explicit outcomes. No fuzzy matching supplies risk or genomic attributes.

The exact current-NCBI name route additionally requires agreeing nonmissing
current genus and family, no conflicting OTT NCBI identifier and an unreserved,
unique leaf. Current names and lineages come from the same registered names/nodes
snapshot; old crosswalk lineages cannot silently substitute for them.

The independent IUCN expansion checks fresh assessment/taxonomy/SIS identity,
global species scope, exact name, genus/family and at least one additional
order/class agreement. Higher ranks may match exact names in the full OTT ancestry;
that establishes named-clade membership, not synonym equivalence. Comparable
conflicts, infraspecies, unsafe existing MATCHED concepts, reservations, repeated
labels and competing proposals are withheld.

Documented name changes require binomials with unchanged specific epithet and
OTT alias or current-NCBI evidence. Genus changes require family/order/class
agreement. Different-epithet aliases need concept review; alias types do not prove
identical circumscriptions. Another assessed species at the alias target,
conflicting current IDs and homonyms anywhere in full OTT taxonomy block acceptance,
even when only one corresponding leaf appears in the tree.

`tree_identity_verified` and `ncbi_to_tree_link_verified` have separate meanings.
New independent IUCN links keep NCBI verification false. Previous accepted links,
original join status and upstream genomic-transfer flags are preserved. Tree
identity does not authorize transfer from an old NCBI/GoaT candidate. Remaining
names are distinguished as species in OTT without a dated leaf, names only at
nonspecies rank, or no recorded OTT name. None establishes biological absence.

## Tree sensitivity and matched-input references

`edge-tree-download` streams the first requested eligible archive members. This is
a convenience sample, not random sampling or the full ensemble. Downloads stop
at that point: whole-archive MD5 is **not verified**. Receipts record received
prefix SHA256, archive member/full-member hashes and saved crown hashes.

Validation requires identical assessed cohorts and common risk draws. Extra
nonmammalian environmental tips without a mammal class may be pruned with paths
preserved and the resulting crown stem excluded; all exclusions/stems are recorded.
Other changed tip universes fail. Rooted topology hashes distinguish date-only
from topology changes. Ranks/top-100 frequencies include tied cutoffs and are
cohort-relative descriptive summaries, not confidence intervals. Two median
trees may differ in topology and dates, so their comparison cannot isolate dating
method. A current calibration cache cannot establish release-specific date
provenance when it postdates the tree release.

For the optional original-author benchmark, install R and run
`just edge-benchmark-setup`, then `just edge-benchmark`. The R packages `ape`,
`phylobase` and `data.table` are installed into an ignored local library. Acquired
author code has no declared repository licence and is never vendored. The wrapper
runs it unmodified in the private output directory and records R session versions.

The benchmark compares serialized identical trees/probability vectors with Duke
and an independent branch-enumeration oracle, at `atol=rtol=1e-10`. The oracle
multiplies other descendants' risks without dividing by focal probability.
The author's stochastic risk generator is not independently reproduced. The
Upham author sample is not the study's exact augmented tree set. Its diagnostic
removes author-labelled extinct taxa/outgroup, prunes to an identical uniquely
mapped cohort, preserves branches and excludes crown stems. It holds published
categories/median probabilities fixed. This deliberately changes branch sharing
and is not production complete-clade scoring or reproduction of published ranks.

## Mammal DNA-gap prototype

The gap is rooted `PD(S ∪ {s}) − PD(S)` on the complete stem-free crown, where S
contains represented tips. The experimental priority multiplies gap Myr by the
existing threat weight; it is distinct from EDGE/EDGE2. Nearest represented tip
and tree-path distance are descriptive, not DNA sequence similarity.

Representation preserves the website rule: nonempty sample, project or EBP
criteria text, or chromosome/complete-genome assembly with BUSCO ≥90. Whitespace
and empty-string semantics are preserved. Samples/projects do not prove completed
good genomes; assembly-quality/resampling diagnostics remain separate. Missing
evidence is not biological absence.

The baseline includes all uniquely linked eligible GoaT mammal species, including
unassessed relatives. Focal identities need fresh safe GoaT matches and unchanged
assessments. Unresolved/unsafe focal identities have null priorities, not zero.
DNA-negative priorities are withheld when an unlinked DNA-positive baseline record
belongs to the same genus/family; other incomplete-family evidence remains flagged.
All estimates are conditional on observed baseline coverage.

Greedy target selection recomputes uncovered branches after every choice and
resolves priority ties by SIS ID. It avoids repeated counting of shared gains;
it is a threat-weighted heuristic, not a proven optimum. Fixed-budget comparison
with legacy priorities also reports cutoff ties.

The baseline review uses documented current names, lineage, full OTT names/aliases
and unique leaves. Merged IDs with multiple leaves remain unresolved despite a
preferred name; internal nodes, hybrids, nonspecies taxa and absent branches are
not grafted or copied. Review receipts bind source/code/output identities and
preserve original input reports. Applying a compatible review transfers only
approved link columns onto fresh evidence. Stale or unapproved changes reject.

## IUCN authority for extinction status

Verified global IUCN species assessments overrule OTT `extinct`/`extinct_inherited`
flags. EX is extinct, EW distinct; DD/non-EX means not classified EX, not proof of
living individuals. Missing/NE assessments remain unresolved. Effective status,
authority, raw flags, conflict/override fields and assessment IDs remain recorded.
Status correction does not relax identity/genomic gates or invent absent branches.
Missing leaves remain unscored coverage gaps. Historical generations are never
silently rewritten.

## Outputs, notebooks and checks

Each stage writes JSON/Markdown reports with source/code/output hashes and
Parquet audit tables: score components and complete-clade scores; link evidence
and class coverage denominators; synonym/remaining-review evidence; sampled
stability and reference residuals; gap/baseline audits and greedy gains. The
benchmark also saves serialized cases, mappings, preparation and R session receipts.
These are local artifacts, not public datasets.

Six marimo companions inspect saved outputs without acquisition or mutation:
`edge_benchmark`, `edge_links`, `edge_links_expanded`, `edge_links_synonyms`,
`edge_validation` and `phylogenetic_gap`. Their PEP 723 headers retain optional
notebook environments. Run `uv run notebooks/<name>.py`, or edit with marimo.
`ARK_RESEARCH_RESULTS` overrides the selected report directory for isolated
fixtures; expanded/synonym baselines follow report provenance. Missing reports
show an explicit empty state; altered output hashes fail verification.

Use focused unittest discovery, for example:

```sh
uv run --group edge python -m unittest discover -s tests -p 'test_edge*.py'
uv run --group edge python -m unittest discover -s tests -p 'test_phylogenetic_gap.py'
uv run python -m unittest discover -s tests -p 'test_extinction_status.py'
```

Optional engine cases skip only when it is absent; exercise them with the group
installed before assessing research readiness. Small synthetic trees can verify
PD/ED, fixed-risk ED2/EDGE2, rooted gaps, greedy gains and identity safeguards.
Arithmetic agreement does not validate global coverage, biological priorities,
dates, uncertainty ensembles, calibration provenance or redistribution rights.
Production integration and scientific dataset acceptance remain separate work.
