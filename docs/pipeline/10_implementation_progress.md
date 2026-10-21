# Scoring/evidence implementation progress

This log tracks implementation of [the milestone plan](10_phylogenetic_scoring_plan.md).
Code completion, fixture validation, scientific acceptance and full-data execution
are separate gates. No new default scientific score is adopted.

**Expanded closure, 20 October:** the follow-up audit found incomplete real
calibration and robustness evidence, and the user expanded scoring to all
acquired clades. These additions now pass: 124 final cloud checks, complete-tree
preparation, the real diagnostic, separate repair sensitivity, the nine-scenario
robustness matrix and a fresh immutable download/restore/reuse/API proof. Earlier
mammal-only completion statements below describe the previous, narrower scope.
Full spatial execution and scientific adoption remain unperformed gates.

## Scope and contracts

- Original reference: complete mammalian crown, stem excluded, branch units Myr.
- Expanded candidate: the full released global root, connecting branches retained,
  stem excluded. Every assessed record remains present with availability reasons.
- DNA scenarios: exact website rule, any recorded assembly, explicitly labelled
  taxon-level assembly/BUSCO proxy. Unknown evidence and unsafe identities stay explicit.
- Retain polygon/point/basin support as OR masks outside serving species lists.
- Range denominators: distinct global res7 cell counts and mapped cell area;
  any-touch distributions are not occupancy. Jurisdiction fractions use cell membership.
- Existing categorical metrics, PMTiles, coarse Arrow and fine arrays stay unchanged.
- Private analytical generations use existing receipt/checksum/atomic-current machinery.
- Do not acquire new feeds, load databases or activate serving.
- Updated execution authorization: larger verification and runs use the cloud
  sandbox; laptop work is limited to lightweight editing and checks. Pre-full-run
  spending is capped at USD 75, including failed attempts. Routine spatial runs
  use the fast profile. A correct-path run is the final action and requires Karl
  August's explicit authorization. This supersedes the original execution default.

## Sequence

| Plan step | State | Evidence / remaining gate |
| --- | --- | --- |
| 1. Freeze baseline/contracts | Implemented | Baseline commit `92b8562cdfe7db69ae7f08b7c3422df58bf51f43`; preserve v3 policy, partial-basin limitations and existing source pins |
| 2. Reusable fixtures/census | Fixture validated | Durable sample discovery, selection/code/dependency identity, independent census cache and opt-in bounded scan workers; 30-archive empty-census-cache 1/4-worker parity measured; OS cache uncontrolled |
| 3. Spatial calibration | Expanded FAST diagnostics complete | Earlier eight-row check plus six complete real geometries covering holes, fragmentation, dateline and large ranges; separate repair sensitivity and explicit cohort exclusions. Correct coverage/error acceptance remains gated |
| 4. Performance | Real-input tuning complete | Archive/FAST/metric worker scaling, 2/4/8-GB reductions, batch no-go result, canonical range improvement and DNA projection/reference parity; correct-mode execution gated |
| 5. Spatial/evidence/metadata | Fixture and fresh metadata validated | OR support, range counts/area, explicit basin omissions, normal metadata build/reconciliation and shared tree-independent DNA evaluator |
| 6. Tree foundation | Mammal reference and global generation validated | Complete 2,294,776-tip released tree; 150,270 checked ED/EDGE rows of 171,604 targets. Mammal reference and independent engine arithmetic retained; unavailable identities explicit |
| 7. Scenarios/detail contract | Contract validated | Compact kernel/cache, paginated typed endpoints, explicit SQL projections, regenerated client types and coverage-profile isolation |
| 8. Stewardship/robustness | Producer and full fixture matrix validated | Boundary producer, fractions, separate aggregates; nine scenarios/21 comparisons with independent OR, fine/coarse and range-budget oracles |
| 9. Isolated rehearsal | Expanded scope passed | Final 124 cloud checks; FAST mixed routes through Arrow/PMTiles and APIs; selected 8 CPU/32 GiB, global command dry-run and committed download/restore/reuse/API proof |
| 10. Full build | Gated | Freshness preflight, scientific acceptance and explicit execution authorization |
| 11. UI/map modes | Deferred behind pipeline | Redesign mocks rejected; requested direction is more filters/options in the existing table. UI selection does not block pipeline work |
| 12. Broader scope | All acquired clades included | Checked descriptive scores across the acquired tree; new sources, tree/date acceptance, portfolios and new default scores remain separate decisions |

## Baseline limitations

