"""Live terminal dashboard for a CoreWeave pipeline run, timed against the latest completed run.

Reads only the run's own telemetry through the sandbox exec channel: the driver
journal, `prepare-report.json`, the monitored run's `progress.jsonl` and its
latest `resources.jsonl` sample. Nothing in the sandbox is written or stopped;
the monitor exits when the sandbox has ended.
The event stream is mirrored locally; when a run passes, its stage and phase
timings become the next baseline in `run-baselines/`.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console

from ark_pipeline.runtime.benchmark_estimates import spatial_calibration
from ark_pipeline.runtime.dashboard import LABELS, Dashboard
from ark_pipeline.runtime.forecasts import Forecast

REPOSITORY = Path(__file__).resolve().parents[3]
BASELINES = Path(__file__).resolve().parents[1] / "run-baselines"
LABELS.update(restore="Restore inputs & plan", retention="Retain outputs")
BUILD_STEP = "full-build"

# Runs remotely under the sandbox's system Python; argv[1] is a JSON request.
REMOTE = r"""
import base64, json, sys, time
from pathlib import Path
request = json.loads(sys.argv[1])
def load(path):
    try:
        return json.loads(Path(path).read_text()), Path(path).stat().st_mtime
    except (OSError, ValueError):
        return None, None
out = {"now": time.time(), "run": None}
journal, _ = load(request["journal"]) if request["journal"] else (None, None)
out["journal"] = journal
report, modified = load(Path(request["preview"]) / "prepare-report.json")
build = next((s for s in (journal or {}).get("steps", []) if s["name"] == request["build_step"]), None)
if journal is not None and (build is None or (modified or 0) < build["started_epoch"]):
    report = None  # restored from an earlier attempt; this run has not reached preparation
if report and report.get("progress_directory"):
    run = Path(report["progress_directory"])
    offset = request["offset"] if request["run"] == str(run) else 0
    out.update(run=str(run), status=report.get("status"), resources=report.get("resources"), base=offset)
    if request["names"]:
        state, _ = load(run / "dashboard-state.json")
        out["names"] = list(state["states"]) if state else None
    try:
        with (run / "progress.jsonl").open("rb") as stream:
            stream.seek(offset)
            out["events"] = base64.b64encode(stream.read(request["limit"])).decode()
    except OSError:
        out["events"] = ""
    try:
        with (run / "resources.jsonl").open("rb") as stream:
            stream.seek(max(0, stream.seek(0, 2) - 262144))
            lines = stream.read().splitlines()
        out["sample"] = json.loads(lines[-1]) if lines else None
    except (OSError, ValueError):
        out["sample"] = None
print(json.dumps(out))
"""


def clock(seconds: float | None) -> str:
    """Whole-second H:MM:SS so every countdown visibly moves once per second."""
    if seconds is None:
        return "—"
    whole = int(max(0, seconds) + 0.5)
    return f"{whole // 3600}:{whole // 60 % 60:02d}:{whole % 60:02d}"


def read_events(path: Path):
    with path.open() as stream:
        for line in stream:
            try:
                yield json.loads(line)
            except ValueError:
                continue


def build_baseline(events: Path, *, label: str, resources: dict, journal: dict | None = None) -> dict:
    """Measured stage and phase durations from one passed run's event stream."""
    started, phases, seconds, order, workload = {}, {}, {}, [], {}
    for event in read_events(events):
        name, kind = event.get("stage"), event.get("kind")
        if kind == "stage_start" and name not in started:
            started[name] = event["time"]
            phases[name] = [(None, event["time"])]
            order.append(name)
        elif kind == "phase" and name in phases and phases[name][-1][0] != event["phase"]:
            phases[name].append((event["phase"], event["time"]))
        elif kind == "workload":
            workload.update(event["counts"])
        elif kind in {"stage_end", "command_end"} and event.get("status") == "passed" and name in started:
            if name not in seconds:
                seconds[name] = event["elapsed"] if kind == "stage_end" else event["wall_seconds"]
                marks = [*phases[name], (None, started[name] + seconds[name])]
                phases[name] = [[label_, round(end - begin, 1)] for (label_, begin), (_, end) in zip(marks, marks[1:])
                                if label_ is not None or end - begin >= 0.5]
    baseline = {"schema_version": 1, "label": label, "mode": (journal or {}).get("mode"), "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "resources": resources, "workload": workload, "order": [n for n in order if n in seconds],
                "stages": {n: round(seconds[n], 1) for n in order if n in seconds},
                "phases": {n: phases[n] for n in order if n in seconds and len(phases[n]) > 1}}
    if (journal or {}).get("continued_from"):
        # Which stages were reused is not recorded; ETAs treat the run as a fresh one.
        baseline["continued_from"] = journal["continued_from"]
    build = next((s for s in (journal or {}).get("steps", []) if s["name"] == BUILD_STEP), None)
    if build and journal.get("finished_epoch"):
        baseline["stages"]["restore"] = round(build["started_epoch"] - journal["started_epoch"], 1)
        baseline["stages"]["retention"] = round(journal["finished_epoch"] - build["started_epoch"] - build["seconds"], 1)
    return baseline


