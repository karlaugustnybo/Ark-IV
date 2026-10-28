# FAST continuation: analysis of retained events — 25 October 2026

The final candidate is selected for local startup and its superseded serving
bundles are archived to ERDA with full checksum readback. This analysis streams
the checksum-verified diagnostics TAR; it does not rerun spatial coverage or
recompute scores.

## What the events establish

There are **104,676 events** over **3.81 hours**.
The successful continuation build took **3.81 hours**, reusing earlier
pair and fine outputs. It is not a fresh end-to-end runtime.

The directly instrumented stage durations are:

| Stage | Recorded duration |
| --- | ---: |
| pairs | 15.6 s |
| lists | 20.5 s |
| boundaries | 0.5 s |
| metadata | 272.0 s |
| coarse_db | 5.6 s |
| coarse_cache | 164.0 s |
| fine_metrics | 95.6 s |
| prepared_inputs | 373.2 s |
| tiles | 812.7 s |

The interval from prepared-input completion to the spatial-analysis receipt is
approximately **184.5 minutes**, or **80.6%** of the continuation.
This is a completion-boundary estimate, including launch/receipt overhead,
not a separately recorded process timer. Spatial analytics dominates the
observed continuation; speeding up the reused pair stage cannot address it.
The other completion-boundary intervals were:

| Interval ending at | Approximate duration |
| --- | ---: |
| DNA receipt | 52 s |
| Phylogenetic receipt | 198 s |
| Opportunity-aggregate receipt | 638 s |

These intervals include intervening orchestration and receipt work. They are
not isolated query timings. The tile stage has its own direct 812.7-second
timer. Preparation and tiles account for the directly timed stages above.

## Monitoring gaps and interpretation

Only **29.3 minutes** have direct named-stage timers.
There are **6 event gaps longer than one minute**; the longest is
**184.4 minutes**. Hashing emitted
**1,001 phase-detail events**. Event volume therefore
must not be interpreted as completed biological/spatial work, an overall
percentage, or continuous performance coverage.

The events do not contain a continuous resource series. Sparse manual readings
showed spatial analytics using roughly 1–2 CPU cores, process RSS reaching about
28.5 GB and cgroup memory reaching the 32 GiB limit. Large DuckDB temporary files
were observed. Worker filesystem usage reached roughly 2 TB, but this is not an
isolated scratch peak. No OOM kill was recorded. These observations identify
memory/spill pressure; they cannot establish exact peaks, I/O throughput, a
specific expensive SQL operator, or the speedup from more RAM/CPUs.

## Consequences for the next run

Retain the new always-on progress/resource recording. The added jurisdiction,
cell-area, range-denominator and stewardship phases will separate the long
spatial block. Use their CPU, cgroup/cache and scratch histories to decide
whether work is serial, compute-bound or spill-bound before changing worker
counts. Further query profiling requires an authorized cloud experiment;
this analysis does not establish an optimal new machine configuration.

## History of the failed run

The failed continuation's status, command log and retention receipt remain
available privately alongside the immutable interrupted v1 checkpoint. They
locate native exit 139 in prepared-input validation, with no recorded OOM kill.
They do not provide a continuous resource history or establish the faulting
native operator. The successful continuation's 104,676 events are a separate
history and cannot fill those missing measurements.

The previous native exit 139 did not reproduce in either the focused replay or
the successful continuation. Its cause remains unresolved. FAST approximation,
missing basins/identities and frozen sources remain explicit; correct-path
validation and scientific adoption have not been performed.