The plan's historical timings and coverage counts are observations, not measurements
of this implementation. Retain current partial-basin omissions explicitly. Current
input versions must be checked through registered-source acquisition before a full
run and pinned for that run. UI work and map modes must reuse retained memberships.

## Checks and measurements

Recorded below as implementation proceeds. Executable experiments use synthetic
fixtures or the checksum-pinned private saved pilot, with isolated outputs. No
production data or processes are modified.

### Cloud checks so far

- First focused cloud batch: 40 passing checks, including production fast
  polygon/point/HydroBASINS paths, reducers, ranges, scenarios and isolated APIs.
- Expanded cloud batch: 75 passing checks, including existing species/API and
  preparation contracts. No correct-path geometry coverage was executed.
- Synthetic probe: 65,535 nodes / 32,768 tips, 5,012 represented tips and 200,000
  memberships (100 cells × 2,000 species), one calculation worker, one DuckDB
  thread and 256 MB DuckDB limit. Linux x86-64, Python 3.13.16; 4 requested CPU,
  12 GiB RAM, 5 visible CPUs. These are synthetic measurements, not global ETAs.
  Gap kernel 0.0218 s; evidence scan 0.0725 s; nearest diagnostic 0.1370 s;
  cold ranking 0.1644 s; warm ranking 0.0406 s; four concurrent warm requests
  0.1272 s; serialization 0.0098 s; dense aggregation 0.0394 s.
  Peak process RSS was about 315 MiB. Full scenario JSON was 12.8 MB; API
  pagination is required and no such full response is sent to the browser.
- Budget ledger and exact payload hashes live in ignored
  `sandbox/coreweave/results/scoring-implementation/`. Reservations count full
  allocation lifetimes conservatively, including a failed initial launch.

### Saved mammal parity (cloud, private inputs)

The compact implementation was compared with the checksum-verified saved pilot,
using its complete 7,965-tip mammalian crown, 12,546 baseline evidence records and
5,945 assessed species. It reproduced all 7,965 marginal gaps exactly (maximum
absolute difference 0 Myr), matched nearest relatives/distances and matched all
4,225 previously eligible priorities with zero priority mismatches. The baseline
again contained 724 represented tips and 59 unresolved positive records. These
are same-snapshot arithmetic results, not updated global coverage or scientific
acceptance. No source receipts were edited; no map/database was activated.

### Earlier headless checks

- Final cloud feature batch: 121 Python tests passed in 24.994 s. This includes
  scenario/EDGE arithmetic, DNA generations, spatial analytics and omission
  semantics, source census/resources, metadata/serving/API contracts, profile
  isolation, fast mixed routes and an 11-stage interrupted/resumed FAST replay.
- After the final census dependency-identity change, all 9 affected census and
  benchmark tests passed in 1.960 s. These overlap the earlier batch; counts
  should not be added as unique coverage.
- An additional 24 existing serving export/coherence/metric checks passed.
- Cloud frontend checks: 13 focused tests passed; Svelte check reported zero
  errors/warnings; targeted client type-aware lint and formatting passed.
- Lightweight local checks: Ruff on changed Python and `git diff --check`.
  No local global-data calculations or heavy verification were run.
- Checked surfaces: typed analytical APIs/generated types, species-detail SQL
  projections, coarse/fine base-payload compatibility, source-route support,
  profile switching, stale-generation and missing/invalid-input responses.
  Real map/table controls, search/selection/reset and keyboard interactions are
  pending UI implementation. Static mocks alone were inspected at 728/360 px.

### Larger synthetic probe and resource comparison

On the same 65,535-node / 32,768-tip tree (5,012 represented tips), the dense
probe used 5 million memberships (500 cells × 10,000 species), one worker, one
DuckDB thread and a 256 MB DB limit. Gap calculation took 0.0220 s, evidence
0.0696 s, nearest diagnostic 0.1413 s, cold ranking 0.1651 s, warm ranking
0.0433 s, four concurrent warm requests 0.1404 s, serialization 0.0107 s and
dense aggregation 1.0200 s. Process RSS peaked near 705 MiB; sampled scratch
high water was 108,658,688 bytes (97 samples at 10 ms, therefore a lower bound).
Full scenario JSON was 12,827,161 bytes, reinforcing pagination. The 1,869-byte
aggregate output is highly compressible synthetic data, not a payload forecast.

A fixed-workload ABBA comparison of 256/512/512/256 MB DB limits measured dense
aggregation at 1.0263/0.9685/0.9949/0.9979 s. Process RSS was approximately
709/875/840/680 MiB. The 256 MB runs sampled 75,104,256/104,464,384 scratch
bytes; 512 MB sampled none. All four cell-score hashes matched exactly. The
small time difference and higher RSS do not warrant changing global defaults.
Cgroup lifetime peak includes other work and is not attributed to one run;
unavailable pressure counters remain explicitly unavailable in the private report.

