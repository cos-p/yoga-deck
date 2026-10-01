"""Diagnostic command-line entry point."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from yoga_deck import __version__
from yoga_deck.diagnostics import (
    InspectionReport,
    build_support_report,
    render_json,
    render_support_json,
    render_support_text,
    render_text,
)
from yoga_deck.diagnostics.journal import EventRecord


class Discovery(Protocol):
    def inspect(self) -> InspectionReport: ...


class EventSource(Protocol):
    def record(self, scenario: str) -> tuple[EventRecord, ...]: ...


class InputRecovery(Protocol):
    def recover_inputs(self) -> None: ...


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="yoga-doctor",
        description="Inspect and test Yoga Deck support.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")
    inspect_parser = subparsers.add_parser(
        "inspect", help="Inspect support without changing the system"
    )
    inspect_parser.add_argument("--json", action="store_true", help="Render normalized JSON")
    report_parser = subparsers.add_parser(
        "report", help="Create a privacy-safe discovery and runtime support report"
    )
    report_parser.add_argument(
        "--redact",
        action="store_true",
        help="Required acknowledgement that only normalized redacted fields are emitted",
    )
    report_parser.add_argument("--json", action="store_true", help="Render normalized JSON")
    replay_parser = subparsers.add_parser(
        "replay", help="Replay a normalized event fixture without changing the system"
    )
    replay_parser.add_argument("fixture", help="Normalized JSONL fixture")
    replay_parser.add_argument("--json", action="store_true", help="Render normalized JSON")
    record_parser = subparsers.add_parser(
        "record", help="Record normalized events through a configured capture adapter"
    )
    record_parser.add_argument("scenario", type=_scenario_name, help="Public scenario label")
    record_parser.add_argument("--output", required=True, help="Destination JSONL fixture")
    record_parser.add_argument(
        "--events", type=_positive_int, default=2, help="Number of switch events to capture"
    )
    subparsers.add_parser("recover-inputs", help="Enable every discovered internal input device")
    test_orient_parser = subparsers.add_parser(
        "test-orientation", help="Run guided live orientation and four-corner alignment check"
    )
    test_orient_parser.add_argument(
        "--timeout", type=float, default=30.0, help="Timeout in seconds per orientation step"
    )
    test_orient_parser.add_argument("--json", action="store_true", help="Render normalized JSON")
    test_orient_parser.add_argument(
        "--live", action="store_true", help="Explicitly allow active Hyprland mutation"
    )
    palm_parser = subparsers.add_parser(
        "test-palm-rejection", help="Run guided native pen/touch arbitration measurements"
    )
    palm_parser.add_argument("--trials", type=_positive_int, default=5)
    palm_parser.add_argument(
        "--scenario",
        action="append",
        choices=("palm_only", "pen_hover_palm", "pen_tip_palm", "recovery"),
        help="Run only this condition; may be repeated",
    )
    palm_parser.add_argument(
        "--seconds",
        type=_positive_int,
        default=30,
        help="Observation time for each ordinary condition (default: 30 seconds)",
    )
    palm_parser.add_argument(
        "--recovery-seconds",
        type=_positive_int,
        default=45,
        help="Observation time for recovery (default: 45 seconds)",
    )
    palm_parser.add_argument("--json", action="store_true", help="Render normalized JSON")
    palm_parser.add_argument(
        "--live", action="store_true", help="Explicitly allow a live input-observation surface"
    )
    pen_parser = subparsers.add_parser(
        "test-pen-actions", help="Measure physical pen buttons, eraser, and pressure"
    )
    pen_parser.add_argument("--trials", type=_positive_int, default=5)
    pen_parser.add_argument("--seconds", type=_positive_int, default=30)
    pen_parser.add_argument(
        "--scenario",
        action="append",
        choices=("proximity", "tip_pressure", "button_near_tip", "button_far_tip", "eraser"),
    )
    pen_parser.add_argument("--json", action="store_true")
    pen_parser.add_argument(
        "--hardware", action="store_true", help="Explicitly allow physical input observation"
    )
    near_button_parser = subparsers.add_parser(
        "test-near-button", help="Verify the near-tip button reaches an application as eraser"
    )
    near_button_parser.add_argument("--trials", type=_positive_int, default=3)
    near_button_parser.add_argument("--seconds", type=_positive_int, default=15)
    near_button_parser.add_argument("--json", action="store_true")
    near_button_parser.add_argument(
        "--live", action="store_true", help="Explicitly allow a live input-observation surface"
    )
    osk_theme_parser = subparsers.add_parser(
        "test-osk-theme", help="Verify a wvkbd palette respawn preserves the Fcitx input context"
    )
    osk_theme_parser.add_argument(
        "--theme", default=None, help="Bundled theme supplying the second palette"
    )
    osk_theme_parser.add_argument("--seconds", type=_positive_int, default=60)
    osk_theme_parser.add_argument(
        "--dwell", type=float, default=3.0, help="Seconds to hold each palette so it can be seen"
    )
    osk_theme_parser.add_argument("--output", type=Path, default=None)
    osk_theme_parser.add_argument("--json", action="store_true")
    osk_theme_parser.add_argument(
        "--live", action="store_true", help="Explicitly allow a live on-screen keyboard surface"
    )
    ocr_parser = subparsers.add_parser(
        "test-ocr-capture", help="Measure interactive OCR selection without retaining text"
    )
    ocr_parser.add_argument("--trials", type=_positive_int, default=5)
    ocr_parser.add_argument("--json", action="store_true", help="Render normalized JSON")
    ocr_parser.add_argument(
        "--live",
        action="store_true",
        help="Explicitly allow the OCR picker and its clipboard result",
    )
    scratchpad_parser = subparsers.add_parser(
        "test-scratchpad", help="Launch the optional Xournal++ scratchpad"
    )
    scratchpad_parser.add_argument(
        "--live", action="store_true", help="Explicitly allow desktop application launch"
    )
    scratchpad_parser.add_argument(
        "--hold-seconds",
        type=_positive_int,
        default=10,
        help="Keep the diagnostic parent alive while the scratchpad becomes visible",
    )
    config_parser = subparsers.add_parser(
        "config", help="Read and change user settings without opening an editor"
    )
    config_subparsers = config_parser.add_subparsers(dest="config_command")
    config_get = config_subparsers.add_parser(
        "get", help="Show settings with the file each effective value came from"
    )
    config_get.add_argument("name", nargs="?", help="Dotted setting name; omit to list every one")
    config_get.add_argument("--json", action="store_true", help="Render normalized JSON")
    config_set = config_subparsers.add_parser("set", help="Override one setting")
    config_set.add_argument("name", help="Dotted setting name")
    config_set.add_argument("value", help="New value, in the same form the settings file uses")
    config_unset = config_subparsers.add_parser(
        "unset", help="Drop one override, handing the setting back to config.toml"
    )
    config_unset.add_argument("name", help="Dotted setting name")
    config_subparsers.add_parser("path", help="Show the settings files and whether they exist")
    resource_parser = subparsers.add_parser(
        "measure-runtime", help="Measure aggregate runtime CPU, memory, and scheduler activity"
    )
    resource_parser.add_argument(
        "--seconds", type=_positive_int, default=60, help="Measurement duration"
    )
    resource_parser.add_argument("--json", action="store_true", help="Render normalized JSON")
    setup_parser = subparsers.add_parser(
        "setup", help="Install the user unit and Omarchy plugin for the installed runtime"
    )
    setup_parser.add_argument(
        "--dry-run", action="store_true", help="Print the planned actions without changing anything"
    )
    setup_parser.add_argument(
        "--dev-link",
        action="store_true",
        help="Symlink the source checkout's omarchy-plugin/ instead of copying the packaged plugin",
    )
    setup_parser.add_argument(
        "--with-theme-hook",
        action="store_true",
        help="Also install the Omarchy theme-set hook for live keyboard retinting",
    )
    setup_parser.add_argument(
        "--enable", action="store_true", help="Enable and (re)start the user service"
    )
    setup_parser.add_argument(
        "--enable-plugin", action="store_true", help="Add the plugin to the Omarchy bar"
    )
    uninstall_parser = subparsers.add_parser(
        "uninstall", help="Recover inputs, then remove the user unit, plugin, and hook"
    )
    uninstall_parser.add_argument(
        "--dry-run", action="store_true", help="Print the planned actions without changing anything"
    )
    uninstall_parser.add_argument(
        "--purge", action="store_true", help="Also remove the ~/.config/yoga-deck settings"
    )
    return parser


def _recover_inputs(runtime_requester: Any | None, input_recovery: InputRecovery | None) -> bool:
    if runtime_requester is None:
        from yoga_deck.adapters.shell_transport import request_sync

        runtime_requester = request_sync
    try:
        response = runtime_requester({"command": "recover_inputs"})
    except Exception:
        response = None
    if (
        isinstance(response, dict)
        and response.get("ok") is True
        and response.get("inputs_enabled") is True
    ):
        return True
    # A missing, failed, or unresponsive runtime must never block emergency access.
    # Its request may still finish later; both paths ask only for enablement.
    if input_recovery is None:
        from yoga_deck.adapters.hyprland_inputs import HyprlandInputRecovery

        input_recovery = HyprlandInputRecovery()
    try:
        input_recovery.recover_inputs()
    except Exception:
        return False
    return True


def _run_install(
    args: argparse.Namespace,
    environment: Mapping[str, str] | None,
    runner: Any | None,
    observe: Any | None,
    recover: Any,
) -> int:
    from yoga_deck import installation as inst

    paths = inst.Paths.from_environment(os.environ if environment is None else environment)
    host = observe(paths) if observe is not None else inst.observe_host(paths)
    if args.command == "setup":
        try:
            assets = inst.load_assets()
        except OSError:
            print("yoga-doctor: packaged_assets_missing", file=sys.stderr)
            return 1
        plan = inst.plan_setup(
            inst.SetupOptions(
                dev_link=args.dev_link,
                with_theme_hook=args.with_theme_hook,
                enable=args.enable,
                enable_plugin=args.enable_plugin,
            ),
            paths,
            assets,
            host,
        )
        print("Prerequisites:")
        for finding in plan.findings:
            status = "ok" if finding.ok else ("MISSING" if finding.required else "missing")
            line = f"  {finding.name}: {status}"
            print(line if finding.ok else f"{line} - {finding.hint}")
    else:
        plan = inst.plan_uninstall(inst.UninstallOptions(purge=args.purge), paths, host)
    for note in plan.notes:
        print(f"note: {note}")
    for error in plan.errors:
        print(f"yoga-doctor: {error}", file=sys.stderr)
    blocked = args.command == "setup" and plan.blocked
    if blocked and not args.dry_run:
        print("Setup blocked; nothing was changed.", file=sys.stderr)
        return 1
    if args.dry_run:
        if blocked:
            print("Setup would be blocked by the missing prerequisites above.")
        print("Planned actions (dry run, nothing changed):")
        for action in plan.actions:
            print(f"- {action.describe()}")
        ok = True
    else:
        print("Actions:")
        ok = inst.execute(
            plan.actions,
            runner=runner if runner is not None else inst.default_runner,
            recover=recover,
        )
    if not ok:
        print(f"yoga-doctor: {args.command}_incomplete", file=sys.stderr)
        return 1
    if plan.next_steps:
        print("Next steps:")
        for step in plan.next_steps:
            print(f"  {step}")
    return 1 if plan.errors or blocked else 0


def _format_setting_value(value: Any) -> str:
    """One display form for every setting type, including the absence of a value."""

    if value is None:
        return "(unset)"
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(getattr(value, "value", value))


def _effective(snapshot: Mapping[str, Any]) -> dict[str, tuple[Any, str]]:
    """Every setting's current value paired with the file that set it, or "default".

    Read off the same snapshot the Omarchy panel is served, so the two clients cannot come to
    different conclusions about what a setting is or where it came from.
    """

    return {
        entry["name"]: (entry["value"], entry["source"] or "default")
        for entry in snapshot["settings"]
    }


def _run_config(args: argparse.Namespace, environment: Mapping[str, str] | None) -> int:
    from yoga_deck.adapters.user_settings import (
        LEGACY_PEN_FILENAME,
        OVERRIDES_FILENAME,
        SETTINGS_FILENAME,
        clear_override,
        config_root,
        load_settings,
        write_override,
    )
    from yoga_deck.core.settings import SETTING_NAMES, SPECS_BY_NAME, settings_snapshot

    command = args.config_command or "get"

    if command == "path":
        root = config_root(environment)
        effective_environment = os.environ if environment is None else environment
        display_root = (
            "$XDG_CONFIG_HOME/yoga-deck"
            if effective_environment.get("XDG_CONFIG_HOME")
            else "~/.config/yoga-deck"
        )
        for filename in (SETTINGS_FILENAME, OVERRIDES_FILENAME, LEGACY_PEN_FILENAME):
            path = root / filename
            print(f"{display_root}/{filename}  {'present' if path.exists() else 'absent'}")
        return 0

    if command in {"set", "unset"} and args.name not in SPECS_BY_NAME:
        print("yoga-doctor: unknown_setting", file=sys.stderr)
        print("Known settings: " + ", ".join(SETTING_NAMES), file=sys.stderr)
        return 2

    if command == "set":
        spec = SPECS_BY_NAME[args.name]
        parsed_value = spec.parse_text(args.value)
        if parsed_value is None:
            # Rejected here rather than stored and quietly dropped at load time. The loader
            # degrades because a keyboard must still appear; a person at a prompt is owed the
            # rule they broke instead.
            print("yoga-doctor: invalid_value", file=sys.stderr)
            print(f"{spec.name} accepts {spec.describe()}.", file=sys.stderr)
            return 2
        # Read before writing: afterwards every origin is the overrides file, and the point
        # of the note is to name the hand-written file the user will now find has no effect.
        shadowed = dict(load_settings(environment=environment).origins).get(spec.name)
        try:
            target = write_override(spec.name, parsed_value, environment=environment)
        except OSError:
            print("yoga-doctor: config_write_failed", file=sys.stderr)
            return 1
        print(f"{spec.name} = {_format_setting_value(parsed_value)}  ({target.name})")
        if shadowed is not None and shadowed != target.name:
            print(f"Note: {shadowed} also sets {spec.name}; this override now wins.")
        return 0

    if command == "unset":
        try:
            removed = clear_override(args.name, environment=environment)
        except OSError:
            print("yoga-doctor: config_write_failed", file=sys.stderr)
            return 1
        source = dict(load_settings(environment=environment).origins).get(args.name, "default")
        if not removed:
            print(f"{args.name} was not overridden; it comes from {source}.")
            return 0
        print(f"{args.name} override removed; it now comes from {source}.")
        return 0

    snapshot = settings_snapshot(load_settings(environment=environment))
    values = _effective(snapshot)
    # `yoga-doctor config` with no verb is a listing, so those two arguments may not exist.
    requested = getattr(args, "name", None)
    as_json = getattr(args, "json", False)

    if requested is not None and requested not in values:
        print("yoga-doctor: unknown_setting", file=sys.stderr)
        print("Known settings: " + ", ".join(SETTING_NAMES), file=sys.stderr)
        return 2
    selected = [requested] if requested else list(values)

    if as_json:
        payload = {
            "settings": {
                name: {"value": values[name][0], "source": values[name][1]} for name in selected
            },
            "ignored": snapshot["ignored"],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if requested:
        spec = SPECS_BY_NAME[requested]
        value, source = values[requested]
        print(f"{spec.name} = {_format_setting_value(value)}")
        print(f"  {spec.summary}")
        print(
            f"  Accepts {spec.describe()}. "
            f"Default {_format_setting_value(spec.default)}. From {source}."
        )
        return 0

    width = max(len(name) for name in selected)
    shown = max(len(_format_setting_value(values[name][0])) for name in selected)
    for name in selected:
        value, source = values[name]
        print(f"{name:<{width}}  {_format_setting_value(value):<{shown}}  {source}")
    for item in snapshot["ignored"]:
        print(
            f"Ignored: {item['source']} sets {item['name']}, which is not a usable setting.",
            file=sys.stderr,
        )
    return 0


def _scenario_name(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value):
        raise argparse.ArgumentTypeError("scenario must be a lowercase public slug")
    return value


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("event count must be positive")
    return parsed


def main(
    argv: Sequence[str] | None = None,
    *,
    discovery: Discovery | None = None,
    event_source: EventSource | None = None,
    input_recovery: InputRecovery | None = None,
    orientation_runner: Any | None = None,
    palm_runner: Any | None = None,
    pen_actions_runner: Any | None = None,
    near_button_runner: Any | None = None,
    osk_theme_runner: Any | None = None,
    ocr_runner: Any | None = None,
    scratchpad_runner: Any | None = None,
    resource_runner: Any | None = None,
    runtime_requester: Any | None = None,
    settings_environment: Mapping[str, str] | None = None,
    install_environment: Mapping[str, str] | None = None,
    install_runner: Any | None = None,
    install_observer: Any | None = None,
) -> int:

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "config":
        return _run_config(args, settings_environment)
    if args.command == "inspect":
        if discovery is None:
            from yoga_deck.adapters.linux_discovery import LinuxDiscovery

            discovery = LinuxDiscovery()
        try:
            report = discovery.inspect()
        except Exception:
            print("yoga-doctor: inspection_failed", file=sys.stderr)
            return 1
        print(render_json(report) if args.json else render_text(report))
        return 0
    if args.command == "report":
        if not args.redact:
            print("yoga-doctor: report_redaction_opt_in_required", file=sys.stderr)
            return 2
        if discovery is None:
            from yoga_deck.adapters.linux_discovery import LinuxDiscovery

            discovery = LinuxDiscovery()
        try:
            inspection = discovery.inspect()
        except Exception:
            inspection = None
        if runtime_requester is None:
            from yoga_deck.adapters.shell_transport import request_sync

            runtime_requester = request_sync
        try:
            runtime_payload = runtime_requester({"command": "status"})
        except Exception:
            runtime_payload = None
        report = build_support_report(inspection, runtime_payload)
        print(render_support_json(report) if args.json else render_support_text(report))
        return 0
    if args.command == "test-pen-actions":
        from yoga_deck.diagnostics.pen_actions import run_guided_pen_campaign

        if not args.hardware:
            print("yoga-doctor: hardware_opt_in_required", file=sys.stderr)
            return 2
        runner = pen_actions_runner or run_guided_pen_campaign
        try:
            summary = runner(
                trials=args.trials,
                seconds=args.seconds,
                scenarios=tuple(args.scenario) if args.scenario else None,
            )
        except KeyboardInterrupt:
            print("yoga-doctor: pen_test_cancelled", file=sys.stderr)
            return 130
        except Exception:
            print("yoga-doctor: pen_test_failed", file=sys.stderr)
            return 1
        payload = summary.as_dict()
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            for scenario, result in payload["scenarios"].items():
                print(
                    f"{scenario}: complete={result['complete']}/{result['attempted']} "
                    f"incomplete={result['incomplete']}"
                )
            pressure = payload["pressure_peak"]
            print(
                "pressure peak: "
                f"count={pressure['count']} median={pressure['median']} max={pressure['max']}"
            )
        return 0
    if args.command == "test-osk-theme":
        from yoga_deck.diagnostics.osk_theme_respawn import run_guided_osk_theme_respawn

        if not args.live:
            print("yoga-doctor: live_desktop_opt_in_required", file=sys.stderr)
            return 2
        runner = osk_theme_runner or run_guided_osk_theme_respawn
        try:
            summary = runner(
                theme=args.theme, seconds=args.seconds, dwell=args.dwell, output=args.output
            )
        except KeyboardInterrupt:
            print("yoga-doctor: osk_theme_test_cancelled", file=sys.stderr)
            return 130
        except Exception:
            print("yoga-doctor: osk_theme_test_failed", file=sys.stderr)
            return 1
        payload = summary.as_dict()
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(
                f"osk theme: focus_preserved={payload['focus_preserved']} "
                f"child_replaced={payload['child_replaced']} "
                f"bus_retained={payload['bus_name_retained']} "
                f"layer_remapped={payload['layer_remapped']} "
                f"geometry_unchanged={payload['geometry_unchanged']} "
                f"remap_ms={payload['remap_ms']}"
            )
        return 0 if payload["focus_preserved"] else 1
    if args.command == "test-near-button":
        from yoga_deck.diagnostics.near_button_app import run_guided_near_button_test

        if not args.live:
            print("yoga-doctor: live_desktop_opt_in_required", file=sys.stderr)
            return 2
        runner = near_button_runner or run_guided_near_button_test
        try:
            summary = runner(trials=args.trials, seconds=args.seconds)
        except KeyboardInterrupt:
            print("yoga-doctor: near_button_test_cancelled", file=sys.stderr)
            return 130
        except Exception:
            print("yoga-doctor: near_button_test_failed", file=sys.stderr)
            return 1
        payload = summary.as_dict()
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(
                f"near button: confirmed={payload['confirmed']}/{payload['attempted']} "
                f"control_raw_pen={payload['control_raw_pen']} "
                f"control_application_pen={payload['control_application_pen']} "
                f"raw_eraser={payload['raw_eraser']} "
                f"application_pen={payload['application_pen']} "
                f"application_eraser={payload['application_eraser']}"
            )
        return 0
    if args.command == "test-ocr-capture":
        from yoga_deck.diagnostics.ocr_capture import run_guided_ocr_capture

        if not args.live:
            print("yoga-doctor: live_desktop_opt_in_required", file=sys.stderr)
            return 2
        runner = ocr_runner or run_guided_ocr_capture
        try:
            summary = runner(trials=args.trials)
        except KeyboardInterrupt:
            print("yoga-doctor: ocr_capture_cancelled", file=sys.stderr)
            return 130
        except Exception:
            print("yoga-doctor: ocr_capture_failed", file=sys.stderr)
            return 1
        payload = summary.as_dict()
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            latency = payload["latency_ms"]
            print(
                f"OCR capture: verified={payload['verified']}/{payload['attempted']} "
                f"unverified={payload['unverified']} command_failed={payload['command_failed']}"
            )
            print(
                "OCR latency (ms): "
                f"count={latency['count']} median={latency['median']} max={latency['max']}"
            )
        return 0
    if args.command == "test-scratchpad":
        import asyncio

        if not args.live:
            print("yoga-doctor: live_desktop_opt_in_required", file=sys.stderr)
            return 2
        if scratchpad_runner is None:
            from yoga_deck.adapters.scratchpad import ScratchpadAdapter

            async def _launch() -> None:
                await ScratchpadAdapter().launch()

            scratchpad_runner = _launch
        try:
            result = scratchpad_runner()
            if hasattr(result, "__await__"):
                asyncio.run(result)
            if scratchpad_runner is not None and args.hold_seconds:
                import time

                time.sleep(args.hold_seconds)
        except Exception:
            print("yoga-doctor: scratchpad_launch_failed", file=sys.stderr)
            return 1
        print("Scratchpad launched.")
        return 0
    if args.command == "measure-runtime":
        from yoga_deck.diagnostics.resource_campaign import run_resource_campaign

        runner = resource_runner or run_resource_campaign
        try:
            summary = runner(duration_seconds=args.seconds, interval_seconds=1.0)
        except (OSError, ValueError):
            print("yoga-doctor: runtime_measurement_failed", file=sys.stderr)
            return 1
        payload = summary.as_dict()
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            rss = payload["rss_mib"]
            print(
                f"Runtime resources: duration={payload['duration_seconds']}s "
                f"samples={payload['samples']} CPU={payload['cpu_percent_one_core']}%"
            )
            print(
                "RSS (MiB): "
                f"min={rss['min']} median={rss['median']} p95={rss['p95']} max={rss['max']}"
            )
            print(f"Scheduler switches/s: {payload['scheduler_switches_per_second']}")
        return 0
    if args.command == "replay":
        from pathlib import Path

        from yoga_deck.diagnostics.journal import JournalError, loads_jsonl
        from yoga_deck.diagnostics.replay import replay

        try:
            records = loads_jsonl(Path(args.fixture).read_text())
            trace = replay(records)
        except JournalError as error:
            print(f"yoga-doctor: {error.code}", file=sys.stderr)
            return 1
        except OSError:
            print("yoga-doctor: replay_failed", file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(trace.as_dict(), indent=2, sort_keys=True))
        else:
            print(
                f"Replayed {len(trace.steps)} events; final sequence {trace.final_state.sequence}."
            )
        return 0
    if args.command == "record":
        if event_source is None:
            from yoga_deck.adapters.tablet_switch import TabletSwitchSource

            event_source = TabletSwitchSource(max_events=args.events)
        from pathlib import Path

        from yoga_deck.diagnostics.journal import JournalError, dumps_jsonl

        try:
            records = event_source.record(args.scenario)
            payload = dumps_jsonl(records)
            Path(args.output).write_text(payload)
        except JournalError as error:
            print(f"yoga-doctor: {error.code}", file=sys.stderr)
            return 1
        except OSError:
            print("yoga-doctor: record_failed", file=sys.stderr)
            return 1
        print(f"Recorded {len(records)} normalized events.")
        return 0
    if args.command == "recover-inputs":
        if not _recover_inputs(runtime_requester, input_recovery):
            print("yoga-doctor: input_recovery_incomplete", file=sys.stderr)
            return 1
        print("Internal inputs enabled.")
        return 0
    if args.command in {"setup", "uninstall"}:
        return _run_install(
            args,
            install_environment,
            install_runner,
            install_observer,
            lambda: _recover_inputs(runtime_requester, input_recovery),
        )
    if args.command == "test-orientation":
        import asyncio

        from yoga_deck.diagnostics.coordinate_oracle import run_coordinate_oracle
        from yoga_deck.diagnostics.orientation_campaign import (
            format_campaign_markdown_report,
            run_guided_physical_campaign,
        )

        if not args.live:
            print("yoga-doctor: live_desktop_opt_in_required", file=sys.stderr)
            return 2

        def _prompt(message: str) -> None:
            print(f"👉 {message}", flush=True)

        def _flicker(orientation: Any) -> bool:
            answer = input(f"Was visible flicker observed entering {orientation.value}? [y/N] ")
            return answer.strip().lower() in {"y", "yes"}

        runner = orientation_runner or run_guided_physical_campaign
        try:
            summary = asyncio.run(
                runner(
                    step_timeout_sec=args.timeout,
                    prompt_callback=_prompt,
                    surface_callback=lambda orientation, targets: run_coordinate_oracle(targets),
                    flicker_callback=_flicker,
                )
            )
        except KeyboardInterrupt:
            print("yoga-doctor: orientation_test_cancelled", file=sys.stderr)
            return 130
        except Exception:
            print("yoga-doctor: orientation_test_failed", file=sys.stderr)
            return 1

        if args.json:
            print(json.dumps(summary.as_dict(), indent=2, sort_keys=True))
        else:
            print(format_campaign_markdown_report(summary))
        return 0
    if args.command == "test-palm-rejection":
        from yoga_deck.diagnostics.palm_rejection import run_guided_palm_campaign

        if not args.live:
            print("yoga-doctor: live_desktop_opt_in_required", file=sys.stderr)
            return 2
        runner = palm_runner or run_guided_palm_campaign
        try:
            summary = runner(
                trials=args.trials,
                observation_seconds=args.seconds,
                recovery_seconds=args.recovery_seconds,
                timeout=max(args.seconds, args.recovery_seconds) + 5,
                scenarios=tuple(args.scenario) if args.scenario else None,
            )
        except KeyboardInterrupt:
            print("yoga-doctor: palm_test_cancelled", file=sys.stderr)
            return 130
        except Exception:
            print("yoga-doctor: palm_test_failed", file=sys.stderr)
            return 1
        payload = summary.as_dict()
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            for scenario, result in payload["scenarios"].items():
                print(
                    f"{scenario}: attempted={result['attempted']} "
                    f"delivered={result['delivered']} suppressed={result['suppressed']}"
                )
            latency = payload["recovery_latency_ms"]
            print(
                "recovery latency (ms): "
                f"count={latency['count']} median={latency['median']} max={latency['max']}"
            )
        return 0
    parser.print_help()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
