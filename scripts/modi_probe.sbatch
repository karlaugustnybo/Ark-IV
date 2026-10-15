#!/usr/bin/env bash
# Submit only after selecting a permitted partition and shared root.
# Set ARK_PROBE_SHARED_ROOT to the mount to inspect. No data are copied.
# This job reads system information only; it does not copy or modify pipeline data.
#SBATCH --job-name=ark-modi-probe
#SBATCH --partition=modi_devel
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=1G
#SBATCH --time=00:10:00

set -euo pipefail

echo "node=$(hostname)"
echo "job_id=${SLURM_JOB_ID:-unknown}"
echo "allocated_cpus=${SLURM_CPUS_PER_TASK:-unknown}"
echo "allocated_memory=${SLURM_MEM_PER_NODE:-unknown}"
free -h
df -h /tmp /dev/shm "${ARK_PROBE_SHARED_ROOT:?Set an explicit shared root}"
stat -f -c '%n %T' /tmp /dev/shm "${ARK_PROBE_SHARED_ROOT:?Set an explicit shared root}" 2>/dev/null || true
python3 --version 2>&1 || true
command -v apptainer || true
command -v uv || true
echo "probe_complete"
