# Data download and updates

For agents using browser or computer control, follow the short
[IUCN browser-download instructions](03_iucn_browser_download.md).

Ark-IV exposes two data-acquisition commands:

```console
just download
just update
```

`just download` bootstraps every missing required source. `just update` checks
the same complete source set and refreshes only releases that are due. Both
commands validate existing files before doing anything, so either command is
safe to rerun. Current files are not downloaded or copied again.

For a read-only check of local source contracts, refresh intervals, and upstream
release metadata, run:

```console
just check-sources
```

This does not download archives, register files, or write an action plan. Exit
code 2 means an input is due, invalid, changed upstream, or its release check
failed. `current` on a source row describes the local inventory; the separate
`*-release` rows describe upstream checks. `unchecked` means no IUCN API token
was available and requires a manual release comparison.

Use `just check-sources --profile taxonomy-review` or `--profile phylogenetic`
to validate optional source packs as well. New releases of intentionally pinned
COL XR/OTT inputs are reported as `update-available`; they do not automatically
advance the pin or block the normal required-source update workflow.

For the complete build through spatial processing, metadata, metrics and map
tiles, use `just data-build` or `just data-update`. These refresh the species
crosswalk after acquisition and before spatial pair processing. Preview the steps
with `just data-build --dry-run`. A missing-file or authorization action returns exit code 2,
so dependent stages stop. `just data-status` inspects readiness without updating
anything. See [the complete workflow](01_data_pipeline.md).

## Storage and setup

Set a writable large-data directory in the ignored `.env` file:

```dotenv
GLOBAL_DATA_ROOT=/path/to/Ark-IV_data
IUCN_DATA_AUTHORIZED=false
```

Without configuration, the portable data root is `./data/external`. A complete
raw acquisition currently needs about 39 GB, while derived map products need
additional space. The active manifest is
`$GLOBAL_DATA_ROOT/acquisition/current.json`; immutable source snapshots and
historical manifests sit below the same acquisition directory.

## First download

Run:

```console
just download
```

The coordinator downloads missing public sources, including HydroBASINS, GoaT,
NCBI Taxonomy, and the pinned GBIF Backbone. It then checks the restricted IUCN
sources.

IUCN does not provide a supported bulk spatial API. If authorized files are
missing, the command creates their exact destination directories and writes:

```text
$GLOBAL_DATA_ROOT/acquisition/action-required.json
```

That plan contains the configured release, official page, exact filenames,
download URLs where available, and destination for every outstanding IUCN
source. The account holder must log into IUCN, accept its terms, submit a
truthful intended-use statement, and download the files through the official
website route.

Keep browser downloads on the large-data disk. The spatial polygon archives
alone are about 33 GB. Do not commit raw IUCN files or credentials.

Once the files are present, record the account holder's authorization in
`.env`:

```dotenv
IUCN_DATA_AUTHORIZED=true
```

Then rerun the same command:

```console
just download
```

It detects the staged files, validates them, and registers them in place. No
separate target-listing, registration, or doctor command is needed.

## Updates

Run:

```console
just update
```

The coordinator:

1. Validates every registered required source.
2. Skips public sources that are still within their update interval.
3. Uses HTTP validators when the provider supplies reliable ones.
4. Compares versioned inventories, such as HydroBASINS v1c, with the registered
   release and required filenames, refreshing when the release differs or required
   inventory files are absent from the manifest.
5. Checks the configured IUCN release against the registered IUCN sources.
6. Automatically validates and registers a newly staged authorized IUCN
   release.
7. Compares live geoBoundaries country release IDs and the HydroBASINS archive
   links with the configured inventories. Differences require inventory review;
   they do not silently select new geometry.

If `IUCN_REDLIST_API_KEY` (or the legacy `IUCN_REDLIST_KEY`) is configured,
`just update` makes one lightweight API version request and compares it with
every configured Red List tabular/spatial release. Monitoring no longer requires
fetching the full assessment catalogue. That optional catalogue remains available
through `sources_acquire update --source iucn-assessment-catalog --root ...`.
Without a token, the upstream IUCN release is explicitly `unchecked`; the operator
must compare it with the published release before changing the inventories.