def latest_baseline(directory: Path = BASELINES, mode: str | None = None) -> Path:
    """Newest baseline, preferring one of the same coverage mode; any mode is a fallback."""
    runs = sorted(((json.loads(p.read_text()), p) for p in directory.glob("*.json")), key=lambda r: r[0]["created_at"])
    if not runs:
        raise SystemExit(f"No run baseline in {directory}")
    same = [path for data, path in runs if data.get("mode") == mode]
    return (same or [path for _, path in runs])[-1]


class PhaseForecast(Forecast):
    """Stage forecast that walks the baseline's measured phases inside a stage.

    A stage's remaining time is the current phase's remaining time (live work
    rate when the phase reports a total, otherwise its baseline) plus the
    baseline of every later phase. An overdue phase without a work rate has no
    defensible countdown and reports unknown rather than zero.
    """

    def __init__(self, prior, resources, baseline):
        super().__init__(prior, resources, "full")
        self.measured, self.phase_base = baseline["stages"], baseline.get("phases", {})
        self.phase_at = {}

    def accept(self, event):
        super().accept(event)
        name, kind = event["stage"], event["kind"]
        if kind == "stage_start":
            self.phase_at[name] = (None, event["time"])
        elif kind == "phase" and self.phase_at.get(name, (None,))[0] != event["phase"]:
            self.phase_at[name] = (event["phase"], event["time"])

    def remaining(self, name, now):
        phases = self.phase_base.get(name)
        current = self.phase_at.get(name)
        labels = [label for label, _ in phases or []]
        if name in self.finished or not current or current[0] not in labels or not self.measured.get(name):
            return super().remaining(name, now)
        # Resource/workload scaling applies to every phase of the stage alike.
        scale = (self.base.get(name) or self.measured[name]) / self.measured[name]
        index = labels.index(current[0])
        left = phases[index][1] * scale - (now - current[1])
        work = self.progress.get(name)
        live = self.work_remaining(name, now)
        if live is not None:
            weight = min(1, 4 * work["completed"] / work["total"])
            left = weight * live + (1 - weight) * max(0, left)
        elif left <= 0:
            return None
        return left + scale * sum(seconds for _, seconds in phases[index + 1:])


