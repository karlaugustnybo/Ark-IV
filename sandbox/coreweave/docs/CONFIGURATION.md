# Explicit payload and execution configuration

`full_run.py`, `profile_benchmark.py`, and `probe.py` share a local-plan-first
interface. Use `--help` for every required argument. Planning requires no SDK,
credentials, provider query, upload or compute. `--run` explicitly requests the configured
allocation and transfers the exact reviewed bundle.

A private JSON payload manifest has this shape:

```json
{
  "files": [
    {
      "root": "/path/to/reviewed-checkout",
      "path": "sandbox/coreweave/scripts/workload.py",
      "destination": "sandbox/coreweave/scripts/workload.py",
      "role": "code",
      "sha256": "REPLACE_WITH_REVIEWED_FILE_SHA256"
    }
  ]
}
```

Review complete file contents and modes before recording their hashes. There
is no recursive glob-based upload discovery. Every dependency, configuration,
lockfile and script must be listed; missing dependencies fail in the prepared
image rather than silently pulling private workstation files. Symlinks,
traversal, duplicate destinations, credential patterns, receipt/cache/history
paths, and changed hashes reject. Code suffix checks supplement manual review;
they do not establish that a file is safe to publish or transfer.

A non-code entry uses `"role": "authorized-input"` and must also record
`provenance` and `transfer_authorization`. The caller must explicitly pass
`--allow-authorized-data`. Include required receipts, census, normalized
relations and boundaries individually with their exact identities and rights;
never substitute roots or alter receipts to make a job accept them. These
manifests remain private. Permission to read a source is insufficient.

Commands are a JSON list of `name`, `command` (string argv), and positive
`timeout` seconds. Log names must be simple unique filenames. For example, in
a prepared image containing the main locked Python environment:

```json
[
  {
    "name": "synthetic",
    "command": ["sh", "-c", "cd /probe && PYTHONPATH=/probe /probe/.venv/bin/python sandbox/coreweave/scripts/workload.py --scratch /scratch/synthetic --rows 1000"],
    "timeout": 120
  }
]
```

An explicit shell command can execute anything its author specifies. Review it
with the payload. Preparation happens in a fresh `/probe`; dependencies and
compilers are the image author's responsibility. Prefer pinned image digests.
The sum of command timeouts must leave 360 seconds for payload and recovery.
A lifetime is a stop limit, not proof that the work or result persistence fits.

```sh
uv run --project sandbox/coreweave --frozen python sandbox/coreweave/scripts/full_run.py \
  --manifest /private/payload.json --commands /private/commands.json \
  --output /private/new-run --image 'YOUR_PREPARED_IMAGE_DIGEST' \
  --cpu-rate YOUR_VERIFIED_RATE --memory-rate YOUR_VERIFIED_RATE
```

Rates are user-supplied planning assumptions, never current-price assertions.
The printed cost excludes durable storage, prior account usage, other jobs,
retries, and any unverified billing details. Add `--run` only after approval of
the concrete target, inputs, budget, and action. The optional `IUCN_REDLIST_API_KEY` or `IUCN_REDLIST_KEY` is forwarded only when already present in the environment for an authorized metadata workflow. No credential file is loaded. `WANDB_API_KEY` is read only
from the process environment in that mode. SDK exception text is not included
in driver reports, but remote application logs remain private.
