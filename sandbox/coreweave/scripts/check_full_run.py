"""Read an explicit driver report; provider contact requires --query-provider."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver-report", type=Path, required=True)
    parser.add_argument("--query-provider", action="store_true")
    parser.add_argument(
        "--diagnostic-command",
        type=Path,
        help="Optional JSON string argv for an explicitly reviewed read-only remote diagnostic",
    )
    args = parser.parse_args(argv)
    report = json.loads(args.driver_report.read_text())
    print(json.dumps(report, indent=2))
    command = json.loads(args.diagnostic_command.read_text()) if args.diagnostic_command else None
    if command is not None and (
        not isinstance(command, list) or not command or any(not isinstance(v, str) for v in command)
    ):
        parser.error("Diagnostic command must be a nonempty string argv")
    if command and not args.query_provider:
        parser.error("Remote diagnostics require --query-provider")
    if not args.query_provider:
        return 0
    from cloud_storage import credentials
    from cwsandbox import AuthStrategy, DataPlaneMode, Sandbox

    credentials()
    sandbox = Sandbox.from_id(
        report["sandbox_id"], auth=AuthStrategy.WANDB, data_plane_mode=DataPlaneMode.GATEWAY
    ).result()
    print("Provider status:", str(sandbox.get_status()))
    if command:
        result = sandbox.exec(command, timeout_seconds=60).result()
        output = args.driver_report.parent / "monitor-last.log"
        output.write_text(result.stdout + "\n" + result.stderr)
        if result.returncode:
            raise RuntimeError("Diagnostic failed; see private monitor log")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
