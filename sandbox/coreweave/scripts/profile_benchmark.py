"""Explicit profiling launcher; uses the same reviewed payload and bounded SDK driver.

Select profile_benchmark_job.py in the command manifest. Census, fixture, profile,
roots, and artifact identities are arguments to that worker, never discovered.
"""

from full_run import main

if __name__ == "__main__":
    raise SystemExit(main())
