# Suggestion panel polish — Checkpoint

## What the owner's screenshot showed

The panel was functional but read as a form: five full-width grey category
bars acting as separators, six equally loud filled-blue buttons (five row
applies plus "Apply all" bottom-left), a severity dot floating in the gutter,
mixed corner radii (20/12/20), and dense red strikethroughs with arrows that
could dangle at a wrapped line end.

## What changed (presentation only)

### One card per suggestion

The category tag moved inside the suggestion card as a small footer tag that
hugs its text; the between-row hairlines are gone and the list breathes with a
12px rhythm. The severity dot now sits on the card's first text line.

### One primary action

Per-row "Apply" is a tonal button (light blue fill, blue text) and "Apply all"
is the only filled primary, right-aligned in the footer. The dismiss × is a
slightly larger ghost button so it is easier to hit.

### Calmer diffs

`diff_html(..., panel_style=True)` is the panel's own spelling: a softer red
for removals and a non-breaking arrow so "old → new" cannot split across a
wrapped line. The default spelling (the main window's review view) is
untouched and tested.

### Chrome

Cards are white with a hairline border (subtle hover tint) instead of grey
fill; controls are 8px, cards 12px, panel 20px, one language. Lists longer
than five suggestions get a slim styled scrollbar, and the checking/clean
states inherit the same header and spacing.

## Deliberately not changed

- No signals, handlers, labels, tooltips, tooltip text, ordering or counts.
- The severity dot colors still mean spelling/grammar/style, untouched.
- Width/height computation and placement (`place_near`, `_clamp_rect`) are
  untouched.
- Focus rings were considered and skipped: the panel uses
  `WindowDoesNotAcceptFocus`, so keyboard focus never lands on its controls.
- The single-edit popup keeps Apply as its primary (it has one suggestion);
  it only inherits the shared chrome and diff spelling.

## Verified in the running build

2.2.2-beta.6 built, signed, notarized (accepted), and installed to
`/Applications`; the previous beta.5 bundle moved to
`previous-versions/ByteProof_2.2.2-beta.5.app`. The installed app reports
2.2.2-beta.6, passes `codesign --verify --deep --strict`, started with
Accessibility already trusted, and the offscreen renders plus the test suite
cover the three design decisions. Details in 90-evidence.md.

## Still open (owner-visible)

* The owner's look at the installed beta is the real confirmation. The two
  screenshots next to this record are offscreen renders of synthetic content
  at the same size, so they compare like for like.
* If the panel ever gains keyboard focus, focus-visible styling becomes worth
  adding.
