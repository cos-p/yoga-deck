# Installing Yoga Deck 0.1.0

This is the supported path for installing the `0.1.0` public alpha on the validated target: a
ThinkPad X390 Yoga running Omarchy with Hyprland and the Omarchy Shell (see
[`release-scope.md`](release-scope.md)). Everything is installed per user. Nothing is written to
`/usr`, `/usr/share/omarchy/` is never touched, and no system package is installed for you.

These are installed:

| Artifact | Location | Installed by |
|---|---|---|
| `yoga-doctor` and `yoga-deck-runtime` | `~/.local/bin/` (tool venv managed by uv or pipx) | `uv tool install` |
| User service `yoga-deck.service` | `~/.config/systemd/user/` | `yoga-doctor setup` |
| Omarchy Shell plugin `cos.yoga-deck` | `~/.config/omarchy/plugins/cos.yoga-deck/` | `yoga-doctor setup` |
| Optional theme hook | `~/.config/omarchy/hooks/theme-set.d/yoga-deck` | `yoga-doctor setup --with-theme-hook` |

The plugin, unit, and hook ship inside the Python package, so `setup` always installs the plugin
that matches the runtime you just installed. Your settings live in `~/.config/yoga-deck/` and are
never created or removed by setup; `uninstall` keeps them unless you pass `--purge`.

## 1. System prerequisites

Yoga Deck does not install packages. Check and install these yourself:

```bash
sudo pacman -S --needed base-devel iio-sensor-proxy uv
```

- `base-devel` (specifically `gcc`): the `evdev` dependency is published on PyPI only as source
  and is compiled during installation. Its Linux input headers come from `linux-api-headers`,
  which `glibc` already pulls in. Without a C compiler the install fails while building `evdev`.
- `iio-sensor-proxy`: needed for automatic rotation. Posture safety works without it.
- `uv`: the recommended installer. `pipx` (`sudo pacman -S python-pipx`) also works.
- Optional: `wvkbd` from the AUR (`yay -S wvkbd`) for the folded-mode on-screen keyboard. See
  [`on-screen-keyboard.md`](on-screen-keyboard.md).

The runtime must be able to read the tablet switch and pen event devices under `/dev/input/`,
which belong to the `input` group:

```bash
sudo usermod -aG input "$USER"   # then log out and back in
```

Membership in `input` lets your user read every input device, including keyboards. That is the
access the posture runtime needs; decide whether you accept it on your machine.
`yoga-doctor setup` reports whether this access is present, and `yoga-doctor inspect` shows
`tablet_switch: permission_denied` when it is not. The group applies only to sessions started
after the change, including the systemd user manager that runs the service, so log out fully
rather than restarting the service. Until then the runtime stays in laptop mode, logs
`device_permission_denied` once, and the panel says it has no input access.

## 2. Install the Python tools

Install from the release tag (Python 3.12 or later; uv picks or downloads a suitable one):

```bash
uv tool install git+https://github.com/cos-p/yoga-deck@v0.1.0
```

or, with pipx:

```bash
pipx install git+https://github.com/cos-p/yoga-deck@v0.1.0
```

Both place `yoga-doctor` and `yoga-deck-runtime` in `~/.local/bin/`, which is where the user unit
expects them. Make sure `~/.local/bin` is on your `PATH` (`uv tool update-shell` adds it).

## 3. Run setup

Preview first; a dry run prints the prerequisite report and the planned actions and changes
nothing:

```bash
yoga-doctor setup --dry-run
```

Then install the unit and plugin:

```bash
yoga-doctor setup
```

Setup:

1. reports missing prerequisites (`systemctl`, `omarchy`, `hyprctl`, `python3`, and the two
   binaries in `~/.local/bin` are required and block setup; input access, iio-sensor-proxy and
   wvkbd are reported as warnings);
2. copies the packaged `yoga-deck.service` to `~/.config/systemd/user/` and runs
   `systemctl --user daemon-reload` when it changed;
