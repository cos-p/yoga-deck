# Optional far-button mapping

Yoga Deck can observe only the X390 Yoga pen's measured side button farthest from the tip. The
source accepts a complete `BTN_STYLUS` cycle, suppresses any later completion within 50 ms, and
assigns monotonically increasing source sequence values before it reaches the pure reducer.
The sequence allocator belongs to the continuous source, so reopening the device after resume,
hotplug, or disconnect does not restart numbering. Each new session resets its held-button and
debounce state; an unmatched release after reconnect cannot trigger an action.

It is safely off by default. A missing mapping file, an omitted setting, or an invalid setting
never emits a semantic action. The near-tip button has an experimental, unbound
application-delivery test for its observed eraser tool mode; the opposite-end eraser and any
pen-silo signal remain unsupported and cannot be configured.

## Explicit opt-in

Only after choosing a workflow, copy
[`config/pen-actions.toml.example`](../config/pen-actions.toml.example) to
`~/.config/yoga-deck/pen-actions.toml` and uncomment exactly one value:

```toml
far_button_action = "capture_text"
# or
far_button_action = "open_scratchpad"
```

The same choice can be made as `[pen] far_button_action` in `~/.config/yoga-deck/config.toml`,
which wins where both files name it. Read through that loader a malformed legacy file degrades to
the unbound default rather than raising; see [`settings.md`](settings.md).

`capture_text` opens Omarchy's interactive OCR region picker and can change the clipboard through
that existing workflow. `open_scratchpad` launches the optional Xournal++ scratchpad. Neither
choice is installed, enabled, or selected by Yoga Deck; the user service is also still uninstalled
by default.

The source reads pen events without injecting input or changing compositor configuration. It
discovers the internal Wacom pen by stable identity and capabilities rather than an `eventN` path.
