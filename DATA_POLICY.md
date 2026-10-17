# Ark-IV data publication policy

Ark-IV's GitHub repository is a **code release**, not a data release. Source
access does not automatically grant redistribution rights, and a derived file
can remain restricted when it exposes row-level values, geometry, identifiers,
or rankings from its inputs.

The repository's `CC0-1.0` dedication applies to Ark-IV application code, not
to input datasets or generated data artifacts. It cannot supply rights that
a data provider has not granted.

This policy is a conservative engineering control, not legal advice. Re-check
provider terms and written permissions before every public data release.

## What may be committed

- Source code, tests, schemas, documentation, and example configuration with no
  credentials or machine-specific paths.
- Small synthetic test fixtures that cannot be traced back to a source record.
- Natural Earth boundary data, which Natural Earth places in the public domain.
- geoBoundaries gbOpen and RESOLVE Ecoregions 2017 assets when CC BY 4.0
  attribution is retained in the asset metadata and in `NOTICE.md`.
- Small Marine Regions-derived catalogue metadata under CC BY 4.0. The source
  EEZ product and geometry remain local because Marine Regions asks users to
  download its products from the official service rather than mirrors.
- Project-created tutorial media after every contributor has confirmed that
  the recording, imagery, audio, fonts, and other included material may be
  published.

## What must remain outside Git

- IUCN Red List downloads, assessment rows, range geometries, API responses,
  and databases or exports that expose those records.
- EDGE lists, ranks, rows, or reconstructable extracts unless ZSL has given
  express written permission for the specific release.
- Global H3 species distributions, cell/species lists, crosswalks, PMTiles,
  Arrow snapshots, Parquet partitions, and database dumps. They are generated
  serving data and may inherit restrictions from IUCN or EDGE.
- Protected Planet WDPCA downloads and derived geometry. Its terms prohibit
  redistribution and sublicensing of the data.
- Raw GBIF, GoaT, Marine Regions, or boundary downloads. Reproducible builders
  should point contributors to the official source instead.
- Secrets, `.env` files, access tokens, private keys, local database contents,
  scratch files, build reports containing absolute source paths, and personal
  information.

Store authorized local inputs and outputs under ignored `data/` paths or an
external data root. Never use Git LFS as a workaround: LFS changes storage, not
the provider's redistribution terms.

## Derived-data release rule

Before publishing any generated dataset or hosted snapshot, record:

1. Every input's provider, version/date, access route, terms, and required
   citation.
2. The written permission or licence clause that allows this exact derivative
   and distribution channel.
3. Whether commercial reuse is prohibited and how that condition is passed to
   downstream users.
4. A record-level disclosure review showing that restricted source values
   cannot be reconstructed beyond the permission granted.
5. The validation report, build commit, and responsible reviewer.

If any item is unresolved, publish the software without the generated data.

Public browser delivery is distribution. An `inline` response, a PMTiles range
request, an Arrow snapshot, a JSON API, or the absence of a download button
does not make the transferred data display-only. In particular, the current
species/cell endpoints can reveal species records and reconstructable spatial
memberships. Do not expose those endpoints or the map-serving artifacts on a
public host until the provider has confirmed the intended derivative and
delivery channel in writing, or the public API has been redesigned and reviewed
to return only permitted non-reconstructable results.

## Automated guard

`just release-check` inspects stage-0 indexed blobs and modes, including files
missing from the worktree. Required documents and boundary attribution are
validated against that same prospective commit. Unresolved index entries and
symlinks require review and fail the check. It separately scans non-ignored
untracked files conservatively before staging; unstaged edits to tracked files
are not part of the prospective commit. Credential patterns are scanned in
bounded chunks for every candidate, including large allowed assets. It rejects
common data formats (including Arrow and PMTiles), files under `data/`, known
restricted database paths, likely credentials, and files near GitHub's size limit.

`just release-history-check` additionally checks every local Git ref for the
two databases removed during the 2026-09-04 history cleanup. This is a
permanent regression guard: it prevents an old clone, branch, or tag from
silently reintroducing the restricted objects. The incident and remediation
are recorded in `docs/reference/restricted-data-history-incident.md`.

## Optional dated-tree investigation

The mammal prototype pins its public inputs in `config/edge_prototype.toml`.
The [dated-tree release](https://zenodo.org/records/19049120) declares CC BY
4.0. Its separate [reference software](https://github.com/jdduke24/dated-complete-tree)
uses BSD-3-Clause; that software licence does not license the tree data.
[Open Tree's data policy](https://tree.opentreeoflife.org/about/licenses)
uses CC0 where pre-existing source terms do not limit it. OTT 3.7.3 includes
third-party taxonomies, so retain its provenance and review inherited terms
before distributing derivatives. [NCBI's policy](https://www.ncbi.nlm.nih.gov/home/about/policies/)
places US-government-created material in the public domain and distinguishes
third-party copyrighted material.

The [Gumbs et al. 2023 article and S1 Data supplement](https://doi.org/10.1371/journal.pbio.3001991)
are used for local scientific comparison with attribution. This is a separate
source from ZSL's website priority lists. All downloaded trees, taxonomies,
supplements, input receipts, species scores and comparison reports remain in
ignored `data/edge/`. The computed outputs also use authorized IUCN
assessment data and therefore follow the existing derived-data release rule.
This optional acquisition is independent of the required global update pipeline.

`config/edge_benchmark.toml` separately pins the original EDGE2 authors' R
calculator and Upham et al. mammal-tree sample. The R source has no declared
repository licence: downloaded files and R-library dependencies stay under
ignored `data/edge/benchmark/` and are not vendored into application code.
Retain attribution for the [mammal source data](https://doi.org/10.5061/dryad.tb03d03)
and the Gumbs supplementary methods. Identity mappings and per-species
benchmarks stay local under the same derived-data release rules. A successful
arithmetic comparison does not establish redistribution rights or certify
official EDGE priority lists.