Failed checks and detected release changes stop the update workflow with exit
code 2 and remain in `action-required.json`, including release-level warnings
that have no download targets yet. An upstream check does not verify changes to
archive contents under unchanged URLs/versions, nor does it discover updates to
the separately pinned IUCN islands geometry or research inputs.

## Idempotence and failure behavior

Every registered file has a byte size and SHA-256 checksum. Sources can also
declare column, archive-member, and exact-size requirements. Downloads are
written to a staging directory first; the active manifest changes only after
the complete source passes validation.

Existing inputs are checked for presence, recorded size, and declared source
contracts before they may be skipped or reused with HTTP validators. Pinned
checksums are always verified; use `sources_acquire doctor --deep` for a full
checksum audit. Missing inputs are reacquired without conditional headers;
damaged immutable snapshots fail rather than being overwritten. A not-modified
response records a separate check time, preserving the original acquisition date
and avoiding repeated requests until the next refresh interval.

Manifest publication merges completed sources under a short filesystem lock.
Independent acquisitions preserve one another's registrations; competing changes
to the same source fail instead of silently replacing the newer selection.

For an isolated, resumable public-source refresh, `sources_acquire update` accepts
`--staging-root /path/to/private/staging`. Existing staging directories remain
untouched when a separate root is selected.

Public HTTP transfers retain a small `.download.json` receipt beside partial
files. Known content lengths must reconcile before validation. A retry resumes
only when the request URL, retained byte count, and an HTTP validator support it;
`If-Range` protects against mixing representations. A changed representation is
downloaded afresh, and a mismatched range is rejected without appending bytes.
Unidentified partial files are not assumed safe to resume.

## COL XR and legacy GBIF review

The legacy 2023 GBIF backbone remains registered for its own numeric identifiers.
The maintained taxonomy used by GBIF is Catalogue of Life XR. The optional
`col-xr` source pins the **2026-10-16 XR** Darwin Core Archive from ChecklistBank;
it does not replace application IUCN identities, existing crosswalk decisions,
or legacy `gbif_taxon_id` values.

```console
just col-download
just check-sources --profile taxonomy-review
just taxonomy-review /private/crosswalk/gbif_review_input.json /private/review/col-xr.json --limit 100
```

The review consumes the current matcher's candidate JSON array. It streams both
registered archives, compares exact canonical species names and documented
synonyms, and requires accepted species targets with compatible supplied kingdom
and family. Missing targets, missing lineage, homonyms and conflicting concepts
remain unresolved or ambiguous. COL generic/specific-epithet fields are used
instead of stripping authorship with a name heuristic.

The report records the queue hash, source release/hash identities, input and
reviewed denominators, and distinct `col_xr`/`legacy_gbif` namespaces. Canonical
agreement is **not an identifier mapping**: `identifier_identity_confirmed` and
`eligible_for_trait_transfer` remain false. This report is not accepted as the
matcher's external-confirmation input. It is the migration review surface before
adding independently confirmed IUCN/NCBI/COL identifier evidence. The COL19 ID
mapping download is a mapping from the 2019 COL checklist, not from GBIF IDs.
All queues and generated reports remain private under the data policy.

Freshness checks query ChecklistBank's latest issued `xrelease` for COL source 3,
rather than the rolling `xr_latest` download alias, which can lag a published
monthly archive. The download directory may reject automated listing requests;
the public JSON metadata API exposes the release identity independently.
An unavailable optional COL/OTT release check remains visible in the report but
does not block required-source acquisition; explicit read-only checks return 2.

## OTT freshness and new releases

