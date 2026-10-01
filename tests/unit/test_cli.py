import argparse
import json
import re
from pathlib import Path

import pytest

from yoga_deck.cli import build_parser, main
from yoga_deck.core import TabletModeChanged
from yoga_deck.diagnostics import (
    Component,
    ComponentStatus,
    InspectionReport,
    Monitor,
)
from yoga_deck.diagnostics.journal import EventRecord, loads_jsonl


class FakeDiscovery:
    def inspect(self) -> InspectionReport:
        return InspectionReport(
            components=(
                Component("tablet_switch", ComponentStatus.AVAILABLE, ("switch",)),
                Component("pen", ComponentStatus.MISSING),
            ),
            monitors=(Monitor("eDP-1", True, 1920, 1080, 1.25, 0),),
        )


PUBLIC_ALPHA_COMMANDS = {
    "config",
    "inspect",
    "measure-runtime",
    "record",
    "recover-inputs",
    "replay",
    "report",
    "setup",
    "test-near-button",
    "test-ocr-capture",
    "test-osk-theme",
    "test-orientation",
    "test-palm-rejection",
    "test-pen-actions",
    "test-scratchpad",
    "uninstall",
}


def _documented_commands(path: Path) -> set[str]:
    return set(re.findall(r"^\| `([a-z][a-z-]*)` \|", path.read_text(), re.MULTILINE))


def test_public_alpha_command_surface_matches_help_and_docs() -> None:
    parser = build_parser()
    subparsers = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    root = Path(__file__).parents[2]

    assert set(subparsers.choices) == PUBLIC_ALPHA_COMMANDS
    assert _documented_commands(root / "README.md") == PUBLIC_ALPHA_COMMANDS
    assert _documented_commands(root / "docs" / "architecture.md") == PUBLIC_ALPHA_COMMANDS


def test_cli_without_command_prints_help(capsys) -> None:
    assert main([]) == 0
    assert "Yoga Deck" in capsys.readouterr().out


def test_inspect_renders_safe_readable_report(capsys) -> None:
    assert main(["inspect"], discovery=FakeDiscovery()) == 0

    output = capsys.readouterr().out
    assert "tablet_switch: available" in output
    assert "pen: missing" in output
    assert "eDP-1: 1920x1080 scale 1.25 transform 0 (internal)" in output


def test_inspect_can_render_json(capsys) -> None:
    assert main(["inspect", "--json"], discovery=FakeDiscovery()) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 2
    assert payload["components"][0]["role"] == "tablet_switch"


def test_inspect_failure_uses_stable_code_without_exception_text(capsys) -> None:
    class BrokenDiscovery:
        def inspect(self) -> InspectionReport:
            raise OSError("private failure at /home/alice/device-serial-123")

    assert main(["inspect"], discovery=BrokenDiscovery()) == 1

    captured = capsys.readouterr()
    assert "inspection_failed" in captured.err
    assert "alice" not in captured.err
    assert "serial" not in captured.err


def test_report_requires_explicit_redaction_opt_in(capsys) -> None:
    assert main(["report"], discovery=FakeDiscovery()) == 2

    captured = capsys.readouterr()
    assert "report_redaction_opt_in_required" in captured.err
    assert captured.out == ""