class CloudDashboard(Dashboard):
    baseline_label = "Last run"
    duration = staticmethod(clock)

    def __init__(self, names, output, resources, baseline, *, journal=False, ui="auto", console=None):
        if not baseline.get('note_mode') and baseline.get('reconstructed_stages'):
            originals = baseline['reconstructed_stages']
            baseline = {**baseline,
                        'stages': {**baseline['stages'], **{name: value['estimated_seconds'] for name, value in originals.items()}},
                        'phases': {name: values for name, values in baseline.get('phases', {}).items() if name not in originals},
                        'label': baseline['label'] + ' + original-work priors'}
        calibration = spatial_calibration(baseline, None if baseline.get('note_mode') else baseline.get('mode'))
        if calibration:
            baseline = {**baseline, 'stages': {**baseline['stages'], 'spatial_analytics': calibration['estimated_seconds']},
                        'phases': {**baseline.get('phases', {}), 'spatial_analytics': calibration.get('phases', [])},
                        'label': baseline['label'] + ' + verified spatial-stage calibration'}
        stages = (["restore"] if journal else []) + names + (["retention"] if journal else [])
        prior = {"path": baseline["path"], "resources": baseline["resources"], "serving_contract": "dynamic-h3-v1",
                 "estimate": {"serving_contract": "dynamic-h3-v1", "projected_workload": baseline["workload"],
                              "stages": [{"name": n, "estimated_seconds": baseline["stages"].get(n)} for n in stages]}}
        super().__init__(stages, output, resources, prior=prior, mode="full", ui=ui, console=console)
        self.mode = "coreweave"
        self.forecast = PhaseForecast(prior, resources, baseline)
        self.baseline_name = baseline["label"] + (f" ({baseline.get('mode') or 'unknown'} mode)"
                                                  if baseline.get("note_mode") else "")
        self.clock_offset = 0.0
        self.received = None
        self.sample_time = None
        self.extra = ""
        self.sent = set()

    def apply_sample(self, sample):
        if not sample or sample.get("time") == self.sample_time:
            return
        self.sample_time = sample["time"]
        self.stats.update({key: sample.get(key) for key in ("cpu", "ram_percent", "ram_used", "ram_total", "rss", "cores")})
        try:  # The container limit, not the host, is what a run can exhaust.
            current, limit = int(sample["memory.current"]), int(sample["memory.max"])
            self.stats.update(ram_used=current, ram_total=limit, ram_percent=100 * current / limit)
        except (KeyError, TypeError, ValueError):
            pass
        if sample.get("cpu") is not None:
            self.history.append(sample["cpu"])
        scratch = sum(v for v in (sample.get("scratch_logical_bytes") or {}).values() if v)
        free = sample.get("output_disk_free_bytes")
        self.extra = f"Scratch {scratch / 2**30:,.0f} GiB" + (f" · disk free {free / 2**30:,.0f} GiB" if free else "")

    def apply_journal(self, journal):
        """Translate the outer driver's restore/build/retention steps into stages."""
        if not journal:
            return
        build = next((s for s in journal.get("steps", []) if s["name"] == BUILD_STEP), None)
        events = [("restore", "stage_start", journal["started_epoch"], {})]
        if build:
            events.append(("restore", "stage_end", build["started_epoch"],
                           {"status": "passed", "elapsed": build["started_epoch"] - journal["started_epoch"]}))
            if build.get("status") == "passed":
                end = build["started_epoch"] + build["seconds"]
                events.append(("retention", "stage_start", end, {}))
                if journal.get("finished_epoch"):
                    events.append(("retention", "stage_end", journal["finished_epoch"],
                                   {"status": "passed", "elapsed": journal["finished_epoch"] - end}))
        for stage, kind, moment, fields in events:
            if (stage, kind) not in self.sent:
                self.sent.add((stage, kind))
                self.accept({"stage": stage, "kind": kind, "time": moment, **fields})
        if journal.get("status") in {"failing", "failed", "stopped"}:
            for name, state in self.states.items():
                if state["status"] == "running":
                    self.accept({"stage": name, "kind": "stage_end", "status": "failed", "time": self.now(),
                                 "elapsed": self.now() - state.get("started", self.now()), "message": journal.get("error", "")})
        self.started = journal["started_epoch"]
        self.status = {"built": "retaining"}.get(journal["status"], journal["status"])

    def now(self):
        return time.time() - self.clock_offset

    def tick(self, pid=None, force=False):
        for event in self.reader.read():
            self.accept(event)
        if "restore" not in self.states and self.forecast.started:
            self.started = min(self.forecast.started.values())
        if self.live:
            self.live.update(self.render(), refresh=True)

    def render(self, width=None, now=None):
        age = None if self.received is None else time.time() - self.received
        freshness = "connecting" if age is None else f"data {age:.0f}s old" + (" · STALE" if age > 30 else "")
        self.note = " · ".join(v for v in (f"Last run: {self.baseline_name}", freshness, self.extra) if v)
        return super().render(width, now or self.now())