A separate support-format fixture reduced seven raw pair rows (2,386 Parquet
bytes) to four fine and three coarse relationships. List files totalled 1,366
bytes and support sidecars 1,013 bytes. Format overhead dominates this fixture;
it provides no global storage projection. No real archive cold-speedup claim,
global ETA or scientific geometry-error acceptance follows from these probes.

### Pipeline continuation and acceptance audit

The follow-up audit found and fixed gaps beyond the earlier separate feature
tests: the preparation command now exposes registered tree/index/tip and
stewardship inputs and can build separate default opportunity generations.
Reviewed identity bridges are retained independently of today's DNA rule.
Acquisition check timestamps no longer prevent unchanged tree-generation reuse;
mixed-case mammal class names are accepted. Sparse evidence/identity fields
survive Parquet publication rather than being dropped by first-row inference.
Unsafe DNA witnesses remain unknown after reload. The typed API now exposes
unresolved qualifying relatives without reviewed genus information.

The final affected cloud batch passed 70 Python tests in 5.052 s, including a
mixed-source FAST generation through real metadata, checked registered tree/DNA
enrichment, separate coarse/fine opportunities, stewardship and APIs. It checks
interruption/reuse, wrong-profile and unreceipted-partition rejection, dynamic
versus saved defaults and unchanged membership/metadata/base-metric hashes.
These tests overlap earlier batches; counts are not additive. The subsequently
extended acceptance fixture also passed in 5.410 s, including production source/
coarse builders, prepared-input recording and validated PMTiles publication.
Coarse Arrow/PMTiles, membership, metadata and fine metric hashes remained
unchanged after optional enrichment. That extended test overlaps the 70-test
batch; it is not 71 unique tests.
All 13 focused frontend tests passed again; the corrected isolated verification
environment produced zero Svelte errors/warnings and zero client lint issues.
Cloud and local OpenAPI exports have identical SHA256
`511c0c66e4339e957044cf9677a5c1daba737372146d5d0de8d2bc254834e7c8`.

A final 5-million-membership probe measured 0.9676 s dense aggregation, 0.0277 s
gap kernel and 0.1642/0.0402 s cold/warm ranking. Peak sampled process RSS was
720,777,216 bytes; scratch high water was 108,658,688 bytes at 10 ms (93 samples).
The separate tree-independent DNA probe used 200,000 synthetic evidence records
and 25,000 target species. Publication took 2.2940 s; cold website/assembly/proxy
scenarios took 2.2982/1.2184/1.2459 s, with website including the initial generation
load. Cached calls took approximately 30–44 microseconds before pagination and
serialization. Four concurrent warm calls reused the completed result. RSS
peaked at 396,124,160 bytes. Synthetic artifact bytes are not global forecasts.

[Full-run acceptance and source observations](12_pipeline_acceptance.md) record
the remaining scientific/execution gates, the current registered snapshots and
public metadata checks. This earlier fixture pass advanced no source. The later
prerequisite closure registered the exact reviewed tree/OTT mirrors and completed
release-checked metadata API caches in isolation; no production manifest advanced.

### Execution gates

- Pipeline work is independent of the later existing-table filter/options UI.
  Rejected redesign mocks have been removed; no real UI components were changed.
- Correct-path geometry coverage has not run. It remains the absolute final
  execution action and requires Karl August's explicit authorization.
- Fresh full-data builds, database loads and serving activation have not run.
  Source freshness and scientific acceptance remain separate gates.
- At the earlier fixture handoff, the private ledger reserved USD 3.0656 of USD 75 across full
  allocation lifetimes, including the failed launch; this is not an invoice.
  Measurements and exact payload hashes remain in the ignored sandbox results.
- Owned cloud sandbox `df5147e2-6b95-4ea3-bbd2-77afa3bd0c21` was explicitly
  stopped at handoff. Earlier successfully created task sandboxes were also
  stopped. No local development servers were started.
- The continuation sandbox `27306bf8-7ced-4581-aa8c-3dc2a86bd73c` was also
  explicitly stopped after recovering the extended acceptance, source metadata,
  dense reduction and DNA scale reports. The extended fixture preserved hashes
  for 13 protected base/membership/metadata files. That completed the earlier
  feature-fixture pass, but did not complete real-input resource tuning or the
  pre-full-run audit. The full-data phase remains unperformed and requires its
  execution scope/budget, source freeze and final correct-path authorization.

