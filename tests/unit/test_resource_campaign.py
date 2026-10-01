from pathlib import Path

from yoga_deck.diagnostics.resource_campaign import (
    ResourceCounters,
    ResourceSample,
    read_process_counters,
    summarize_resource_samples,
)


def test_read_process_counters_normalizes_proc_data(tmp_path: Path) -> None:
    process = tmp_path / "42"
    process.mkdir()
    fields = ["0"] * 50
    fields[11] = "150"
    fields[12] = "50"
    (process / "stat").write_text("42 (runtime worker) " + " ".join(fields) + "\n")
    (process / "status").write_text(
        "Name:\truntime\n"
        "VmRSS:\t2048 kB\n"
        "voluntary_ctxt_switches:\t7\n"
        "nonvoluntary_ctxt_switches:\t3\n"
    )

    counters = read_process_counters(
        42,
        proc_root=tmp_path,
        clock_ticks=100,
    )

    assert counters == ResourceCounters(
        cpu_seconds=2.0,
        rss_bytes=2 * 1024 * 1024,
        scheduler_switches=10,
    )


def test_resource_summary_contains_only_aggregate_measurements() -> None:
    summary = summarize_resource_samples(
        (
            ResourceSample(0.0, ResourceCounters(10.0, 10 * 1024 * 1024, 100)),
            ResourceSample(5.0, ResourceCounters(10.1, 12 * 1024 * 1024, 104)),
            ResourceSample(10.0, ResourceCounters(10.2, 11 * 1024 * 1024, 108)),
        )
    )

    assert summary.as_dict() == {
        "duration_seconds": 10.0,
        "samples": 3,
        "cpu_percent_one_core": 2.0,
        "rss_mib": {"min": 10.0, "median": 11.0, "p95": 12.0, "max": 12.0},
        "scheduler_switches_per_second": 0.8,
    }
    rendered = repr(summary).lower()
    assert "pid" not in rendered
    assert "/home/" not in rendered
