# Authorized storage transfers

`cloud_storage.py --help` describes uploads, downloads and `run-and-save`.
Every command requires an explicit `--root`. A local `--dry-run` plans uploads
without credentials, network or a sandbox. Actual transfers additionally
require `--authorize-transfer` after rights and destination review.

```sh
uv run --project sandbox/coreweave --frozen python sandbox/coreweave/scripts/cloud_storage.py \
  upload-inputs --root /private/acquired-inputs --dry-run
```

Input selection follows `acquisition/current.json` and the optional GBIF/EDGE
lookup files recognized by the pipeline. Verify that all selected inputs may
be transferred to the target service. Source manifests, sizes and declared
SHA256 identities are validated; the input files must stay unchanged. There
is no default workstation volume and no data relocation.

Actual upload commands accept entity, project, catalog name, state directory
and a positive byte-size cap (`--max-gb`, decimal GB). The cap is a transfer
safety limit, not remaining provider quota or a price guarantee. The project
must be private; this does not establish data rights. Credentials are supplied
through `WANDB_API_KEY` in the environment, never printed or loaded from a file.

Files are SHA256-addressed artifacts. Only committed artifacts with matching
identity may be reused; pending objects are not reusable. Each receipt records
exact backing versions. The catalog commits only after all file objects have
committed. A state-directory file lock excludes concurrent upload supervisors.
Retain the catalog and its backing versions together; deleting a backing
artifact breaks restoration. No implicit provider deletion is performed.

Result selection defaults to final output folders plus root JSON/PMTiles;
repeat `--include relative/path` for an explicit set. Includes replace the
default selection. Review complete output contents, including logs, IDs and
paths. `run-and-save ... -- COMMAND ARGS` executes only when explicitly
requested, then persists available results even when that command fails, and
returns its exit code after persistence. A failed transfer remains a failure.

`download-results --artifact ENTITY/PROJECT/CATALOG:VERSION --root DIRECTORY
--authorize-transfer` verifies immutable backing versions, paths, byte sizes
and SHA256 before replacing a file. Full inventories are validated before
restoring any entry. Traversal, duplicate/reserved paths and symlink destinations
reject. Use a private destination without concurrent writers. Parallel restore
uses the same checks with a bounded number of workers and per-path staging.

The optional Bash supervisor `upload_overnight.sh --root DIRECTORY ...` retries
transient failures; integrity errors stop. `--background` creates a tracked
separate process and prints its PID/log. On macOS it uses caffeinate when
available. Run it only for an authorized transfer. Neither the supervisor nor
storage commands start a compute sandbox. Logs and receipts are private.
