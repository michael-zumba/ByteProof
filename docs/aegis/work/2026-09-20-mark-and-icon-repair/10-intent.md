# App icon, logo and settings icons — Task Intent

## Requested outcome

The owner is not happy with the beta's app icon, logo and in-app icon work.
Wanted: a clean, clear, sharp icon/logo for the app, and the in-app design
reviewed and fixed. Free and open-source icon artwork may be sourced from the
internet and used.

## Goal

Every mark the owner sees - Dock icon, Finder icon, menu bar template, window
and About logos, Settings rail glyphs and page chrome - is crisp at the size it
is drawn, comes from a source that is legally clean for a proprietary app, and
no longer carries the artefacts of traced bitmap artwork or a mis-scaled
renderer.

## Success evidence

- Every mark renders whole at 1x and 2x (measured ink bounds inside the canvas).
- The app icon has artwork per size: line art at 64 and up, a solid silhouette
  at 16 and 32, inside Apple's 824/1024 plate.
- Rail and utility glyphs come from an open-source icon set with the licence
  text shipped in the repository and in the app bundle.
- Full test suite green, including new regression tests that fail against the
  old renderer and the old copy.
- The beta is built, signed, notarised and installed at /Applications.

## Stop condition

- `done`: the evidence above is true and the owner has the beta installed.
- `needs-verification`: artwork shipped without the rendering and size checks.
- `blocked`: no source of openly licensed artwork that fits a proprietary app.

## Non-goals

- Redesigning the product's identity: the brain mark stays, as the ByteMind
  website shares it.
- Updating the website's own copies of the icon (`assets/byteproof/*.png`,
  favicon) or the Microsoft Store listing art: separate surfaces, flagged to
  the owner.
