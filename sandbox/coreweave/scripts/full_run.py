"""Plan or execute an explicit reviewed code/input bundle and bounded job commands."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from launch import cost_plan, run_remote, sdk_options, validate_stages
from payload import build_payload


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument(
        "--commands",
        type=Path,
        required=True,
        help="JSON list of name/command/timeout objects; argv, no implicit shell",
    )
    p.add_argument("--output", type=Path, required=True)
    p.add_argument(
        "--image", required=True, help="Prepared image identity; preferably pinned digest"
    )
    p.add_argument("--cpu", type=int, default=8)
    p.add_argument("--memory-gib", type=int, default=24)
    p.add_argument("--scratch-gib", type=int, default=512)
    p.add_argument("--lifetime-seconds", type=int, default=3600)
    p.add_argument(
        "--cpu-rate", type=float, required=True, help="User-verified USD/core/hour scenario"
    )
    p.add_argument(
        "--memory-rate", type=float, required=True, help="User-verified USD/GiB/hour scenario"
    )
    p.add_argument("--allow-authorized-data", action="store_true")
    p.add_argument(
        "--run",
        action="store_true",
        help="Explicitly start paid compute and transfer the reviewed payload",
    )
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    plan = cost_plan(
        args.cpu, args.memory_gib, args.lifetime_seconds, args.cpu_rate, args.memory_rate
    )
    if not 60 <= args.lifetime_seconds <= 50400 or args.scratch_gib <= 0:
        p.error("Lifetime must be 60–50400 seconds; scratch must be positive")
    commands = json.loads(args.commands.read_text())
    try:
        validate_stages(commands)
    except ValueError as error:
        p.error(str(error))
    if not commands:
        p.error("At least one command is required")
    if sum(s["timeout"] for s in commands) + 360 >= args.lifetime_seconds:
        p.error("Reserve at least 360 seconds beyond command timeouts for payload and recovery")
    payload, identity = build_payload(
        args.manifest, allow_authorized_data=args.allow_authorized_data
    )
    plan.update(
        payload=identity, image=args.image, commands=commands, mode="run" if args.run else "plan"
    )
    print(json.dumps(plan, indent=2), flush=True)
    if not args.run:
        return 0
    if not os.environ.get("WANDB_API_KEY"):
        p.error("Supply WANDB_API_KEY in the environment; no credential file is loaded")
    options = sdk_options(plan, args.scratch_gib, args.image)
    run_remote(
        payload,
        identity,
        commands,
        args.output,
        options,
        recover=[
            ("/scratch/ark-output/run-report.json", "run-report.json"),
            ("/scratch/profile-report/profile-report.json", "profile-report.json"),
        ],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
