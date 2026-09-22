# App icon, logo and settings icons — Evidence

## Regression tests, red before green

- `QT_QPA_PLATFORM=offscreen ./venv/bin/python -m pytest tests/test_hardening.py
  -k "retina or not_square or screen_they_are_on"` → **3 failed** before the
  renderer fix (`test_marks_are_laid_out_in_points_on_a_retina_screen`,
  `test_the_rail_and_menu_bar_marks_survive_a_retina_screen`,
  `test_a_mark_keeps_its_shape_when_it_is_not_square`), **passed** after.
  Measured on the old code, 16pt at 2x: ink at (13, 3)-(31, 31) of a 32px
  canvas - the top-left quarter of the mark - against (2, 2)-(30, 30) after.
  The same measurement on `logo.svg` at 24pt/2x ran off the canvas edge
  ((4, 5)-(45, 47) of 48).
- `... -k blurbs` → **failed** with the Live Check blurb restored
  ("... while you write. #6a6760; font-size: 12px;"), **passed** after the
  copy fix.

## Full suite

- `./scripts/run_tests.sh` → `test_hardening.py 159 passed`,
  `test_live_preview.py 137 passed`, `test_smoke.py 104 passed`,
  "All test files passed."

## Artwork

- The app logo is the ByteMind mark: ink box (232, 251)-(795, 774) in the old
  1024 canvas, now `logo/logo.svg` with `viewBox="220 239 588 548"` and no
  plate, so the UI draws it edge to edge instead of inside a white card.
- `scripts/build_app_icons.py` builds `logo.png`, `app_icon.png`, `logo.icns`
  (10 entries), `logo.ico` (7 entries) and the three MSIX tiles. 16, 24, 32 and
  48 use the solid form; 64 and up use the line art.
- The solid form is derived, not drawn: the artwork's outline is grown 4px at a
  320px mask to close the hemisphere the logo draws open, then filled. The
  builder aborts if the filled shape is not at least 1.15x the ink it came
  from - the check that caught the first attempt leaking and shipping a
  shattered silhouette.
- Glyphs: Lucide 1.47.0 (ISC), pinned; licence text in
  `assets/LUCIDE-LICENSE.txt`, bundled by both spec files, credited in About.

## Rendered proof (offscreen, 2x)

- Settings dialog: rail glyphs draw whole on both the cream row and the
  selected green pill; page headers show prose only.
- Main window and About: the ByteMind mark at 68 and 84px, sharp on cream.
- Icon ladder 16/32/64/128/256/1024 on a dark background: solid lobes at 16 and
  32, crisp line art from 64 up.

## Shipped build

- `./venv/bin/python tools/bump_version.py 2.2.1-beta.6` (pre-release: the
  public feed is untouched), `./build_macos.sh arm64` - signed, notarised and
  stapled.
- `ditto dist/ByteProof.app /Applications/ByteProof.app` → installed beta is
  2.2.1-beta.6, and `logo.icns`, `logo.svg` and `logo.png` inside the bundle
  hash-match the repository files.