### Follow-up readiness audit and real-input tuning

The follow-up audit found hardcoded full-job resources,
planning that tried to discover a sample before staging, cgroup file cache
reducing automatic worker availability, and shared DuckDB setup that applied
threads but ignored configured memory/scratch limits. These are corrected and
connection-level/cloud contracts pass. The launcher also now accepts an existing
IUCN cache and explicit cache-only mode; its previous flag-presence check failed
to recognize the current metadata command's default capability.

An explicit FAST partial-basin profile retains the existing audited omissions;
strict profiles continue to fail on missing IDs. The FAST omission/reuse check
passed separately, without running correct geometry coverage.

All 30 selected polygon archives were restored and checksum-verified privately
in the cloud. Regenerating the 1,000-row current-v3 sample took 1,152.1 seconds
and produced exactly the saved fixture's SHA256
`3d4998518a598b8f85e1413b90c7052bf6e9b22ba5f8b1831547dd6fdcd35f3b`.
Preparation was serial and peaked near 2.57 GiB process RSS; it is separate from
coverage timing. Native archive census, worker/RAM, real 2/4/8-GB DuckDB limits,
batches, payloads and registered-source preparation were subsequently checked;
the measured configuration decision is in the resource evidence. No global
speedup is claimed.

### Real-input findings, 20 October continuation

See [resource evidence](13_resource_evidence.md) for populations, timings,
within-host versus cross-host comparisons and the cost recommendation. The
largest measured algorithmic improvement is eliminating a redundant global
pair DISTINCT only after proving canonical serving-list uniqueness. On the
original 1.154-billion-membership base-52 part, range calculation fell from
140.48 to 17.27 seconds and sampled scratch from 22.97 GB to zero, with exact
integer/identity parity and machine-precision area differences.

The real-source preparation audit also found historical baseline-review flag
normalization and nonqualifying-bridge ordering problems. The historical flags
and review guard are preserved. Additional reviewed IUCN bridges are applied
only after the original review passes, cannot overwrite a different reviewed or
direct tip, and remain rule independent. Normal registered mammal preparation
passed with 724 website represented tips, 1,030 for any assembly and 89 for the
configured quality proxy. The checked scope is the original 7,965-tip crown,
5,945 assessed species and 12,546 baseline evidence records.

The final integration audit added a missing stewardship input producer. It
streams distinct mapped cells using existing tile boundary intersections,
retains overlapping jurisdictions, pins boundary source hashes and includes
the generated memberships in the analytical receipt. The main preparation
entry point forwards repeated framework/path options and rejects ambiguous
inputs before scheduling expensive work. The initial 20 affected cloud checks
passed; the final 117-check cloud batch also passed the publication/receipt additions.

A session interruption allowed three bounded sandboxes to expire. Recovered
measurements were preserved; unretained closure outputs were regenerated and
checkpointed before subsequent work. The finalization allocation used requested
4 CPU/16 GiB. Conservative full-lifetime reservations total USD 37.2864 of 75,
including failed attempts and expired allocations; this is not an invoice.
Foundation inputs and the completed prerequisite pack are committed in the
authorized private storage project. The earlier incomplete handoff is not used.

### Final pre-full evidence, 20 October 2026

This section records the earlier mammal candidate. The later all-clade scope,
representative calibration and robustness closure are recorded below.

The final cloud batch passed 117 checks in 28.318 seconds, with FAST geometry
coverage only. It includes mixed polygon/point/basin routes, source/metadata
contracts, tree/DNA generation and scenario arithmetic, separate analytics,
stewardship production, interruption/reuse, API/profile isolation and unchanged
base payload checks. These overlap earlier batches; their counts are not additive.

The normal metadata factory and shared release-checked reconciliation produced
173,498 metadata records. This is a metadata population, not scored coverage.
The DNA generation covers 171,604 assessed targets and 1,795,920 evidence
records. Its single-pass results and full metadata matched an independent
multipass reference under the website, assembly and quality 90/95 scenarios.
The fresh registered mammal generation retained all 7,965 crown tips and
matched the saved 7,965 gaps / 4,225 eligible priorities exactly.

The bounded real calibration used eight deterministic rows from the verified
current-v3 sample. FAST unsimplified/simplified outputs had 63,947/63,798 cells,
63,778 common cells and 169/20 excluded cells. Common-cohort top 1/5/10 percent
overlap was 1.0; correlation is null because the compared richness was constant.
This does not establish global simplification error or correct-path acceptance.

