# Script contracts

Use `--help`; SDKs are imported only by explicit provider/transfer operations.
Use the main locked environment with `PYTHONPATH=.` for pipeline workers and
local analysis; the isolated sandbox project supplies the optional SDK.

| Script | Contract |
|---|---|
| `full_run.py` | Plan or execute explicit hash-pinned bundle and bounded argv stages |
| `probe.py`, `profile_benchmark.py` | Same driver interface; choose synthetic/profile worker commands explicitly |
| `payload.py` | Deterministic regular-file packer, exact review hashes, per-input authorization |
| `launch.py` | Injectable SDK driver, bounded calls, report recovery and context cleanup |
| `cloud_storage.py` | Explicit-root resumable committed artifacts and inventory-verified restores |
| `download_results_parallel.py` | Same restoration checks with bounded parallelism |
| `check_full_run.py` | Local explicit report; provider query only with flag |
| `restore_serving.py` | Verify inventory and relocate pins; optional compatible serving validator |
| `full_pipeline_job.py` | Actual-main full-stage plan, watchdogs and explicit optional persistence |
| `preflight_checks.py` | Manifest checksums and explicit tests; no global build |
| `profile_benchmark_job.py` | Explicit benchmark fixture and resource sampler |
| `cloud_census.py` | Explicit archive/config/local census, SHA-matched single-archive calibration |
| `profile_supplemental.py` | Explicit-root point/basin normalization diagnostic |
| `profile_density.py` | Bounded dense-list stress and worker-output equivalence |
| `analyze_profile.py` | Local actual-phase summary, optional matplotlib chart |
| `cost_model.py` | Conditional scenarios with explicit user rates and reserves |
| `workload.py` | Synthetic kernel/SQL/optional tile diagnostic with fresh output |
| `artifact_speed_test.py` | Explicit synthetic transfer/paid diagnostic; plan by default |

Workers run only when requested; importing a module never scans global data,
loads a secret or launches a job. Reports can contain restricted derived values
and are private. Ordinary tests use small synthetic inputs and fake SDKs.