def test_report_composes_discovery_and_runtime_as_redacted_json(capsys) -> None:
    def status_requester(payload):
        assert payload == {"command": "status"}
        return {
            "schema_version": 1,
            "available": True,
            "sequence": 42,
            "posture": "laptop",
            "inputs_enabled": True,
            "orientation": "normal",
            "orientation_lock": False,
            "error": None,
        }

    assert (
        main(
            ["report", "--redact", "--json"],
            discovery=FakeDiscovery(),
            runtime_requester=status_requester,
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 2
    assert payload["redacted"] is True
    assert payload["runtime"]["posture"] == "laptop"
    assert "sequence" not in payload["runtime"]
    assert payload["discovery"]["monitors"][0]["connector"] == "eDP-1"


def test_report_source_failures_emit_readable_degraded_report_without_private_text(
    capsys,
) -> None:
    class BrokenDiscovery:
        def inspect(self) -> InspectionReport:
            raise OSError("/home/alice/event12 private monitor description")

    def broken_requester(payload):
        raise RuntimeError("clipboard-secret device-id-123")

    assert (
        main(
            ["report", "--redact"],
            discovery=BrokenDiscovery(),
            runtime_requester=broken_requester,
        )
        == 0
    )

    output = capsys.readouterr().out
    assert "status: degraded" in output
    assert "discovery: unavailable (discovery_unavailable)" in output
    assert "runtime: unavailable (runtime_unavailable)" in output
    assert "alice" not in output
    assert "clipboard-secret" not in output


def test_measure_runtime_renders_only_normalized_aggregates(capsys) -> None:
    class Summary:
        def as_dict(self):
            return {
                "duration_seconds": 30.0,
                "samples": 31,
                "cpu_percent_one_core": 0.1,
                "rss_mib": {"min": 20.0, "median": 20.5, "p95": 21.0, "max": 21.0},
                "scheduler_switches_per_second": 1.2,
            }

    def runner(**kwargs):
        assert kwargs == {"duration_seconds": 30, "interval_seconds": 1.0}
        return Summary()

    assert (
        main(
            ["measure-runtime", "--seconds", "30", "--json"],
            resource_runner=runner,
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["cpu_percent_one_core"] == 0.1
    assert "pid" not in payload


def test_measure_runtime_failure_uses_stable_error(capsys) -> None:
    def runner(**kwargs):
        raise OSError("private process details")

    assert main(["measure-runtime", "--seconds", "1"], resource_runner=runner) == 1
    captured = capsys.readouterr()
    assert "runtime_measurement_failed" in captured.err
    assert "private" not in captured.err


def test_replay_command_outputs_deterministic_summary(capsys) -> None:
    fixture = Path(__file__).parents[1] / "fixtures/events/fold_rotate_unfold.jsonl"

    assert main(["replay", str(fixture), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["events"] == 3
    assert payload["final_state"]["internal_inputs_enabled"] is True


def test_replay_error_does_not_echo_private_path(tmp_path, capsys) -> None:
    private = tmp_path / "alice-private.jsonl"
    private.write_text("not-json\n")

    assert main(["replay", str(private)]) == 1

    captured = capsys.readouterr()
    assert "malformed_event_record" in captured.err
    assert str(private) not in captured.err


def test_record_writes_normalized_events_from_injected_source(tmp_path, capsys) -> None:
    class FakeEventSource:
        def record(self, scenario: str) -> tuple[EventRecord, ...]:
            assert scenario == "fold-unfold"
            return (
                EventRecord(0, TabletModeChanged(True)),
                EventRecord(50, TabletModeChanged(False)),
            )

    output = tmp_path / "normalized.jsonl"

    assert (
        main(
            ["record", "fold-unfold", "--output", str(output)],
            event_source=FakeEventSource(),
        )
        == 0
    )

    assert len(loads_jsonl(output.read_text())) == 2
    assert "Recorded 2 normalized events." in capsys.readouterr().out


def test_record_without_capture_adapter_does_not_create_file(tmp_path, capsys) -> None:
    class UnavailableSource:
        def record(self, scenario: str) -> tuple[EventRecord, ...]:
            from yoga_deck.diagnostics.journal import JournalError

            raise JournalError("capture_unavailable")

    output = tmp_path / "should-not-exist.jsonl"

    assert (
        main(
            ["record", "fold-unfold", "--output", str(output)],
            event_source=UnavailableSource(),
        )
        == 1
    )

    assert not output.exists()
    assert "capture_unavailable" in capsys.readouterr().err


def test_recover_inputs_uses_injected_safe_recovery(capsys) -> None:
    class FakeRecovery:
        def recover_inputs(self) -> None:
            self.called = True

    recovery = FakeRecovery()

    assert (
        main(
            ["recover-inputs"],
            input_recovery=recovery,
            runtime_requester=lambda _: {"available": False},
        )
        == 0
    )

    assert recovery.called is True
    assert "Internal inputs enabled." in capsys.readouterr().out


def test_test_orientation_renders_report(capsys) -> None:
    from yoga_deck.diagnostics.orientation_campaign import (
        AlignmentSample,
        OrientationAlignmentResult,
        OrientationCampaignSummary,
        OrientationTransitionRecord,
    )

    sample = AlignmentSample("center", 768.0, 432.0, 768.0, 432.0, 0.0, True)
    align = OrientationAlignmentResult("normal", 0, 1536.0, 864.0, (sample,), (sample,), True)
    trans = OrientationTransitionRecord(None, "normal", "normal", 42.0)
    fake_summary = OrientationCampaignSummary(
        mount_direction_valid=True,
        orientations_tested=("normal",),
        transitions=(trans,),
        alignments=(align,),
        external_monitors_preserved=True,
        starting_transform_restored=True,
        delivery_latencies_ms={"count": 1, "min": 42.0, "median": 42.0, "p95": 42.0, "max": 42.0},
        flicker_observed=False,
        summary_passed=True,
    )

    async def fake_runner(**kwargs):
        if "prompt_callback" in kwargs and kwargs["prompt_callback"]:
            kwargs["prompt_callback"]("testing prompt")
        return fake_summary

    assert main(["test-orientation", "--live"], orientation_runner=fake_runner) == 0

    output = capsys.readouterr().out
    assert "Orientation and Four-Corner Alignment Validation" in output
    assert "Mount Direction Valid**: Pass" in output


def test_test_orientation_failure(capsys) -> None:
    async def broken_runner(**kwargs):
        raise RuntimeError("simulated failure")

    assert main(["test-orientation", "--live"], orientation_runner=broken_runner) == 1

    err = capsys.readouterr().err
    assert "orientation_test_failed" in err


def test_test_orientation_requires_explicit_live_flag(capsys) -> None:
    assert main(["test-orientation"]) == 2
    assert "live_desktop_opt_in_required" in capsys.readouterr().err


def test_test_palm_rejection_requires_explicit_live_flag(capsys) -> None:
    assert main(["test-palm-rejection"]) == 2
    assert "live_desktop_opt_in_required" in capsys.readouterr().err


def test_test_palm_rejection_renders_normalized_json(capsys) -> None:
    from yoga_deck.diagnostics.palm_rejection import PalmSummary, ScenarioSummary

    summary = PalmSummary(
        {"palm_only": ScenarioSummary(5, 5, 0)},
        (20.0, 30.0),
    )

    def fake_runner(**kwargs):
        assert kwargs["trials"] == 5
        assert kwargs["observation_seconds"] == 30
        assert kwargs["recovery_seconds"] == 45
        assert kwargs["timeout"] == 50
        assert kwargs["scenarios"] is None
        return summary

    assert (
        main(
            ["test-palm-rejection", "--live", "--json"],
            palm_runner=fake_runner,
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["scenarios"]["palm_only"]["attempted"] == 5
    assert payload["recovery_latency_ms"]["median"] == 25.0


def test_test_palm_rejection_can_select_conditions(capsys) -> None:
    from yoga_deck.diagnostics.palm_rejection import PalmSummary

    def fake_runner(**kwargs):
        assert kwargs["scenarios"] == ("pen_hover_palm", "recovery")
        return PalmSummary({}, ())

    assert (
        main(
            [
                "test-palm-rejection",
                "--live",
                "--scenario",
                "pen_hover_palm",
                "--scenario",
                "recovery",
                "--json",
            ],
            palm_runner=fake_runner,
        )
        == 0
    )


def test_test_pen_actions_requires_hardware_opt_in(capsys) -> None:
    assert main(["test-pen-actions"]) == 2
    assert "hardware_opt_in_required" in capsys.readouterr().err


def test_near_button_application_test_requires_live_opt_in(capsys) -> None:
    assert main(["test-near-button"]) == 2
    assert "live_desktop_opt_in_required" in capsys.readouterr().err


def test_near_button_application_test_renders_normalized_json(capsys) -> None:
    from yoga_deck.diagnostics.near_button_app import NearButtonSummary

    summary = NearButtonSummary(
        attempted=3,
        control_raw_pen=1,
        control_application_pen=1,
        raw_eraser=3,
        application_pen=3,
        application_eraser=2,
        confirmed=2,
        raw_only=1,
        application_only=0,
    )

    def fake_runner(**kwargs):
        assert kwargs == {"trials": 3, "seconds": 15}
        return summary

    assert (
        main(
            ["test-near-button", "--live", "--trials", "3", "--seconds", "15", "--json"],
            near_button_runner=fake_runner,
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == summary.as_dict()


def test_ocr_capture_requires_live_opt_in(capsys) -> None:
    assert main(["test-ocr-capture"]) == 2
    assert "live_desktop_opt_in_required" in capsys.readouterr().err


def test_scratchpad_launch_requires_live_opt_in(capsys) -> None:
    assert main(["test-scratchpad"]) == 2
    assert "live_desktop_opt_in_required" in capsys.readouterr().err


def test_scratchpad_launch_uses_injected_runner(capsys) -> None:
    calls = []

    assert (
        main(
            ["test-scratchpad", "--live", "--hold-seconds", "1"],
            scratchpad_runner=lambda: calls.append("launch"),
        )
        == 0
    )
    assert calls == ["launch"]
    assert capsys.readouterr().out == "Scratchpad launched.\n"


def test_ocr_capture_renders_privacy_safe_json(capsys) -> None:
    from yoga_deck.diagnostics.ocr_capture import OcrCaptureSummary

    summary = OcrCaptureSummary(2, 1, 1, 0, (100.0, 200.0))

    def fake_runner(**kwargs):
        assert kwargs == {"trials": 2}
        return summary

    assert (
        main(
            ["test-ocr-capture", "--live", "--trials", "2", "--json"],
            ocr_runner=fake_runner,
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == summary.as_dict()


def test_test_pen_actions_renders_normalized_json(capsys) -> None:
    from yoga_deck.diagnostics.pen_actions import PenActionSummary, ScenarioResult

    summary = PenActionSummary(
        {"button_far_tip": ScenarioResult(attempted=5, complete=5, incomplete=0)},
        (0.25, 0.75),
    )

    def fake_runner(**kwargs):
        assert kwargs == {"trials": 5, "seconds": 30, "scenarios": None}
        return summary

    assert (
        main(
            ["test-pen-actions", "--hardware", "--json"],
            pen_actions_runner=fake_runner,
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["scenarios"]["button_far_tip"]["complete"] == 5
    assert payload["pressure_peak"]["median"] == 0.5


@pytest.mark.parametrize("response", [None, {"ok": False}, {"available": False}, {"ok": True}])
def test_recovery_falls_back_for_unavailable_or_failed_runtime(response, capsys):
    class Recovery:
        called = False

        def recover_inputs(self):
            self.called = True

    recovery = Recovery()
    assert (
        main(["recover-inputs"], input_recovery=recovery, runtime_requester=lambda _: response) == 0
    )
    assert recovery.called


def test_recovery_uses_runtime_and_does_not_mutate_independently():
    class Recovery:
        def recover_inputs(self):
            pytest.fail("recovery must be owned by runtime")

    requests = []

    def requester(payload):
        requests.append(payload)
        return {"ok": True, "inputs_enabled": True}

    assert main(["recover-inputs"], input_recovery=Recovery(), runtime_requester=requester) == 0
    assert requests == [{"command": "recover_inputs"}]


def test_recovery_works_without_runtime_directory():
    class Recovery:
        called = False

        def recover_inputs(self):
            self.called = True

    def requester(payload):
        raise RuntimeError("xdg_runtime_dir_unavailable")

    recovery = Recovery()
    assert main(["recover-inputs"], input_recovery=recovery, runtime_requester=requester) == 0
    assert recovery.called


def test_inspect_reports_unreadable_runtime_inputs_with_remediation(capsys) -> None:
    class DeniedDiscovery:
        def inspect(self) -> InspectionReport:
            return InspectionReport(
                components=(
                    Component("tablet_switch", ComponentStatus.PERMISSION_DENIED, ("switch",)),
                    Component("pen", ComponentStatus.PERMISSION_DENIED, ("absolute", "keys")),
                ),
                diagnostics=("input_permission_denied",),
            )

    assert main(["inspect"], discovery=DeniedDiscovery()) == 0

    output = capsys.readouterr().out
    assert "tablet_switch: permission_denied [switch]" in output
    assert "tablet_switch: available" not in output
    assert "remediation input_group_required:" in output
    assert "docs/install.md" in output
