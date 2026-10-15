"""Synthetic probe launcher; uses the explicit manifest/command interface.

Select workload.py with a small --rows value in the commands file. A plan never
loads credentials or contacts a provider. --run starts paid compute.
"""

from full_run import main

if __name__ == "__main__":
    raise SystemExit(main())
