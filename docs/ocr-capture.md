# OCR capture contract

Yoga Deck uses `omarchy capture text` as its initial OCR backend. The command opens an
interactive region picker and places the recognized result in the user's clipboard. Yoga Deck
never captures the selected image, reads the recognized result, or includes either in logs,
reports, fixtures, or diagnostics.

## Action boundary

The only supported initial request is:

```text
PenActionRequested(CAPTURE_TEXT, source_sequence)
  -> CaptureText(reducer_sequence)
  -> OcrCoordinator
  -> AsyncOmarchyOcrAdapter (runtime)
  -> omarchy capture text
```

`source_sequence` is monotonically increasing in the pen adapter. A reducer state retains the
last accepted source sequence across session reconnects, so duplicate or reordered input cannot repeat an OCR request.
The adapter must apply the measured 50 ms far-button debounce before creating this event; the
reducer does not attempt to infer physical button timing. The X390 Yoga's near-tip button and
eraser remain unbound.

The OCR adapter invokes the stable Omarchy command with a 120-second user-interaction limit,
discards standard output and error, and converts every command failure to the stable
`ocr_capture_failed` code. It never reads the clipboard. The runtime uses an asyncio subprocess
in an owned process group; the synchronous adapter remains available for guided diagnostics.

Accepted capture effects become tracked runtime tasks and are serialized by the OCR coordinator,
which maintains its own sequence watermark. The reducer and compositor locks are released while
the picker is open, so posture changes, orientation, reconciliation, and shell recovery proceed.
A failed capture consumes its sequence and cannot be replayed by a duplicate event. Later fresh
requests still run. Shutdown first recovers inputs, then cancels active and queued captures;
timeout and cancellation kill the owned process group and reap the command, including when
cancellation arrives during process creation.

No physical pen event source or user-visible binding is installed by this contract. A later phase
may connect the measured far-from-tip `BTN_STYLUS` cycle only after both OCR and button-source
reliability measurements show acceptable results and the user explicitly authorizes a mapping.

## Guided measurement

`yoga-doctor test-ocr-capture --live --trials 5` is deliberately opt-in. Each trial asks the user
to select a prepared, non-sensitive sample in the native picker and then answer whether the result
matched. The emitted JSON and summary contain only trial counts, user verification counts, command
failure counts, and end-to-end timing. They contain no OCR text, selection geometry, images,
paths, command output, or clipboard contents.

The command can change the clipboard as part of the existing Omarchy workflow. Run it only when
that is acceptable, and restore any needed clipboard data beforehand.

The exploratory campaign is recorded in
[`results/ocr-capture-2026-08-26.md`](results/ocr-capture-2026-08-26.md). A follow-up prepared-
target campaign produced five user-verified results from five trials with no command failures; see
[`results/ocr-reliability-2026-08-27.md`](results/ocr-reliability-2026-08-27.md). That narrow
result does not itself authorize a pen binding: button-source reliability and explicit user
authorization remain separate requirements.
