# Read-only cluster resource probe

`scripts/modi_probe.sbatch` prints requested allocation, node memory, filesystem
capacity/type and available Python/container tooling. Review its partition and
resource requests for the target scheduler before explicitly submitting it.
Set `ARK_PROBE_SHARED_ROOT` to the authorized shared mount. The script does not
transfer pipeline data or launch a build. A scheduled probe can still consume
allocation; do not submit it merely to validate repository code.

Check Python against the project's declared requirement and inspect the actual
installed image versions. Do not infer a user's quota from aggregate filesystem
free space. RAM-backed `/dev/shm`, node-local ephemeral scratch and durable shared
storage have different lifetimes. Confirm quotas, retention and compute-node
access independently before staging any authorized inputs.

Stage only the necessary permitted working set. Preserve manifests and hashes,
write bounded checkpoints, and validate transfers before depending on them.
Source access does not authorize transfer of IUCN/EDGE or derived datasets.
Historical node/job IDs, account observations, input counts and execution logs
are private evidence rather than public reproducibility instructions.
