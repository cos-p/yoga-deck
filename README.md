# Yoga Deck

Yoga Deck makes a convertible laptop behave like a tablet when you fold it and like a laptop when
you open it, on Linux with Hyprland. Fold the screen back and the internal keyboard, touchpad, and
TrackPoint stop responding, the display, touchscreen, and pen rotate together with the device, and
an on-screen keyboard appears when a text field has focus. Open it again and everything is back.

Its first and only validated target is the **Lenovo ThinkPad X390 Yoga** running
[Omarchy](https://omarchy.org) (Arch Linux, Hyprland, and the Quickshell-based Omarchy Shell). The
code is adapter-based so other convertibles can be added, but nothing else is supported yet.

**Status: `0.1.0`, public alpha.** It runs daily on the target machine. It has not yet been run on
anyone else's. If you have an X390 Yoga with Omarchy, [testing it](#help-test-it) is the most
useful thing you can do.

## What it does

- **Posture safety.** The ThinkPad tablet switch (`SW_TABLET_MODE`) decides laptop versus folded.
  Folded disables only the internal keyboard, touchpad, and TrackPoint. External keyboards and
  mice are never touched, and laptop mode always ends with internal inputs enabled.
- **Coordinated rotation.** The accelerometer (via `iio-sensor-proxy`) rotates the internal
  display, touchscreen, and pen in one step, keeping your fractional scale and leaving external
  monitors alone. Orientation lock and manual rotation are in the panel.
- **On-screen keyboard.** In folded mode, [wvkbd](https://github.com/jjsullivan5196/wvkbd) shows
  and hides with text focus through Omarchy's existing Fcitx, themed from the active Omarchy
  theme. See [`docs/on-screen-keyboard.md`](docs/on-screen-keyboard.md).
- **Recovery.** Startup, shutdown, suspend/resume, device reconnect, and monitor events all
  re-check real state. `yoga-doctor recover-inputs` re-enables the internal inputs at any time.
- **Omarchy Shell panel.** A bar widget shows posture and rotation, with controls for orientation
  lock, rotation, keyboard settings, and recovery.
- **Pen extras, off by default.** The far-from-tip pen button can be mapped to OCR capture
  (`omarchy capture text`) or an optional Xournal++ scratchpad. See
  [`docs/pen-action-mapping.md`](docs/pen-action-mapping.md).
- **`yoga-doctor`.** A diagnostic CLI for inspection, redacted support reports, event recording
  and offline replay, and guided hardware tests.

## Requirements

- ThinkPad X390 Yoga with Omarchy 4 (Hyprland 0.56 era) and the Omarchy Shell.
- Python 3.12 or later, and `uv` (or `pipx`).
- `base-devel`: the `evdev` dependency is built from source during installation.
- `iio-sensor-proxy` for automatic rotation. Posture safety works without it.
- Optional: `wvkbd` from the AUR for the folded-mode on-screen keyboard.
- Your user in the **`input` group**, so the service can read the tablet switch and pen. This
  lets your user read every input device, including keyboards. Decide whether you accept that.

## Install

```bash
sudo pacman -S --needed base-devel iio-sensor-proxy uv
yay -S wvkbd                                  # optional: on-screen keyboard

sudo usermod -aG input "$USER"
```

**Log out completely, wait about 15 seconds, then log back in** (or reboot). A quick
logout/login can leave your systemd user manager running with the old groups, and the service
then stays in laptop mode with `tablet_switch: permission_denied`.

```bash
uv tool install git+https://github.com/cos-p/yoga-deck@v0.1.0
yoga-doctor setup --dry-run                   # reports prerequisites; changes nothing
yoga-doctor setup --enable --enable-plugin    # installs and starts the service, adds the widget
yoga-doctor inspect                           # tablet switch, inputs, Wacom, eDP-1 should be "available"
```

`setup` without flags installs the user service and plugin but enables neither. Everything is per
user: nothing goes into `/usr`, and `/usr/share/omarchy` is never modified. Prerequisites,
verification, the optional live theme hook, and upgrades are covered in
[`docs/install.md`](docs/install.md).

**If the internal keyboard or touchpad stays disabled in laptop mode**, run this from an external
keyboard, SSH, or the on-screen keyboard (the panel also has a recovery button):

```bash
yoga-doctor recover-inputs
```

**Uninstall:**

```bash
yoga-doctor uninstall          # re-enables inputs, removes service, plugin, hook; --purge drops settings
uv tool uninstall yoga-deck
```

## Known limitations

- **Hyprland restarting inside a running session is not live-tested.** Logging out and back in is
  handled: the service stops with the session and starts with the next one. If Hyprland itself
  crashes and is relaunched in the same session, internal inputs come back enabled (inhibition is
  never written to config), but folded-mode inhibition and rotation may not be reapplied. Fix it
  with `systemctl --user restart yoga-deck`. See
  [`docs/hyprland-reconnect.md`](docs/hyprland-reconnect.md).
- **A key on the on-screen keyboard can occasionally keep repeating.** If the keyboard hides while
  a finger is still on a key (for example Enter in a search box, which moves focus away), wvkbd
  0.20 never sends the release and Hyprland does not send one by default. It was seen once and has
  not been reproduced on demand. Pressing any key, or moving focus, stops it. Hyprland's
  `input:virtualkeyboard:release_pressed_on_close` option should prevent it but has not been
  tested yet. See [`docs/on-screen-keyboard.md`](docs/on-screen-keyboard.md).
- **External monitors and docks have not been physically tested.** Rotation only targets the
  internal display and should leave others alone, but that is covered by tests, not hardware.
- **Folded is binary.** Tent and stand are not detected separately; the hinge sensor readings are
  preliminary.
- **No pen-silo detection.** The X390 has not shown a distinct event for pen insertion or removal,
  so none is claimed. Pen proximity is a different signal.
- **The near-tip pen button and the pen's opposite end have no actions.** Only the far-from-tip
  button is mappable, and only if you turn it on.
- **On-screen keyboard coverage is partial.** Folded touch typing and recovery passed live; pen
  typing and a broad app matrix (GTK, Qt, Chromium/Electron, XWayland) have not been run.
- **One machine.** Every live result so far comes from a single X390 Yoga.

The full release boundary is in [`docs/release-scope.md`](docs/release-scope.md).

## Release notes

### 0.1.0 (public alpha)

First public release.

- Folded/laptop posture from the ThinkPad tablet switch, inhibiting only the internal keyboard,
  touchpad, and TrackPoint.
- Accelerometer rotation of display, touchscreen, and pen together, preserving fractional scale
  and external monitor layout; orientation lock and manual rotation.
- Folded-mode on-screen keyboard (wvkbd through Fcitx), themed from the active Omarchy theme and
  configurable from the panel or `~/.config/yoga-deck/config.toml`.
- Re-checks state at startup, shutdown, suspend/resume, device reconnect, and monitor
  add/remove; follows a changed Hyprland instance.
- Clean shutdown on `SIGTERM`: internal inputs are re-enabled in-process, with
  `recover-inputs` as a systemd backup.
- Missing `input` group access is reported clearly in `yoga-doctor setup`, `yoga-doctor inspect`
  (`permission_denied`), the service log (once), and the panel, instead of failing silently.
- Omarchy Shell panel plugin `cos.yoga-deck`.
- `yoga-doctor setup` and `uninstall` for per-user installation and removal.
- Opt-in far-from-tip pen button actions: OCR capture and Xournal++ scratchpad.
- `yoga-doctor` diagnostics: `inspect`, `report --redact`, `record`, `replay`, guided hardware
  tests, `recover-inputs`.

Measured evidence for the claims above is in [`docs/results/`](docs/results/). Limitations are
listed [above](#known-limitations).

## Help test it

If you have an X390 Yoga on Omarchy, please run through this after installing, and
[open an issue](https://github.com/cos-p/yoga-deck/issues) with what you saw. Attach the output
of `yoga-doctor report --redact`, which leaves out serials, usernames, paths, and network data,
plus your Omarchy and Hyprland versions (`hyprctl version`).

**Basics**

- [ ] `yoga-doctor inspect` lists the tablet switch, internal keyboard, touchpad, TrackPoint, Wacom
      touch and pen, and `eDP-1`, all `available`.
- [ ] Fold to tablet: internal keyboard, touchpad, and TrackPoint stop responding. Unfold: they work
      again. Do this at least 10 times, some of them quickly.
- [ ] An external USB or Bluetooth keyboard and mouse keep working while folded.
- [ ] The panel widget shows the correct posture and rotation, and its controls work.

**Rotation** (folded)

- [ ] The screen follows the device in all four orientations, in the right direction.
- [ ] Touch and pen land under your finger or pen tip in all four corners in every orientation.
      `yoga-doctor test-orientation --live` guides you through this.
- [ ] Orientation lock stops rotation but folding still disables the keyboard.
- [ ] Your display scale is unchanged after rotating back.

**On-screen keyboard** (with wvkbd installed)

- [ ] It appears when you tap a text field while folded and hides when focus leaves.
- [ ] Typing works in a terminal, a browser, and one GTK or Qt app.
- [ ] No key stays pressed. If one does, note what you were doing.

**Robustness**

- [ ] Suspend while folded, resume, then unfold: the inputs come back.
- [ ] Suspend in laptop mode, resume: nothing is disabled.
- [ ] Palm on the screen while writing with the pen: no stray touches
      (`yoga-doctor test-palm-rejection --live`).
- [ ] If you have an external monitor or dock: plug it in and unplug it, folded and unfolded. Its
      layout and scale must not change.
- [ ] `systemctl --user stop yoga-deck` while folded re-enables the internal inputs.

If anything goes wrong, `yoga-doctor recover-inputs` gets the internal inputs back, and
`journalctl --user -u yoga-deck -n 50` shows what the service saw.

## Development

Requires Python 3.12 or later.

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

The default test run excludes hardware, virtual-input, and live-desktop tests, so it never touches
your desktop. To run the service and plugin from a checkout, use `yoga-doctor setup --dev-link`;
see [`docs/install.md`](docs/install.md#developing-from-a-checkout).

The core is a pure state machine: hardware and synthetic events go through a reducer that returns
new state and planned effects, and only adapters talk to evdev, D-Bus, Hyprland, or the shell. See
[`docs/architecture.md`](docs/architecture.md) for the design and test strategy.

## `yoga-doctor` commands

| Command | Purpose and safety boundary |
|---|---|
| `setup` | Install the user unit and a copy of the packaged Omarchy plugin; enables nothing without `--enable`/`--enable-plugin`; supports `--dry-run`. See [docs/install.md](docs/install.md). |
| `uninstall` | Recover inputs first, then remove the unit, plugin, and hook; keeps settings unless `--purge`; supports `--dry-run`. |
| `inspect` | Read normalized hardware and compositor support data; never mutates the system. |
| `report` | Combine normalized discovery/runtime status; requires `--redact`. See [docs/support-report.md](docs/support-report.md). |
| `recover-inputs` | Explicitly re-enable every discovered owned internal input. |
| `config` | Read and change user settings; `get`, `set`, `unset`, `path`. See [docs/settings.md](docs/settings.md). |
| `replay` | Replay sanitized JSONL through the reducer as an offline dry run. |
| `record` | Observe tablet-switch events and write a normalized JSONL fixture. |
| `test-orientation` | Run guided display/touch/pen alignment; requires `--live`. |
| `test-palm-rejection` | Run the guided application-delivery campaign; requires `--live`. |
| `test-osk-theme` | Retint the on-screen keyboard mid-session and check focus survives; requires `--live`. |
| `test-pen-actions` | Observe guided physical pen input; requires `--hardware`. |
| `test-near-button` | Compare raw and application eraser delivery; requires `--live`. |
| `test-ocr-capture` | Open the OCR picker and change the clipboard; requires `--live`. |
| `test-scratchpad` | Launch the optional scratchpad application; requires `--live`. |
| `measure-runtime` | Read aggregate runtime CPU, memory, and scheduler activity. |

Use `yoga-doctor <command> --help` for options. Hardware and live-desktop commands refuse to run
without their explicit opt-in flag.

## Documentation

- [`docs/install.md`](docs/install.md): installation, upgrade, uninstall
- [`docs/release-scope.md`](docs/release-scope.md): what 0.1.0 claims and what it does not
- [`docs/architecture.md`](docs/architecture.md): design and test strategy
- [`docs/settings.md`](docs/settings.md): `config.toml` and the panel settings
- [`docs/on-screen-keyboard.md`](docs/on-screen-keyboard.md) and
  [`docs/osk-theming.md`](docs/osk-theming.md): wvkbd setup and theming
- [`docs/rotation.md`](docs/rotation.md), [`docs/input-inhibition.md`](docs/input-inhibition.md),
  [`docs/suspend-resume.md`](docs/suspend-resume.md),
  [`docs/hyprland-reconnect.md`](docs/hyprland-reconnect.md),
  [`docs/monitor-hotplug.md`](docs/monitor-hotplug.md): runtime contracts
- [`docs/host-baseline.md`](docs/host-baseline.md): the measured target machine
- [`docs/results/`](docs/results/): dated measurement reports

## License

MIT. See [LICENSE](LICENSE).