The isolated full-candidate command passed its read-only dry-run. Its source
manifest, review/generation receipts and code snapshot are frozen separately
from the original input catalogue. Full execution, final correct-path coverage,
scientific adoption, production loads and the table UI remain unperformed.

Private `scoring-prefull-final:v0` is committed. A fresh download through the
normal storage command passed its SHA256 check, extraction preserved relative
generation links, the mammal generation passed load validation and the normal
source-pinned DNA CLI returned `reused` against the restored output. The final
evidence is committed separately as `scoring-prefull-readiness:v0`. See the
[acceptance record](12_pipeline_acceptance.md#durable-prerequisite-retention)
for restoration scope and the full-run command. No further prerequisite
implementation or tuning work is outstanding for this retained-source scope.

Cleanup is complete: all 12 identified task sandboxes are stopped, including
the finalization allocation `115081e9-88c0-4aa9-8820-2c377bf4f5d0`; the tracked
report watcher has exited. No local development server was started. Final
lightweight local Ruff checks across pipeline/backend/cloud scripts/tests and
`git diff --check` passed. No supported code changed after the final cloud suite.

### Expanded all-acquired-clade closure

The user expanded the candidate beyond the original mammal crown. The complete
released root now retains all 2,294,776 tips and connecting branches, with the
incoming stem excluded in Myr. All 171,604 assessed records across 74 classes
remain in the output; 150,270 have checked ED/EDGE components in 68 classes.
The default gap has 95,702 available rows and 4,134 withheld for incomplete
baselines, 50,434 with unsafe genomic identities and 21,334 without
checked tree identities. These are availability counts, not global scientific
acceptance or a promise of 100% biological coverage.

The new compact ED/EDGE implementation matched the pinned object engine on
small trees with EX/EW, unknown relatives, polytomies and unary paths. Complete
preparation took approximately 153 seconds in the first measured execution;
the isolated parsing/risk probe took 37.46 seconds and peaked at 1.192 GB RSS.
The original mammal path remains available; global-root and mammal-crown
values describe different scopes and are not interchangeable.

The final input audit reconciles every SIS/assessment ID/normalized-name triple
across the independent reviewed genomic and tree-link generations. All 171,604
real identities matched. Adding that guard preserved exact Parquet hashes for
tree, evidence, species, scores and default gaps. No source receipt was edited.
The final cloud suite passed **124 checks in 28.25 seconds**, FAST geometry only.

The missing representative calibration now covers six complete real geometries,
including holes, fragmentation, antimeridian spans and very large bboxes, with
per-species changes and common-cohort hotspot reporting. The 1,000-row parent
sample has no invalid geometries; separate synthetic repair comparisons retain
the production zero-area rejection and adopt no alternate repair. The robustness
workflow has nine scenarios and 21 comparisons with independent source-OR,
fine/coarse sum and mapped-species range-budget oracles. Full populations and
limits are in [resource evidence](13_resource_evidence.md).

The exact expanded candidate command passed its read-only plan check. Private
`scoring-all-clades-final:v1` and `scoring-all-clades-readiness:v0` are committed.
A fresh normal storage download verified the inventory/hash, restored the same
isolated paths and relative links, and the normal source-pinned global CLI
returned `reused`. The restored generation passed all three DNA rules, concurrent
warm requests, invalid-parameter and stale-generation checks through fixture HTTP.
This supersedes the narrower readiness claim; it does not perform or authorize
the full spatial build, final correct-path coverage, database load, activation,
scientific adoption or table UI.

Conservative full-lifetime compute reservations now total **USD 45.7984 / 75**,
including expired attempts. This is not an invoice. The interrupted expanded
worker's local diagnostic reports survived; its unretained generation was
regenerated, then committed after each phase. Future full execution needs its
own lifetime/budget and durable retention window rather than the short
prerequisite worker limits.

The supplementary restored-API resource probe passed in 64.981 seconds with
5.076 GB sampled peak process RSS and scratch zero, including loaded tree/DNA
evidence and scenario caches. Its report is committed privately as
`scoring-all-clades-api-resources:v0`. The selected 8 CPU/32 GiB remains adequate
for the observed workload; API process counts need explicit serving memory.

Expanded cleanup is complete: all 14 identified task sandboxes are stopped,
including `a9880949-6349-46fa-ba05-6bf8361f12e5`. No local dev server or report
watcher remains. Final local Ruff and whitespace checks passed. No supported
code changed after the final 124-check cloud suite; subsequent edits are docs.
Checked surfaces were source/identity boundaries, generation/checkpoint reuse,
headless DNA/tree/range/robustness stages, coarse/fine base compatibility and
isolated scenario APIs. Real table/map interactions remain the later UI step.
