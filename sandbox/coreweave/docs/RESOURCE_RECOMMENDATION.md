# Resource planning from explicit evidence

Choose resources from the intended workload and current allocation policy;
this code release does not publish historical account results or recommend a
configuration as globally proven. CPU, RAM, scratch and workers are explicit
inputs. More requested CPU saves cost only if completion gets sufficiently
faster at the resulting total hourly rate.

Use `profile_benchmark_job.py` with explicit roots, sample, profile, boundaries
and worker count. It collects cgroup CPU/memory/events and process RSS/PSS/USS,
restores the benchmark hook on exit, and joins its sampler thread on failure.
Counters unavailable on a platform remain unavailable. `analyze_profile.py`
summarizes actual phases and optionally charts local evidence. Sample weights
across processes are not elapsed wall time or exact native-call accounting.

`profile_supplemental.py` measures actual-main point transformation and basin
normalization, without claiming geometry reconciliation or basin-to-H3 filling.
`profile_density.py` creates bounded high-richness list fixtures from explicitly
supplied metadata and compares production worker outputs at four and six
workers. Its row/species/partition limits are configurable. Synthetic density
using real metadata still produces restricted results. Keep them private.

Compare equivalent outputs before timing. Record hardware/image, dependency
locks, source hashes, profile, fixture, rows, resolutions, worker/thread counts,
cache order, native memory and scratch. Alternate trial order and distinguish
per-process DuckDB limits from Python/Arrow/native memory. Sampling can miss
peaks. Shared filesystem free space is not a per-job quota reservation.

`cost_model.py` takes supplied CPU/RAM rates and ordered reserve scenarios.
Its stratified paired bootstrap is sensitivity analysis, not a confidence
interval, global measurement, finite all-route bound or invoice. Census
calibration binds the selected source SHA; a single-archive speed ratio does
not measure full-source I/O. Account quota, storage, setup, transfers, missing
source geometries, retries and downstream audits require separate evidence.

Optional `--census`, `--archive` and `--probe-root` together replace the source-scan
stage with the hash-matched single-archive calibration. The complete local census
must name the same archive hashes as the acquisition manifest and match the
selected profile's row policy and current size bands. Its census method and the
selected archive's size/count/exclusion observations must match the new scan;
missing or incompatible fields reject calibration. Its unchanged full population
remains the projection population. This checks compatibility and the selected
archive, not independent proof of the other archives' census calculations.
`--extra-stages` takes an explicit JSON stage list using the driver's
name/command/timeout schema. Use it to measure `profile_supplemental.py` and
`profile_density.py` after the benchmark, with their explicit arguments. The
sampler remains active, records output/spill allocation and free space, and each
extra stage gets before/after counters and a private log. No stress stage runs
merely because a script exists. Supplied commands remain the caller's responsibility.

Prepare an authorized profiling subset using `cloud_storage.py download-inputs
--only acquisition/current.json --only EXACT/MANIFEST/PATH ...` with an immutable
catalog and explicit transfer authorization. This preserves the original complete
manifest: missing excluded full archives are intentional for the fixture, and
must not be mistaken for a complete source download. A later full build needs
the complete verified input set. Run a sampling profiler, when available in the
prepared image, as an explicit reviewed command around the worker; recover its
Speedscope file using a separate authorized storage command. `analyze_profile.py`
retains optional density reports and corrected conditional polygon projections
when their explicit companion reports are present, never a fixed recommendation.

For actual-main benchmark output, pass `--pairs /private/pairs-report.json`
and `--population /private/population.json` to `cost_model.py` alongside the
completed benchmark report. These explicit companions avoid assuming a historical
combined-report schema. Older explicit reports containing `full_pairs` and
`full_population` remain accepted. The population needs the stated cloud I/O
calibration; a plain local report does not establish that calibration.

Extra profiling stages use the same owned process-group cleanup as full-worker
stages. A timeout still records a failed measured stage, restores benchmark hooks
and joins the sampler; group cleanup completes first with a 15-second TERM grace
and KILL escalation. Successful or failed leaders also have leftover group
members stopped. Commands must not detach workers into independent sessions.
