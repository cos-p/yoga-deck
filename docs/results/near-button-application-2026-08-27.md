# Near-button application observation — 2026-08-27

## Scope

This exploratory session investigated a new observation: actions performed with the side button
nearest the pen tip produced `BTN_TOOL_RUBBER` lifecycles, rather than `BTN_STYLUS2`. A temporary
Quickshell surface was added to determine whether that raw tool mode can reach Qt applications as
`PointerDevice.Eraser`. No events were injected and no desktop configuration was changed.

## Result

One synchronized near-button slot contained both one complete raw eraser lifecycle and one Qt
application eraser observation. This demonstrates an application delivery path for the observed
eraser mode. Earlier hover-only and incompletely synchronized windows are not reliability trials.

The final two-phase run did not observe its normal-pen control at either boundary, so the overall
campaign is incomplete. It does not yet establish a controlled reliability denominator or justify
an action binding. The hardware test requires a valid raw/application normal-pen control plus 3/3
correlated near-button trials before that stronger claim can be made.

## Consequences

- The near-tip switch should no longer be described as electrically non-responsive.
- Its candidate behavior is a tool-mode change to eraser, not a second barrel-button click.
- It remains unbound until the controlled hardware campaign passes.
- The opposite end of the pen and chassis-silo events remain unverified.
