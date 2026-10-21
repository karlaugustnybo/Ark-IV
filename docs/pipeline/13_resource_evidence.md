# Pre-full-run resource evidence

Measured privately in CoreWeave on 19–20 October 2026. These are bounded
same-input stage measurements, not a global runtime forecast. Geometry coverage
used FAST throughout. Correct-path coverage has not run.

## CPU and worker scaling

The final metric trial used 16 original dense res7 list parts, 13,176,688 cells
and 10,418,454,895 species memberships. Each worker used one DuckDB/Arrow thread
and a production initializer, aggregation, validation and receipt publication.
On one requested 16-CPU/64-GiB host, alternating 8/16/8 workers took
121.89/112.41/123.36 seconds for the production work. Total trial time including
separate input hashing was 132.51/123.08/134.07 seconds. Peak process-tree RSS
for the sequence was 20.54 GB; sampled scratch was zero. All 16 output value
hashes agreed with the independent 8-CPU/32-GiB runs.

On requested 8 CPU/32 GiB, two 8-worker runs took 212.62/208.77 seconds for
production work, plus about 13.7 seconds each for separately measured hashing.
Host CPU models differed between allocations, so the cross-host difference
cannot be attributed to RAM. Earlier 16-CPU/32-GiB trials also fit below roughly
20.3 GB RSS. The measured metric workload does not require 64 GiB.

A source census over all 30 original polygon archives (about 33 GB) took
238.07 seconds with one archive worker and 87.83 seconds with four. The
source identities, counts and exclusions agreed exactly. Verified cache replay,
including checksum work, took 21.45/8.79 seconds. An empty census cache is not
an empty OS file cache: staging/read order was uncontrolled.

FAST pairs used 24 selected rows from the verified 1,000-row current-v3 sample,
including size-bin representatives and forced extremes. 4/8/16 workers took
104.87/100.04/97.72 seconds, with peak RSS 3.13/4.11/5.65 GB. Ordered logical
pair hashes matched across all three runs. This workload's long tail limits
extra CPU benefit; it does not support halving the full run by doubling CPUs.

The complete sample regeneration itself took 1,152.13 seconds and produced
exactly the saved sample's bytes. Verified selection reuse took 0.395 seconds.
The historical saved sample already had the current v3 policy; no scientific
selection change or receipt edit was needed.

## Algorithmic spill reduction

The original global range builder unnecessarily deduplicated already unique
canonical serving memberships. The replacement proves sorted unique cells,
disjoint base-cell partitions and sorted unique per-cell species in bounded
Arrow batches, then avoids the global pair `DISTINCT`. Arbitrary or overlapping
inputs still use the generic deduplication path; malformed conventional parts
fail validation.

For the same original base-52 part (823,543 cells, 1,154,246,810 memberships,
11,848 species), requested 4 CPU/16 GiB, four DB threads and a 4-GB DB limit:

| Range builder | Wall seconds | Peak RSS | Sampled scratch high water |
| --- | ---: | ---: | ---: |
| Original global DISTINCT | 140.48 | 4.76 GB | 22.97 GB |
| Validated canonical lists | 17.27 | 0.686 GB | 0 |

IDs, counts and all non-area values matched exactly. Area sums agreed at
relative tolerance 1e-12 (maximum observed relative difference 1.45e-14), due
to floating-point summation order. This is about 8.1× for this measured stage,
not for the whole pipeline. Raising RAM would have hidden avoidable work.

The same canonical contract is used for separate fine opportunity aggregation.
A bounded independent per-cell oracle covered the actual base-52 memberships;
no tree score or synthetic weight experiment is presented as global coverage.

## Real raw-pair reductions and batches

Two original polygon pair outputs were partitioned once; partitioning took
389.41 seconds with four DB threads. The densest base-52 partition contained
806,036,346 raw rows, reduced to 801,097,296 memberships in 823,543 cells.
All three DB-limit trials produced exactly the same ordered list and support
value hashes, including 4,939,050 removed duplicates.

| DuckDB limit | Wall seconds | Peak process RSS | Sampled scratch high water |
| --- | ---: | ---: | ---: |
| 2 GB | 164.49 | 4.02 GB | 26.57 GB |
| 4 GB | 149.90 | 4.61 GB | 23.03 GB |
| 8 GB | 135.04 | 8.89 GB | 15.17 GB |

