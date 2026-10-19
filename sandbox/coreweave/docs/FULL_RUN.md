# Pipeline plans, preflight and full jobs

The driver takes a reviewed payload manifest and explicit bounded command list;
see [configuration](CONFIGURATION.md). It does not discover historical inputs,
receipt paths, artifacts, normalized relations or credentials. A plan is local.
`--run` allocates compute and uploads the reviewed bundle.

`full_pipeline_job.py --help` describes the worker. It requires explicit input
and fresh output roots, a profile, an immutable input artifact identity for
provenance, a result catalog name, a compute budget, and a JSON mapping of all
five boundary names (`admin0`, `admin1`, `eez`, `conservation_framework`,
`municipality`) to supplied files. It reuses actual-main benchmark downstream
commands, replacing sampled pairs/lists with production builders. It does not
claim that deferred lossless validation ran. Profile identity and coverage
semantics remain those of the selected pipeline implementation.

`--mode plan` prints commands; `preflight` verifies the acquisition manifest
and declared checksums without global filling; `full` executes the planned
stages. Inputs must already be staged and authorized. `preflight_checks.py`
can additionally run explicit unittest filenames. Manifest verification does
not attest to source completeness, geometry reconciliation or missing-basin
policy. The independently developed source/serving PRs must be integrated and
rechecked before relying on their newer omission or serving contracts.

The worker samples disk and cgroup working-memory proxy during owned processes,
with a deadline and scratch headroom. Disappearing-file `du` errors are tolerated
only when they still supply a valid total; other disk probe failures stop work.
The memory proxy is `anon + shmem + kernel`; missing counters are unavailable,
not zero. Polling cannot prevent sudden OOM or provider-forced termination.

Selected result persistence is opt-in with `--persist`. Review output rights
before enabling it. The worker records stage failure separately from persistence
failure and waits for the catalog commit before returning success. The final
local report records persistence state; the catalog contains the report as it
stood before its own commit. Provider hard lifetime can interrupt persistence.
There is no promise of automatic durable checkpoints or unattended recovery.
The driver attempts report recovery while its owned context is active, then
exits that context on success or failure. Confirm settled provider status after
an authorized real job; a context exit request is not a billing receipt.

Historical checkpoint migrations are not generic recovery APIs. These tools do
not rewrite old receipts, replace source fingerprints or resume incompatible
stages. Retain exact sources, schemas, profiles and receipts for a future
reviewed compatibility procedure.

Download every inventory file and verify hashes before `restore_serving.py`.
It writes `serving.json` in a separate sibling generation named with a
`-restored` suffix, checks pinned files against the verified inventory and
refreshes local mtimes; original configuration and receipts stay unchanged.
The relocated config continues to reference the verified downloaded data.
By default it does not publish `tiles/current`. `--publish-current` additionally
requires the compatible `map_serving.validate_config` integration from the
independent serving PR. That module is absent from this PR's actual-main base;
ordinary restoration and checks remain independent. A relocated configuration
alone is not evidence that the complete serving API is valid.
Publication points `tiles/current` at this restored generation, so consumers
read the validated relocated `current/serving.json`. Existing restored generations
and current selections (including dangling links) are never replaced. Failed
validation leaves the restored config for inspection and publishes no current
selection; use a fresh download for another restoration attempt.

Stage input downloads before the worker with a reviewed `cloud_storage.py
 download-inputs` command including the immutable catalog and
`--authorize-transfer`. Supply the same artifact to the worker for provenance;
`--input-receipt` additionally checks a committed matching catalog receipt.
The driver command list can perform this download, preflight, full worker and
explicit output transfer in sequence. It does not automatically grant transfer
rights or infer a historical active run. `check_full_run.py` defaults to a local
report; `--query-provider` optionally contacts only the selected sandbox.
`--diagnostic-command` takes reviewed JSON argv (60-second timeout) and writes
a private monitoring log. A command can execute arbitrary operations; keep it
read-only when inspecting a live run. The checker does not stop or retry jobs.

When compatible spatial/serving code is integrated, the full worker preserves
its generated omission audit in map metadata and records `serving-selection.json`
from the actual validated generation. On actual main these optional products
may be unavailable, which is recorded explicitly. The integration must verify
source geometry/relationship reconciliation, the selected strict/partial profile,
metadata reconciliation and complete serving validation together. The standalone
PR does not introduce these other PRs' scientific or runtime implementations.
Result persistence selects regular generation paths and completed products;
`tiles/current` symlinks and transient spill directories are excluded.

Local stages run in an owned process group. Before persistence or the next stage,
cleanup sends TERM, allows up to 15 seconds for the group to finish, then sends
KILL to surviving group members and reaps the leader. This also applies when a
leader exits successfully but leaves workers behind. Stage commands must keep
workers in that group; detached daemons are outside this lifecycle contract.
