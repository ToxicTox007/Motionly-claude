#!/usr/bin/env python3
"""Sample a Compose project's resource use and judge its container health.

``sample`` records each service's peak memory and CPU from ``docker stats``
until a stop file appears. ``verdict`` (via :func:`verdict`) fails a run when
any container restarted, was OOM-killed, or stopped: a long-running service
must still be running and a one-shot must have exited 0. The CPU-stage
budgets (Decision D2) are measured with this sampler.

Usage::

    resource_sampler.py sample --project P --output peaks.json --stop-file stop
    resource_sampler.py states --project P
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

_UNITS = {
    "b": 1,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
}
_QUANTITY = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)\s*$")


def parse_bytes(text: str) -> int:
    """Parse docker stats sizes such as ``512MiB``, ``1.5GiB`` or ``900kB``."""
    match = _QUANTITY.match(text)
    if not match or match.group(2).lower() not in _UNITS:
        raise ValueError(f"unrecognised size: {text!r}")
    return int(float(match.group(1)) * _UNITS[match.group(2).lower()])


def parse_percent(text: str) -> float:
    return float(text.strip().rstrip("%") or 0)


def verdict(states: dict[str, dict], *, one_shots: set[str]) -> list[str]:
    """Problems that fail a run, sorted by service name."""
    problems: list[str] = []
    for service in sorted(states):
        state = states[service]
        if state["restart_count"]:
            problems.append(f"{service} restarted {state['restart_count']} time(s)")
        if state["oom_killed"]:
            problems.append(f"{service} was OOM-killed")
        if service in one_shots:
            if not state["running"] and state["exit_code"] != 0:
                problems.append(f"{service} exited with code {state['exit_code']}")
        elif not state["running"]:
            problems.append(f"{service} is not running (exit code {state['exit_code']})")
    return problems


def _docker(*args: str) -> str:
    return subprocess.run(
        ["docker", *args], check=True, capture_output=True, text=True
    ).stdout


def project_containers(project: str) -> dict[str, str]:
    """Container name -> Compose service, for every container of the project."""
    output = _docker(
        "ps",
        "--all",
        "--filter",
        f"label=com.docker.compose.project={project}",
        "--format",
        '{{.Names}}\t{{.Label "com.docker.compose.service"}}',
    )
    return dict(line.split("\t", 1) for line in output.splitlines() if "\t" in line)


def container_states(project: str) -> dict[str, dict]:
    containers = project_containers(project)
    if not containers:
        return {}
    inspected = json.loads(_docker("inspect", *containers))
    states: dict[str, dict] = {}
    for container in inspected:
        service = container["Config"]["Labels"]["com.docker.compose.service"]
        state = container["State"]
        states[service] = {
            "restart_count": int(container.get("RestartCount") or 0),
            "oom_killed": bool(state.get("OOMKilled")),
            "running": bool(state.get("Running")),
            "exit_code": int(state.get("ExitCode") or 0),
            "memory_limit_bytes": int(container["HostConfig"].get("Memory") or 0) or None,
        }
    return states


def sample_once(project: str) -> dict[str, dict[str, float]]:
    containers = project_containers(project)
    running = _docker(
        "ps", "--filter", f"label=com.docker.compose.project={project}", "--format", "{{.Names}}"
    ).split()
    if not running:
        return {}
    output = _docker("stats", "--no-stream", "--format", "{{json .}}", *running)
    samples: dict[str, dict[str, float]] = {}
    for line in output.splitlines():
        row = json.loads(line)
        service = containers.get(row["Name"])
        if service is None:
            continue
        used = row["MemUsage"].split("/", 1)[0]
        samples[service] = {
            "memory_bytes": parse_bytes(used),
            "cpu_percent": parse_percent(row["CPUPerc"]),
        }
    return samples


def run_sampler(project: str, output: Path, stop_file: Path, interval: float) -> None:
    peaks: dict[str, dict[str, float]] = {}
    count = 0
    started = time.monotonic()
    while True:
        stopping = stop_file.exists()
        try:
            current = sample_once(project)
        except (subprocess.CalledProcessError, ValueError, json.JSONDecodeError):
            current = {}
        count += 1
        for service, sample in current.items():
            peak = peaks.setdefault(service, {"memory_bytes": 0, "cpu_percent": 0.0, "samples": 0})
            peak["memory_bytes"] = max(peak["memory_bytes"], sample["memory_bytes"])
            peak["cpu_percent"] = max(peak["cpu_percent"], sample["cpu_percent"])
            peak["samples"] += 1
        output.write_text(
            json.dumps(
                {
                    "project": project,
                    "interval_seconds": interval,
                    "sample_rounds": count,
                    "duration_seconds": round(time.monotonic() - started, 1),
                    "peaks": peaks,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        if stopping:
            return
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    sample = commands.add_parser("sample")
    sample.add_argument("--project", required=True)
    sample.add_argument("--output", type=Path, required=True)
    sample.add_argument("--stop-file", type=Path, required=True)
    sample.add_argument("--interval", type=float, default=5.0)
    states = commands.add_parser("states")
    states.add_argument("--project", required=True)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "sample":
        run_sampler(args.project, args.output, args.stop_file, args.interval)
    else:
        print(json.dumps(container_states(args.project), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