class RunEnded(Exception):
    """The sandbox reached a terminal state; there is nothing left to poll."""


class Poller:
    """Fetch telemetry continuously; rendering never waits for the network."""

    def __init__(self, execute, arguments, mirror: Path, interval: float):
        self.execute, self.arguments, self.mirror, self.interval = execute, arguments, mirror, interval
        self.lock = threading.Lock()
        self.snapshot, self.error, self.received = None, None, None
        self.clock_offset = 0.0
        self.remote_run, self.offset, self.names = None, 0, None
        self.stopped = threading.Event()
        self.ended = False

    def poll(self):
        request = {"journal": self.arguments.journal, "preview": self.arguments.preview_root, "build_step": BUILD_STEP,
                   "run": self.remote_run, "offset": self.offset, "names": self.names is None, "limit": 4 * 1024 * 1024}
        sent = time.time()
        snapshot = json.loads(self.execute(["python3", "-c", REMOTE, json.dumps(request)]))
        received = time.time()
        if snapshot["run"] and snapshot["run"] != self.remote_run:
            self.remote_run, self.offset, self.names = snapshot["run"], 0, None
        data = b""
        if snapshot["run"]:
            data = base64.b64decode(snapshot["events"])
            path = self.mirror / Path(snapshot["run"]).name / "progress.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("r+b" if path.exists() else "wb") as stream:
                stream.truncate(snapshot["base"])
                stream.seek(snapshot["base"])
                stream.write(data)
            self.offset = snapshot["base"] + len(data)
            self.names = self.names or snapshot.get("names")
        with self.lock:
            self.snapshot = {**snapshot, "names": self.names, "events": None,
                             "caught_up": len(data) < request["limit"]}
            self.received, self.error = received, None
            self.clock_offset = (sent + received) / 2 - snapshot["now"]

    def run_forever(self):
        while not self.stopped.is_set():
            started = time.monotonic()
            try:
                self.poll()
            except RunEnded:
                self.ended = True
                return
            except Exception as error:  # noqa: BLE001 - shown in the dashboard and retried
                with self.lock:
                    self.error = f"{type(error).__name__}: {str(error)[:200]}"
            self.stopped.wait(max(0, self.interval - (time.monotonic() - started)))

    def start(self):
        threading.Thread(target=self.run_forever, daemon=True).start()


def sandbox_executor(sandbox_id: str):
    from cloud_storage import credentials
    from cwsandbox import AuthStrategy, DataPlaneMode, Sandbox, SandboxStatus

    ended = {SandboxStatus.COMPLETED, SandboxStatus.FAILED, SandboxStatus.TERMINATED}
    credentials()
    sandbox = Sandbox.from_id(sandbox_id, auth=AuthStrategy.WANDB, data_plane_mode=DataPlaneMode.GATEWAY).result()

    def execute(argv):
        try:
            result = sandbox.exec(argv, timeout_seconds=30).result()
        except Exception:
            if sandbox.get_status() in ended:
                raise RunEnded from None
            raise
        if result.returncode:
            raise RuntimeError(result.stderr[-400:] or f"exit {result.returncode}")
        return result.stdout

    return execute


