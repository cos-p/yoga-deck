# Omarchy OCR prepared-target reliability measurement — 2026-08-27

## Scope

This guided live campaign measured `omarchy capture text` on the X390 Yoga with a prepared,
non-sensitive rendered-text target. Each of five trials used the native region picker: the user
dragged a region around rendered text without native text selection, then independently verified
the result without supplying Yoga Deck any recognized text.

The timing starts immediately before launching the command and ends when it returns. It therefore
includes user region selection, the native picker, screenshot capture, OCR, and clipboard write;
it is not an isolated OCR-engine benchmark.

No screenshot, selection geometry, source image, recognized text, clipboard data, source URL, or
command output was retained.

## Results

| Measure | Observation |
|---|---|
| Requested trials | 5 |
| User-verified matches | 5/5 |
| Explicitly unverified trials | 0/5 |
| Command failures | 0/5 |
| End-to-end latency median | 5,536.741 ms |
| End-to-end latency maximum | 9,102.591 ms |

Every trial used the same prepared rendered-text workflow and explicit region-picker guidance.
There were no exclusions from this campaign. The earlier 2026-08-26 exploratory results remain a
separate record: their terminal-selection and picker-attribution ambiguities were not used here.

## Product consequences

- `omarchy capture text` is a viable initial OCR backend for the measured prepared-target flow.
- This five-trial result is not a claim of general OCR accuracy, nor a reason to retain source or
  recognized text. Broader target, language, accessibility, and failure-mode coverage remains
  unmeasured.
- No pen button is bound. A separately publishable far-button reliability campaign must complete
  before considering a debounced semantic action source, and any user-visible mapping still needs
  explicit authorization.
