# FAST run report — 25 October 2026

The retained-source FAST candidate completed all eight preparation commands:
spatial pairs, membership aggregation, global serving preparation, spatial
analytics, DNA evidence, global phylogenetic enrichment, opportunity aggregates
and tiles. All run workers are stopped; the monitoring timer is disabled.

The successful **continuation** took **3 h 49 m** of build time, excluding
restoration and retention. It reused earlier pair/fine outputs; this is not a
fresh end-to-end runtime. Earlier attempts timed out during spatial analytics
and crashed in prepared-input validation. The crash did not reproduce in a
275.5-second diagnostic replay or the successful continuation; its cause is
still unknown.

The outputs contain **96,631,588 fine cells**, **115,408 mapped species** and
92,966,356 jurisdiction memberships. The assessed population is separately
171,604 records; 150,270 received checked ED/EDGE scores using the complete
2,294,776-tip tree. Existing basin omissions and unresolved identities remain
explicit. These are frozen-input, approximate FAST results, not a latest-data
build, scientific adoption or correct-path validation. Nothing was activated
at build completion; the later local selection is recorded below.

## Performance evidence retained

The final diagnostics archive was downloaded and SHA-256-verified. It contains
**104,676 progress events**, command logs and source/output receipts. Selected
measured continuation timings are:

| Stage | Time | Interpretation |
| --- | ---: | --- |
| Pair stage | 15.6 s | Receipt/checksum reuse, not geometry generation |
| Membership lists | 20.5 s | Reused retained outputs |
| Species metadata | 272.0 s | Preparation scan |
| Coarse cache | 164.0 s | Preparation |
| Fine metrics | 95.6 s | Validation/reuse, not a fresh global build |
| Prepared-input validation | 373.2 s | Passed all checks |
| Tiles | 812.7 s | Complete tile stage |

We **do not have continuous CPU/RAM/I/O history** for this run. Thirty-minute
observations showed spatial analytics using roughly 1–2 CPU cores, process RSS
reaching about 28.5 GB, cgroup memory reaching its 32 GiB limit, large DuckDB spill
files, and worker filesystem usage reaching roughly 2 TB. Filesystem usage is
not an isolated scratch measurement; sampled observations are not exact peaks.
No OOM kill was recorded. The configured 8 GB DuckDB limit did not bound total
process/container memory. The long spatial-analytics section emitted no internal
phase markers, so we cannot reliably attribute its time or spills to a query.
This is the main measurement gap to address before further resource tuning.

The subsequent [retained-event analysis](../performance/2026-10-25_fast_run_events.md)
measures 184.5 minutes between prepared-input completion and the spatial receipt,
80.6% of the continuation. This includes orchestration/receipt overhead; it is
not an isolated query timer. Failed-run logs and the interrupted checkpoint
remain available privately; continuous resource history is unavailable there too.

## Retention and cost

Private catalogue: `scoring-full-candidate-final:v0` in the existing
`karlaugustkroghnybo/ark-pipeline-storage` project. Its `COMMITTED` inventory
matches the recovered receipt: **21,718,538,240 bytes**, comprising preview,
spatial and diagnostic TARs. At initial retention verification only the diagnostics had been downloaded.
The later local restoration below downloaded and checksum-verified the complete
pack and checked its serving paths.

All four full-run/diagnostic allocations are stopped. Their conservative
full-lifetime compute reservations total **$35.7504**; this is not an invoice
and excludes the earlier pre-full verification budget and storage charges.

## Monitoring for future runs

`serving_prepare` now uses the monitored runner in **every terminal mode**.
`--ui plain` controls presentation only. Under the `progress_directory` recorded
in `prepare-report.json`, each run retains:

- `progress.jsonl`: stage/phase events and work counts where instrumented;
- `resources.jsonl`: five-second sampled process RSS/CPU, live-process I/O,
  cgroup memory/cache/OOM/CPU/I/O/pressure counters, scratch logical sizes and
  free disk space; unsupported counters are null;
- `logs/`, `dashboard-state.json`: command output and resumable stage state;
- `prepare-report.json`: command outcomes, durations and sampled RSS/write summaries.

The runner itself now writes `command_start`/`command_end` records (exit code,
wall time, peak RSS) for every stage, so CLIs without their own progress events
(DNA evidence, phylogenetic scores, opportunity aggregates) still have stage
boundaries. `prepare-report.json` is written before the first command, so a
monitor never follows a previous attempt. Cloud runs start with `just cloud-run` (`--precise` for exact coverage), which
opens the live monitor (`sandbox/coreweave/scripts/watch_run.py`); its ETAs come from
`sandbox/coreweave/run-baselines/`, seeded from this run (four stage durations
inferred from per-PID event windows to about 2 s).

Spatial analytics now reports jurisdiction-cell counts and range/stewardship
phases. Counts without a known total remain counts, not invented percentages.
Fault tracing stays enabled. The cloud full-pipeline runner also records telemetry by default; its
persistence list includes run monitoring and the preparation report on success
or failure. Sampling is
best effort; it cannot recover missing history or guarantee capture of brief peaks.

For future agent-operated cloud runs, keep the agreed 30-minute status-update
timer active until retained results and worker cleanup are confirmed. The code
records telemetry automatically; user-facing scheduled updates still require
the agent's run-specific timer. No new cloud run is needed to enable this change.

Verification of the monitoring change: 38 focused fixture/subprocess checks
passed, including plain-mode recording and owned-worker cleanup; changed Python
files pass Ruff and whitespace checks. These were small local checks, not a
second global run. No new cloud allocation was started.

## Local selection after the run

The final TARs were downloaded to the external T7 disk and SHA-256-verified.
Scientific receipts were preserved; a separate local runtime descriptor relocates
serving paths. Future `just start` / `just dev` launches select the final map,
matching fine partitions and DNA/phylogenetic/spatial/membership generations.
The isolated database `ark_iv_fast_20261025` contains 173,498 metadata rows,
171,604 phylogenetic rows and 150,270 nonnull ED scores. Its import took 47.5
seconds and uses bounded Parquet batches without constructing the scenario engine.

Local HTTP checks passed for health, stats, species pagination, assessment
details, range details, source support, map metadata, a PMTiles range request and
one fine tile. These are targeted serving checks, not a second global pipeline
run. The full-tree scenario endpoint was not warmed locally. After explicit authorization, the API at `127.0.0.1:8000` was restarted with
the new selection; live health, counts and range-detail requests passed.
Three superseded serving bundles were archived to the mounted ERDA account:
`mac-storage-archive/2026-10-25/Ark-IV/superseded-serving`. Their TARs total
**10,343,976,960 bytes**. Each was downloaded back in full and matched its
SHA-256 and byte count before the three old T7 directories were removed.
The archive includes a checksum manifest and restoration notes. Raw acquisition
inputs and old PostgreSQL databases were preserved. The requested replacement
API remains running at `http://127.0.0.1:8000`.
