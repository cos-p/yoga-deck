# Optional scratchpad contract

Yoga Deck's initial scratchpad adapter launches the already-installed Xournal++ application as
`xournalpp`, using a fixed argument vector with no shell, document path, or personal content.
It does not create, open, save, inspect, or retain a document.
On this host, the child process receives `GDK_BACKEND=wayland`: the unqualified launch did not
produce a visible window, while this temporary child-only setting did. It does not modify the
user's persistent environment or desktop configuration. The child also starts a new session so a
short-lived diagnostic CLI does not own its lifetime.

The pure action path is:

```text
PenActionRequested(OPEN_SCRATCHPAD, source_sequence)
  -> LaunchScratchpad(reducer_sequence)
  -> ScratchpadCoordinator
  -> ScratchpadAdapter
  -> xournalpp
```

As with OCR, the semantic action's source sequence must be monotonic so replayed or reordered
input cannot relaunch the application. No physical pen binding is installed or enabled. The
measured far-from-tip button remains unassigned pending a stronger button and OCR reliability
campaign.

Use `yoga-doctor test-scratchpad --live` only when launching a desktop application is intended.
It keeps its diagnostic parent alive for ten seconds by default so the graphical child can become
visible; this is a validation aid, not a persistent desktop setting. It does not alter Omarchy or
Hyprland configuration.

On 2026-08-26, the 15-second visible-validation launch opened Xournal++ and accepted pen input.
The command is intentionally unbound: a future persistent runtime binding must be preceded by the
separate far-button reliability campaign.
