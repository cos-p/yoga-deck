# Omarchy OCR capture measurement — 2026-08-26

## Scope

This guided live campaign measured `omarchy capture text` on the X390 Yoga using the existing
Omarchy region-picker workflow. It retained no screenshots, selected regions, recognized text,
clipboard data, source URLs, or command output. The user selected only non-sensitive visible text
and independently judged whether the result matched.

The timing starts immediately before launching the command and ends when it returns. It therefore
includes user region selection, the native picker, screenshot capture, OCR, and clipboard write;
it is not an isolated OCR-engine benchmark.

## Results

| Measure | Observation |
|---|---|
| Requested trials | 5 |
| User-verified matches | 2/5 |
| Explicitly unverified trials | 3/5 |
| Command failures | 0/5 |
| End-to-end latency median | 9,397.670 ms |
| End-to-end latency maximum | 22,305.374 ms |

The first trial used terminal text, which could be copied by terminal selection independently of
OCR, and was correctly recorded as unverified. Two further trials were not visibly attributable to
the region-picker workflow and were also recorded as unverified rather than treated as failures or
successes. The final two browser-region trials were user-verified.

## Product consequences

- `omarchy capture text` is usable as the first OCR backend: it completed every invocation and
  produced two independently verified results.
- Current evidence is insufficient to advertise a reliability rate or bind the far pen button to
  OCR. The three unverified trials must remain distinct from both successes and command failures.
- A future reliability campaign should use a prepared rendered-text target, clear visual picker
  instructions, and a non-confounded verification method before any live pen binding.
- Yoga Deck must continue to treat all source and recognized text as private and retain only the
  aggregate measurement above.
