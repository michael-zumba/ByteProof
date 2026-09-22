# App icon, logo and settings icons — Checkpoint

## What was wrong

1. `fitted_mark` (src/gui.py) laid a mark out in device pixels on a canvas that
   already carried a device pixel ratio, so on any Retina screen every glyph
   was drawn at twice its box and the canvas kept the top-left quarter of it.
   The Settings rail, the Settings identity mark and the menu bar template all
   shipped as fragments.
2. The app icon was one drawing at every size: 16 and 32 px were a grey-green
   smudge, and the plate was full-bleed rather than Apple's 824/1024 grid.
3. `logo/logo.svg` was an auto-trace of a bitmap (potrace transform in the path
   data), so the in-app logo wobbled at the 60-84 px the UI draws it.
4. Settings showed a document glyph while the window, Dock and About showed the
   brain: two marks for one identity.
5. The Live Check page shipped its inline CSS in the sentence under the title
   ("... while you write. #6a6760; font-size: 12px;").

## What changed

Owner direction during the session: the app logo stays the ByteMind logo - the
mark, without the "ByteMind Ltd" wordmark - and the work concentrates on the
icons inside the app.

- **Renderer**: `fitted_mark` now lays out in logical points, and
  `_tinted_pixmap` keeps an icon's aspect ratio instead of stretching it into a
  square. This is the fix that makes every existing glyph render whole.
- **App logo**: `logo/logo.svg` is the ByteMind mark again - the owner's own
  artwork, not a redraw - with the plate removed and the viewBox cropped to the
  ink so it fills the box the UI gives it. An earlier pass in this session had
  replaced it with a lookalike; the file now says so in a comment so a future
  pass does not repeat it.
- **App icon**: `logo/app-icon.svg` = the same mark on Apple's 824 pt plate.
  `scripts/build_app_icons.py` composes `.icns`, `.ico`, `logo.png` and the Store
  tiles, using the line art at 64px and up and the mark filled solid at 16 and
  32, where its strokes are thinner than a pixel. The solid form is derived
  from the artwork (its outline is grown a few pixels to close the right
  hemisphere, which the logo draws open, then filled); nothing is redrawn, and
  the tool refuses to ship a silhouette that came out holed.
- **Icon set**: rail and utility glyphs now come from Lucide 1.47.0 (ISC),
  pinned by version with the upstream licence text in
  `assets/LUCIDE-LICENSE.txt`, bundled into the app and credited in About.
  Files: settings-general, settings-live, settings-automation,
  settings-connect, settings-local, license, update, mail, menubar
  (file-check-2), plus the two chevrons cropped to their ink for the
  stylesheet-sized form controls.
- **Copy**: the Live Check blurb is prose again.
- **Consistency**: the Settings identity mark is the brand mark, so window,
  Settings, About, Dock and the app icon all carry one identity.
- **Promo art**: the traced mark in `assets/hero-banner.svg` and
  `assets/social-preview.svg`/`.png` replaced with the new mark.

## Owner decisions still open

- The menu bar glyph is Lucide's `file-check-2` (a page with a check) rather
  than the mark: at 18 points the mark's two hemispheres merge. Say the word
  and it becomes the mark's solid silhouette instead.
- `assets/menubar-brand.svg` (the old traced brain kept as an alternative) is
  now dead weight; it is one delete away.