This sequence retained OS cache and overlapped some independent prerequisite
work on the same allocation. It is a bounded configuration comparison, not a
noise-free estimate of every stage. The 8-GB setting reduced scratch by roughly
34% and time by roughly 10% versus 4 GB. It fits within 32 GiB for the serial
reduction. Native DuckDB profiles and sampled process/scratch metrics are both
retained; they measure different things. The configured DB limit is not a
process or sandbox memory cap. [DuckDB memory limits](https://duckdb.org/docs/current/operations_manual/limits).

The reduced list was 51,960,971 bytes and keyed support sidecar 154,021,783
bytes (2.96× the list bytes). That sidecar is an explicit storage cost outside
base map payloads, measured on two polygon sources, not a global projection.
It records source support, not confidence. The full build must retain enough
private disk and result-storage capacity for these artifacts.

Changing metric batches from 16,384 cells / 250,000 relationships to 65,536 /
1,000,000 produced identical output hashes, with outer times 77.22/77.38 seconds
on the same original dense part. Larger batches showed no useful speedup; retain
the bounded existing production behavior rather than increasing batch defaults.

## DNA preparation and scenarios

Projecting taxon ID plus six raw evidence fields before Python materialization
preserved every selected raw value, ordering and NULL across all 1,795,920
species-level evidence records. The wide-read generation took 84.64 seconds
and 6.59 GB peak RSS; the projected call took 36.06 seconds and 3.57 GB RSS.
The latter reused the same generation and ran second with uncontrolled OS cache;
this is evidence of lower memory, not an isolated 2.35× projection benchmark.

The later single-pass scenario implementation classifies every relative once,
counts all evidence and retains focal state only for unique safe targets. An independent multipass reference matched all 171,604 assessed rows and
full scenario metadata exactly under website, any-assembly and quality proxy
thresholds 90/95. In-process HTTP page-100 requests took 12.98 seconds for the
first website call (including generation load), 4.96/6.51 seconds for cold
assembly/proxy scenarios, and 3–7 ms for cached calls. Mammal calls took 0.612
seconds including the first generation load and 0.075/0.153 seconds for cold
alternatives, with cached pages around 9–17 ms. These are server-side probes,
not browser/network latency. Pagination remains required. The checked mammal
population is 7,965 complete-crown tips and 5,945 assessed species. The original
website baseline has 724 represented tips; any assembly gives 1,030 and the
configured quality proxy 89. These are same-snapshot scenario differences.

## Configuration decision

Use requested **8 CPU / 32 GiB** as the cost-oriented default. Keep automatic
workers capped at eight and native/DB threads at one inside each metric worker.
Four archive scan workers have a demonstrated benefit. Explicit 16 metric
workers are supported for a 16-CPU allocation, but the within-host gain above
eight was only about 8–10%; extra CPUs need a stage-specific reason. Do not
increase RAM to accelerate a serial loop. Use an explicit 8-GB main-process DB limit for serial reductions and keep
`RES7_MEMORY_LIMIT=2GB` per metric worker. The recipe supports these independently;
existing defaults remain unchanged when the override is absent. Increasing RAM
above 32 GiB is unnecessary for the observed workload.

Official prices checked 20 October: CPU $0.128/core-hour and memory
$0.0212/GiB-hour. Thus 8/32 is $1.7024/hour, 16/32 $2.7264/hour and 16/64
$3.4048/hour before any separately billed services. A five-hour 8/32 allocation
would be $8.512 at those rates. These are arithmetic projections, not an invoice.
[CoreWeave sandbox pricing](https://wf.coreweave.com/forge-pricing/inference-agents).

The private budget ledger reserves complete allocation lifetimes, including
failed launches and time-limit expiry; this deliberately overstates shorter
runs. The pre-full cap remains $75. No correct-path run, full global build,
database load or serving activation is included in these measurements.
The earlier mammal closure reserved $37.2864. Expanded-scope conservative
reservations total **$45.7984 / $75**, including all failed attempts and complete
allocation lifetimes. No invoice total was available.

## Final acceptance and retained evidence

The final 117 cloud checks passed in 28.318 seconds with FAST geometry only.
The mixed-route rehearsal, fresh retained-source metadata/mammal preparation,
real calibration and normal immutable-artifact download/restore/reuse passed.
The [acceptance record](12_pipeline_acceptance.md) gives the exact scoped gates,
private retention identities and unperformed full/correct-path actions.
The superseding expanded suite passed 124 checks in 28.25 seconds and the
committed all-clade candidate passed normal download/restore/reuse and restored
scenario API checks. The earlier 117-check count belongs to the mammal closure.

## All-acquired-clade extension

The acquired release contains 2,294,776 tips and 4,589,551 array nodes. The
expanded path retains the complete root `cellular_organisms_ott93302`, stem
excluded, and audits all 171,604 assessed targets across 74 IUCN classes.
Checked ED/EDGE components were produced for 150,270 targets (87.57%), in 68
classes; six classes had no checked tree identity. This is availability across
all acquired clades, not 100% species coverage or accepted tree dates.

Initial full-tree preparation, including reviewed mammal-input guards, DNA
preparation, global identities, scoring and publication, took approximately
152.76 seconds on requested 8 CPU/32 GiB. It used retained source bytes with
uncontrolled OS cache. A separate process measured complete-tree parsing at
9.398 seconds and 128-draw risk calculation at 26.713 seconds. Total measured
probe time was 37.462 seconds, peak sampled process RSS 1.192 GB, scratch zero.
These are stage observations, not a guarantee of the full spatial build's ETA.
The new depth-group array path matches the pinned reference engine on small
trees with unknown risks, EX/EW, polytomies and unary branches. Full-tree ED
reconciled with retained PD at 39,556,112.394876 Myr.

The broader calculation therefore does not justify a larger sandbox than the
selected 8 CPU/32 GiB. It reuses published spatial memberships. Only verified
IUCN EX removes a tip from ED/EDGE, while the gap tree keeps the full released
scope; this particular checked assessment snapshot contains no EX records.

A separate measured restored-API process loaded the full DNA and tree
generations and exercised all three rules, cached pages and four concurrent
warm requests. It completed in 64.981 seconds, with sampled peak RSS
5,076,140,032 bytes, cgroup working proxy 5,078,601,728 bytes and scratch zero.
This includes loaded evidence and cached scenarios, rather than only the tree
kernel. It fits the selected sandbox with substantial headroom. These numbers
come from the completed standard resource probe.

## Completed representative diagnostic and robustness matrix

The verified 1,000-row sample contains 136 fragmented geometries (at least 20
parts), 282 with holes and 61 spanning more than 180 degrees of longitude.
All 1,000 are valid. The expanded FAST comparison uses six complete source
geometries, sample IDs 10, 642, 802, 884, 928 and 994: a tiny-hole control,
275-part fragmentation, 542-hole geometry, antimeridian span, a large range
and a 3,322-part wide geometry with bbox area over 30,000 square degrees.
The result is a deliberately selected diagnostic, not a population error bound.

Simplified/unsimplified FAST comparison took 18.848 seconds in its measured
process and peaked at 1.022 GB RSS. Per-species Jaccard ranged from 0.979308
to 1.0. Reference/alternative cell populations were 1,478,190/1,472,230, with
1,467,283 common cells and 10,907/4,947 exclusions. Common-cohort rank
correlation was 0.997627; deterministic top 1/5/10 percent overlap was
0.981258/0.994875/0.997029. This measures simplification within FAST semantics,
not missing exact boundary contacts or accepted global scientific error.

Since the real sample contains no invalid inputs, repair sensitivity uses
explicit synthetic invalid cases separately. A valid control preserved all
three cells. An accepted overlapping polygon produced 1,995 cells with the
configured repair and 2,015 with the labelled `buffer(0)` alternative; the latter
added 20 cells. The zero-area case remained production-rejected. No alternative
repair was adopted and simplification was disabled for this comparison.

The robustness rehearsal covers nine scenarios and 21 comparisons: categorical
scoring, website/assembly/quality gaps, flat threat weights, range-rarity and
polygon/point/basin-only subsets. Its 96 assessed targets include 91 mapped and
five unmapped species, 993 fine cells and 19,841 distinct memberships. Independent
oracles reconcile production OR masks, fine sums, distinct coarse sums and the
global range-rarity budget over mapped species. Common-cohort reports include
ecosystem/status strata and nonconstant ranks. Synthetic changes demonstrate
the workflow; they are not biological findings or a choice of default score.
