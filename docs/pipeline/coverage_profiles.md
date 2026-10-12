# Coverage profiles and coherent serving bundles

`data-tiles` prepares one coarse archive, optional content-hashed Arrow snapshot,
raw fine lists, fine aggregates, matching PostgreSQL exports and boundary inputs
from the recorded preparation. Fine map cells are served dynamically. Use
`data-tiles --static-fine` for the optional full static fine archive, published
under `GLOBAL_PREVIEW_ROOT/tiles/static/current/` (or `OUTPUT_DIR/static/current/`
with an explicit tile-builder output directory). Its receipts, generations and
writer lock are separate from the default dynamic `tiles/current/` publication.
Static builds never select or replace the managed dynamic startup bundle; a
standalone static build still requires `data-tiles` before managed startup/load.
Static archives can be consumed directly through their published PMTiles and
metadata paths. A recorded coarse Arrow snapshot is copied into the static
generation and verified during reuse alongside its archive and metadata. Both
modes can be built first, refreshed, and reused independently.
`global-start`
selects a validated published bundle, or the compatible legacy preview;
`GLOBAL_SERVING_CONFIG` selects an explicit configuration. `global-db-load` loads
that configuration's recorded exports, rather than independently selected paths.
These commands build or load data only when deliberately run by an authorized operator.

Files are pinned by hard link under the atomic replacement producer contract.
Cross-filesystem mutable inputs are copied; managed generation inputs can be
referenced. Concurrent in-place writes to published inodes are unsupported.
Preparation verifies recorded checksums and guards observed changes before
publication. Configuration validation checks schemas, expected base-cell sets,
boundary records and recorded size/modification versions. These are finite
validation checks, not a guarantee of crash atomicity or arbitrary reader safety.
A preparation without SQL exports can serve a map but cannot be loaded into SQL.
Recorded SQL lists and optional inverse/expanded membership exports are compared
with the prepared coarse membership, including species IDs and resolutions.

## Whole-map selection

**Include passage in map scores** selects a complete serving dataset. Metadata,
Arrow, PMTiles, dynamic fine tiles, species queries, cell details, statistics and
SQL pools select the same profile. Profile selection is part of request URLs and
cache keys. Switching reloads the page; navigation retains the selected profile.
Without a profile manifest, only the resident dataset is available. Requests for
an unconfigured passage dataset return HTTP 409, without a resident fallback.
Unknown or repeated profile selections return HTTP 400.

Build spatial membership and downstream metrics with the same spatial profile
throughout preparation. The existing default
`config/spatial_semantics_iucn_richness_v3.toml` accepts seasons `[1,2,3,5]`.
A passage profile must be a deliberately prepared, separately supplied profile
accepting `[1,2,3,4,5]`. Do not relabel a resident bundle as passage-inclusive.
New metadata records the spatial profile digest, presence, origin and seasons.
Registration requires these declared seasons; it does not independently prove
that upstream spatial membership applied them.

Load each completed bundle into a separate PostgreSQL database before enabling
it. Registration rejects identical database URLs, but does not attest database
contents: the operator must load the matching exports. Set
`COVERAGE_PROFILES_PATH` to a private JSON manifest:

```json
{
  "schema_version": 1,
  "profiles": {
    "resident": {
      "serving_config": "resident/serving.json",
      "database_url_env": "ARK_RESIDENT_DATABASE_URL"
    },
    "with-passage": {
      "serving_config": "with-passage/serving.json",
      "database_url_env": "ARK_PASSAGE_DATABASE_URL"
    }
  }
}
```

Relative configuration paths resolve against the manifest directory. Credentials
stay in the named environment variables. Restart after configuring profiles.
`/api/coverage-profiles` exposes only labels, availability and selected ID.

## Separate species-range supplement

The species-range **Include passage** control adds a coarse display supplement;
it does not alter default map richness or scores. Build this optional artifact
with `serving_metadata --build-passage` or `GLOBAL_SPECIES_PASSAGE=true`, or run
`python -m ark_pipeline.cli.species_passage` with explicit registered source root,
profile, species metadata and output paths. Eligible passage rows use season 4,
the selected presence/origin codes, and direct unsimplified resolution-3 any-touch
coverage. Explicit `iucn_sis_id` metadata maps source IDs to serving IDs; names and
numeric equality do not establish that relationship. Ambiguous mappings reject
the build. Preparation accepts the optional supplement beside species metadata
only with its matching producer receipt. A passage Parquet found only in the SQL
export directory is rejected; an explicit verified supplement takes precedence
over any export copy. Prepared inputs record and pin that artifact; the loader
replaces its SQL table transactionally alongside the ordinary exports.
The passage receipt binds the profile and species mapping observed around intake.
Their checksums and file identities are checked again before replacing output;
observed changes reject the attempt and preserve the previous supplement/receipt.
These checks assume atomic input replacement and do not guarantee detection of
every transient concurrent mutation.

Requests with `include_passage=true` return the union at resolution 3, including
species with only passage coverage. An absent supplement returns HTTP 503.
`include_passage=false` preserves ordinary available-resolution fallback.

## Missing evidence and cache contracts

Optional metadata fields preserve spatial-only records and historical assessment
markers. Source categories on unresolved spatial records are explanatory evidence,
not reassigned threat scores. Old exports remain loadable with existing defaults.

Absent raw fine directories or requested base partitions return
`species_data_unavailable=true`, empty species and `stats=null`. The details UI
shows unavailable evidence without zero statistics or an empty-community claim.
A valid partition with no requested cell returns an available empty result.
Aggregate fine map rendering remains independent of raw-list availability.

Fine count caching keys the resolved generation and observed partition identities;
replacement, growth and current-symlink switches invalidate cached footer totals.
Dynamic resolution-7 availability/completeness is recomputed from the active
aggregate source, removing inherited static flags. Completeness retains the
existing rule that nonempty raw and aggregate base-cell sets must match.

Archive format version and representation identity are separate. Metadata supplies
`archive_version`; the client includes it in its PMTiles URL. PMTiles full/range
responses use an opaque ETag and `Cache-Control: no-cache`. A stale `v` returns
409 rather than bytes from another generation. Conditional and range reads use
one open descriptor; an If-Range mismatch returns the full current representation.
Older mutable URLs are not promised immutable bytes. Arrow retains its content-
hashed URL, and dynamic fine delivery retains its coverage version/profile key.

Cell requests validate the H3 cell and requested resolution before lookup, normalize
valid IDs, and reject unsupported nonempty ecosystem filters. Score weights and
possible threat/DNA products must be finite; settled defaults are unchanged.
