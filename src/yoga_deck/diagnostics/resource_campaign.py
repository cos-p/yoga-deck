"""Privacy-safe runtime resource measurements for reliability campaigns."""

from __future__ import annotations

import math
import os
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any


@dataclass(frozen=True)
class ResourceCounters:
    cpu_seconds: float
    rss_bytes: int
    scheduler_switches: int


@dataclass(frozen=True)
class ResourceSample:
    elapsed_seconds: float
    counters: ResourceCounters


@dataclass(frozen=True)
class ResourceCampaignSummary:
    duration_seconds: float
    samples: int
    cpu_percent_one_core: float
    rss_mib: dict[str, float]
    scheduler_switches_per_second: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "duration_seconds": self.duration_seconds,
            "samples": self.samples,
            "cpu_percent_one_core": self.cpu_percent_one_core,
            "rss_mib": self.rss_mib,
            "scheduler_switches_per_second": self.scheduler_switches_per_second,
        }


def read_process_counters(
    process_id: int,
    *,
    proc_root: Path = Path("/proc"),
    clock_ticks: int | None = None,
) -> ResourceCounters:
    """Read narrow counters without retaining process names or command lines."""

    process_root = proc_root / str(process_id)
    stat_text = (process_root / "stat").read_text()
    closing_paren = stat_text.rfind(")")
    if closing_paren < 0:
        raise OSError("invalid_process_stat")
    stat_fields = stat_text[closing_paren + 1 :].split()
    if len(stat_fields) <= 12:
        raise OSError("invalid_process_stat")

    ticks = clock_ticks if clock_ticks is not None else os.sysconf("SC_CLK_TCK")
    cpu_seconds = (int(stat_fields[11]) + int(stat_fields[12])) / ticks

    rss_kib: int | None = None
    voluntary = 0
    involuntary = 0
    for line in (process_root / "status").read_text().splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        if key == "VmRSS":
            rss_kib = int(value.split()[0])
        elif key == "voluntary_ctxt_switches":
            voluntary = int(value.strip())
        elif key == "nonvoluntary_ctxt_switches":
            involuntary = int(value.strip())
    if rss_kib is None:
        raise OSError("missing_process_rss")

    return ResourceCounters(
        cpu_seconds=cpu_seconds,
        rss_bytes=rss_kib * 1024,
        scheduler_switches=voluntary + involuntary,
    )


def summarize_resource_samples(
    samples: Sequence[ResourceSample],
) -> ResourceCampaignSummary:
    if len(samples) < 2:
        raise ValueError("at_least_two_samples_required")
    first = samples[0]
    last = samples[-1]
    duration = last.elapsed_seconds - first.elapsed_seconds
    if duration <= 0:
        raise ValueError("positive_duration_required")

    rss_values = sorted(sample.counters.rss_bytes / (1024 * 1024) for sample in samples)
    cpu_delta = max(0.0, last.counters.cpu_seconds - first.counters.cpu_seconds)
    switch_delta = max(
        0,
        last.counters.scheduler_switches - first.counters.scheduler_switches,
    )
    p95_index = max(0, math.ceil(0.95 * len(rss_values)) - 1)
    return ResourceCampaignSummary(
        duration_seconds=round(duration, 3),
        samples=len(samples),
        cpu_percent_one_core=round(cpu_delta / duration * 100, 3),
        rss_mib={
            "min": round(rss_values[0], 3),
            "median": round(median(rss_values), 3),
            "p95": round(rss_values[p95_index], 3),
            "max": round(rss_values[-1], 3),
        },
        scheduler_switches_per_second=round(switch_delta / duration, 3),
    )


def _user_service_process_id() -> int:
    result = subprocess.run(
        (
            "systemctl",
            "--user",
            "show",
            "yoga-deck.service",
            "--property=MainPID",
            "--value",
        ),
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if result.returncode != 0:
        raise OSError("runtime_service_unavailable")
    try:
        process_id = int(result.stdout.strip())
    except ValueError as error:
        raise OSError("runtime_service_unavailable") from error
    if process_id <= 0:
        raise OSError("runtime_service_unavailable")
    return process_id


def run_resource_campaign(
    *,
    duration_seconds: int = 60,
    interval_seconds: float = 1.0,
    process_id_resolver: Callable[[], int] = _user_service_process_id,
    counter_reader: Callable[[int], ResourceCounters] = read_process_counters,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> ResourceCampaignSummary:
    """Sample the deployed runtime and return aggregates without process identity."""

    if duration_seconds < 1 or interval_seconds <= 0:
        raise ValueError("invalid_measurement_duration")
    process_id = process_id_resolver()
    started = clock()
    samples: list[ResourceSample] = []
    while True:
        now = clock()
        samples.append(ResourceSample(now - started, counter_reader(process_id)))
        remaining = duration_seconds - (now - started)
        if remaining <= 0:
            break
        sleep(min(interval_seconds, remaining))
    return summarize_resource_samples(tuple(samples))
