# Fcitx UI restoration analysis — 2026-08-29

## Scope

This follows the X390 Yoga observation recorded in `docs/on-screen-keyboard.md`: after Yoga Deck
releases `org.fcitx.Fcitx5.VirtualKeyboard` on unfold, Fcitx 5.1.21 can report an empty `CurrentUI`
instead of `classicui`. Input delivery continues and a later tablet entry still works. No live
desktop call was made for this analysis; it uses the already-recorded observation, the installed
5.1.21 addon metadata/headers, and the matching upstream source tag.

## State transition

1. In laptop mode, Fcitx is in `PhysicalKeyboard` mode and `classicui` is eligible.
2. `ShowVirtualKeyboard` calls `setInputMethodMode(OnScreenKeyboard)` and makes the DBus virtual
   keyboard UI eligible.
3. When Yoga Deck releases the UI bus name, Fcitx's service watcher calls `setAvailable(false)`,
   clears virtual-keyboard visibility, and asks `UserInterfaceManager` to recalculate availability.
4. The input-method mode is still `OnScreenKeyboard`. The manager accepts only an
   `OnScreenKeyboard` UI in that mode, so unavailable `virtualkeyboard` is rejected and physical
   `classicui` is also rejected. `CurrentUI` consequently becomes the empty string.

This follows directly from the 5.1.21 implementation:

- [`virtualkeyboard.cpp`](https://github.com/fcitx/fcitx5/blob/5.1.21/src/ui/virtualkeyboard/virtualkeyboard.cpp)
  sets availability from bus-owner presence and calls `updateAvailability`; owner loss does not set
  physical input mode.
- [`userinterfacemanager.cpp`](https://github.com/fcitx/fcitx5/blob/5.1.21/src/lib/fcitx/userinterfacemanager.cpp)
  selects an OSK UI only in `OnScreenKeyboard` mode and a physical UI only in `PhysicalKeyboard`
  mode.
- [`dbusmodule.cpp`](https://github.com/fcitx/fcitx5/blob/5.1.21/src/modules/dbus/dbusmodule.cpp)
  exposes `CurrentUI` as a getter; it exposes no public method to select a UI or input-method mode.

## Rejected workarounds

- Calling `HideVirtualKeyboard` before releasing the name is insufficient. In 5.1.21 it changes
  input-method mode back to physical only when virtual-keyboard auto-show is disabled; Yoga Deck
  relies on auto-show for editable-field behavior.
- Restarting Fcitx or Omarchy xcompose is disruptive and discards process-owned input-method state.
- Injecting a physical key merely to trigger Fcitx's physical-key path violates Yoga Deck's
  synthetic-input safety invariant.
- Keeping the virtual-keyboard owner alive in laptop mode would leave an OSK UI eligible outside
  the posture that authorizes it.
- Mutating Fcitx configuration around every fold would be slower, failure-prone, and would change
  the user's input-method policy rather than restore lifecycle state.

## Safe conclusion

There is no supported, non-synthetic Yoga Deck call that restores `classicui` on this Fcitx
version. The current fallback is therefore correct: release the owned name and child, recover
internal inputs, tolerate an empty `CurrentUI`, and allow the next fold to register normally.

The bounded follow-up is an upstream report proposing that loss of the active OSK UI transition
Fcitx to `PhysicalKeyboard` before availability is recalculated, or that Fcitx expose an explicit
supported lifecycle method. Upstream must choose the policy location; Yoga Deck should not carry a
private D-Bus convention or synthetic event as a substitute.
