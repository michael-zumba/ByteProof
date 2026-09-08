# Live Proofread Preview (Grammarly-style) — Beta Design

## Goal

Before the user presses the existing Proofread button, ByteProof shows live
suggestions for the currently selected text in supported apps: pink-red dashed
underlines under words the AI would change, a hover popup presenting the edits
in track-changes style, and one-click apply. A General-settings toggle turns
the whole feature off. The feature must be token-efficient.

## Scope

In scope: macOS beta. Microsoft Word, Apple Pages, Apple Mail, Microsoft
Outlook (native), plus TextEdit and Notes as reference apps. Live trigger,
underline rendering, hover popup, click-to-apply, settings toggle, and the
full test campaign.

Out of scope: Windows live preview (different UI Automation stack), changes to
the existing Word tracked-changes proofread and hotkey flow, and automatic
underlining in every possible third-party app.

## Decisions

1. macOS only for the beta.
2. Clicking a suggestion applies that single edit in place; the popup also has
   "Apply all", which runs the existing full-selection proofread pipeline.
3. Previews default to the Local AI model (free, offline, zero cloud tokens);
   a General-settings checkbox opts into the active provider instead.
4. General settings gain: "Live suggestions (beta)" on/off, a delay slider,
   and the preview-provider checkbox.

## Current state

- `generic_editing.GenericTextEditor` reads selected text via AX
  (`kAXSelectedTextAttribute`) with a clipboard fallback, and replaces text by
  Cmd+V after activation.
- `logic.polish_selection_once` runs the non-Word polish path and returns
  `(status, original, corrected, comment, review_start)`.
- `word_integration` drives Word through AppleScript with tracked changes.
- `SettingsDialog` has a sidebar (General, Automation, Connect, Local AI) and a
  General page built around `make_setting_row`.
- The live probe on this Mac confirmed `AXBoundsForRange` returns screen
  rectangles and `AXReplaceRangeWithText` is available for parameterized
  replacement; `AXIsProcessTrusted()` is true.

## Architecture

New modules, one responsibility each:

- `src/live_preview.py` — pure logic: edit model, JSON parsing, mapping edits
  to character ranges, trigger decisions, and the in-memory cache. No Qt, no
  I/O, no network; fully unit-testable.
- `src/live_overlay.py` — the transparent overlay window that paints dashed
  underlines, hit-tests them, and shows the hover popup. Emits callbacks, never
  calls the AI.
- `src/live_service.py` — the orchestrator (QObject): monitors the frontmost
  app and selection, debounces, consults the cache, runs the provider in a
  worker, feeds rectangles to the overlay, and applies edits.
- `src/generic_editing.py` (extended) — AX helpers: selection text + range +
  character bounds, absolute range replacement with fallbacks.
- `src/word_integration.py` (extended) — Word native dashed underlines,
  sub-range apply as tracked changes, underline cleanup, and best-effort
  screen-rect estimation for popup anchoring.
- `src/logic.py` (extended) — `preview_edits_once` and a shared
  `_request_completion` used by both proofread and preview paths.

```
frontmost app + AX selection
        |
        v
live_service (poll 350ms, debounce 900ms, cache)
        |  decision yes
        v
preview_edits_once (Local AI by default)
        |  JSON edits
        v
map edits to char ranges (live_preview)
        |  ranges + absolute selection start
        v
live_overlay (dashed pink-red underlines, hover popup)
        |  click
        v
apply: AXReplaceRangeWithText (apps) / Word AppleScript (Word)
```

## Data model

Provider output is a JSON object:

```json
{"edits": [{"before": "teh", "after": "the", "reason": "Spelling"}]}
```

`Edit(before, after, reason, start, end)` where `start`/`end` are offsets into
the original selected text. `Edit` comes from the provider without offsets;
`EditSpan` adds them after mapping.

## Core flow

### 1. Trigger

The service samples the frontmost app every 350 ms. A preview runs only when
all of these hold:

- feature enabled in settings;
- Accessibility permission granted;
- the frontmost app is in the supported bundle-id set (Word, Pages, Mail,
  Outlook, TextEdit, Notes) and is not ByteProof itself;
- `kAXSelectedTextAttribute` is non-empty, the selected range length is within
  8..`max_chars` characters;
- the selection fingerprint changed since the last preview;
- no preview is in flight (latest-wins: a newer selection cancels and
  supersedes the older one);
- the selection has been stable for the configured delay (default 900 ms,
  range 400..2000 ms).

The cache key is `(bundle_id, sha1(selected_text), sha1(context_before +
context_after), settings_fingerprint)`. A hit reuses stored edits with zero
provider calls. Cache is LRU, maximum 64 entries, in-memory only.

### 2. Capture

Read `kAXSelectedTextAttribute`, `kAXSelectedTextRangeAttribute` (absolute
start for apply), and up to 200 context characters before/after from
`kAXValueAttribute`. No keystrokes, no clipboard, so monitoring never disturbs
the user.

### 3. AI preview

`preview_edits_once` resolves the preview provider: Local AI when
`live_preview.use_local_model` is true and the local server can start,
otherwise the active provider. It sends the preview system prompt plus the
selection and context, requesting only a compact edits JSON. Output is capped
at 512 tokens and 12 edits. Local model output is passed through
`_clean_local_model_output` before JSON extraction.