3. copies the packaged plugin into `~/.config/omarchy/plugins/cos.yoga-deck/` and runs
   `omarchy plugin validate` on it;
4. prints the next steps.

It does **not** enable or start the service and does **not** add the plugin to the bar unless
asked. Optional flags:

| Flag | Effect |
|---|---|
| `--enable` | `systemctl --user enable yoga-deck.service`, then `restart` (starts it, or reloads an upgraded runtime) |
| `--enable-plugin` | `omarchy plugin enable cos.yoga-deck` (needs a running Omarchy Shell) |
| `--with-theme-hook` | `omarchy hook install theme-set …` so the on-screen keyboard follows theme changes live |
| `--dev-link` | Development only: symlink the source checkout's `omarchy-plugin/` instead of copying |
| `--dry-run` | Print the plan without changing anything |

Setup is idempotent: rerunning it rewrites nothing that is already current. It refuses to replace
a `cos.yoga-deck` plugin directory whose manifest has a different id.

## 4. Verify before enabling

```bash
yoga-doctor inspect
yoga-doctor report --redact
```

`inspect` should list the tablet switch, the internal keyboard, touchpad, and TrackPoint, the
Wacom touch and pen devices, and `eDP-1` as internal. `report --redact` reports the runtime as
unavailable until the service runs. Both are read-only and safe to share; see
[`support-report.md`](support-report.md).

## 5. Enable

```bash
systemctl --user enable --now yoga-deck.service
omarchy plugin enable cos.yoga-deck
```

(Equivalent: `yoga-doctor setup --enable --enable-plugin`.) Then check:

```bash
systemctl --user status yoga-deck.service
journalctl --user -u yoga-deck.service -n 50
yoga-doctor report --redact
```

Fold the machine into tablet mode: the internal keyboard, touchpad, and TrackPoint stop
responding and the screen rotates with the device. Unfold: they respond again. External keyboards
and mice are never disabled.

## Emergency input recovery

If the internal keyboard or touchpad stays disabled in laptop mode, from an external keyboard,
SSH, or the on-screen keyboard run:

```bash
yoga-doctor recover-inputs
```

It asks the running service to re-enable the internal inputs and falls back to re-enabling them
through Hyprland directly. Stopping the service (`systemctl --user stop yoga-deck.service`) also
runs recovery, and the panel has a recovery button.

## Upgrade

```bash
uv tool install --force git+https://github.com/cos-p/yoga-deck@v0.1.1   # the new tag
yoga-doctor setup --enable        # refreshes unit and plugin copy, restarts the service
```

With pipx use `pipx install --force …`. Rerun `setup` after every upgrade so the plugin copy
matches the runtime; add `--with-theme-hook` again if you use the hook.

## Uninstall

```bash
yoga-doctor uninstall --dry-run   # preview
yoga-doctor uninstall             # add --purge to also delete ~/.config/yoga-deck/
uv tool uninstall yoga-deck       # or: pipx uninstall yoga-deck
```

`uninstall` first re-enables the internal inputs, then disables the bar widget, stops and
disables the service, removes the unit and reloads systemd, and removes the plugin and theme hook.
It removes the plugin directory only when its manifest id is `cos.yoga-deck` and the hook only when
it is the Yoga Deck hook; anything else is left in place and reported. Remove the Python tool last,
because `uninstall` is part of it. If you added yourself to the `input` group only for Yoga Deck,
`sudo gpasswd -d "$USER" input` reverts that.

## Developing from a checkout

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/yoga-doctor setup --dev-link --dry-run
```

`--dev-link` makes the installed plugin follow the checkout. `omarchy plugin validate` refuses
symlinks, so setup validates the checkout directory itself. The unit still runs the binaries in
`~/.local/bin`; point them at the checkout yourself (for example
`uv tool install --editable .`) when you want the service to run development code.
