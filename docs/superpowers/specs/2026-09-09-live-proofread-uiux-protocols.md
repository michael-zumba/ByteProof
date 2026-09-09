# UI/UX Polish Protocols — Live Suggestions

Approved by the owner on 2026-09-09. Implemented one protocol group per
build cycle so each step can be verified live before the next.

## Implementation order (owner-approved)

1. P2.1 Loading pill + P2.2 Undo after apply
2. P1 Onboarding & trust (readiness card + permission guidance)
3. P3 Option B: shared design tokens + diff-view reuse in the main window
4. P2.4 Reason color dots, then P4.1 status chip, P4.3 quit flow
5. Backlog: P2.3 suggestion dismiss, P2.5 panel position memory, P4.4
   per-app filter

---

## Protocol 1 — Onboarding & Trust

Goal: eliminate silent failure when Accessibility permission is off,
revoked by a reinstall, or flaky.

- Readiness card in the main window: `Live: on · Accessibility · provider`.
- "Open System Settings" deep link when permission is missing.
- One-line re-grant hint after app updates.
- "Test now" button probing the frontmost app (reuses diagnostics).

## Protocol 2 — Suggestion panel: expectation, undo, dismiss

- P2.1 Loading pill: a small "Checking…" pill at the selection the moment
  a provider call starts; hidden when the panel appears or the call ends.
- P2.2 Undo: after a successful apply, an "Undo" pill (10 s lifetime)
  restores the previous text at the recorded absolute range.
- P2.3 Dismiss: per-row "don't suggest this again" (session dictionary).
- P2.4 Reason color dots: Spelling=red, Grammar=amber, Word choice=blue.
- P2.5 Panel position memory per display after dragging.

## Protocol 3 — One visual language

- Option B approved: shared design-token module (spacing, radii, focus,
  hover/pressed states) used by the main window and the live panel.
- Reuse the panel's diff renderer (struck red → green replacement with
  dimmed context) in the main window's corrected-text view.

## Protocol 4 — Status transparency & quit friction

- P4.1 Live status chip (tray tooltip + main window footer).
- P4.2 Provider + latency in the panel title tooltip.
- P4.3 Quit flow: close = tray silently, Quit quits instantly, remember
  confirmation choice.
- P4.4 Optional per-app filter in Settings.

## Safety rules

- Every change is test-covered (tests/test_live_preview.py) and shipped as
  a signed, notarized installer on the beta branch.
- No engine/timing/apply-path regressions: the existing 80+ tests plus the
  smoke suite must stay green each round.