### 4. Mapping

For each edit, locate `before` in the selected text: exact match first, then
word-boundary fuzzy matching via `difflib.get_close_matches`. Overlapping
edits resolve deterministically: longer spans win, then earlier spans; losers
are dropped. Edits that cannot be mapped are ignored.

### 5. Rendering

Overlay underlines for AX-cooperative apps: query `AXBoundsForRange` for the
changed character ranges, convert screen coordinates to overlay-local
coordinates, and paint pink-red dashed underlines (rose `#E23A5B`, dash
pattern). The overlay input mask is the union of the underline rectangles, so
it is click-through everywhere else. Hovering an underline opens the popup;
clicking the underline or the popup row applies the edit.

Word: apply real `wdUnderlineDotDotDash` pink formatting to the mapped
sub-ranges of the document (fallback `wdUnderlineDotted`/`wdRed` when a style
constant is unavailable). Underline state is reverted when the selection
changes or the preview is dismissed. The popup is anchored using an estimated
screen rectangle derived from AppleScript page-relative position, window frame
from AX, and zoom; if estimation fails, the popup anchors above the selection
card and lists all edits.

### 6. Apply

Generic apps: `AXReplaceRangeWithText` with the absolute range
(selection start + mapped offsets). Fallbacks in order: set
`kAXSelectedTextRange` then `kAXSelectedTextAttribute`; else clipboard +
activate + paste of the sub-range text. Word: replace the mapped document
sub-range through AppleScript with Track Changes on, then clear that
underline.

After applying, the service re-reads the selection, updates the cache (removes
applied edits, keeps the rest mapped against the new text when possible), and
re-renders without a new provider call.

## Interaction spec

- Underline: pink-red `#E23A5B`, dashed, under the changed span only.
- Hover: immediate popup (within ~100 ms) showing `~~before~~` struck through
  in red, `→ after` in green, and the reason in grey, matching the Proposed
  Changes styling.
- Click: applies that edit; the popup closes; the underline disappears.
- Apply all: popup button runs the existing full-selection proofread apply.
- Dismiss: selection change, app switch, Escape, or feature toggle off hides
  underlines and popup; Word underlines are cleared.
- No preview call repeats for an unchanged selection.

## Efficiency contract

| Measure | Requirement |
| --- | --- |
| Sampling | AX-only, 350 ms, no keystrokes/clipboard |
| Debounce | 900 ms default (400..2000) after last selection change |
| Context | 200 chars per side |
| Selection | 8..1500 chars (max configurable) |
| Output | max 512 tokens, max 12 edits |
| Cache | LRU 64, in-memory, hash-keyed |
| Concurrency | one preview at a time, latest-wins |
| Default provider | Local AI (zero cloud tokens) |

These values are constants in `live_preview.py` and are asserted by tests.

## Settings

`settings.json` gains a top-level block:

```json
"live_preview": {
  "enabled": true,
  "delay_ms": 900,
  "max_chars": 1500,
  "use_local_model": true
}
```

The General page gains "Live suggestions (beta)" (checkbox), "Preview delay"
(400..2000 ms slider), "Prefer Local AI for live suggestions" (checkbox). The
service starts/stops with the toggle.

## Licensing and permissions

- Accessibility permission is required; when missing, the service stays idle
  and the existing permission messaging applies.
- Local AI previews do not consume free-mode daily proofreads. If the user
  opts into the active cloud provider, the existing free-mode/access checks
  from the polish path apply.

## Error handling and degradation

- Unsupported or unreadable app: no preview, no error noise.
- Provider error: clear underlines, log to `capture.log`-style debug log, show
  a toast once per error burst.
- AX bounds unavailable: fall back to the floating selection card with the
  full diff.
- Range replacement unavailable: clipboard fallback; on failure, keep the
  suggestion visible and report "Could not apply".

## Testing strategy

1. Unit (TDD) for `live_preview.py`: parse, mapping (exact, fuzzy, overlaps,
   unmappable), decision table, cache.
2. Unit for settings schema and prompt selection; integration for
   `preview_edits_once` with a mocked transport.
3. Integration for AX helpers against a scriptable TextEdit fixture.
4. Overlay: offscreen paint + hit-test tests; popup content model tests.
5. Service: mocked AX + fake provider; assert debounce, cache hit, single
   in-flight call, apply plan.
6. Word: mocked `osascript` for underline/apply/estimate; a live Word run when
   Word is installed.
7. Live campaign: TextEdit, Mail compose, Pages, Word. Efficiency metrics:
   provider-call count, timestamps, token counts. Accuracy: a fixed corpus of
   defective sentences verified across repeated runs.
8. Regression: full `tests/test_smoke.py` plus packaged build via
   `./build_macos.sh`.

## Risks and mitigations

- Word AX bounds unsupported: native formatting + estimated anchor + card
  fallback (above).
- Local model latency: async worker, debounce, cache, 512-token cap.
- Overlay drift on scroll/resize: 250 ms coordinate refresh while visible,
  NSWorkspace activation notifications for app/window changes.
- Multi-display: overlay follows the display containing the host window.
- macOS 13 lacks `AXReplaceRangeWithText`: fallback chain in Apply (above).