The optional dated-tree input remains paired with the reviewed **OTT 3.7.3** pin.
`just check-sources --profile phylogenetic` compares that pin with numeric stable
release directories on the official [OTT download server](https://files.opentreeoflife.org/ott/).
Draft directory names and the `current/` alias are not used to choose a version.

On 2026-10-27, 3.7.3 was still the newest versioned archive. The live
`POST https://api.opentreeoflife.org/v3/taxonomy/about` endpoint reported version
`3.7` / source `ott3.7draft3`; that deployed service response does not resolve the
newer downloadable patch number. The published
[3.7.3 release notes](https://tree.opentreeoflife.org/about/taxonomy-version/ott3.7.3)
date the patch to 20 June 2025.

When a newer archive appears, stage its explicit versioned URL separately,
record/check its SHA-256 and internal version, and compare `taxonomy.tsv`,
`synonyms.tsv`, and retired-ID mappings in `forwards.tsv`. Review changes against
the dated tree's OTT tips before advancing the acquisition and prototype pins
together. Retain the previous snapshot and receipts. New taxonomy alone neither
updates the dated phylogeny nor justifies moving biological/genomic evidence to
a different species concept.

For IUCN, the configured inventories currently describe Red List 2026-1:

- 30 polygon archives;
- 17 point archives;
- 14 HydroBASINS relationship-table archives;
- the authorized assessment and taxonomy tables.

HydroSHEDS HydroBASINS geometry is a separate public source: standard revision
v1c, standard and customized-with-lakes archives for all nine regions, levels
1-12. Its server does not provide useful stable HTTP validators, so its versioned
inventory is authoritative. Both commands report a complete eighteen-file v1c
inventory as `current` without network transfer; a nine-file standard-only
manifest is refreshed at the same provider release.

Interrupted or invalid downloads never replace a working manifest. Rerunning
the same command resumes the workflow from its recorded state and reports one
consolidated status for all required sources.

## Offline-verifiable acquisition safeguards

The GoaT exporter requests native Red List and sequencing/sample attributes,
retains field provenance separately from values, and checks unique IDs and a
stable total before publishing. Page checkpoints include request identity;
incompatible requests require an explicit restart. Interrupted runs retain
committed pages and the previously selected output.

When the tree-only and attribute-query totals agree, the exporter first requests
a lightweight page of taxon IDs, then fetches the full attributes for exactly
those IDs. This avoids repeating global attribute aggregation for every page.
Each attribute batch must return exactly the requested IDs in order, with a
matching total; ignored ID filters, omissions, or changed identity totals abort
before the page is committed. Different tree/attribute totals retain the original
filtered traversal. Values, estimates, lineage, provenance, output order, and
existing compatible checkpoints are preserved. Resumed-run throughput and ETA
count only rows downloaded in that run.

A live paired sample on 2026-10-27 returned identical result arrays for 9,990
taxa: the original query took 36.1 seconds, while ID discovery plus the restricted
attribute query took 16.2 seconds (about 2.2 times faster). Requests ran sequentially
through Bun on macOS arm64 with gzip; provider cache state was uncontrolled.
This is a page-sized measurement, not a measured full-refresh runtime. A separate
172-taxon live export completed through the new two-page path and final count
validation. Provider implementation: [API ID-set filtering](https://github.com/genomehubs/cli-generator/blob/main/crates/genomehubs-api/src/routes/search.rs).

The optional IUCN catalogue requires exact unique-row reconciliation against a
stable advertised total for each kingdom. Early empty pages, overshoot, repeated
pages, changing totals, or release drift abort before replacing the selected
catalogue. Without an advertised total, an empty page terminates pagination.
Advertised totals remain in metadata; complete current-release catalogues remain
reusable under metadata schema 2; legacy completion metadata is refreshed because
it does not prove total reconciliation. Interrupted runs reuse completed pages for
the same release. Page, catalogue, and metadata writes use independent temporary
files and atomic replacement, so writers cannot collide on a shared `.part` file.
Versioned public inventories also check required filenames and expected
sizes, reusing checksum-verified files when the inventory grows at one release.

`sources_fetch_iucn_details` (invoked with `uv run python -m
ark_pipeline.cli.sources_fetch_iucn_details`) takes `--queue /path/to/queue.json`
and `--output-dir /path/to/private/evidence` and fetches explicitly queued `sis_ids` and
`assessment_ids`. Add `--include-latest-global` to fetch latest global assessments
only when the returned SIS identity agrees. Cached responses must match both
endpoint and release. Missing IDs remain explicit; authentication errors and
release drift abort. Retrieval does not reconcile taxonomic identities, and all
responses and reports remain private under the data policy.