def watch(execute, args, console=None) -> int:
    baseline_path = args.baseline or latest_baseline()
    baseline = {**json.loads(baseline_path.read_text()), "path": str(baseline_path)}
    # A baseline from the other coverage mode still guides the shape, but say so.
    baseline["note_mode"] = getattr(args, "mode", None) not in {None, baseline.get("mode")}
    mirror = args.mirror
    poller = Poller(execute, args, mirror, args.poll_seconds)
    if args.once:
        poller.poll()
    else:
        poller.start()
    dashboard, key, saved = None, None, False
    try:
        while True:
            with poller.lock:
                snapshot, error, received, offset = poller.snapshot, poller.error, poller.received, poller.clock_offset
            if poller.ended:
                status = dashboard.status if dashboard else None
                (console or Console()).print(f"Sandbox has ended (last seen: {status or 'no telemetry'}).")
                return 0 if status == "passed" else 1
            if snapshot:
                names = snapshot.get("names") or baseline["order"]
                resources = snapshot.get("resources") or baseline["resources"]
                current = (snapshot["run"], bool(snapshot["journal"]), tuple(names), json.dumps(resources, sort_keys=True))
                if current != key:
                    if dashboard:
                        dashboard.__exit__(None, None, None)
                    output = mirror / (Path(snapshot["run"]).name if snapshot["run"] else "waiting")
                    output.mkdir(parents=True, exist_ok=True)
                    dashboard = CloudDashboard(names, output, resources, baseline, journal=bool(snapshot["journal"]),
                                               ui="plain" if args.once else "auto", console=console)
                    dashboard.__enter__()
                    key = current
                dashboard.received, dashboard.clock_offset = received, offset
                dashboard.apply_journal(snapshot["journal"])
                dashboard.apply_sample(snapshot.get("sample"))
                if not snapshot["journal"]:
                    dashboard.status = snapshot.get("status") or "starting"
                if error:
                    dashboard.logs.append((time.time(), "Telemetry fetch failed · retrying · " + error))
                dashboard.tick()
                finished = dashboard.status in {"passed", "failed", "interrupted", "stopped", "action-required"}
                if finished and dashboard.status == "passed" and snapshot["run"] and snapshot["caught_up"] and not saved:
                    saved = save_baseline(snapshot, mirror, args)
                if args.once or (finished and not args.keep_open):
                    if args.once:
                        (console or Console()).print(dashboard.render())
                    return 0 if dashboard.status in {"running", "retaining", "passed"} else 1
            elif error and args.once:
                raise SystemExit(error)
            time.sleep(1 - time.time() % 1)  # redraw on whole seconds
    except KeyboardInterrupt:
        return 130
    finally:
        poller.stopped.set()
        if dashboard:
            dashboard.__exit__(None, None, None)


def save_baseline(snapshot, mirror: Path, args, directory: Path = BASELINES) -> bool:
    """Write one baseline per sandbox; a second watcher of the same run finds it and skips."""
    events = mirror / Path(snapshot["run"]).name / "progress.jsonl"
    started = (snapshot["journal"] or {}).get("started_epoch", time.time())
    day = datetime.fromtimestamp(started, UTC).strftime("%Y-%m-%d")
    baseline = build_baseline(events, label=f"{day} CW run {args.sandbox_id[:8]}",
                              resources=snapshot.get("resources") or {}, journal=snapshot["journal"])
    directory.mkdir(exist_ok=True)
    try:
        with (directory / f"{day}-{args.sandbox_id}.json").open("x") as stream:
            stream.write(json.dumps(baseline, indent=2) + "\n")
    except FileExistsError:
        pass
    return True


def parser():
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument("--sandbox-id", required=True, help="Running sandbox to read (read-only exec).")
    result.add_argument("--preview-root", default="/probe/full-candidate",
                        help="Remote serving_prepare preview root containing prepare-report.json.")
    result.add_argument("--journal", default="/probe/fullrun/status.json",
                        help="Remote driver journal with restore/build/retention steps; '' to omit.")
    result.add_argument("--baseline", type=Path, help="Baseline JSON (default: newest in run-baselines/).")
    result.add_argument("--mirror", type=Path, help="Local event mirror (default: .tmp/cloud-runs/<sandbox>).")
    result.add_argument("--poll-seconds", type=float, default=1.0, help="Minimum spacing between telemetry fetches.")
    result.add_argument("--once", action="store_true", help="Fetch once, print one frame and exit.")
    result.add_argument("--keep-open", action="store_true", help="Keep the display open after the run ends.")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    args.mode = None
    args.mirror = args.mirror or REPOSITORY / ".tmp/cloud-runs" / args.sandbox_id
    args.mirror.mkdir(parents=True, exist_ok=True)
    return watch(sandbox_executor(args.sandbox_id), args)


if __name__ == "__main__":
    os.environ.setdefault("WANDB_SILENT", "true")
    raise SystemExit(main())
