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
| `cloud_run.py` | `just cloud-run`: confirm cost, launch a self-ending run, watch |
| `watch_run.py` | Read-only live terminal monitor with baseline ETAs; saves a passed run as the next baseline |
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

Full preparation records progress and sampled resources even with `--ui plain`.
Keep `runs/` and `prepare-report.json` in persisted results; do not replace the
monitored `serving_prepare` path with a bare subprocess launcher. See the
[25 October run report](../../../docs/pipeline/14_fast_run_report_2026_10_25.md)
for the retained files and their measurement limits.

Run the full pipeline on CoreWeave with one command:

```sh
just cloud-run            # fast coverage (approximate native overlap)
just cloud-run --precise  # exact edge/corner coverage; separate outputs
just cloud-watch [id]     # reattach after Ctrl-C (the run keeps going)
just cloud-stop [id]      # stop a sandbox now
```

`cloud-run` prints mode, size, maximum compute cost and the code being shipped
(working tree, including uncommitted changes), and asks before creating the
sandbox. The sandbox's main command runs setup, the driver and retention, then
exits, so the sandbox ends itself (two minutes after the final status) whether
or not a monitor is open. A setup or driver crash is recorded as `failed` and
ends it the same way. Monitors only read; the first to see a passed run saves its
timings as `run-baselines/<date>-<sandbox>.json`, and later ones skip it. It needs the private `results/cloud-run.json`
(driver, extra files, image, size, rates) and `WANDB_API_KEY`, which the recipe
takes from the environment or `sandbox/coreweave/.env`. Allocations are recorded
in `results/cloud-runs.json`.

ETAs come from the newest file in `run-baselines/` with the same coverage mode,
falling back to another mode (shown in the header). The seed baseline is the
25 October FAST continuation, which reused pairs, lists and fine partitions;
its short receipt-reuse times do not describe a cold build.
Same-mode watches now use the recovered original-work pair/list/fine priors.
The verified full-input spatial-stage calibration supplies its new stage and
phase timings only while the producer code matches. Historical run records stay
unchanged; a fallback from another coverage mode does not use these calibrations.
